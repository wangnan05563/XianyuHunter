"""智能客服配置与审计 API - 配置获取/热更新 + 审计日志查询

设计要点：
- GET /config 返回合并后的配置（yaml 基线 + DB 覆盖）
- PUT /config 仅支持热更新字段（_UPDATABLE_KEYS 白名单），写入 DB + 审计日志
- 审计日志仅存 sha256 hash 不存明文（敏感字段保护，见 repo_chatbot.add_audit_log）
- chatbot 子容器为 None 时统一返回 403 CHATBOT_DISABLED

认证：由 BearerAuthMiddleware 统一处理。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from xianyu_hunter.web.deps import get_container

router = APIRouter(prefix="/api/chatbot", tags=["chatbot-config"])

# 配置 key 常量：白名单与合并逻辑共享同一字符串字面量，提取为常量避免散落修改
# 同时规避 SonarQube S1192（字符串字面量重复 3+ 次需提取为常量）
KEY_ENABLED = "enabled"
KEY_MAX_HISTORY_TURNS = "max_history_turns"
KEY_SESSION_TIMEOUT_MIN = "session_timeout_min"
KEY_RAG_TOP_K = "rag.top_k"
KEY_RAG_SIMILARITY_THRESHOLD = "rag.similarity_threshold"
KEY_RAG_MAX_CONTEXT_CHARS = "rag.max_context_chars"
KEY_AGENT_ENABLE_TOOLS = "agent.enable_tools"
KEY_AGENT_MAX_TOOL_ROUNDS = "agent.max_tool_rounds"
KEY_AGENT_TOOL_TRIGGER_MODE = "agent.tool_trigger_mode"
KEY_KB_AUTO_UPDATE_ENABLED = "kb.auto_update_enabled"
KEY_KB_UPDATE_INTERVAL_HOURS = "kb.update_interval_hours"
KEY_FAQ_SIMILARITY_THRESHOLD = "faq.similarity_threshold"
KEY_FAQ_CONFIRM_THRESHOLD = "faq.confirm_threshold"
KEY_ESCALATION_CONTACT = "escalation.contact"
KEY_ESCALATION_FEEDBACK_THRESHOLD = "escalation.feedback_threshold"
KEY_ESCALATION_FEEDBACK_WINDOW_MIN = "escalation.feedback_window_min"
KEY_ESCALATION_SANITIZE_PII = "escalation.sanitize_pii"
KEY_WELCOME_MESSAGE = "welcome_message"
# T05072（FR-E-4）：llm.* 六键补进白名单。
# 修复既有缺陷：Config.tsx LLM 模型 Tab 一直在调 updateConfig('llm.model', ...)，
# 但这些 key 不在白名单里，PUT 会被 pydantic 422 拒绝，前端只在 catch 里回滚 UI，
# 用户改模型名永远不生效且只有一条「更新失败」toast。
KEY_LLM_MODEL = "llm.model"
KEY_LLM_TEMPERATURE = "llm.temperature"
KEY_LLM_MAX_TOKENS = "llm.max_tokens"
KEY_LLM_HTTP_TIMEOUT_SEC = "llm.http_timeout_sec"
KEY_LLM_FIRST_TOKEN_TIMEOUT_SEC = "llm.first_token_timeout_sec"
KEY_LLM_VISION_MODEL = "llm.vision_model"


# 支持热更新的配置 key 白名单
# 为什么用集合：O(1) 查找；为什么显式列出：避免误开放需要重启的字段（如 llm.model / kb.doc_paths / kb.embedding_model）
# 覆盖 Config.tsx 中所有 updateConfig 调用，避免前端编辑被 422 拒绝导致 UI 闪回
_UPDATABLE_KEYS: set[str] = {
    KEY_ENABLED,
    KEY_MAX_HISTORY_TURNS,
    KEY_SESSION_TIMEOUT_MIN,
    KEY_RAG_TOP_K,
    KEY_RAG_SIMILARITY_THRESHOLD,
    KEY_RAG_MAX_CONTEXT_CHARS,
    KEY_AGENT_ENABLE_TOOLS,
    KEY_AGENT_MAX_TOOL_ROUNDS,
    KEY_AGENT_TOOL_TRIGGER_MODE,
    KEY_KB_AUTO_UPDATE_ENABLED,
    KEY_KB_UPDATE_INTERVAL_HOURS,
    KEY_FAQ_SIMILARITY_THRESHOLD,
    KEY_FAQ_CONFIRM_THRESHOLD,
    KEY_ESCALATION_CONTACT,
    KEY_ESCALATION_FEEDBACK_THRESHOLD,
    KEY_ESCALATION_FEEDBACK_WINDOW_MIN,
    KEY_ESCALATION_SANITIZE_PII,
    KEY_WELCOME_MESSAGE,
    KEY_LLM_MODEL,
    KEY_LLM_TEMPERATURE,
    KEY_LLM_MAX_TOKENS,
    KEY_LLM_HTTP_TIMEOUT_SEC,
    KEY_LLM_FIRST_TOKEN_TIMEOUT_SEC,
    KEY_LLM_VISION_MODEL,
}


def _get_chatbot_or_403() -> dict[str, Any]:
    """获取 chatbot 子容器，未启用时抛 403"""
    container = get_container()
    if container.chatbot is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "CHATBOT_DISABLED", "message": "智能客服未启用"},
        )
    return container.chatbot


class ConfigUpdateRequest(BaseModel):
    """配置更新请求（仅支持热更新字段）"""
    key: str = Field(..., description="配置 key（必须在 updatable_keys 中）")
    value: str = Field(..., max_length=1000, description="配置值（字符串形式）")

    @field_validator("key")
    @classmethod
    def validate_updatable(cls, v: str) -> str:
        if v not in _UPDATABLE_KEYS:
            raise ValueError(f"配置项 {v} 不支持热更新")
        return v


def _hash_value(value: str | None) -> str | None:
    """对配置值取 sha256 hash（审计日志不存明文）"""
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# DB 覆盖应用规则表：(db_key, result_path, type_converter, hot_reload)
# S3776 修复：将 17 个重复 if 分支合并为表驱动遍历，主函数复杂度从 17 降至 ~3
# type_converter 决定如何把 DB 中的字符串转换为目标类型：
# - "bool": "true"→True（小写比较）
# - "int": int()
# - "float": float()
# - "str": 原样保留
# hot_reload（T05209，FR-E-2）：该键保存后是否对运行时组件立即生效。
# 口径 = 组件是否实时读 config 对象（True）vs 构造时固化（False）：
# - True：RAGEngine/Agent/Orchestrator/ContextManager/FAQMatcher/Escalation
#         均在每次调用时实时读 self._config.xxx / self._llm_config.xxx，
#         原地更新 chatbot["config"] 对应字段即可生效；
# - False：构造时固化的键，热加载只改快照不改行为，必须诚实标重启生效：
#   * enabled：chatbot 子容器启停在启动时判断（container.py:555）
#   * session_timeout_min：Escalation.__init__ 拷贝为 self._session_timeout_min
#   * kb.auto_update_enabled / kb.update_interval_hours：KBRefreshScheduler.start() 固化
#   * llm.http_timeout_sec：RAGEngine/Agent/IntentClassifier 的 httpx client 构造时固化
# welcome_message 不在此表：只存 DB，GET /welcome 实时读 DB，天然热，无需应用到 cfg 对象。
_DB_OVERRIDE_RULES: list[tuple[str, str, str, bool]] = [
    (KEY_ENABLED, "enabled", "bool", False),
    (KEY_MAX_HISTORY_TURNS, "max_history_turns", "int", True),
    (KEY_SESSION_TIMEOUT_MIN, "session_timeout_min", "int", False),
    (KEY_RAG_TOP_K, "rag.top_k", "int", True),
    (KEY_RAG_SIMILARITY_THRESHOLD, "rag.similarity_threshold", "float", True),
    (KEY_RAG_MAX_CONTEXT_CHARS, "rag.max_context_chars", "int", True),
    (KEY_AGENT_ENABLE_TOOLS, "agent.enable_tools", "bool", True),
    (KEY_AGENT_MAX_TOOL_ROUNDS, "agent.max_tool_rounds", "int", True),
    (KEY_AGENT_TOOL_TRIGGER_MODE, "agent.tool_trigger_mode", "str", True),
    (KEY_KB_AUTO_UPDATE_ENABLED, "kb.auto_update_enabled", "bool", False),
    (KEY_KB_UPDATE_INTERVAL_HOURS, "kb.update_interval_hours", "int", False),
    (KEY_FAQ_SIMILARITY_THRESHOLD, "faq.similarity_threshold", "float", True),
    (KEY_FAQ_CONFIRM_THRESHOLD, "faq.confirm_threshold", "float", True),
    (KEY_ESCALATION_CONTACT, "escalation.contact", "str", True),
    (KEY_ESCALATION_FEEDBACK_THRESHOLD, "escalation.feedback_threshold", "int", True),
    (KEY_ESCALATION_FEEDBACK_WINDOW_MIN, "escalation.feedback_window_min", "int", True),
    (KEY_ESCALATION_SANITIZE_PII, "escalation.sanitize_pii", "bool", True),
    # T05072：llm.* 六键进 DB 覆盖规则，与白名单对齐——GET /config 才能把
    # 已保存的 LLM 草稿值回读出来，否则写进了 DB 却永远读不回。
    # T05209：http_timeout_sec 构造固化标重启生效，其余五键组件实时读标热加载。
    (KEY_LLM_MODEL, "llm.model", "str", True),
    (KEY_LLM_TEMPERATURE, "llm.temperature", "float", True),
    (KEY_LLM_MAX_TOKENS, "llm.max_tokens", "int", True),
    (KEY_LLM_HTTP_TIMEOUT_SEC, "llm.http_timeout_sec", "int", False),
    (KEY_LLM_FIRST_TOKEN_TIMEOUT_SEC, "llm.first_token_timeout_sec", "int", True),
    (KEY_LLM_VISION_MODEL, "llm.vision_model", "str", True),
]

# db_key → 规则 索引（热加载与 restart_required_keys 元数据共用）
_RULE_BY_KEY: dict[str, tuple[str, str, str, bool]] = {r[0]: r for r in _DB_OVERRIDE_RULES}


def _restart_required_keys() -> list[str]:
    """白名单中保存后必须重启才对运行时生效的键（T05209 元数据，供前端标注）。"""
    return sorted(key for key, (_p, _c, _h, hot) in _RULE_BY_KEY.items() if not hot)


def _convert_db_override(raw: str, conv: str) -> bool | int | float | str:
    """按类型转换器把 DB 字符串值转换为目标类型。"""
    if conv == "bool":
        return raw.lower() == "true"
    if conv == "int":
        return int(raw)
    if conv == "float":
        return float(raw)
    return raw


def _apply_db_overrides(result: dict[str, Any], db_overrides: dict[str, str]) -> None:
    """按 _DB_OVERRIDE_RULES 表把 DB 覆盖值写入 result 对应路径。"""
    for db_key, path, conv, _hot in _DB_OVERRIDE_RULES:
        if db_key not in db_overrides:
            continue
        converted = _convert_db_override(db_overrides[db_key], conv)
        # 支持 "section.field" 形式的嵌套路径
        parts = path.split(".")
        target = result
        for p in parts[:-1]:
            target = target[p]
        target[parts[-1]] = converted


@router.get("/config")
def get_config() -> dict[str, Any]:
    """获取合并后的配置（yaml 基线 + DB 覆盖）

    DB 中存在的热更新 key 覆盖 yaml 默认值，未存在的用 yaml 默认值。
    """
    chatbot = _get_chatbot_or_403()
    return _merged_config(chatbot)


# ============== 运行时热加载（T05209 / FR-E-2） ==============

def _get_nested_attr(root: Any, path: str) -> Any:
    """按点号路径沿属性链取值（如 "rag.top_k" → root.rag.top_k）。"""
    obj = root
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set_nested_attr(root: Any, path: str, value: Any) -> None:
    """按点号路径沿属性链赋值（原地更新，组件持引用即可见）。"""
    parts = path.split(".")
    obj = root
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def hot_reload_runtime_config(chatbot: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """把变更键应用到运行时配置对象（原地更新 chatbot["config"]）。

    数据源：
    - DB 中存在覆盖值 → 类型转换后 setattr；
    - DB 中不存在（空串恢复默认已删行）→ 从 chatbot["yaml_baseline"] 读启动基线值回填。

    口径（与 _DB_OVERRIDE_RULES 第 4 列一致）：
    - hot_reload=True：原地更新 cfg 对应字段——组件实时读 self._config.xxx，立即生效；
    - hot_reload=False：跳过并归入 restart_required——组件构造时固化，热改快照不改行为；
    - 不在规则表的键（welcome_message）：只存 DB、GET /welcome 实时读 DB，天然热，无需应用。

    单键失败不中断整体（failed 列表上报），保证保存主流程不受热加载异常拖累。
    """
    cfg = chatbot["config"]
    baseline = chatbot.get("yaml_baseline") or cfg
    repo = chatbot["repo"]
    reloaded: list[str] = []
    restart_required: list[str] = []
    failed: list[dict[str, str]] = []

    for key in keys:
        rule = _RULE_BY_KEY.get(key)
        if rule is None:
            continue
        _db_key, path, conv, hot = rule
        if not hot:
            restart_required.append(key)
            continue
        try:
            raw = repo.get_config(key)
            if raw is None:
                value = _get_nested_attr(baseline, path)
            else:
                value = _convert_db_override(raw, conv)
            _set_nested_attr(cfg, path, value)
            reloaded.append(key)
        except Exception as e:  # noqa: BLE001 单键失败不阻断其他键
            logger.warning(f"热加载失败: key={key} error={e}")
            failed.append({"key": key, "error": str(e)})

    if reloaded:
        logger.info(f"配置热加载生效: {reloaded}")
    if restart_required:
        logger.info(f"配置已保存，重启后生效: {restart_required}")
    return {
        "reloaded": reloaded,
        "restart_required": restart_required,
        "failed": failed,
    }


def _merged_config(chatbot: dict[str, Any]) -> dict[str, Any]:
    """构建合并后的配置视图（yaml 基线 + DB 覆盖）。

    T05072：从 get_config 中抽出，供 GET /config 与 POST /config/batch 共用，
    保证「展示」与「dry-run diff 基线」永远同一份口径。
    """
    cfg = chatbot["config"]
    db_overrides = chatbot["repo"].get_all_config()

    # 以 yaml 配置为基线
    result: dict[str, Any] = {
        KEY_ENABLED: cfg.enabled,
        KEY_MAX_HISTORY_TURNS: cfg.max_history_turns,
        KEY_SESSION_TIMEOUT_MIN: cfg.session_timeout_min,
        "rag": cfg.rag.model_dump(),
        "llm": cfg.llm.model_dump(),
        "agent": cfg.agent.model_dump(),
        "kb": cfg.kb.model_dump(),
        "faq": cfg.faq.model_dump(),
        "escalation": cfg.escalation.model_dump(),
    }

    # 应用 DB 覆盖（仅热更新字段，类型转换与 _UPDATABLE_KEYS 对应）
    _apply_db_overrides(result, db_overrides)

    # welcome_message 单独处理：白名单允许 PUT 热更新，但 _DB_OVERRIDE_RULES
    # 仅覆盖嵌套路径字段（a.b 形式），而 welcome_message 是顶层字段；
    # 且空字符串/None 语义上等价于"使用默认欢迎语"，所以原样透传 DB 值
    # 让前端可读+可写（Config.tsx 用 placeholder 提示默认文案）。
    result["welcome_message"] = db_overrides.get(KEY_WELCOME_MESSAGE)

    result["updatable_keys"] = sorted(_UPDATABLE_KEYS)
    # T05209：重启生效键元数据——前端可据此对这类字段标注「重启生效」，
    # 避免用户误以为保存即生效（运行时热加载口径见 _DB_OVERRIDE_RULES 第 4 列）
    result["restart_required_keys"] = _restart_required_keys()
    return result


@router.put("/config")
def update_config(req: ConfigUpdateRequest) -> dict[str, Any]:
    """更新配置（仅热更新字段）

    流程：读旧值 → 写 DB → 写审计日志（仅 hash）→ 热加载运行时 → 返回新旧值。
    T05072：保留为兼容入口；新前端统一走 POST /config/batch（dry-run diff 预览 + 批量原子提交）。
    T05209：落库成功后调用 hot_reload_runtime_config，热加载键立即对运行时生效。
    """
    chatbot = _get_chatbot_or_403()
    result = _apply_single_update(chatbot, req.key, req.value)
    if result.get("changed"):
        result["hot_reload"] = hot_reload_runtime_config(chatbot, [req.key])
    return result


def _apply_single_update(chatbot: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    """按既有单 key 语义应用一次配置更新（写 DB + 审计日志，仅 hash）。

    从 update_config 中抽出（T05072）：PUT /config 与 POST /config/batch 共用，
    保证逐 key 兼容入口与批量入口的落库/审计行为完全一致。
    """
    repo = chatbot["repo"]

    old_value = repo.get_config(key)
    # 跳过未变更的写入，减少审计日志噪音
    if old_value == value:
        return {
            "ok": True,
            "key": key,
            "old_value": old_value,
            "new_value": value,
            "changed": False,
        }

    # 空 value 视为"恢复默认"：直接删除 DB 中的覆盖行，而不是写入 value=''
    # 的空记录（空记录会被 get_config 当成有效覆盖值返回，污染前端状态）。
    # 仅对非必填的 string 字段启用此短路；其他类型（bool/int/float）传空串
    # 不会走到这里，因为 pydantic ConfigUpdateRequest 会先校验。
    if value == "":
        deleted = repo.delete_config(key)
        repo.add_audit_log(
            action="delete_config",
            target=key,
            old_value_hash=_hash_value(old_value),
            new_value_hash=_hash_value(None),
            source="web",
        )
        logger.info(f"配置已恢复默认: key={key} (deleted={deleted})")
        return {
            "ok": True,
            "key": key,
            "old_value": old_value,
            "new_value": None,
            "changed": True,
            "deleted": deleted,
        }

    repo.set_config(key, value)
    repo.add_audit_log(
        action="update_config",
        target=key,
        old_value_hash=_hash_value(old_value),
        new_value_hash=_hash_value(value),
        source="web",
    )
    logger.info(f"配置更新: key={key} old={old_value} new={value}")

    return {
        "ok": True,
        "key": key,
        "old_value": old_value,
        "new_value": value,
        "changed": True,
    }


@router.get("/audit-logs")
def list_audit_logs(
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """审计日志列表（按 created_at DESC）"""
    chatbot = _get_chatbot_or_403()
    items = chatbot["repo"].list_audit_logs(action=action, limit=limit)
    return {"items": items, "total": len(items)}


# ============== 统一保存范式（T05072 / FR-E-4，D-7 裁决：纳入） ==============

# GET /config 响应里的非配置元数据键：不算用户可编辑字段，diff 时必须剔除
# T05209：restart_required_keys 与 updatable_keys 同属元数据
_META_KEYS = frozenset({"updatable_keys", "restart_required_keys"})


class ConfigBatchRequest(BaseModel):
    """整份客服配置批量提交（统一保存范式）

    config 与 GET /config 同形（嵌套结构）；后端展平成白名单点号键后做 diff，
    只处理白名单内的字段差异——前端多带/少带非白名单字段不会造成误写。
    """
    config: dict[str, Any] = Field(..., description="整份客服配置（与 GET /config 同形，可不含 updatable_keys）")
    dry_run: bool = Field(False, description="true=只返回 diff 不写库（预览）")


def _flatten_path(obj: Any, path: str) -> Any:
    """按点号路径取值；路径中断返回 None（视为「未提供该字段」）"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _normalize_for_db(value: Any) -> str:
    """把前端提交的 JSON 值归一化为 DB 字符串形式。

    与既有逐 key PUT 的前端行为（String(value)）对齐：
    bool → 'true'/'false'（小写，_convert_db_override 按 lower()=='true' 读回）；
    None → ''（恢复默认语义，_apply_single_update 里删除覆盖行）；
    其余 str()（int/float/str）。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _diff_updatable_fields(chatbot: dict[str, Any], submitted: dict[str, Any]) -> list[dict[str, Any]]:
    """白名单口径的 diff：{path, op, old, new}，仅返回有差异的键。

    - current 取 _merged_config（yaml 基线 + DB 覆盖），submitted 为前端草稿；
    - 非白名单键（含 updatable_keys 元数据、faq CRUD 等结构差异）一律忽略；
    - submitted 缺字段视作 None：None 与 current 值不同会记 modify（new=null），
      与 _normalize_for_db(None)→''（恢复默认）语义一致，由前端保证只提交完整草稿。
    """
    current = _merged_config(chatbot)
    diffs: list[dict[str, Any]] = []
    for key in sorted(_UPDATABLE_KEYS):
        if key in _META_KEYS:
            continue
        old_raw = _flatten_path(current, key)
        new_raw = _flatten_path(submitted, key)
        # 统一按 DB 字符串形式比较，避免 int/float/bool 的 JSON 类型噪音
        old_str = _normalize_for_db(old_raw)
        new_str = _normalize_for_db(new_raw)
        if old_str == new_str:
            continue
        if new_str == "":
            op = "delete"
            new_display: Any = None
        else:
            op = "modify" if old_str else "add"
            new_display = new_raw
        diffs.append({"path": key, "op": op, "old": old_raw, "new": new_display})
    return diffs


@router.post("/config/batch")
def batch_update_config(req: ConfigBatchRequest) -> dict[str, Any]:
    """客服配置批量保存（统一保存范式）

    dry_run=true：只返回 diffs，不写库、不写审计（与 /api/config/save 的 dry_run 口径一致）。
    dry_run=false：对 diff 逐项走 _apply_single_update（写 DB + 审计日志），返回应用结果；
    落库成功后统一热加载（T05209）——热加载键立即对运行时组件生效，重启生效键归入
    hot_reload.restart_required 由前端提示。
    """
    chatbot = _get_chatbot_or_403()
    diffs = _diff_updatable_fields(chatbot, req.config)

    if req.dry_run:
        return {"ok": True, "dry_run": True, "diffs": diffs}

    applied: list[dict[str, Any]] = []
    for d in diffs:
        value_str = _normalize_for_db(_flatten_path(req.config, d["path"]))
        applied.append(_apply_single_update(chatbot, d["path"], value_str))
    changed = sum(1 for r in applied if r.get("changed"))
    changed_keys = [r["key"] for r in applied if r.get("changed")]
    hot_reload = hot_reload_runtime_config(chatbot, changed_keys) if changed_keys else None
    return {
        "ok": True,
        "dry_run": False,
        "diffs": diffs,
        "applied": applied,
        "changed": changed,
        "hot_reload": hot_reload,
    }


# ============== FAQ 管理 ==============
class FAQUpsertRequest(BaseModel):
    """FAQ 新增/更新请求

    字段 enabled (boolean) 对应 DB 中 is_active (int 0/1)，
    路由层做转换，前端不感知底层存储差异。
    """
    id: int | None = Field(None, description="FAQ ID；None 表示新增")
    question: str = Field(..., min_length=1, max_length=500, description="问题")
    answer: str = Field(..., min_length=1, max_length=2000, description="答案")
    category: str = Field("general", max_length=50, description="分类")
    enabled: bool = Field(True, description="是否启用")
    sort_order: int = Field(0, ge=0, description="排序权重")


@router.get("/faq")
def list_faqs(
    active_only: bool = Query(False, description="仅返回启用的 FAQ"),
) -> list[dict[str, Any]]:
    """FAQ 列表（按 sort_order ASC, id ASC）

    返回数组格式（非 {items, total}），与前端 FAQ[] 类型对齐。
    """
    chatbot = _get_chatbot_or_403()
    items = chatbot["repo"].list_faqs(active_only=active_only)
    # 字段映射：后端 is_active (int) → 前端 enabled (boolean)
    for item in items:
        item["enabled"] = bool(item.pop("is_active", 1))
    return items


@router.post("/faq", status_code=201)
def upsert_faq(req: FAQUpsertRequest) -> dict[str, Any]:
    """FAQ 新增/更新（upsert）

    id 为 None 时新增，否则更新已有 FAQ。
    FAQMatcher 有 5 分钟 TTL 缓存，更新后最多 5 分钟内生效（_load_faqs）。
    """
    chatbot = _get_chatbot_or_403()
    repo = chatbot["repo"]
    result = repo.upsert_faq(
        faq_id=req.id,
        question=req.question,
        answer=req.answer,
        category=req.category,
        is_active=1 if req.enabled else 0,
        sort_order=req.sort_order,
    )
    repo.add_audit_log(
        action="faq_upsert",
        target=str(result["id"]),
        old_value_hash=None,
        new_value_hash=_hash_value(req.question[:50]),
        source="web",
    )
    logger.info(f"FAQ upsert: id={result['id']} question={req.question[:30]}")
    result["enabled"] = bool(result.pop("is_active", 1))
    return result


@router.delete("/faq/{faq_id}")
def delete_faq(faq_id: int) -> dict[str, Any]:
    """删除 FAQ"""
    chatbot = _get_chatbot_or_403()
    repo = chatbot["repo"]
    ok = repo.delete_faq(faq_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail={"code": "FAQ_NOT_FOUND", "message": "FAQ 不存在"},
        )
    repo.add_audit_log(
        action="faq_delete",
        target=str(faq_id),
        old_value_hash=None,
        new_value_hash=None,
        source="web",
    )
    logger.info(f"FAQ 删除: id={faq_id}")
    return {"ok": True, "id": faq_id}


# ============== 欢迎语（M1 引导卡）==============

# 默认欢迎语：DB 未配置时使用，前端可在用户首次进入时叠加"个性化"策略
_DEFAULT_WELCOME_MESSAGE = "Hi，我是智能客服小蜜，请问有什么可以帮您？"


@router.get("/welcome")
def get_welcome() -> dict[str, Any]:
    """获取欢迎语（M1 引导卡数据源）

    优先从 chatbot_config 表读 welcome_message，未配置时用默认值。
    chatbot 禁用时返回默认欢迎语（引导卡与 LLM 解耦）。
    """
    from sqlalchemy import select
    from xianyu_hunter.infra.db_models import ChatbotConfigRow

    container = get_container()
    message = _DEFAULT_WELCOME_MESSAGE
    updated_at: str | None = None
    # 走 chatbot 子容器获取 Session：与现有 get_config 保持一致的访问路径
    if container.chatbot is not None:
        repo = container.chatbot["repo"]
        db_value = repo.get_config(KEY_WELCOME_MESSAGE)
        if db_value:
            message = db_value
        # updated_at 单独查一次（get_config 不返回时间戳，避免破坏现有签名）
        with repo._session() as session:
            row = session.execute(
                select(ChatbotConfigRow).where(ChatbotConfigRow.key == KEY_WELCOME_MESSAGE)
            ).scalars().first()
            if row and row.updated_at:
                updated_at = row.updated_at.isoformat()
    return {
        "message": message,
        "persona": "xiaomi",  # 预留，不引入多 persona
        "updated_at": updated_at,
    }
