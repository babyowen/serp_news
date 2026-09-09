"""Versioned configuration contract. Never imports or executes application config."""
import hashlib
import json
import math
import re
import string
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


class ConfigConflict(ConfigError):
    pass


class ConfigVersionError(ConfigError):
    """Invalid user-supplied version, distinct from a damaged store."""


class ConfigVersionNotFound(ConfigVersionError):
    pass


USER_FIELDS = {
    "NEWS_SCORE_PROMPT": {"keyword", "title", "content"},
    "NEWS_ITEM_SUMMARY_USER_PROMPT_500": {"title", "content"},
    "NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION": {"title", "content", "business_type_catalog"},
    "NEWS_ITEM_SHORT_CONTENT_USER_PROMPT_GJJ_REGION": {"title", "content", "business_type_catalog"},
    "NEWS_BUSINESS_TYPE_USER_PROMPT_GJJ": {"title", "content", "business_type_catalog"},
    "NEWS_REGION_USER_PROMPT_GJJ": {"title", "content"},
    "NEWS_SUMMARY_USER_PROMPT": {"keyword", "news_list"},
    "NEWS_SUMMARY_JUDGE_USER_PROMPT": {"system_prompt", "user_prompt", "summary"},
    "NEWS_SUMMARY_OPTIMIZE_USER_PROMPT": {"system_prompt", "user_prompt", "summary", "judge_suggestion"},
    "NEWS_SUMMARY_HOTSPOT_USER_PROMPT": {"prev_summary", "today_summary"},
}
SYSTEM_NAMES = {
    "NEWS_SCORE_SYSTEM_MSG", "NEWS_SCORE_SYSTEM_MSG_ELDER_CARE",
    "NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK", "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500",
    "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION",
    "NEWS_ITEM_SHORT_CONTENT_SYSTEM_PROMPT_GJJ_REGION", "NEWS_BUSINESS_TYPE_SYSTEM_PROMPT_GJJ",
    "NEWS_REGION_SYSTEM_PROMPT_GJJ", "NEWS_SUMMARY_SYSTEM_PROMPT",
    "NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT", "NEWS_SUMMARY_OPTIMIZE_SYSTEM_PROMPT",
    "NEWS_SUMMARY_HOTSPOT_SYSTEM_PROMPT",
}
REQUIRED_PROMPTS = SYSTEM_NAMES | set(USER_FIELDS)
REQUIRED_SETTINGS = {
    "SEARCH_KEYWORDS", "blacklist_keywords", "NEWS_RULE_BASED_SCORING",
    "NEWS_SUMMARY_FILTER_SOURCEAPI", "NEWS_SUMMARY_RESULT_FILENAME", "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL", "USE_EMOJI_OUTPUT", "EMOJI_MAP", "USE_ICON_MANAGER",
    "FORCE_ICON_THEME", "ICON_MANAGER_CONFIG", "ENABLE_LEGACY_UNICODE_CLEAN",
}
RESERVED_CONFIG_NAMES = REQUIRED_SETTINGS | {
    "DEFAULT_KEYWORDS", "DEFAULT_KEYWORD", "KEYWORD_SPECIFIC_SYSTEM_PROMPTS",
    "NEWS_SUMMARY_MODELS", "NEWS_SUMMARY_PLATFORM", "NEWS_SUMMARY_MODEL",
    "API_KEY", "SERPAPI_KEY", "GNEWS_API_KEY", "DEEPSEEK_API_KEY",
}
STAGES = {"scoring", "item_summarizer", "region"}
REQUEST_FIELDS = {
    "temperature", "top_p", "max_tokens", "max_completion_tokens", "presence_penalty",
    "frequency_penalty", "seed", "timeout", "stream",
}


def encode(value):
    # Preserve dictionary order: keyword order is part of the runtime contract.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def parse_json(text):
    def invalid_constant(value):
        raise ConfigError("非法 JSON 数值")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ConfigError(f"重复 JSON 字段: {key}")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ConfigError(f"配置 JSON 无效: {exc}") from exc


def keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigError(f"{label} 字段不兼容，必须包含且仅包含: {', '.join(sorted(expected))}")


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError(f"{label} 必须是非空字符串")


