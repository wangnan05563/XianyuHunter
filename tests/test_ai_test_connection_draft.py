"""FR-E-1：AI 连通性测试必须测「用户表单草稿」而非服务端已保存配置。

原先 /test-connection、/test-embedding 无请求体，前端只能「先保存再测试」，
导致测试失败的错误配置也被落盘。改造后请求体可选：
- 传草稿 → 用草稿发 dry-run 请求，不落盘
- 掩码回显 key（****开头）→ 回退服务端真值（否则拿掩码请求必然 401 假失败）
- 不传 → 维持旧行为（用已保存配置），兼容旧客户端
"""
from types import SimpleNamespace

import httpx
import pytest

from xianyu_hunter.web.routes import api_ai


SAVED = SimpleNamespace(
    ai_enabled=True,
    openai_base_url="https://saved.example.com/v1",
    openai_api_key="sk-saved-real-key-1234",
    openai_model="saved-model",
    embedding_base_url="https://emb-saved.example.com/v1",
    embedding_api_key="sk-emb-saved-key-5678",
    embedding_model="saved-emb-model",
    embedding_dimensions=1024,
)


class _FakeResponse:
    def __init__(self):
        self.status_code = 200
        self.text = ""

    def json(self):
        return {"model": "upstream-model", "data": [{"embedding": [0.1, 0.2]}]}


class _FakeClient:
    """捕获 test 端点实际发出的请求，供断言「测的到底是哪套配置」"""

    last_post: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    def post(self, url, json=None, headers=None):
        _FakeClient.last_post = {"url": url, "json": json, "headers": headers}
        return _FakeResponse()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.last_post = None
    monkeypatch.setattr(api_ai.httpx, "Client", _FakeClient)
    monkeypatch.setattr(api_ai, "record_usage", lambda *a, **k: None)
    return _FakeClient


@pytest.fixture
def saved_settings(monkeypatch):
    monkeypatch.setattr(api_ai, "get_settings", lambda: SAVED)
    return SAVED


def test_connection_uses_draft_not_saved(fake_http, saved_settings):
    """表单草稿（新 base_url/新模型/新 key）必须原样出现在上游请求里"""
    result = api_ai.test_ai_connection(
        api_ai.AiTestConnectionBody(
            base_url="https://draft.example.com/v1",
            api_key="sk-draft-key-9999",
            model="draft-model",
        )
    )
    req = fake_http.last_post
    assert result["ok"] is True
    assert req["url"] == "https://draft.example.com/v1/chat/completions"
    assert req["json"]["model"] == "draft-model"
    assert req["headers"]["Authorization"] == "Bearer sk-draft-key-9999"


def test_connection_masked_key_falls_back_to_saved(fake_http, saved_settings):
    """GET /config 回显的是脱敏 key：原样传回时必须回退服务端真值，
    否则拿 '****xxxx' 请求上游必然 401，用户会误判 key 无效"""
    result = api_ai.test_ai_connection(
        api_ai.AiTestConnectionBody(
            base_url="https://draft.example.com/v1",
            api_key="****1234",
            model="draft-model",
        )
    )
    req = fake_http.last_post
    assert result["ok"] is True
    assert req["headers"]["Authorization"] == "Bearer sk-saved-real-key-1234"


def test_connection_without_body_keeps_saved_behavior(fake_http, saved_settings):
    """不传请求体 = 旧行为：测服务端已保存配置（兼容旧客户端）"""
    result = api_ai.test_ai_connection()
    req = fake_http.last_post
    assert result["ok"] is True
    assert req["url"] == "https://saved.example.com/v1/chat/completions"
    assert req["json"]["model"] == "saved-model"


def test_embedding_draft_overrides_key_and_dimensions(fake_http, saved_settings):
    """embedding 远程草稿：key/dimensions 用草稿值，且 dimensions=0 不进 payload"""
    result = api_ai.test_embedding_connection(
        api_ai.AiTestEmbeddingBody(
            base_url="https://emb-draft.example.com/v1",
            api_key="sk-emb-draft-key-0000",
            model="draft-emb-model",
            dimensions=0,
        )
    )
    req = fake_http.last_post
    assert result["ok"] is True
    assert req["url"] == "https://emb-draft.example.com/v1/embeddings"
    assert req["json"]["model"] == "draft-emb-model"
    assert "dimensions" not in req["json"]
    assert req["headers"]["Authorization"] == "Bearer sk-emb-draft-key-0000"


def test_embedding_masked_key_falls_back_to_embedding_then_llm(
    fake_http, saved_settings, monkeypatch
):
    """掩码 key 回退链：embedding 配置优先，embedding 为空再回退 LLM 配置"""
    monkeypatch.setattr(
        api_ai, "get_settings",
        lambda: SimpleNamespace(**{**vars(SAVED), "embedding_api_key": ""}),
    )
    api_ai.test_embedding_connection(
        api_ai.AiTestEmbeddingBody(base_url="https://emb-draft.example.com/v1", api_key="****5678")
    )
    assert fake_http.last_post["headers"]["Authorization"] == "Bearer sk-saved-real-key-1234"


def test_embedding_explicit_empty_base_url_means_local(
    monkeypatch, saved_settings
):
    """草稿 base_url 显式空串 = 用户切到本地模式，不能静默回退远程服务端配置"""
    calls: list[str] = []
    monkeypatch.setattr(api_ai, "_test_local_embedding", lambda model: calls.append(model) or {"ok": True})
    result = api_ai.test_embedding_connection(
        api_ai.AiTestEmbeddingBody(base_url="", model="local-model")
    )
    assert result["ok"] is True
    assert calls == ["local-model"]
