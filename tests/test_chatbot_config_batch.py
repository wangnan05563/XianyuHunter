"""客服配置批量保存（统一保存范式）测试 —— T05072 / FR-E-4

覆盖 POST /api/chatbot/config/batch 的 dry-run diff 与落库行为，
以及 llm.* 六键补进白名单的既有缺陷修复（前端 LLM 模型 Tab 曾被 422 拒绝）。

为什么重要：统一保存范式的核心承诺是「预览所见即落库所得」——
dry-run 返回的 diffs 与 dry_run=False 实际写库的字段集合必须一致，
且未变更字段绝不产生审计噪音。
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xianyu_hunter.infra.yaml_config import ChatbotConfig
from xianyu_hunter.web.routes import api_chatbot_config


@pytest.fixture
def chatbot_container():
    """构造 chatbot 子容器 mock：真实 ChatbotConfig + 记录型 repo"""
    cfg = ChatbotConfig()
    repo = MagicMock()
    repo.get_all_config.return_value = {}
    repo.get_config.return_value = None
    repo.delete_config.return_value = True
    return {"config": cfg, "repo": repo}


@pytest.fixture
def batch_app(chatbot_container):
    app = FastAPI()
    app.include_router(api_chatbot_config.router)
    with patch(
        "xianyu_hunter.web.routes.api_chatbot_config.get_container",
        return_value=MagicMock(chatbot=chatbot_container),
    ):
        yield app, chatbot_container


def _full_payload() -> dict:
    """构造与 GET /config 同形的完整草稿（从默认 ChatbotConfig 推导）"""
    cfg = ChatbotConfig().model_dump()
    payload = {
        "enabled": cfg["enabled"],
        "max_history_turns": cfg["max_history_turns"],
        "session_timeout_min": cfg["session_timeout_min"],
        "rag": cfg["rag"],
        "llm": cfg["llm"],
        "agent": cfg["agent"],
        "kb": cfg["kb"],
        "faq": cfg["faq"],
        "escalation": cfg["escalation"],
        "welcome_message": None,
    }
    return payload


class TestBatchDryRun:
    def test_no_change_returns_empty_diffs(self, batch_app):
        """与当前配置完全相同的草稿 → diffs 为空，不写库"""
        app, container = batch_app
        client = TestClient(app)
        resp = client.post("/api/chatbot/config/batch", json={"config": _full_payload(), "dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["diffs"] == []
        container["repo"].set_config.assert_not_called()

    def test_single_change_appears_in_diffs(self, batch_app):
        """改一个字段 → 恰好一条 modify diff，old/new 正确"""
        app, _ = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["rag"]["top_k"] = 8
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        data = resp.json()
        assert data["diffs"] == [
            {"path": "rag.top_k", "op": "modify", "old": 5, "new": 8}
        ]

    def test_non_updatable_fields_ignored(self, batch_app):
        """非白名单字段（kb.doc_paths / faq.similarity_threshold 等）不进 diff、不写库"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["kb"]["doc_paths"] = ["/tmp/evil"]
        payload["kb"]["embedding_model"] = "someone-else-model"
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        data = resp.json()
        assert data["diffs"] == []
        container["repo"].set_config.assert_not_called()

    def test_metadata_key_excluded(self, batch_app):
        """updatable_keys 元数据被剔除，不产生噪音 diff"""
        app, _ = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["updatable_keys"] = ["tampered"]
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        assert resp.json()["diffs"] == []

    def test_welcome_message_empty_means_delete(self, batch_app):
        """welcome_message 提交空串 → delete op（恢复默认）。

        前提是 DB 里存在覆盖值：无覆盖时（current=None）提交空串本就无差异。
        """
        app, container = batch_app
        container["repo"].get_all_config.return_value = {"welcome_message": "自定义欢迎语"}
        client = TestClient(app)
        payload = _full_payload()
        payload["welcome_message"] = ""
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        diffs = resp.json()["diffs"]
        assert len(diffs) == 1
        assert diffs[0]["path"] == "welcome_message"
        assert diffs[0]["op"] == "delete"
        assert diffs[0]["new"] is None


