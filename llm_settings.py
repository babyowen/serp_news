"""LLM 接入配置：三个 AI 阶段的模型与参数全部来自环境变量（.env）。

换模型 = 修改 .env 的 LLM_* 变量 → config_cli.py check-model 验证连通 → 重启常驻服务。
本模块只依赖 config_schema，不得 import runtime_config（会形成循环导入）。
"""
import os

from config_schema import ConfigError, validate_request_params, validate_url

STAGES = ("scoring", "item_summarizer", "region")
_STAGE_PREFIX = {"scoring": "SCORING", "item_summarizer": "ITEM_SUMMARIZER", "region": "REGION"}
# 与 config_schema.REQUEST_FIELDS 对应；stream 固定为 False（调用方不支持流式），不开放为变量。
_TUNABLES = ("temperature", "top_p", "max_tokens", "max_completion_tokens",
             "presence_penalty", "frequency_penalty", "seed", "timeout")
_KNOWN_SUFFIXES = {"MODEL", "BASE_URL", "API_KEY"} | {name.upper() for name in _TUNABLES}
DEFAULT_TIMEOUT = 60


def _parse_number(raw, label):
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"{label} 必须为数值") from None
    except OverflowError:
        raise ConfigError(f"{label} 数值超出可表示范围") from None


def stage_profile(stage):
    """返回阶段的 {api_key, base_url, model, parameters}；每次调用实时读取环境变量。"""
    prefix = _STAGE_PREFIX.get(stage)
    if prefix is None:
        raise ConfigError(f"未知模型阶段: {stage}")
    full_prefix = f"LLM_{prefix}_"
    # 该命名空间归本应用所有：拼错的变量必须报错，不能被静默忽略。
    unknown = sorted(name for name in os.environ
                     if name.startswith(full_prefix) and name[len(full_prefix):] not in _KNOWN_SUFFIXES)
    if unknown:
        raise ConfigError(f"存在未知的 {full_prefix}* 环境变量: {', '.join(unknown)}；本配置不支持 STREAM")
    base_url = (os.getenv(full_prefix + "BASE_URL") or os.getenv("LLM_BASE_URL") or "").strip()
    if not base_url:
        raise ConfigError("请设置 LLM_BASE_URL（OpenAI 兼容接入点，例如 http://api.agent-router.cn/v1）")
    model = (os.getenv(full_prefix + "MODEL") or os.getenv("LLM_MODEL") or "").strip()
    if not model:
        raise ConfigError(f"请设置 {full_prefix}MODEL（或全局 LLM_MODEL）")
    # 固定注入 stream=False 与默认超时，保持与外部配置迁移前的请求行为一致。
    parameters = {"stream": False, "timeout": DEFAULT_TIMEOUT}
    for name in _TUNABLES:
        raw = os.getenv(full_prefix + name.upper())
        if raw is None or not raw.strip():
            continue
        label = full_prefix + name.upper()
        parameters[name] = _parse_number(raw.strip(), label)
    # 与 config_schema.validate 相同的保证：任何解析异常都收敛为 ConfigError，
    # 逐阶段失败隔离，不向调用方泄漏 urlsplit/数值转换的原始异常。
    try:
        validate_url(base_url)
        validate_request_params(parameters, f"LLM_{prefix}")
    except ConfigError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError(f"LLM_{prefix} 配置无效: {type(exc).__name__}") from None
    # 空白密钥等同于未配置，避免 /admin/models 显示虚假的"已配置"徽标。
    api_key = os.getenv(full_prefix + "API_KEY") or os.getenv("LLM_API_KEY")
    if api_key is not None and not api_key.strip():
        api_key = None
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
