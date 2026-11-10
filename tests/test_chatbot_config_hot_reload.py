"""客服配置运行时热加载测试 —— T05209 / FR-E-2

覆盖 hot_reload_runtime_config 的三条语义：
1. 热加载键（hot_reload=True）：落库后原地更新 chatbot["config"]，组件实时读即生效；
2. 重启生效键（hot_reload=False）：只落库不应用，归入 restart_required（诚实口径——
   Escalation/KBRefreshScheduler/httpx client 等构造时固化，热改快照不改行为）；
3. 恢复默认（DB 删行）：从 chatbot["yaml_baseline"] 回填启动基线值，
   而不是留下上一次热加载的脏值。

为什么重要：修复前「保存即热更新」只对 GET 展示生效，运行时组件读启动 yaml 快照
（container.py:554 深拷贝），保存后 UI 显示新值但行为仍是旧值。
"""
import copy
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from xianyu_hunter.infra.repo_chatbot import ChatbotRepository
from xianyu_hunter.infra.yaml_config import ChatbotConfig
from xianyu_hunter.web.routes import api_chatbot_config


@pytest.fixture
def chatbot_container(tmp_path):
    """真实 repo（文件 SQLite，与项目测试惯例一致）+ 真实 ChatbotConfig + 基线快照"""
    engine = create_engine(f"sqlite:///{tmp_path / 'chatbot_hot_reload.db'}")
    repo = ChatbotRepository(engine)
    cfg = ChatbotConfig()
    return {
        "config": cfg,
        "repo": repo,
        "yaml_baseline": copy.deepcopy(cfg),
    }


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
    return {
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


class TestBatchConfirmHotReload:
    def test_hot_reload_applies_rag_top_k(self, batch_app):
        """热加载键：batch confirm 保存 rag.top_k=8 → 运行时 cfg.rag.top_k 立即 == 8"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["rag"]["top_k"] = 8
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] == 1
        assert "rag.top_k" in data["hot_reload"]["reloaded"]
        assert data["hot_reload"]["restart_required"] == []
        assert container["config"].rag.top_k == 8

    def test_hot_reload_applies_llm_model(self, batch_app):
        """热加载键：llm.model 保存后 RAGEngine/Agent 实时读的 cfg.llm.model 立即更新"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["llm"]["model"] = "deepseek-chat"
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        data = resp.json()
        assert "llm.model" in data["hot_reload"]["reloaded"]
        assert container["config"].llm.model == "deepseek-chat"

    def test_restart_required_key_not_applied(self, batch_app):
        """重启生效键：llm.http_timeout_sec 落库但 cfg 不变（httpx client 构造固化）"""
        app, container = batch_app
        client = TestClient(app)
        default_timeout = container["config"].llm.http_timeout_sec
        payload = _full_payload()
        payload["llm"]["http_timeout_sec"] = default_timeout + 37
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        data = resp.json()
        assert data["changed"] == 1
        assert data["hot_reload"]["reloaded"] == []
        assert "llm.http_timeout_sec" in data["hot_reload"]["restart_required"]
        assert container["config"].llm.http_timeout_sec == default_timeout

    def test_mixed_keys_split_correctly(self, batch_app):
        """混合提交：热键与重启键同时变更，reloaded / restart_required 各归其位"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["rag"]["max_context_chars"] = 9000
        payload["enabled"] = not payload["enabled"]  # 重启生效键
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        data = resp.json()
        assert data["changed"] == 2
        assert data["hot_reload"]["reloaded"] == ["rag.max_context_chars"]
        assert data["hot_reload"]["restart_required"] == ["enabled"]
        assert container["config"].rag.max_context_chars == 9000
        assert container["config"].enabled == ChatbotConfig().enabled  # 未被热改

    def test_delete_restores_yaml_baseline(self, batch_app):
        """恢复默认：先热加载 top_k=8，再提交空串删 DB 行 → cfg 回填基线值 5

        这是 yaml_baseline 快照存在的意义：热加载过的 cfg 已带脏值，
        「删覆盖行=恢复默认」必须回填启动基线，而不是保持脏值。
        """
        app, container = batch_app
        client = TestClient(app)
        # 第一步：保存 8（热加载生效）
        payload = _full_payload()
        payload["rag"]["top_k"] = 8
        client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": False})
        assert container["config"].rag.top_k == 8
        # 第二步：提交 None（→ '' → 删 DB 行）恢复默认
        payload2 = _full_payload()
        payload2["rag"]["top_k"] = None
        resp = client.post("/api/chatbot/config/batch", json={"config": payload2, "dry_run": False})
        data = resp.json()
        assert data["changed"] == 1
        assert "rag.top_k" in data["hot_reload"]["reloaded"]
        baseline_top_k = ChatbotConfig().rag.top_k
        assert container["config"].rag.top_k == baseline_top_k

    def test_dry_run_never_touches_runtime(self, batch_app):
        """dry_run 只出 diff：不写库、不热加载、cfg 不变"""
        app, container = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["rag"]["top_k"] = 8
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        data = resp.json()
        assert len(data["diffs"]) == 1
        assert data.get("hot_reload") is None
        assert container["config"].rag.top_k == ChatbotConfig().rag.top_k


class TestPutSingleKeyHotReload:
    def test_put_changed_triggers_hot_reload(self, batch_app):
        """PUT 单键兼容入口：changed → 响应含 hot_reload 且 cfg 已更新"""
        app, container = batch_app
        client = TestClient(app)
        resp = client.put("/api/chatbot/config", json={"key": "rag.top_k", "value": "9"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["hot_reload"]["reloaded"] == ["rag.top_k"]
        assert container["config"].rag.top_k == 9

    def test_put_unchanged_no_hot_reload(self, batch_app):
        """值未变（changed=False）→ 无 hot_reload 字段，不产生热加载噪音

        PUT 语义是「DB 覆盖」：DB 无行时提交同值也算 add（changed=True）。
        真正的「未变」= DB 已有覆盖行且值相同。
        """
        app, container = batch_app
        container["repo"].set_config("rag.top_k", "9")
        client = TestClient(app)
        resp = client.put("/api/chatbot/config", json={"key": "rag.top_k", "value": "9"})
        data = resp.json()
        assert data["changed"] is False
        assert "hot_reload" not in data


class TestMergedConfigMetadata:
    def test_restart_required_keys_exposed(self, batch_app):
        """GET /config 暴露 restart_required_keys 元数据，供前端标注「重启生效」"""
        app, _ = batch_app
        client = TestClient(app)
        resp = client.get("/api/chatbot/config")
        data = resp.json()
        assert "restart_required_keys" in data
        for key in (
            "enabled",
            "session_timeout_min",
            "kb.auto_update_enabled",
            "kb.update_interval_hours",
            "llm.http_timeout_sec",
        ):
            assert key in data["restart_required_keys"], f"{key} 应标为重启生效"
        # 热加载键绝不能出现在重启生效列表里
        assert "rag.top_k" not in data["restart_required_keys"]
        assert "llm.model" not in data["restart_required_keys"]

    def test_restart_required_not_in_diff(self, batch_app):
        """restart_required_keys 是元数据：进 diff 时必须被剔除（与 updatable_keys 同口径）"""
        app, _ = batch_app
        client = TestClient(app)
        payload = _full_payload()
        payload["restart_required_keys"] = ["tampered"]
        payload["updatable_keys"] = ["tampered"]
        resp = client.post("/api/chatbot/config/batch", json={"config": payload, "dry_run": True})
        # tampered 值只出现在元数据键里，白名单遍历不会读它们 → 无 diff
        assert resp.json()["diffs"] == []


class TestHotReloadFunction:
    """hot_reload_runtime_config 函数级边界行为"""

    def test_failure_isolated(self):
        """单键转换失败不影响其他键：failed 上报，其余键照常生效"""
        repo = MagicMock()
        repo.get_config.side_effect = lambda k: {"rag.top_k": "8", "faq.similarity_threshold": "oops"}.get(k)
        cfg = ChatbotConfig()
        container = {"config": cfg, "repo": repo, "yaml_baseline": copy.deepcopy(cfg)}
        result = api_chatbot_config.hot_reload_runtime_config(
            container, ["rag.top_k", "faq.similarity_threshold"]
        )
        assert result["reloaded"] == ["rag.top_k"]
        assert container["config"].rag.top_k == 8
        assert len(result["failed"]) == 1
        assert result["failed"][0]["key"] == "faq.similarity_threshold"

    def test_missing_baseline_falls_back_to_cfg(self):
        """旧容器无 yaml_baseline 键：热加载不崩，恢复默认退化为 no-op（不抛异常）"""
        repo = MagicMock()
        repo.get_config.return_value = None  # DB 无覆盖 → 走 baseline 回填
        cfg = ChatbotConfig()
        container = {"config": cfg, "repo": repo}  # 故意缺 yaml_baseline
        result = api_chatbot_config.hot_reload_runtime_config(container, ["rag.top_k"])
        assert result["reloaded"] == ["rag.top_k"]
        assert container["config"].rag.top_k == ChatbotConfig().rag.top_k

    def test_unknown_key_skipped_silently(self):
        """不在规则表的键（如 welcome_message，只存 DB 天然热）→ 不进任何列表"""
        repo = MagicMock()
        cfg = ChatbotConfig()
        container = {"config": cfg, "repo": repo, "yaml_baseline": copy.deepcopy(cfg)}
        result = api_chatbot_config.hot_reload_runtime_config(container, ["welcome_message"])
        assert result == {"reloaded": [], "restart_required": [], "failed": []}
        repo.get_config.assert_not_called()
