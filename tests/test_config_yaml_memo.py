"""B2 残留：config.yaml 解析结果的进程内 memo（`api_config._load_yaml`）

判据按「memo 只有这几条同时成立才算安全」来写：
1. 文件没变就只解析一次 —— memo 的全部意义（专项探针量到解析占 52 ms/请求）；
2. 手工编辑配置文件后，下一次读立刻跟上 —— 用文件指纹而不是 TTL 才有这条；
3. 经 API 写盘后立刻读到新值，**即使新内容与旧内容同尺寸、且落在同一个 mtime tick
   里** —— Windows 文件系统时间粒度约 15.6 ms，这种情况下指纹判定失效，只有写盘配对
   作废能兜住「保存后立刻读」这条产品主循环；
4. 指纹含文件路径 —— 两份「同尺寸 + 同时间戳」的不同文件不得互相串味；
5. 调用方原地改写返回值不污染 memo —— `_deep_merge` 合并、`_redact_terminal` 脱敏
   都是就地操作；
6. 并发读不把「写盘前的旧值」存回 memo —— 解析期间可能被别的请求写盘插队，只有
   读取代次（`_yaml_epoch`）没变过才允许写回。

外加源码门禁：任何写 `CONFIG_FILE` 的函数必须配对作废 memo，未来新增写路径忘了
作废会直接被钉红。另有一条用例专门钉住「绕过 API 的同指纹写盘，memo 挡不住」这个
残余边界，免得后来人把「靠文件指纹」当成万能保障。

本文件只读写 tmp_path 下的副本，绝不碰仓库 config/config.yaml。
"""
from __future__ import annotations

import ast
import inspect
import os

import pytest
import yaml

from xianyu_hunter.web.routes import api_config

DISK_V1 = {"search": {"keyword": "AAAA", "min_price": 10}}
DISK_V2 = {"search": {"keyword": "BBBB", "min_price": 10}}
# 「AAAA」→「BBBB」同长度，dump 后字节数一致；用来复现同尺寸写盘
FIXED_NS = (10 ** 16, 10 ** 16)


