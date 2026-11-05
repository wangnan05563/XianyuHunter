"""配置加载（pydantic-settings）

支持热更新：修改 AI 配置后无需重启服务，
通过 update_ai_config() 即时生效。
"""
from __future__ import annotations

import secrets as _secrets
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 路径统一入口：PyInstaller 打包后基于 _MEIPASS / exe 目录，
# 开发模式下基于 __file__ 推算项目根（不依赖 CWD）
from xianyu_hunter.paths import get_app_dir, get_env_file


def _default_embedding_engine() -> str:
    """本地 embedding 的默认引擎跟随「这份产物里实际装着哪条链」。

    onnx 形态的安装包在构建期就把 torch/sentence_transformers 整棵子树排除了
    （xianyu-hunter.spec 的 _excludes）。若默认值写死 "st"，冻结包首次向量检索会
    先撞一次 ImportError、再靠 embedding_service._get_local_backend 的回退兜住——
    结果正确但每进程白跑一趟，且日志里留一条误导性的 warning。

    只探测「装了什么」，不替用户做决定：显式配置（.env / 构造参数）优先级不变。
    """
    try:
        return "st" if find_spec("sentence_transformers") is not None else "onnx"
    except (ImportError, ValueError):
        # 探测本身失败（异常形态的冻结 importer 等）→ 退回历史行为，不改变语义
        return "st"


