"""P1-5 数据导出 API 单元测试"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xianyu_hunter.config import get_settings
from xianyu_hunter.infra.repository import Repository
from xianyu_hunter.web.app import app
from xianyu_hunter.web.deps import get_container


_TEST_TOKEN = "test-token-for-export-api"


@pytest.fixture
def tmp_repo() -> Repository:
    """使用临时数据库"""
    import tempfile
    from pathlib import Path
    # 清除 get_settings 缓存，设置测试 token
    get_settings.cache_clear()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db_path = str(Path(d) / "test.db")
        repo = Repository(db_path)
        yield repo
        repo.engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
def client(tmp_repo: Repository, monkeypatch) -> TestClient:
    """构造 TestClient，注入临时 repository 和测试 token"""
    # 直接 patch get_settings 返回带测试 token 的实例，
    # 避免认证中间件读取真实 .env 中的 token
    from xianyu_hunter.config import Settings
    test_settings = Settings(web_token=_TEST_TOKEN)
    monkeypatch.setattr(
        "xianyu_hunter.config.get_settings",
        lambda: test_settings,
    )
    # app.py dispatch 内部 from xianyu_hunter.config import get_settings
    # 所以也要 patch app 模块内的引用（虽然 dispatch 内部是局部导入，但保险起见）
    monkeypatch.setattr(
        "xianyu_hunter.web.app.get_settings",
        lambda: test_settings,
        raising=False,
    )

    class _FakeContainer:
        def __init__(self, repo):
            self.repo = repo
    fake = _FakeContainer(tmp_repo)
    app.dependency_overrides[get_container] = lambda: fake
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _auth_headers() -> dict[str, str]:
    """返回带 Bearer token 的请求头"""
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


def _seed_data(repo: Repository) -> None:
    """填充测试数据：1 个任务 + 2 个商品 + 1 个评估 + 1 个订单 + 2 个事件"""
    now = datetime.now(timezone.utc)
    repo.upsert_task({
        "id": "t1", "name": "测试任务", "keyword": "iphone",
        "mode": "confirm", "cron": "*/5 * * * *",
    })
    repo.batch_upsert_items([
        {
            "id": "i1", "task_id": "t1", "title": "iPhone 13",
            "price": 3999.0, "seller_id": "s1", "first_seen": now,
        },
        {
            "id": "i2", "task_id": "t1", "title": "iPhone 14",
            "price": 4999.0, "seller_id": "s2", "first_seen": now - timedelta(days=1),
        },
    ])
    repo.save_evaluation({
        "item_id": "i1", "seller_id": "s1", "score": 80,
        "risk_level": "low", "created_at": now,
    })
    repo.upsert_order({
        "id": "o1", "task_id": "t1", "item_id": "i1", "seller_id": "s1",
        "price": 3999.0, "status": "paid", "created_at": now,
    })
    # 插入事件
    from xianyu_hunter.infra.db_models import EventRow
    with repo.engine.begin() as conn:
        conn.execute(EventRow.__table__.insert().values(
            type="order", task_id="t1", item_id="i1", stage="buy",
            level="info", message="订单已支付", created_at=now,
        ))
        conn.execute(EventRow.__table__.insert().values(
            type="system", task_id=None, item_id=None, stage="startup",
            level="info", message="服务启动", created_at=now,
        ))


def test_export_datasets_list(client: TestClient) -> None:
    """列出可导出数据集"""
    resp = client.get("/api/export", headers=_auth_headers())
    assert resp.status_code == 200
    data = resp.json()
    keys = {d["key"] for d in data["datasets"]}
    assert keys == {"items", "evaluations", "orders", "events"}


def test_export_items_csv(client: TestClient, tmp_repo: Repository) -> None:
    """导出商品 CSV"""
    _seed_data(tmp_repo)
    resp = client.get("/api/export/items", headers=_auth_headers())
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    # 含 BOM 头
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 表头 + 2 条数据
    assert len(rows) == 3
    assert "id" in rows[0]
    assert "title" in rows[0]
    titles = {r[rows[0].index("title")] for r in rows[1:]}
    assert "iPhone 13" in titles


def test_export_rows_header_matches_body(client: TestClient, tmp_repo: Repository) -> None:
    """X-Export-Rows 必须与 CSV 数据行数一致（T02587 / FR-C-2）

    前端据此区分「0 行」与「有数据」：没有这个头，导出成功提示只能一律说「正在导出」，
    筛选写错导致 0 行时也照样报成功。
    """
    _seed_data(tmp_repo)
    resp = client.get("/api/export/items", headers=_auth_headers())
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert resp.headers["x-export-rows"] == str(len(rows) - 1)

    # 过滤到空结果：只剩表头，头里必须如实写 0
    empty = client.get(
        "/api/export/items", params={"task_id": "no-such-task"}, headers=_auth_headers()
    )
    assert empty.status_code == 200
    empty_rows = list(csv.reader(io.StringIO(empty.content.decode("utf-8-sig"))))
    assert len(empty_rows) == 1
    assert empty.headers["x-export-rows"] == "0"


def test_export_items_filter_by_task(client: TestClient, tmp_repo: Repository) -> None:
    """按 task_id 过滤商品"""
    _seed_data(tmp_repo)
    # 再插一个不属 t1 的商品
    now = datetime.now(timezone.utc)
    tmp_repo.batch_upsert_items([{
        "id": "i3", "task_id": "t2", "title": "Other",
        "price": 100.0, "first_seen": now,
    }])
    resp = client.get("/api/export/items", params={"task_id": "t1"}, headers=_auth_headers())
    assert resp.status_code == 200
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 表头 + 2 条数据（i1, i2）
    assert len(rows) == 3


def test_export_orders_csv(client: TestClient, tmp_repo: Repository) -> None:
    """导出订单 CSV"""
    _seed_data(tmp_repo)
    resp = client.get("/api/export/orders", headers=_auth_headers())
    assert resp.status_code == 200
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 2  # 表头 + 1 条


def test_export_events_csv(client: TestClient, tmp_repo: Repository) -> None:
    """导出事件 CSV"""
    _seed_data(tmp_repo)
    resp = client.get("/api/export/events", headers=_auth_headers())
    assert resp.status_code == 200
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 表头 + 2 条事件
    assert len(rows) == 3


def test_export_evaluations_csv(client: TestClient, tmp_repo: Repository) -> None:
    """导出评估 CSV"""
    _seed_data(tmp_repo)
    resp = client.get("/api/export/evaluations", headers=_auth_headers())
    assert resp.status_code == 200
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # 表头 + 1 条评估
    assert len(rows) == 2


def test_export_invalid_dataset_returns_400(client: TestClient) -> None:
    """未知数据集返回 400"""
    resp = client.get("/api/export/unknown", headers=_auth_headers())
    assert resp.status_code == 400


def test_export_items_time_filter(client: TestClient, tmp_repo: Repository) -> None:
    """时间范围过滤"""
    _seed_data(tmp_repo)
    # 只查最近 1 小时
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = client.get("/api/export/items", params={"start": start}, headers=_auth_headers())
    assert resp.status_code == 200
    content = resp.content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    # i1 是 now，i2 是 1 天前，应只返回 i1
    assert len(rows) == 2  # 表头 + i1


# ==================== D4：边生成边发，且输出逐字节不变 ====================

def _legacy_csv_text(rows: list[dict], columns: list[str]) -> str:
    """旧实现（整份 StringIO 再单元素 yield）的独立参照

    刻意在测试里重写一遍，而不是从实现导入：若参照也来自被测模块，
    「逐字节相同」就退化成同义反复。
    """
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue()


class _TrackingRows(list):
    """记录迭代进度，用来证明首块产出时还没有把数据行全部取出来"""

    def __init__(self, data) -> None:
        super().__init__(data)
        self.seen = 0

    def __iter__(self):
        for row in super().__iter__():
            self.seen += 1
            yield row


def test_csv_stream_output_is_byte_identical_to_legacy() -> None:
    """流式改造不能改变任何一个字节（含 BOM、CRLF、引号转义、None→空串）"""
    from xianyu_hunter.web.routes.api_export import _DATASET_COLUMNS, _iter_csv_text

    columns = _DATASET_COLUMNS["items"]
    rows = [
        {
            "id": "i1", "task_id": "t1", "title": "iPhone 13, 128G",
            "price": 3999.0, "publish_time": "2026-11-01T00:00:00",
            "region": None, "seller_id": "s1", "want_cnt": 3, "view_cnt": 10,
            "first_seen": "2026-10-31T00:00:00", "last_seen": None,
        },
        {
            # 缺列 + 显式 None 都要落成空串；引号与换行交给 csv 转义
            "id": "i2", "title": '带"引号"和\n换行', "price": 0.0, "region": "杭州",
        },
    ]
    assert "".join(_iter_csv_text(rows, columns)) == _legacy_csv_text(rows, columns)


def test_csv_stream_emits_before_reading_all_rows() -> None:
    """首块必须早于全量取数：这是「流式」与「先拼满」的分水岭"""
    from xianyu_hunter.web.routes.api_export import _DATASET_COLUMNS, _iter_csv_text

    columns = _DATASET_COLUMNS["items"]
    rows = _TrackingRows({"id": f"i{n}", "title": "x" * 40} for n in range(20_000))
    it = _iter_csv_text(rows, columns)

    first = next(it)
    assert first.startswith("\ufeffid,"), "BOM 与表头必须在第一块里"
    assert rows.seen == 0, "首块产出时不应已取过任何数据行"

    next(it)
    assert 0 < rows.seen < len(rows), "第二块也应是部分取数，而不是全量拼完再发"


def test_csv_stream_chunk_size_does_not_scale_with_row_count() -> None:
    """单块大小有上限：块大小随行数放大就说明仍在攒全量"""
    from xianyu_hunter.web.routes.api_export import _DATASET_COLUMNS, _iter_csv_text

    columns = _DATASET_COLUMNS["items"]
    rows = [{"id": f"i{n}", "title": "x" * 40, "price": n} for n in range(50_000)]
    chunks = list(_iter_csv_text(rows, columns))

    assert sum(len(c) for c in chunks) > 2 * 1024 * 1024, "样本太小则断言无意义"
    assert len(chunks) > 4
    assert max(len(c) for c in chunks) < 400_000, (
        f"单块已达 {max(len(c) for c in chunks)} 字符，仍在攒全量"
    )


def _legacy_log_csv(candidates: list[dict]) -> str:
    """日志 CSV 旧实现的独立参照（列、截断、tags 拼接都不能变）"""
    from xianyu_hunter.web.routes.api_logs import _event_tags

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "level", "stage", "task_id", "item_id", "message", "tags"])
    for e in candidates:
        writer.writerow([
            e.get("id", ""),
            e.get("created_at", ""),
            e.get("level", ""),
            e.get("stage", ""),
            e.get("task_id", "") or "",
            e.get("item_id", "") or "",
            (e.get("message") or "")[:500],
            ",".join(_event_tags(e)),
        ])
    return buf.getvalue()


def test_log_csv_stream_output_is_byte_identical_to_legacy() -> None:
    from xianyu_hunter.web.routes.api_logs import _iter_log_csv

    candidates = [
        {"id": 1, "created_at": "2026-11-06T00:00:00", "level": "info", "stage": "run",
         "task_id": "t1", "item_id": None, "message": "正常,带逗号",
         "payload": '{"tags": ["a", "b"]}'},
        # message 超过 500 字符必须被截断；payload 为空时 tags 落空串
        {"id": 2, "created_at": "", "level": "error", "stage": "", "task_id": None,
         "item_id": "i1", "message": "长" * 600, "payload": None},
    ]
    assert "".join(_iter_log_csv(candidates)) == _legacy_log_csv(candidates)


def test_log_csv_stream_emits_before_reading_all_rows() -> None:
    """日志 CSV 同样不能先拼满：首块（表头）必须在取任何数据行之前产出"""
    from xianyu_hunter.web.routes.api_logs import _iter_log_csv

    candidates = _TrackingRows({"id": n, "message": "x" * 60} for n in range(20_000))
    it = _iter_log_csv(candidates)

    assert next(it).startswith("id,"), "表头必须是第一块"
    assert candidates.seen == 0, "首块产出时不应已取过任何数据行"

    next(it)
    assert 0 < candidates.seen < len(candidates), "第二块也应是部分取数"


def _legacy_log_file_body(root: Path) -> str:
    """原始日志导出旧实现的独立参照（两个文件的 join 语义）"""
    chunks: list[str] = []
    for fname in ("run.stdout.log", "run.stderr.log"):
        p = root / fname
        if p.exists():
            chunks.append(f"===== {fname} =====\n")
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _assert_same_text(got: str, expected: str) -> None:
    """长文本比较先比长度、再定位首个差异

    直接 `assert got == expected` 在失败时会让 pytest 渲染整份 diff：
    1 MB 以上的日志用例会因此把变异验证拖到几分钟（D4 变异验证实测踩到过）。
    """
    if got == expected:
        return
    assert len(got) == len(expected), f"长度不同：{len(got)} vs {len(expected)}"
    offset = next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b), 0)
    raise AssertionError(
        f"首个差异在偏移 {offset}："
        f"{got[offset:offset + 40]!r} != {expected[offset:offset + 40]!r}"
    )


async def _collect_body(resp) -> str:
    return "".join(
        [c.decode("utf-8") if isinstance(c, bytes) else c async for c in resp.body_iterator]
    )


async def _collect_chunks(resp) -> list[str]:
    return [c.decode("utf-8") if isinstance(c, bytes) else c async for c in resp.body_iterator]


def _db_admin_container(repo: Repository):
    return types.SimpleNamespace(repo=repo)


def _seed_bulk_items(repo: Repository, count: int) -> int:
    """在 _seed_data 的 2 条之上再灌 count 条，返回导出应有的总行数"""
    now = datetime.now(timezone.utc)
    rows = [
        {"id": f"bulk{i:05d}", "task_id": "t1", "title": f"批量商品 {i}",
         "price": 100.0 + i, "seller_id": "s1", "first_seen": now}
        for i in range(count)
    ]
    # SQLite 单语句变量有上限，批量插入必须切片
    for i in range(0, len(rows), 200):
        repo.batch_upsert_items(rows[i:i + 200])
    return count + 2


def test_db_admin_export_csv_chunks_are_batched_not_per_row(tmp_repo: Repository) -> None:
    """db-admin 表导出：块数必须与「块」同阶，而不是与「行」同阶

    旧实现逐行 yield，StreamingResponse 每行一次线程池往返：18 403 行 events
    实测 2.4 s（perf/2026-10-31-full-api/results/d4_export_probe_before_r4.json）。
    """
    from xianyu_hunter.web.routes import api_db_admin as admin

    _seed_data(tmp_repo)
    total = _seed_bulk_items(tmp_repo, 3000)

    resp = admin.export_rows("items", format="csv", container=_db_admin_container(tmp_repo))
    assert resp.headers["x-export-rows"] == str(total)
    chunks = asyncio.run(_collect_chunks(resp))
    body = "".join(chunks)

    assert body.startswith("\ufeff"), "BOM 丢了 Excel 会把中文认成乱码"
    assert len([ln for ln in body.splitlines() if ln.strip()]) - 1 == total
    assert len(chunks) < 50, f"chunk 数 {len(chunks)} 与行数同阶，说明仍在逐行 yield"
    assert max(len(c) for c in chunks) <= 2 * admin._EXPORT_CHUNK_CHARS


def test_db_admin_export_json_streams_and_stays_valid(tmp_repo: Repository) -> None:
    """JSON 分支同样分块：分块只能改传输边界，不能让 JSON 失效"""
    from xianyu_hunter.web.routes import api_db_admin as admin

    _seed_data(tmp_repo)
    total = _seed_bulk_items(tmp_repo, 3000)

    resp = admin.export_rows("items", format="json", container=_db_admin_container(tmp_repo))
    chunks = asyncio.run(_collect_chunks(resp))
    data = json.loads("".join(chunks))

    assert len(data) == total
    assert resp.headers["x-export-rows"] == str(total)
    assert len(chunks) < 50
    assert max(len(c) for c in chunks) <= 2 * admin._EXPORT_CHUNK_CHARS


@pytest.mark.parametrize(
    "stdout_text,stderr_text",
    [
        ("stdout 行1\n中文行2\n", ""),   # 空 stderr 也是一个元素：join 会留一个多余换行
        ("", "只有 stderr\n"),
        (None, "stdout 不存在\n"),        # 只存在一个文件
    ],
)
def test_log_file_stream_matches_legacy_bytes(
    tmp_path: Path, monkeypatch, stdout_text, stderr_text
) -> None:
    from xianyu_hunter.web.routes.api_logs import _export_log_file

    monkeypatch.chdir(tmp_path)
    if stdout_text is not None:
        (tmp_path / "run.stdout.log").write_text(stdout_text, encoding="utf-8")
    if stderr_text is not None:
        (tmp_path / "run.stderr.log").write_text(stderr_text, encoding="utf-8")

    got = asyncio.run(_collect_body(_export_log_file()))
    _assert_same_text(got, _legacy_log_file_body(tmp_path))


def test_log_file_stream_reads_in_blocks_and_matches_whole_file_decode(
    tmp_path: Path, monkeypatch
) -> None:
    """多块读取 + 中文 + 混合换行，输出必须与整份 read_text 完全一致

    这里锁两件事：①文件确实被分块读（否则峰值仍随文件大小线性增长）；
    ②`newline=None` 的通用换行转换与增量解码都没被绕开——只按二进制读再解码，
    `\\r\\n` 会原样留下；整块 `errors="replace"` 则会把跨边界的汉字换成替换符。
    """
    from xianyu_hunter.web.routes import api_logs

    block = api_logs._LOG_FILE_BLOCK_CHARS
    unit = "中文行\r\n" + "a" * 50 + "\n"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run.stdout.log").write_text(unit * (block // len(unit) + 10), encoding="utf-8")

    chunks = list(api_logs._iter_log_file_chunks())
    assert len(chunks) >= 4, "超过一个块大小的文件必须分成多块读取"
    assert max(len(c) for c in chunks) <= 2 * block

    got = asyncio.run(_collect_body(api_logs._export_log_file()))
    _assert_same_text(got, _legacy_log_file_body(tmp_path))
    assert "\ufffd" not in got
