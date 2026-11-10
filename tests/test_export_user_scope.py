"""FR-D-4：数据导出必须按当前账号（request.state.user_id）隔离。

原先 export_dataset 无 user 作用域，多账号部署时一份 CSV 会混入
其他账号的 items/evaluations/orders/events。本文件钉两件事：
1. 四个数据集的 repo 查询都收到了 user_id（与 timeline.py 同口径）
2. evaluations 直查表的分支真的按 user_id 过滤（本单新增的 where）
"""
from types import SimpleNamespace
from datetime import datetime

import pytest
from sqlalchemy import create_engine

from xianyu_hunter.infra.db_models import Base, EvaluationRow
from xianyu_hunter.web.routes import api_export


class _RecordingRepo:
    """记录 list_* 收到的 user_id，返回空行即可（不测数据层，测传参）"""

    def __init__(self):
        self.calls: dict[str, dict] = {}

    def list_items(self, **kw):
        self.calls["items"] = kw
        return []

    def list_orders(self, **kw):
        self.calls["orders"] = kw
        return []

    def list_events(self, **kw):
        self.calls["events"] = kw
        return []


def _export(dataset: str, repo, user_id: str | None = "user-a"):
    request = SimpleNamespace(state=SimpleNamespace(user_id=user_id))
    container = SimpleNamespace(repo=repo)
    response = api_export.export_dataset(
        dataset=dataset,
        request=request,
        task_id=None, start=None, end=None, status=None, level=None,
        limit=100, container=container,
    )
    # 不消费 body_iterator：starlette 会把同步生成器包装成 async iterator，
    # 这里只断言传参与响应头（X-Export-Rows 即导出行数，0 行可判空结果）
    return response


@pytest.mark.parametrize("dataset", ["items", "orders", "events"])
def test_repo_queries_receive_user_id(dataset):
    repo = _RecordingRepo()
    response = _export(dataset, repo)
    assert repo.calls[dataset]["user_id"] == "user-a"
    assert response.headers["X-Export-Rows"] == "0"


def test_missing_state_user_id_passes_none():
    """getattr 兜底 None=不过滤：与 timeline.py 口径一致（中间件缺失时不误杀数据）"""
    repo = _RecordingRepo()
    request = SimpleNamespace(state=SimpleNamespace())
    api_export.export_dataset(
        dataset="orders", request=request,
        task_id=None, start=None, end=None, status=None, level=None,
        limit=100, container=SimpleNamespace(repo=repo),
    )
    assert repo.calls["orders"]["user_id"] is None


def _eval_container_with_rows():
    """内存 SQLite 建 evaluations 表，插两个用户的行，验证直查分支真的过滤"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[EvaluationRow.__table__])
    with engine.begin() as conn:
        for uid in ("user-a", "user-b"):
            conn.execute(EvaluationRow.__table__.insert().values(
                item_id=f"item-{uid}", seller_id="s1", score=80,
                risk_level="low", dimension_scores="{}",
                reject_reasons=None, created_at=datetime(2026, 11, 10),
                user_id=uid,
            ))

    repo = SimpleNamespace(engine=engine, _row_to_dict=lambda r: dict(r._mapping))
    return SimpleNamespace(repo=repo)


def test_list_all_evaluations_filters_by_user():
    container = _eval_container_with_rows()
    rows = api_export._list_all_evaluations(container, limit=100, user_id="user-a")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user-a"


def test_list_evaluations_by_items_filters_by_user():
    """双保险：item_ids 命中但评估行归属他人时不得混入（防历史脏数据）"""
    container = _eval_container_with_rows()
    rows = api_export._list_evaluations_by_items(
        container, item_ids={"item-user-a", "item-user-b"}, limit=100, user_id="user-a"
    )
    assert len(rows) == 1
    assert rows[0]["user_id"] == "user-a"