class Settings(BaseSettings):
    """全局配置，从 .env 读取"""
    # 为什么用 get_env_file() 而不是 ".env"：
    # 打包后 CWD 可能不确定（虽然 launcher.py 已 chdir，但 pydantic-settings
    # 在模块导入时解析 env_file，如果导入顺序早于 chdir 则读取失败）
    # 使用绝对路径确保任何情况下都能正确定位 .env
    model_config = SettingsConfigDict(
        env_file=str(get_env_file()),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 日志
    log_level: str = "INFO"

    # 推送服务
    serverchan_key: str = ""
    pushplus_token: str = ""
    bark_server: str = "https://api.day.app"
    bark_key: str = ""
    # P1-3：新增通知渠道配置（也可通过 keyring 存储）
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    wecom_webhook_url: str = ""
    dingtalk_webhook_url: str = ""
    dingtalk_secret: str = ""
    webhook_url: str = ""
    webhook_token: str = ""

    # AI 服务配置
    ai_enabled: bool = True  # AI 全局开关：关闭后所有 AI 功能降级到规则模式
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # 模型名称可配置：支持 DeepSeek(deepseek-chat) / 智谱(glm-4-flash) 等
    openai_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o"

    # Embedding 服务独立配置（与 LLM 解耦）：
    # - 留空时 fallback 到 OPENAI_* 配置（向后兼容）
    # - 配置后 KB/FAQ 向量化走独立 endpoint，避免 DeepSeek 等不支持 /embeddings 的服务商
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    # 0 表示未配置，由 cfg.kb.embedding_dimensions 兜底
    embedding_dimensions: int = 0
    # 本地 embedding 引擎：st=sentence-transformers(torch) / onnx=ONNX Runtime(无 torch)
    # 默认值由 _default_embedding_engine() 按产物内实际存在的依赖链解析
    # （onnx 形态的冻结包里没有 torch，写死 "st" 会白撞一次回退）；显式配置仍可覆盖两种方向。
    embedding_engine: str = Field(default_factory=_default_embedding_engine)

    # Web 认证：留空则首次启动自动生成并写入 .env
    # 本地个人工具场景下，token 防止同网络其他设备随意访问
    web_token: str = ""

    # 慢接口线程池专用道的容量（见 web/slow_lane.py）；<=0 关闭隔离，全部回到共享的 40 令牌池。
    # 4 不是随手写的：按压测实测「吞吐 × 单次占用」算出的慢族在途需求最坏为 2.14（96 并发档），
    # 4 个槽位的吞吐上限 9.16 rps 高于实测需求 4.9 rps —— 既够用，也不会把慢族自己堵死。
    slow_lane_tokens: int = 4

    # 进程内读缓存（web/cache.py）的条目上限，超过后按 LRU 驱逐；配 <=0 或读配置失败时
    # 退回代码里的兜底值 2000（同一数值）。
    # 2000 的来由：实测正常流量只会留下个位数条目（全量 GET 扫一遍 = 7 条），
    # 单条最深 7.9 KB、按每 task 2.0 条 / 8.8 KB 的增长率外推，2000 条的最坏驻留约 8.8 MB
    # —— 即上限本身不是省内存的手段，只是把「未约束查询参数把键空间撑爆」这条路的兜底封死。
    cache_max_entries: int = 2000

    # 闲鱼站点基础 URL：支持通过环境变量切换镜像/测试环境，
    # 避免在多个模块中硬编码导致切换困难
    xianyu_base_url: str = "https://www.goofish.com"

    # 路径
    # 走 paths.py 统一入口：PyInstaller 打包后基于 _MEIPASS / exe 目录，
    # 开发模式下基于 __file__ 推算项目根（不依赖 CWD）
    project_root: Path = get_app_dir()


@lru_cache
def get_settings() -> Settings:
    """单例获取配置"""
    s = Settings()
    # 首次启动自动生成 token 并持久化到 .env
    if not s.web_token:
        s.web_token = _generate_and_persist_token()
    # 优先从 keyring 读取敏感字段（迁移后 .env 中为占位符）
    _load_secrets_from_keyring(s)
    return s


def is_ai_disabled() -> bool:
    """AI 功能总开关是否已关闭（T02891 裁决 A：全量纳管的统一判据）

    判定权只放在这一处：此前只有 api_ai / api_ai_deep / worker 读 ai_enabled，
    chatbot、RAG、远程 Embedding、价格趋势解读四处零判断，
    于是「关闭总开关」之后仍在打付费端点。

    每次调用都重读 settings：开关是热更新的，构造期快照会让关闭之后的调用继续漏网。
    """
    return not bool(get_settings().ai_enabled)


def _load_secrets_from_keyring(s: Settings) -> None:
    """从 keyring 加载敏感字段，覆盖 .env 中的值

    迁移到 keyring 后，.env 中对应字段为占位符 __MIGRATED_TO_KEYRING__，
    需要从 keyring 读取真实值。keyring 中无值时保持 .env 原值（向后兼容）。
    """
    from xianyu_hunter.infra import secrets as _secret_store
    api_key = _secret_store.get_secret(_secret_store.KEY_OPENAI_API_KEY)
    # 空字符串也是有效状态：表示当前预设明确没有 Key，必须覆盖旧 .env 值。
    if api_key is not None:
        s.openai_api_key = api_key
    # Embedding API Key 独立存储：DeepSeek 等不支持 /embeddings 时需切换到
    # Ollama/Jina 等服务，凭证不应与 LLM 混用
    emb_key = _secret_store.get_secret(_secret_store.KEY_EMBEDDING_API_KEY)
    if emb_key is not None:
        s.embedding_api_key = emb_key


def update_ai_config(
    ai_enabled: bool | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    vision_model: str | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key: str | None = None,
    embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
) -> None:
    """热更新 AI 配置（无需重启服务）

    同时更新内存中的 Settings 单例和 .env 文件，
    确保下次启动也能读到最新值。

    注意：embedding_* 参数为 None 表示不修改；空字符串表示清除该字段。
    """
    s = get_settings()
    env_updates: dict[str, str] = {}

    if ai_enabled is not None:
        s.ai_enabled = ai_enabled
        env_updates["AI_ENABLED"] = str(ai_enabled)
    if base_url is not None:
        s.openai_base_url = base_url
        env_updates["OPENAI_BASE_URL"] = base_url
    if api_key is not None:
        s.openai_api_key = api_key
        # 敏感字段写入 keyring 而非 .env 明文
        from xianyu_hunter.infra import secrets as _secret_store
        _secret_store.set_secret(_secret_store.KEY_OPENAI_API_KEY, api_key)
    if model is not None:
        s.openai_model = model
        env_updates["OPENAI_MODEL"] = model
    if vision_model is not None:
        s.openai_vision_model = vision_model
        env_updates["OPENAI_VISION_MODEL"] = vision_model

    # Embedding 配置独立处理：留空时 fallback 到 OPENAI_*（向后兼容）
    if embedding_base_url is not None:
        s.embedding_base_url = embedding_base_url
        env_updates["EMBEDDING_BASE_URL"] = embedding_base_url
    if embedding_api_key is not None:
        s.embedding_api_key = embedding_api_key
        from xianyu_hunter.infra import secrets as _secret_store
        _secret_store.set_secret(_secret_store.KEY_EMBEDDING_API_KEY, embedding_api_key)
    if embedding_model is not None:
        s.embedding_model = embedding_model
        env_updates["EMBEDDING_MODEL"] = embedding_model
    if embedding_dimensions is not None:
        s.embedding_dimensions = embedding_dimensions
        env_updates["EMBEDDING_DIMENSIONS"] = str(embedding_dimensions)

    if env_updates:
        _persist_to_env(env_updates)


def _persist_to_env(updates: dict[str, str]) -> None:
    """将配置变更持久化到 .env 文件（upsert 语义）"""
    env_path = get_env_file()
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                # 同名 key 可能已有多行（历史追加造成，见 T04183）：只在首次出现处落新值，
                # 其余重复行丢弃。逐行改写的话一次 upsert 会留下 N 行同值，文件永远收敛不下来。
                if key not in updated_keys:
                    new_lines.append(f"{key}={updates[key]}")
                    updated_keys.add(key)
                continue
        new_lines.append(line)

    # 追加新增的键
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    try:
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError as e:
        from xianyu_hunter.infra.logger import get_logger
        get_logger().warning("写入 .env 失败: %s", e)


def _generate_and_persist_token() -> str:
    """生成随机 token 并写入 .env（走 upsert 通道，同名行原地替换而非追加）"""
    token = _secrets.token_urlsafe(32)
    # 写失败只警告、不阻断启动（用户可手动写 .env 或经 /api/auth/verify 设置）：
    # 该语义由 _persist_to_env 内部吞 OSError 提供，不要再在这里另开一条 open("a") 旁路。
    _persist_to_env({"WEB_TOKEN": token})
    return token
