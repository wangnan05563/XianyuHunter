"""本地 embedding 默认引擎跟随「产物内实际装着哪条链」（T04101）

为什么单独立一个测试：发布流水线切到 onnx 形态后，冻结包里根本没有
torch/sentence_transformers（xianyu-hunter.spec 的 _excludes），而
`Settings.embedding_engine` 的字面默认值曾是 "st" —— 首次向量检索要先撞一次
ImportError、再靠 embedding_service 的回退兜住。结果对，但每进程白跑一趟，
且日志里留一条误导性的 warning。

覆盖三档语义：
- 探测到 sentence_transformers → "st"（开发环境 / st 包，行为不变）
- 探测不到（onnx 包）→ "onnx"
- 探测本身抛异常 → 回落 "st"（不改变历史行为）
以及「显式配置优先于自动探测」「首选引擎可用时不触发回退」。
"""
from __future__ import annotations

import xianyu_hunter.config as config_mod
from xianyu_hunter.config import Settings


def _make_settings(**kw) -> Settings:
    """绕开 .env：本测试只关心「默认值」，不能让本机 %APPDATA% 下的冻结配置插手"""
    return Settings(_env_file=None, **kw)


class TestDefaultEngineProbe:
    def test_resolves_st_when_sentence_transformers_present(self, monkeypatch):
        monkeypatch.setattr(config_mod, "find_spec", lambda name: object())
        assert _make_settings().embedding_engine == "st"

    def test_resolves_onnx_when_sentence_transformers_absent(self, monkeypatch):
        # onnx 形态冻结包的构建期形态：torch 全家桶被 spec 排除
        monkeypatch.setattr(config_mod, "find_spec", lambda name: None)
        assert _make_settings().embedding_engine == "onnx"

    def test_falls_back_to_st_when_probe_raises(self, monkeypatch):
        def boom(name):
            raise ImportError("simulated broken importer")
        monkeypatch.setattr(config_mod, "find_spec", boom)
        assert _make_settings().embedding_engine == "st"


class TestExplicitConfigWins:
    def test_constructor_value_beats_probe(self, monkeypatch):
        monkeypatch.setattr(config_mod, "find_spec", lambda name: None)  # 探测会给 onnx
        assert _make_settings(embedding_engine="st").embedding_engine == "st"

    def test_env_var_beats_probe(self, monkeypatch):
        monkeypatch.setattr(config_mod, "find_spec", lambda name: object())  # 探测会给 st
        # 无 env_prefix：环境变量名即字段名（case_sensitive=False）
        monkeypatch.setenv("EMBEDDING_ENGINE", "onnx")
        assert Settings(_env_file=None).embedding_engine == "onnx"


class TestNoWastedFallback:
    """首选引擎与构建形态匹配时，_get_local_backend 不应再触发回退尝试"""

    def _service_with_recorder(self, monkeypatch, engine: str, available: set[str]):
        from xianyu_hunter.modules.chatbot import embedding_service as es_mod
        from xianyu_hunter.modules.chatbot.embedding_service import EmbeddingService

        calls: list[str] = []

        class _StubSettings:
            # 与 tests/test_embedding_backend_equivalence.py 的占位配置同形：
            # EmbeddingService.__init__ 会读 embedding_base_url 判本地/远程
            embedding_base_url = ""
            embedding_api_key = ""
            openai_api_key = ""
            embedding_engine = engine

        def fake_build(self, eng):
            calls.append(eng)
            return object() if eng in available else None

        monkeypatch.setattr(
            es_mod, "get_settings", lambda: _StubSettings()
        )
        monkeypatch.setattr(EmbeddingService, "_build_local_backend", fake_build)
        return EmbeddingService(ai_usage=object(), model="x", dimensions=512), calls

    def test_onnx_package_selects_onnx_directly(self, monkeypatch):
        # onnx 包 + 跟随构建形态解析出的默认值 → 一次命中，无 st 失败尝试
        svc, calls = self._service_with_recorder(monkeypatch, "onnx", {"onnx"})
        assert svc._get_local_backend() is not None
        assert calls == ["onnx"]

    def test_mismatch_still_falls_back(self, monkeypatch):
        # 用户显式配 st 但包里没有 torch：回退是既有安全网，必须保住
        svc, calls = self._service_with_recorder(monkeypatch, "st", {"onnx"})
        assert svc._get_local_backend() is not None
        assert calls == ["st", "onnx"]