class TestBatchApply:
    def test_apply_writes_only_changed_keys(self, batch_app):
        """落库只写有差异的白名单键，且每条都写审计日志"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["rag"]["top_k"] = 8
        payload["llm"]["model"] = "qwen-plus"
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        data = resp.json()
        assert data["dry_run"] is False
        assert data["changed"] == 2

        written = {c.args[0]: c.args[1] for c in container["repo"].set_config.call_args_list}
        assert written == {"rag.top_k": "8", "llm.model": "qwen-plus"}
        assert container["repo"].add_audit_log.call_count == 2

    def test_apply_roundtrip_readable(self, batch_app):
        """落库后用 _convert_db_override 能读回正确类型（字符串闭环）"""
        from xianyu_hunter.web.routes.api_chatbot_config import _convert_db_override

        assert _convert_db_override("true", "bool") is True
        assert _convert_db_override("false", "bool") is False
        assert _convert_db_override("8", "int") == 8
        assert _convert_db_override("0.5", "float") == 0.5
        assert _convert_db_override("qwen-plus", "str") == "qwen-plus"

    def test_apply_no_change_writes_nothing(self, batch_app):
        """零差异提交不写库、不写审计（与逐 key PUT 的去噪语义一致）"""
        app, container = batch_app
        client = TestClient(app)
        resp = client.post("/api/chatbot/config/batch", json={"config": _full_payload(), "dry_run": False})
        data = resp.json()
        assert data["changed"] == 0
        container["repo"].set_config.assert_not_called()
        container["repo"].add_audit_log.assert_not_called()

    def test_apply_reflects_in_next_preview(self, batch_app):
        """落库后再次 dry-run 应为零差异（预览所见即落库所得）"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["escalation"]["contact"] = "QQ群 123456"

        first = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        assert first.json()["changed"] == 1

        # 模拟 repo 状态更新：get_all_config 反映已写入的覆盖
        container["repo"].get_all_config.return_value = {"escalation.contact": "QQ群 123456"}
        second = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        assert second.json()["diffs"] == []


class TestLlmWhitelistFix:
    def test_put_llm_model_now_accepted(self, batch_app):
        """既有缺陷修复：PUT llm.model 不再被白名单 422 拒绝"""
        app, container = batch_app
        client = TestClient(app)
        resp = client.put("/api/chatbot/config", json={"key": "llm.model", "value": "gpt-4o-mini"})
        assert resp.status_code == 200
        assert resp.json()["changed"] is True
        container["repo"].set_config.assert_called_once_with("llm.model", "gpt-4o-mini")

    def test_put_llm_temperature_accepted(self, batch_app):
        app, _ = batch_app
        client = TestClient(app)
        resp = client.put("/api/chatbot/config", json={"key": "llm.temperature", "value": "0.7"})
        assert resp.status_code == 200

    def test_unknown_key_still_rejected(self, batch_app):
        """白名单外的 key 仍被拒绝（安全边界不因批量入口放松）"""
        app, _ = batch_app
        client = TestClient(app)
        resp = client.put("/api/chatbot/config", json={"key": "kb.doc_paths", "value": "x"})
        assert resp.status_code == 422


class TestDegraded:
    def test_batch_returns_403_when_chatbot_disabled(self):
        """chatbot=None 时 batch 端点与其他路由一样 403 降级"""
        app = FastAPI()
        app.include_router(api_chatbot_config.router)
        mock_container = MagicMock()
        mock_container.chatbot = None
        with patch(
            "xianyu_hunter.web.routes.api_chatbot_config.get_container",
            return_value=mock_container,
        ):
            client = TestClient(app)
            resp = client.post("/api/chatbot/config/batch", json={"config": {}, "dry_run": True})
            assert resp.status_code == 403
            assert resp.json()["detail"]["code"] == "CHATBOT_DISABLED"