def validate_keywords(value):
    if not isinstance(value, dict) or not value:
        raise ConfigError("至少保留一个主关键词")
    for main, terms in value.items():
        nonempty(main, "主关键词")
        if main != main.strip() or any(c in main for c in ("/", "\r", "\n")) or main in (".", ".."):
            raise ConfigError("主关键词不能包含路径分隔符、换行或首尾空白")
        if not isinstance(terms, list) or not terms:
            raise ConfigError(f"主关键词 {main} 至少需要一个检索词")
        for term in terms:
            nonempty(term, "检索词")


def validate_url(value):
    nonempty(value, "接口地址")
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConfigError("接口地址必须为不含凭据、查询参数或片段的 HTTP(S) URL")


def validate_api_env(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*_API_KEY", value):
        raise ConfigError("密钥必须通过 *_API_KEY 环境变量引用")


def _validate(document):
    keys(document, {"schema_version", "prompts", "settings", "keyword_prompt_ids", "models", "legacy"}, "配置")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ConfigError("不支持的配置结构版本")
    prompts = document["prompts"]
    if not isinstance(prompts, dict) or not REQUIRED_PROMPTS <= set(prompts):
        raise ConfigError("缺少现用或归档提示词")
    for name, prompt in prompts.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ConfigError("提示词 ID 必须为大写字母、数字和下划线")
        if name in RESERVED_CONFIG_NAMES:
            raise ConfigError(f"提示词 ID {name} 是配置读取入口的保留名称")
        keys(prompt, {"text", "status"}, name)
        nonempty(prompt["text"], name)
        if prompt["status"] not in ("active", "archived"):
            raise ConfigError(f"{name} 状态无效")
        if name in REQUIRED_PROMPTS and not name.startswith("NEWS_SUMMARY_") and prompt["status"] != "active":
            raise ConfigError(f"当前调用方要求 {name} 保持 active")
        if name in USER_FIELDS:
            try:
                parts = list(string.Formatter().parse(prompt["text"]))
                fields = {field for _, field, _, _ in parts if field is not None}
                if fields != USER_FIELDS[name] or any(spec or conversion for _, _, spec, conversion in parts):
                    raise ConfigError(f"{name} 占位符必须为 {sorted(USER_FIELDS[name])}，不支持转换或格式说明")
                prompt["text"].format(**{field: "检查" for field in fields})
            except (ValueError, KeyError, IndexError) as exc:
                raise ConfigError(f"{name} 模板无效: {exc}") from exc
    settings = document["settings"]
    # Reject unknown fields instead of allowing old editors to silently drop them.
    keys(settings, REQUIRED_SETTINGS, "settings")
    validate_keywords(settings["SEARCH_KEYWORDS"])
    if not isinstance(settings["blacklist_keywords"], list) or not all(isinstance(v, str) for v in settings["blacklist_keywords"]):
        raise ConfigError("黑名单必须为字符串列表")
    if not isinstance(settings["NEWS_RULE_BASED_SCORING"], list):
        raise ConfigError("规则评分必须为列表")
    for rule in settings["NEWS_RULE_BASED_SCORING"]:
        if not isinstance(rule, dict) or not {"main_keyword", "title_contains"} <= set(rule) or set(rule) - {"main_keyword", "title_contains", "score"}:
            raise ConfigError("规则评分字段不兼容")
        nonempty(rule["main_keyword"], "规则主关键词")
        nonempty(rule["title_contains"], "规则标题条件")
        if type(rule.get("score", 0)) is not int or not 0 <= rule.get("score", 0) <= 5:
            raise ConfigError("规则评分必须为 0–5 的整数")
    for name in ("USE_EMOJI_OUTPUT", "USE_ICON_MANAGER", "ENABLE_LEGACY_UNICODE_CLEAN"):
        if type(settings[name]) is not bool:
            raise ConfigError(f"{name} 必须为布尔值")
    for name in ("EMOJI_MAP", "ICON_MANAGER_CONFIG"):
        if not isinstance(settings[name], dict):
            raise ConfigError(f"{name} 必须为对象")
    icons = settings["ICON_MANAGER_CONFIG"]
    keys(icons, {"show_environment_info", "console_output", "default_log_file", "file_logging", "custom_emoji_map"}, "图标配置")
    for name in ("show_environment_info", "console_output", "file_logging"):
        if type(icons[name]) is not bool:
            raise ConfigError(f"图标配置 {name} 必须为布尔值")
    nonempty(icons["default_log_file"], "日志路径")
    for mapping in (settings["EMOJI_MAP"], icons["custom_emoji_map"]):
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ConfigError("图标映射必须为字符串到字符串的对象")
    validate_url(settings["DEEPSEEK_BASE_URL"])
    nonempty(settings["DEEPSEEK_MODEL"], "兼容模型名")
    nonempty(settings["NEWS_SUMMARY_RESULT_FILENAME"], "归档摘要文件名模板")
    for name in ("NEWS_SUMMARY_FILTER_SOURCEAPI", "FORCE_ICON_THEME"):
        if settings[name] is not None and not isinstance(settings[name], str):
            raise ConfigError(f"{name} 必须为字符串或 null")
    routes = document["keyword_prompt_ids"]
    if not isinstance(routes, dict):
        raise ConfigError("专用提示词映射必须为对象")
    for keyword, name in routes.items():
        nonempty(keyword, "专用映射关键词")
        if not isinstance(name, str) or name not in prompts or prompts[name]["status"] != "active":
            raise ConfigError(f"{keyword} 引用了不存在或归档的提示词")
        if name in USER_FIELDS or "USER_PROMPT" in name:
            raise ConfigError(f"{keyword} 的专用评分映射必须引用 System 提示词，不能引用 User 模板")
    keys(document["models"], STAGES, "模型阶段")
    for stage, model in document["models"].items():
        keys(model, {"platform", "model", "base_url", "api_key_env", "parameters"}, stage)
        nonempty(model["platform"], "模型平台")
        nonempty(model["model"], "模型名称")
        validate_url(model["base_url"])
        validate_api_env(model["api_key_env"])
        params = model["parameters"]
        if not isinstance(params, dict) or set(params) - REQUEST_FIELDS:
            raise ConfigError(f"{stage} 存在不支持的请求参数")
        if params.get("stream", False) is not False:
            raise ConfigError("当前调用方不支持流式响应")
        for name, value in params.items():
            if name == "stream":
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError(f"{stage}.{name} 必须为有限数值")
            if name in ("timeout", "max_tokens", "max_completion_tokens") and value <= 0:
                raise ConfigError(f"{name} 必须为正数")
            if name in ("seed", "max_tokens", "max_completion_tokens") and type(value) is not int:
                raise ConfigError(f"{name} 必须为整数")
            if name == "temperature" and not 0 <= value <= 2:
                raise ConfigError("temperature 必须在 0–2 之间")
            if name == "top_p" and not 0 <= value <= 1:
                raise ConfigError("top_p 必须在 0–1 之间")
    legacy = document["legacy"]
    keys(legacy, {"selection", "models"}, "归档模型配置")
    keys(legacy["selection"], {"platform", "model"}, "归档模型选择")
    for selector in legacy["selection"].values():
        if set(selector) == {"env", "default"}:
            if selector["env"] not in ("NEWS_SUMMARY_PLATFORM", "NEWS_SUMMARY_MODEL"):
                raise ConfigError("不支持的归档模型环境变量")
            nonempty(selector["default"], "归档模型默认值")
        elif set(selector) == {"value"}:
            nonempty(selector["value"], "归档模型选择")
        else:
            raise ConfigError("归档模型选择字段不兼容")
    if not isinstance(legacy["models"], dict) or not legacy["models"]:
        raise ConfigError("缺少归档模型配置")
    for platform, variants in legacy["models"].items():
        nonempty(platform, "归档模型平台名称")
        if not isinstance(variants, dict) or not variants:
            raise ConfigError("归档模型列表无效")
        for name, profile in variants.items():
            nonempty(name, "归档模型条目名称")
            keys(profile, {"api_key_env", "base_url", "model"}, "归档模型")
            validate_api_env(profile["api_key_env"])
            validate_url(profile["base_url"])
            nonempty(profile["model"], "归档模型名")
    encode(document).encode("utf-8")
    return document


def validate(document):
    try:
        return _validate(document)
    except ConfigError:
        raise
    except (TypeError, KeyError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError("配置结构、字段类型或字符编码无效") from exc


def differences(before, after, path=""):
    """Readable leaf differences, including order-only changes."""
    if type(before) is dict and type(after) is dict:
        result = []
        if list(before) != list(after) and set(before) == set(after):
            result.append({"path": path + ".<order>", "before": list(before), "after": list(after)})
        for key in dict.fromkeys([*before, *after]):
            child = f"{path}.{key}" if path else key
            if key not in before or key not in after:
                result.append({"path": child, "before": before.get(key), "after": after.get(key), "added": key not in before, "removed": key not in after})
            else:
                result.extend(differences(before[key], after[key], child))
        return result
    return [] if type(before) is type(after) and before == after else [{"path": path, "before": before, "after": after}]
