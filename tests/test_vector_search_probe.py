"""T04185：只读向量检索探针的分阶段判据

验收要区分四种形态（EMBEDDING_UNAVAILABLE / EMPTY_COLLECTION / NO_MATCH / OK），
`RAGEngine.retrieve` 把这几种统统塌成空列表，所以探针分阶段调 embed → count → search。
另一半判据是「真的只读」：调用前后 chunk_count 不变。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from xianyu_hunter.web.routes import api_vector_admin as va


class _Store:
    def __init__(self, count: int, raw: list[dict[str, Any]] | None = None) -> None:
        self.persist_path = "/tmp/probe"
        self.collection_name = "kb_chunks"
        self._count = count
        self._raw = raw or []
        self.search_calls: list[tuple[list[float], int]] = []

    async def count(self) -> int:
        return self._count

    async def search(self, query_embedding: list[float], top_k: int = 5) -> list[dict]:
        self.search_calls.append((query_embedding, top_k))
        return self._raw


class _Embedding:
    def __init__(self, vec: list[float] | None) -> None:
        self._vec = vec if vec is not None else []
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        return self._vec


def _patch(monkeypatch: pytest.MonkeyPatch, store: _Store, emb: _Embedding) -> None:
    chatbot = {
        "vector_store": store,
        "embedding_service": emb,
        "config": SimpleNamespace(rag=SimpleNamespace(top_k=5, similarity_threshold=0.65)),
    }
    monkeypatch.setattr(va, "_get_chatbot_or_403", lambda: chatbot)


def _hit(source: str, similarity: float) -> dict[str, Any]:
    return {
        "content": "x" * 400, "source_file": source, "section_path": "A/B",
        "line_start": 1, "line_end": 9, "doc_type": "manual",
        "distance": 2 * (1 - similarity), "similarity": similarity,
    }


@pytest.mark.asyncio
async def test_embed_failure_reports_embedding_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """引擎不可用要单独成态，不能和「空库」混成同一个空结果。"""
    store, emb = _Store(count=10), _Embedding(None)
    _patch(monkeypatch, store, emb)

    out = await va.search_probe(q="内存条", top_k=None)

    assert out["outcome"] == "EMBEDDING_UNAVAILABLE"
    assert out["embedding_dim"] == 0
    assert store.search_calls == [], "向量化失败时不应继续打检索"


@pytest.mark.asyncio
async def test_empty_collection_still_proves_embedding_worked(monkeypatch: pytest.MonkeyPatch) -> None:
    """T04109 卡住的那一格：本机 chunk_count=0 时探针仍要能证明 embed 跑通。"""
    store, emb = _Store(count=0), _Embedding([0.1, 0.2, 0.3])
    _patch(monkeypatch, store, emb)

    out = await va.search_probe(q="内存条", top_k=None)

    assert out["outcome"] == "EMPTY_COLLECTION"
    assert out["embedding_dim"] == 3
    assert out["chunk_count_before"] == 0
    assert "重建" in out["message"]
    assert store.search_calls == [], "空集合不必打检索"


@pytest.mark.asyncio
async def test_ok_applies_threshold_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """阈值以下的片段进 raw_hits 但不进 hits；前后 chunk_count 相同即证明只读。"""
    raw = [_hit("a.md", 0.91), _hit("b.md", 0.40)]
    store, emb = _Store(count=12, raw=raw), _Embedding([0.1, 0.2, 0.3])
    _patch(monkeypatch, store, emb)

    out = await va.search_probe(q="内存条", top_k=3)

    assert out["outcome"] == "OK"
    assert [h["source_file"] for h in out["hits"]] == ["a.md"]
    assert {h["source_file"] for h in out["raw_hits"]} == {"a.md", "b.md"}
    assert out["chunk_count_after"] == out["chunk_count_before"] == 12
    assert store.search_calls == [([0.1, 0.2, 0.3], 3)], "top_k 覆盖要透传到检索"
    assert len(out["raw_hits"][0]["snippet"]) == 200, "片段必须截断，别把整篇吐出去"


@pytest.mark.asyncio
async def test_no_raw_match_is_not_reported_as_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    store, emb = _Store(count=12, raw=[]), _Embedding([0.1])
    _patch(monkeypatch, store, emb)

    out = await va.search_probe(q="无关词", top_k=None)

    assert out["outcome"] == "NO_MATCH"
    assert out["hits"] == []


@pytest.mark.asyncio
async def test_recalled_but_below_threshold_is_its_own_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """召回了但全部低于阈值 ≠ 没召回：前者是语义不匹配，后者可能是索引问题。"""
    store, emb = _Store(count=12, raw=[_hit("a.md", 0.31)]), _Embedding([0.1])
    _patch(monkeypatch, store, emb)

    out = await va.search_probe(q="无关词", top_k=None)

    assert out["outcome"] == "BELOW_THRESHOLD"
    assert out["hits"] == []
    assert len(out["raw_hits"]) == 1


def test_probe_is_get_and_requires_auth() -> None:
    """只读探针必须是 GET，且不能落进认证白名单（否则等于对外暴露向量查询接口）。"""
    from xianyu_hunter.web.middleware.auth import PUBLIC_PREFIXES
    from xianyu_hunter.web.routes.api_vector_admin import router

    route = next(r for r in router.routes if r.path == "/api/vector-admin/search-probe")
    assert route.methods == {"GET"}
    assert not any(route.path.startswith(prefix) for prefix in PUBLIC_PREFIXES)