class _CountingYaml:
    """只替换 api_config 模块里绑定的 yaml 名字，不影响其他模块的解析计数"""

    def __init__(self, real) -> None:
        self.real = real
        self.loads = 0

    def safe_load(self, text, *args, **kwargs):
        self.loads += 1
        return self.real.safe_load(text, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture(autouse=True)
def sandbox_config(monkeypatch, tmp_path):
    """把 CONFIG_FILE 指到 tmp 副本，并保证 memo 从空开始、结束时不外漏"""
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(api_config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(api_config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(api_config, "reload_config", lambda *a, **k: None)
    monkeypatch.setattr(api_config, "_yaml_memo", None)
    return cfg


@pytest.fixture
def counter(monkeypatch):
    counting = _CountingYaml(yaml)
    monkeypatch.setattr(api_config, "yaml", counting)
    return counting


def write_disk(data: dict, *, stamp: tuple[int, int] | None = None) -> None:
    path = api_config.CONFIG_FILE
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if stamp is not None:
        os.utime(path, ns=stamp)


def fingerprint() -> tuple[int, int]:
    st = api_config.CONFIG_FILE.stat()
    return (st.st_mtime_ns, st.st_size)


# ==================== 1. 命中：不重复解析 ====================
def test_unchanged_file_parses_only_once(sandbox_config, counter):
    write_disk(DISK_V1)

    first = api_config._load_yaml()
    second = api_config._load_yaml()
    third = api_config._load_yaml()

    assert counter.loads == 1, "同一份未变的文件被解析了多次，memo 没生效"
    assert first == second == third == {**DISK_V1}


# ==================== 2. 手工编辑立刻生效 ====================
def test_external_edit_is_picked_up_immediately(sandbox_config, counter):
    """不走 API 的编辑（直接改文件）也要下一次读就跟上 —— 这是「用指纹不用 TTL」的核心理由"""
    write_disk(DISK_V1)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"

    write_disk(DISK_V2)
    out = api_config._load_yaml()
    assert out["search"]["keyword"] == "BBBB"
    assert counter.loads == 2


def test_missing_file_then_created(sandbox_config):
    assert api_config._load_yaml() == {}
    write_disk(DISK_V1)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"


# ==================== 3. 写盘配对作废（指纹失效的那条兜底） ====================
def test_api_write_invalidates_even_when_fingerprint_is_identical(sandbox_config, counter):
    """同尺寸 + 同一 mtime tick：光靠指纹必然读到旧值，所以写盘必须显式作废"""
    write_disk(DISK_V1, stamp=FIXED_NS)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"
    before = fingerprint()

    body = api_config.ConfigSaveBody(payload={"search": {"keyword": "BBBB"}}, dry_run=False)
    assert api_config.save_config(body)["ok"] is True
    # 复现 Windows 的时间戳碰撞：写盘后把 mtime 强制改回同一点，且内容同尺寸
    os.utime(api_config.CONFIG_FILE, ns=FIXED_NS)
    assert fingerprint() == before, "样本没构造成功：指纹必须相同，否则这条用例没有牙"

    out = api_config._load_yaml()
    assert out["search"]["keyword"] == "BBBB", "写盘后没作废 memo，读到了旧值"
    # save_config 内部那次 _load_yaml 走命中，作废后这一次才是真解析
    assert counter.loads == 2


def test_rollback_path_also_invalidates(sandbox_config):
    """备份恢复同样要作废 memo —— 与上一条同样的构造，只是走 restore-backup 这条路"""
    write_disk(DISK_V1, stamp=FIXED_NS)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"
    bak = api_config.BACKUP_DIR / "config.yaml.bak.20260101_000000"
    bak.parent.mkdir(parents=True, exist_ok=True)
    bak.write_text(
        yaml.safe_dump(DISK_V2, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    assert api_config.restore_backup(bak.name)["ok"] is True
    os.utime(api_config.CONFIG_FILE, ns=FIXED_NS)  # 同尺寸 + 同一时间戳，只剩显式作废
    assert api_config._load_yaml()["search"]["keyword"] == "BBBB"


def test_out_of_band_same_fingerprint_write_is_the_residual_gap(sandbox_config):
    """残余风险要钉住：别的模块绕过 api_config 写同一份文件时，只有指纹判定在挡

    这条用例断言的是「memo 在此场景会读到旧值」，用来锁死设计边界：不能把
    「靠文件指纹」当成万能保障，跨模块写路径必须自己保证 mtime 或尺寸有变化
    （真实进程里两次写盘之间必然跨过时间粒度，且 API 写路径已配对作废）。
    """
    write_disk(DISK_V1, stamp=FIXED_NS)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"

    # 绕过 API：直接换成同尺寸内容并把时间戳拨回同一点
    api_config.CONFIG_FILE.write_text(
        yaml.safe_dump(DISK_V2, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    os.utime(api_config.CONFIG_FILE, ns=FIXED_NS)

    assert api_config._load_yaml()["search"]["keyword"] == "AAAA", (
        "行为变了：如果这里读到了 BBBB，说明失效判定已不依赖写盘配对作废，"
        "本条与上一条用例应合并成一条"
    )


# ==================== 4. 并发读不把写盘前的旧值存回 memo ====================
def test_read_overlapping_a_save_does_not_cache_the_pre_write_value(sandbox_config, monkeypatch):
    """并发窗口：解析进行到一半时另一个请求完成了一次「同尺寸 + 同 tick」的写盘

    这个请求返回写盘前的内容是可以的（它的读取快照本就早于写盘）；不能接受的是
    **把那份旧值存回 memo** —— 指纹在新旧两版之间完全相同，之后每个请求都会命中旧值，
    直到有人做了一次改变尺寸的写盘。代次（`_yaml_epoch`）就是挡这个的。
    """
    write_disk(DISK_V1, stamp=FIXED_NS)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"
    monkeypatch.setattr(api_config, "_yaml_memo", None)

    class _RacingYaml(_CountingYaml):
        def safe_load(self, text, *args, **kwargs):
            result = super().safe_load(text, *args, **kwargs)
            # 模拟另一个请求刚好在这之后落盘：同尺寸、时间戳也拨回同一点
            api_config.CONFIG_FILE.write_text(
                yaml.safe_dump(DISK_V2, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            os.utime(api_config.CONFIG_FILE, ns=FIXED_NS)
            api_config._invalidate_yaml_memo()
            return result

    racing = _RacingYaml(yaml)
    monkeypatch.setattr(api_config, "yaml", racing)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"  # 本次读到的是快照，允许

    monkeypatch.setattr(api_config, "yaml", _CountingYaml(yaml))
    assert api_config._load_yaml()["search"]["keyword"] == "BBBB", (
        "写盘前的旧值被存回了 memo：指纹相同，之后所有请求都会读到 AAAA"
    )


def test_switching_config_file_does_not_serve_previous_path(sandbox_config, monkeypatch, tmp_path):
    """指纹必须带路径：两份「同尺寸 + 同时间戳」的不同文件不能互相串味

    生产里对应「切配置目录/多实例」，测试里对应每个用例自己的 tmp 副本 ——
    少了路径，本文件从第二个用例起就会读到上一个文件的缓存。
    """
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(
        yaml.safe_dump(DISK_V1, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    b.write_text(
        yaml.safe_dump(DISK_V2, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    os.utime(a, ns=FIXED_NS)
    os.utime(b, ns=FIXED_NS)
    assert a.stat().st_size == b.stat().st_size
    assert a.stat().st_mtime_ns == b.stat().st_mtime_ns, "样本没构造成功：时间戳必须相同"

    monkeypatch.setattr(api_config, "CONFIG_FILE", a)
    assert api_config._load_yaml()["search"]["keyword"] == "AAAA"

    monkeypatch.setattr(api_config, "CONFIG_FILE", b)
    assert api_config._load_yaml()["search"]["keyword"] == "BBBB", "指纹没带路径，读到了另一个文件的缓存"


# ==================== 5. 返回值不被写脏 ====================
def test_returned_dict_is_a_deep_copy(sandbox_config):
    """调用方会原地改这份 dict（合并、逐层脱敏）；共享对象等于把缓存改脏

    `_load_yaml` 有两条 return（冷路径、命中路径），两条都必须是深拷贝 —— 只测一条
    会让另一条的退化悄悄通过（第一版就在这里漏了）。
    """
    write_disk(DISK_V1)

    cold = api_config._load_yaml()  # 冷路径：返回的就是刚存进 memo 的那个对象
    cold["search"]["keyword"] = "MUTATED"
    cold["injected"] = True

    warm = api_config._load_yaml()  # 命中路径
    assert warm["search"]["keyword"] == "AAAA", "冷路径把 memo 里的对象直接交给了调用方"
    assert "injected" not in warm

    warm["search"]["keyword"] = "MUTATED_AGAIN"
    again = api_config._load_yaml()
    assert again["search"]["keyword"] == "AAAA", "命中路径把 memo 里的对象直接交给了调用方"


def test_save_flow_reads_fresh_value_after_its_own_write(sandbox_config, counter):
    """连续两次 POST /save：第二次必须看到第一次的结果

    这条盯的是「save 用自己的 `_load_yaml()` 当下盘内容做基线」。若 memo 不在写盘后
    作废，第二次保存会拿第一次保存之前的旧基线合并，把用户刚存的东西覆盖回旧值。
    """
    write_disk(DISK_V1)
    api_config.save_config(
        api_config.ConfigSaveBody(payload={"search": {"min_price": 99}}, dry_run=False)
    )
    body = api_config.ConfigSaveBody(payload={"search": {"keyword": "BBBB"}}, dry_run=False)
    out = api_config.save_config(body)
    assert [d["path"] for d in out["diffs"]] == ["search.keyword"], (
        f"第二次保存的基线不该是第一次之前的旧值：{out['diffs']}"
    )
    assert counter.loads == 2, "两次保存各重解析一次；多算说明写盘后 memo 被重复作废"


# ==================== 6. 读接口端到端：memo 不得让任何响应给出旧值 ====================
def test_raw_endpoint_reflects_the_latest_file(sandbox_config):
    write_disk(DISK_V1)
    assert "AAAA" in api_config.get_raw_yaml()["raw"]

    api_config.save_config(
        api_config.ConfigSaveBody(payload={"search": {"keyword": "BBBB"}}, dry_run=False)
    )
    raw = api_config.get_raw_yaml()["raw"]
    assert "BBBB" in raw and "AAAA" not in raw


# ==================== 7. 源码门禁：写 CONFIG_FILE 必须配对作废 ====================
def _calls_writing(node: ast.AST, name: str, attr: str) -> list[ast.AST]:
    hits = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
            continue
        if sub.func.attr != attr:
            continue
        target = sub.func.value
        if isinstance(target, ast.Name) and target.id == name:
            hits.append(sub)
    return hits


def test_every_config_write_invalidates_memo():
    """每个 `CONFIG_FILE.write_text(...)` 都要有一个 `_invalidate_yaml_memo()` 配对

    只靠文件指纹不够（见上），所以作废不能漏；这里按「写点数量 == 作废数量」判，
    漏一个或写在别的函数里都会红。
    """
    tree = ast.parse(inspect.getsource(api_config))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = _calls_writing(fn, "CONFIG_FILE", "write_text")
        if not writes:
            continue
        invalidates = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_invalidate_yaml_memo"
        ]
        if len(invalidates) < len(writes):
            offenders.append(
                f"{fn.name}: 写盘 {len(writes)} 处、作废 {len(invalidates)} 处"
            )
    assert not offenders, "以下函数写了 config.yaml 却没配足够的 memo 作废：\n" + "\n".join(
        offenders
    )


def test_writes_actually_exist_in_source():
    """门禁自身不能空转：6 个写点少了就说明有人重构掉了，得让人看见"""
    tree = ast.parse(inspect.getsource(api_config))
    total = sum(
        len(_calls_writing(fn, "CONFIG_FILE", "write_text"))
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert total >= 6, f"预期至少 6 处 CONFIG_FILE 写盘，实际 {total} 处"
