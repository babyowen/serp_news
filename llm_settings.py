"""LLM 接入配置：三个 AI 阶段的模型与参数全部来自环境变量（.env）。

换模型 = 修改 .env 的 LLM_* 变量 → config_cli.py check-model 验证连通 → 重启常驻服务。
本模块只依赖 config_schema，不得 import runtime_config（会形成循环导入）。
"""
import os

from config_schema import REQUEST_FIELDS, ConfigError, validate_request_params, validate_url

STAGES = ("scoring", "item_summarizer", "region")
_STAGE_PREFIX = {"scoring": "SCORING", "item_summarizer": "ITEM_SUMMARIZER", "region": "REGION"}
# 从 REQUEST_FIELDS 派生，保持单一事实来源；stream 固定为 False（调用方不支持流式），不开放为变量。
_TUNABLES = tuple(sorted(REQUEST_FIELDS - {"stream"}))
# 全局变量只支持接入三要素；请求参数必须按阶段设置（LLM_<阶段>_<参数>），
# 全局 LLM_TEMPERATURE 之类会被拒绝而不是静默失效。
_GLOBAL_SUFFIXES = {"MODEL", "BASE_URL", "API_KEY"}
_KNOWN_SUFFIXES = _GLOBAL_SUFFIXES | {name.upper() for name in _TUNABLES}
DEFAULT_TIMEOUT = 60


def _env_value(name):
    """读取环境变量并 strip；纯空白等同于未设置（返回 None）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _parse_number(raw, label):
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"{label} 必须为数值") from None


def stage_profile(stage):
    """返回阶段的 {api_key, base_url, model, parameters}；每次调用实时读取环境变量。"""
    prefix = _STAGE_PREFIX.get(stage)
    if prefix is None:
        raise ConfigError(f"未知模型阶段: {stage}")
    full_prefix = f"LLM_{prefix}_"
    # LLM_* 命名空间归本应用所有：全局与阶段级拼错的变量都必须报错，不能被静默忽略。
    stage_prefixes = tuple(f"LLM_{p}_" for p in _STAGE_PREFIX.values())

    def _unknown(name):
        for sp in stage_prefixes:
            if name.startswith(sp):
                # 阶段变量：剥掉阶段前缀后必须是已知后缀（LLM_SCORING_API_KEY → API_KEY）。
                return name[len(sp):] not in _KNOWN_SUFFIXES
        # 全局变量：只有接入三要素；LLM_TEMPERATURE 之类参数必须按阶段设置。
        return name[len("LLM_"):] not in _GLOBAL_SUFFIXES

    unknown = sorted(name for name in os.environ if name.startswith("LLM_") and _unknown(name))
    if unknown:
        raise ConfigError(f"存在未知的 LLM_* 环境变量: {', '.join(unknown)}；本配置不支持 STREAM")
    base_url = _env_value(full_prefix + "BASE_URL") or _env_value("LLM_BASE_URL")
    if not base_url:
        raise ConfigError("请设置 LLM_BASE_URL（OpenAI 兼容接入点，例如 http://api.agent-router.cn/v1）")
    model = _env_value(full_prefix + "MODEL") or _env_value("LLM_MODEL")
    if not model:
        raise ConfigError(f"请设置 {full_prefix}MODEL（或全局 LLM_MODEL）")
    # 固定注入 stream=False 与默认超时，保持与外部配置迁移前的请求行为一致。
    parameters = {"stream": False, "timeout": DEFAULT_TIMEOUT}
    # 与 config_schema.validate 相同的保证：任何解析异常都收敛为 ConfigError，
    # 逐阶段失败隔离，不向调用方泄漏 urlsplit/数值转换的原始异常。
    try:
        for name in _TUNABLES:
            raw = _env_value(full_prefix + name.upper())
            if raw is None:
                continue
            parameters[name] = _parse_number(raw, full_prefix + name.upper())
        validate_url(base_url)
        validate_request_params(parameters, f"LLM_{prefix}")
    except ConfigError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError(f"LLM_{prefix} 配置无效: {type(exc).__name__}") from None
    # 空白密钥等同于未设置：回退全局；全局也为空则 api_key=None（/admin/models 显示未配置）。
    api_key = _env_value(full_prefix + "API_KEY") or _env_value("LLM_API_KEY")
    return {"api_key": api_key, "base_url": base_url, "model": model, "parameters": parameters}


def all_stage_settings():
    return {stage: stage_profile(stage) for stage in STAGES}


def _probe(profile):
    from openai import OpenAI
    if not profile["api_key"]:
        return {"ok": False, "error": "缺少 LLM API Key（LLM_API_KEY 或按阶段 LLM_<阶段>_API_KEY）"}
    try:
        with OpenAI(api_key=profile["api_key"], base_url=profile["base_url"],
                    timeout=60, max_retries=0) as client:
            response = client.chat.completions.create(
                model=profile["model"], **profile["parameters"],
                messages=[{"role": "user", "content": "Reply with OK."}])
            if not response.choices or not response.choices[0].message.content:
                raise ValueError("empty response")
    except Exception as exc:
        # Provider 异常消息可能包含密钥或请求细节，不能透传。
        status = getattr(exc, "status_code", None)
        return {"ok": False, "error": f"模型检查失败（{type(exc).__name__}, HTTP {status}）"}
    return {"ok": True, "model": profile["model"], "base_url": profile["base_url"],
            "parameters": profile["parameters"]}


def check_stages(stages=None):
    """逐阶段发送一次固定小请求验证连通；返回 {stage: result}，不抛异常。"""
    return {stage: _check_stage(stage) for stage in (stages or STAGES)}


def _check_stage(stage):
    try:
        profile = stage_profile(stage)
    except ConfigError as exc:
        return {"ok": False, "error": str(exc)}
    return _probe(profile)
