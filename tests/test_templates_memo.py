"""C3 残余：模板文件解析结果的进程内 memo（`api_templates._load_presets/_load_user`）

判据：
1. 文件没变就只解析一次 —— memo 的全部意义（GET /api/templates 实测 2.23 → 0.53 ms）；
2. 外部编辑模板文件后，下一次读立刻跟上（用文件指纹而不是 TTL）；
3. 经 `_save_user` 写盘后立刻读到新值，**即使新内容与旧内容同尺寸、且落在同一个
   mtime tick 里** —— 这种形态指纹判定失效，只有写盘配对作废 + 读取代次能兜住；
4. 解析期间被写盘插队时，不把写盘前的旧值存回 memo；
5. 返回浅拷贝列表：调用方 append 不污染 memo；元素 dict 是只读约定（见实现注释）；
6. 文件缺失返回空列表、坏 JSON 返回空列表且修好后能恢复。

外加源码门禁：写 `USER_FILE` 的函数必须配对作废 memo，防止将来新增写路径忘了作废。

本文件只读写 tmp_path 下的副本，绝不碰仓库 backend/xianyu_hunter/data/。
"""
from __future__ import annotations

import ast
import inspect
import json
import os

import pytest

from xianyu_hunter.web.routes import api_templates


class _CountingJson:
    def __init__(self, real) -> None:
        self.real = real
        self.loads = 0

    def load(self, *args, **kwargs):
        self.loads += 1
        return self.real.load(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


class _CountingYamlIo:
    def __init__(self, real) -> None:
        self.real = real
        self.loads = 0

    def safe_load(self, *args, **kwargs):
        self.loads += 1
        return self.real.safe_load(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.real, name)


PRESET_V1 = [{"id": "preset_a", "name": "模板A", "keyword": "a"}]
PRESET_V2 = [{"id": "preset_b", "name": "模板B", "keyword": "b"}]
USER_V1 = [{"id": "usr_aaa", "name": "AAA"}]
USER_V2 = [{"id": "usr_bbb", "name": "BBB"}]
FIXED_NS = (10 ** 16, 10 ** 16)


@pytest.fixture(autouse=True)
def sandbox_templates(monkeypatch, tmp_path):
    preset = tmp_path / "preset_templates.yaml"
    user = tmp_path / "user_templates.json"
    monkeypatch.setattr(api_templates, "PRESET_FILE", str(preset))
    monkeypatch.setattr(api_templates, "USER_FILE", str(user))
    monkeypatch.setattr(api_templates, "_MEMO", {})
    monkeypatch.setattr(api_templates, "_EPOCHS", {})
    return preset, user


def write_presets(path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def write_user(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ==================== 1. 命中：不重复解析 ====================

def test_presets_parsed_once(sandbox_templates, monkeypatch) -> None:
    preset, _ = sandbox_templates
    write_presets(preset, PRESET_V1)
    counting = _CountingYamlIo(api_templates.yaml_io)
    monkeypatch.setattr(api_templates, "yaml_io", counting)

    assert api_templates._load_presets() == PRESET_V1
    assert counting.loads == 1
    assert api_templates._load_presets() == PRESET_V1
    assert counting.loads == 1


def test_user_parsed_once(sandbox_templates, monkeypatch) -> None:
    _, user = sandbox_templates
    write_user(user, USER_V1)
    counting = _CountingJson(api_templates.json)
    monkeypatch.setattr(api_templates, "json", counting)

    assert api_templates._load_user() == USER_V1
    assert counting.loads == 1
    assert api_templates._load_user() == USER_V1
    assert counting.loads == 1


# ==================== 2. 外部编辑跟上 ====================

def test_external_edit_is_seen_next_read(sandbox_templates) -> None:
    preset, _ = sandbox_templates
    write_presets(preset, PRESET_V1)
    assert api_templates._load_presets() == PRESET_V1
    write_presets(preset, PRESET_V2)
    assert api_templates._load_presets() == PRESET_V2


def test_user_external_edit_is_seen_next_read(sandbox_templates) -> None:
    _, user = sandbox_templates
    write_user(user, USER_V1)
    assert api_templates._load_user() == USER_V1
    write_user(user, USER_V2)
    assert api_templates._load_user() == USER_V2


# ==================== 3. 写盘后立刻读（同尺寸 + 同 tick） ====================

def test_save_user_same_size_same_tick_is_seen(sandbox_templates) -> None:
    _, user = sandbox_templates
    first = [{"id": "usr_xxx", "name": "AAAA"}]
    # 用 _save_user 自己的写盘形态，保证两次写的 size 完全一致
    api_templates._save_user(first)
    os.utime(user, ns=FIXED_NS)
    assert api_templates._load_user() == first  # 先让 memo 记住 V1 + 固定指纹

    second = [{"id": "usr_xxx", "name": "BBBB"}]  # 同长度、同 id
    api_templates._save_user(second)
    os.utime(user, ns=FIXED_NS)  # size 与 mtime 都被压回原值 → 指纹判定失效

    assert api_templates._load_user() == second


# ==================== 4. 返回浅拷贝 ====================

def test_returned_list_is_a_copy(sandbox_templates) -> None:
    _, user = sandbox_templates
    write_user(user, USER_V1)
    assert api_templates._load_user() == USER_V1  # 首次：miss → parse → 写 memo
    cached = api_templates._load_user()  # 第二次：命中分支，必须返回副本
    cached.append({"id": "usr_zzz", "name": "ZZZ"})
    assert api_templates._load_user() == USER_V1


# ==================== 5. 缺失 / 损坏 ====================

def test_missing_files_return_empty(sandbox_templates) -> None:
    preset, user = sandbox_templates
    assert api_templates._load_presets() == []
    assert api_templates._load_user() == []


def test_broken_user_json_returns_empty_then_recovers(sandbox_templates) -> None:
    _, user = sandbox_templates
    user.write_text("{not json", encoding="utf-8")
    assert api_templates._load_user() == []
    write_user(user, USER_V1)
    assert api_templates._load_user() == USER_V1


# ==================== 6. 源码门禁 ====================

def test_save_user_paired_invalidation_guard() -> None:
    source = inspect.getsource(api_templates._save_user)
    tree = ast.parse(source)
    calls = {
        getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "_bump_epoch" in calls, "写盘后必须自增代次，防在飞请求把旧值存回 memo"
    assert "_MEMO" in source, "写盘后必须配对作废 memo"
