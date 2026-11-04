"""T04490：打包版默认配置下本地 embedding 的两个失效点

- 随包 models/ 里的权重没被运行期读到（裸名被 transformers 当 Hub 仓库 id 联网）
- 本地模式下模型名沿用远程 API provider 的默认值，embed 恒空 → 检索静默无上下文
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from xianyu_hunter import paths
from xianyu_hunter.modules.chatbot import embedding_service as es
from xianyu_hunter.modules.chatbot.local_embedding import LocalEmbeddingBackend


def test_随包裸名命中(tmp_path, monkeypatch):
    (tmp_path / "models" / "bge-small-zh-v1.5").mkdir(parents=True)
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    got = paths.resolve_bundled_model_path("bge-small-zh-v1.5")
    assert got == str((tmp_path / "models" / "bge-small-zh-v1.5").resolve())


def test_带org前缀的HF仓库id按末段命中(tmp_path, monkeypatch):
    (tmp_path / "models" / "bge-small-zh-v1.5").mkdir(parents=True)
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    assert paths.resolve_bundled_model_path("BAAI/bge-small-zh-v1.5") == str(
        (tmp_path / "models" / "bge-small-zh-v1.5").resolve()
    )


def test_未命中返回None(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    assert paths.resolve_bundled_model_path("nope-not-here") is None
    assert paths.resolve_bundled_model_path("") is None
    assert paths.resolve_bundled_model_path(str(tmp_path / "already" / "abs")) is None


def test_相对名字不能跳出models目录(tmp_path, monkeypatch):
    outside = tmp_path / "secret"
    outside.mkdir()
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    assert paths.resolve_bundled_model_path("../secret") is None


def _install_fake_st(monkeypatch, captured: list[str]) -> None:
    class FakeModel:
        def __init__(self, name: str, device: str = "") -> None:
            captured.append(name)

        def get_embedding_dimension(self) -> int:
            return 512

    mod = ModuleType("sentence_transformers")
    mod.SentenceTransformer = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def test_后端用随包绝对路径加载(monkeypatch, tmp_path):
    bundled = tmp_path / "models" / "bge-small-zh-v1.5"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    captured: list[str] = []
    _install_fake_st(monkeypatch, captured)

    backend = LocalEmbeddingBackend("BAAI/bge-small-zh-v1.5")
    backend._ensure_loaded()
    assert captured == [str(bundled.resolve())]
    assert backend.dimensions == 512


def test_没随包目录时保持原名去联网(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "get_app_dir", lambda: tmp_path)
    captured: list[str] = []
    _install_fake_st(monkeypatch, captured)

    LocalEmbeddingBackend("BAAI/some-other")._ensure_loaded()
    assert captured == ["BAAI/some-other"]


class _Settings:
    embedding_base_url = "local"
    embedding_engine = "st"


@pytest.fixture(autouse=True)
def _local_engine(monkeypatch):
    monkeypatch.setattr(es, "get_settings", lambda: _Settings())


def _service(model: str) -> es.EmbeddingService:
    return es.EmbeddingService(ai_usage=None, model=model)


def test_本地模式换掉远程模型名():
    svc = _service("text-embedding-3-small")
    assert svc._is_local is True
    assert svc._model == es._LOCAL_FALLBACK_MODEL


def test_本地模式不兜底用户显式写的其它名字():
    # 反证腿：把默认名换成一个不存在的本地名，必须原样留着，让失败可见
    assert _service("does-not-exist-xyz")._model == "does-not-exist-xyz"


def test_远程模式不动模型名(monkeypatch):
    class Remote(_Settings):
        embedding_base_url = "https://api.example.com/v1"
        embedding_api_key = "k"
        openai_api_key = ""

    monkeypatch.setattr(es, "get_settings", lambda: Remote())
    svc = es.EmbeddingService(ai_usage=None, model="text-embedding-3-small")
    assert svc._is_local is False
    assert svc._model == "text-embedding-3-small"
