"""C1 回归：快照/数据目录体积只遍历一次，且 async `/status` 的遍历不占事件循环

判据来自 `perf/2026-10-31-full-api/OPTIMIZATIONS.md` C1：`_list_snapshots` 原先为
`size_bytes` 与 `size_display` 各调一次 `_dir_size(d)`，同一棵子树走两遍
（实测 `GET /api/vector-admin/snapshots` avg 233.8 ms 的主因）。
本文件钉三件事：① 每个快照目录只遍历一次；② 两个字段来自同一个值；
③ `/status`（async handler）把磁盘遍历交给了工作线程 —— 事件循环被同步 rglob
占住时，同一 key 的单飞跟随者（B1）只能干等，合并重算救不了长尾。
"""
from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from xianyu_hunter.web import cache as web_cache
from xianyu_hunter.web.routes import api_vector_admin as va

KIB = 1024


class _FakeStore:
    """只暴露 api_vector_admin 用到的三个成员，不碰真 ChromaDB"""

    def __init__(self, persist_path: Path) -> None:
        self.persist_path = str(persist_path)
        self.collection_name = "kb_chunks"

    async def count(self) -> int:
        return 7


def _mkfile(path: Path, size: int) -> None:
    """按逻辑大小造文件：truncate 出稀疏文件，不真占内存/磁盘

    写成 `b"\\0" * size` 的话，`7 GiB` 那一档会在内存里造出 7 GiB 字节串
    （实测把整个文件从 53 s 拖到 400 s）。`_dir_size` 只看 `st_size`，稀疏文件等价。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)


@pytest.fixture
def persist(tmp_path: Path) -> Path:
    """data/ 形状：主目录散文件 + 一个普通子目录 + snapshots/ 下两个快照

    每个快照目录里的字节数刻意不同（1.5 KiB / 3 KiB），这样「两遍遍历」和
    「拿错目录的体积」都能被断言抓住。
    """
    root = tmp_path / "data"
    _mkfile(root / "chroma.sqlite3", 100)
    _mkfile(root / "collection" / "nested" / "bin", 200)
    _mkfile(root / "snapshots" / "manual_20261030" / "a.bin", 1536)
    _mkfile(root / "snapshots" / "v3" / "b.bin", 1024)
    _mkfile(root / "snapshots" / "v3" / "sub" / "c.bin", 2048)
    return root


@pytest.fixture
def walk_once(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """把 `_dir_size` 换成「记录调用目标 + 转调原实现」的版本

    只计数不改行为，所以体积断言与遍历次数断言用的是同一份真实遍历。
    """
    calls: list[tuple[str, int]] = []
    original = va._dir_size

    def spy(path: Path) -> int:
        size = original(path)
        calls.append((path.name, size))
        return size

    monkeypatch.setattr(va, "_dir_size", spy)
    return calls


# ==================== 1. 每个目录只遍历一次 ====================
def test_list_snapshots_walks_each_dir_once(persist, walk_once):
    items = va._list_snapshots(_FakeStore(persist))
    assert [name for name, _ in walk_once] == ["manual_20261030", "v3"], (
        f"每个快照目录应各遍历一次，实际遍历序列：{walk_once}"
    )


def test_status_walks_snapshot_dirs_once(persist, walk_once, monkeypatch):
    """`/status` 复用同一个 `_list_snapshots`，所以它同样只走一遍

    主目录体积（`_main_data_size`）也调 `_dir_size`，故这里按名字筛快照目录，
    避免把「collection」子目录那一次算成重复。
    """
    result = asyncio.run(_call_status(persist, monkeypatch))
    snapshot_walks = [c for c in walk_once if c[0] in ("manual_20261030", "v3")]
    assert len(snapshot_walks) == 2
    assert result["snapshot_count"] == 2


# ==================== 2. 两个字段同源，不会自相矛盾 ====================
@pytest.mark.parametrize(
    "size_bytes,expected_display",
    [
        (0, "0.0 B"),
        (1023, "1023.0 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (3072, "3.0 KB"),
    ],
)
def test_size_display_is_format_of_size_bytes(persist, size_bytes, expected_display):
    """display 必须由同一个 bytes 值格式化而来

    这里只用小文件走端到端（`_format_size` 的 MB/GB/TB 分支由下面的纯算术用例覆盖：
    为它造 7 GiB 真文件会把临时盘写满，实测触发过 `Errno 28 No space left on device`）。
    """
    _mkfile(persist / "snapshots" / "manual_20261030" / "a.bin", size_bytes)
    items = va._list_snapshots(_FakeStore(persist))
    by_id = {it["id"]: it for it in items}
    assert by_id["manual_20261030"]["size_bytes"] == size_bytes
    assert by_id["manual_20261030"]["size_display"] == expected_display


@pytest.mark.parametrize(
    "size_bytes,expected_display",
    [
        (5 * KIB * KIB, "5.0 MB"),
        (1024 * KIB * KIB, "1.0 GB"),
        (7 * KIB * KIB * KIB, "7.0 GB"),
        (2 * KIB * KIB * KIB * KIB, "2.0 TB"),
    ],
)
def test_format_size_upper_branches(size_bytes, expected_display):
    """MB/GB/TB 分支：大数不值得落盘，直接测格式化函数本身"""
    assert va._format_size(size_bytes) == expected_display


def test_two_snapshots_do_not_share_size(persist):
    """两个快照体积不同：若有人把遍历结果缓存成一份，第二个会拿到第一个的值"""
    by_id = {it["id"]: it for it in va._list_snapshots(_FakeStore(persist))}
    assert by_id["manual_20261030"]["size_bytes"] == 1536
    assert by_id["v3"]["size_bytes"] == 3072
    assert by_id["v3"]["size_display"] == "3.0 KB"


# ==================== 3. 体积语义不变 ====================
def test_nested_directories_count_and_dirs_contribute_zero(persist):
    """递归到嵌套子目录，目录本身不计字节（原来靠 is_file() 过滤，改遍历方式不能丢）"""
    _mkfile(persist / "snapshots" / "v3" / "sub" / "deep" / "d.bin", 1)
    by_id = {it["id"]: it for it in va._list_snapshots(_FakeStore(persist))}
    assert by_id["v3"]["size_bytes"] == 1024 + 2048 + 1


def test_missing_root_lists_nothing(tmp_path):
    assert va._list_snapshots(_FakeStore(tmp_path / "absent")) == []


def test_main_data_size_excludes_snapshots(persist):
    """`data_size_bytes` 不含 snapshots/，否则快照膨胀会把「主数据大小」带跑"""
    assert va._main_data_size(persist) == 100 + 200


def test_main_data_size_on_missing_root(tmp_path):
    assert va._main_data_size(tmp_path / "absent") == 0


# ==================== 4. /status 的遍历不在事件循环上 ====================
async def _call_status(persist: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    store = _FakeStore(persist)
    monkeypatch.setattr(va, "_get_chatbot_or_403", lambda: {"vector_store": store})
    # bypass_cache=True：既不读也不写「命中」路径，但包装器仍会 _set_cache，
    # 所以收尾要清一次，免得假 container 的体积活到其他用例
    try:
        return await va.get_status(bypass_cache=True)
    finally:
        web_cache.invalidate_stats_cache()


def test_status_disk_walk_happens_off_the_event_loop(persist, monkeypatch):
    """`_dir_size` 必须在非当前线程里被调用

    退化路径（把遍历直接写回 async handler 的循环里）在这里翻红：那时
    `threading.get_ident()` 等于主线程 ident。
    """
    idents: list[int] = []
    original = va._dir_size
    monkeypatch.setattr(
        va, "_dir_size", lambda p: (idents.append(threading.get_ident()), original(p))[1]
    )

    async def scenario() -> dict[str, Any]:
        loop_thread = threading.get_ident()
        task = asyncio.create_task(_call_status(persist, monkeypatch))
        ticks = 0
        while not task.done():
            # 循环只有在遍历真的让出了线程时才有机会转起来
            await asyncio.sleep(0)
            ticks += 1
            if ticks > 2000:
                break
        result = await task
        assert idents and loop_thread not in idents, (
            "磁盘遍历跑在事件循环线程上，会占住同 key 的单飞跟随者"
        )
        return result

    result = asyncio.run(scenario())
    assert result["snapshot_count"] == 2


def test_status_fields(persist, monkeypatch):
    result = asyncio.run(_call_status(persist, monkeypatch))
    assert result["chunk_count"] == 7
    assert result["collection_name"] == "kb_chunks"
    assert result["data_size_bytes"] == 300
    assert result["data_size_display"] == "300.0 B"
    assert result["snapshot_count"] == 2
    assert result["last_snapshot_at"] is not None


def test_status_empty_when_no_snapshots(persist, monkeypatch):
    shutil.rmtree(persist / "snapshots")
    result = asyncio.run(_call_status(persist, monkeypatch))
    assert result["snapshot_count"] == 0
    assert result["last_snapshot_at"] is None
