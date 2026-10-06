"""Shared strict scoring. The historical module name remains for compatibility."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from config_schema import ConfigError
from runtime_config import value, model_arguments, model_credentials
from topic_config import TOPIC
from news_freshness import eligible
from scoring_policy import messages, cap_score

@dataclass(frozen=True)
class ScoreResult:
    score: int | None
    status: str
    error_code: str | None = None
    attempts: int = 0
    raw_response: str | None = None
    token_usage: dict | None = None

def parse_government_affairs_score(raw):
    if not isinstance(raw, str) or raw.strip() not in {"0", "1", "2", "3", "4", "5"}:
        raise ValueError("invalid_score_output")
    return int(raw.strip())

def error_code(exc):
    text = (type(exc).__name__ + " " + str(exc)).lower()
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "authentication"
    if status == 402 or any(s in text for s in ("insufficient_quota", "insufficient balance", "余额不足")):
        return "quota"
    if any(s in text for s in ("context_length", "context length", "token limit", "maximum context")):
        return "context_limit"
    if isinstance(exc, (ValueError, IndexError, AttributeError, TypeError)):
        return "invalid_output"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if status == 429:
        return "rate_limit"
    if any(s in text for s in ("connection", "network", "ssl", "socket")):
        return "network"
    return "provider_error"

def record_result(result):
    # Never copy requests, raw provider errors, credentials or responses to normal logs.
    path = Path(os.environ.get("RUN_LOG_PATH", "output/run_log.txt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[GOV_SCORE] status={result.status} score={result.score} "
                     f"error={result.error_code} attempts={result.attempts}\n")
    return result

def score_result(title, content, keyword, main_keyword, pool, max_retries=3, retry_interval=5, context=None):
    if type(max_retries) is not int or not 1 <= max_retries <= 3:
        raise ValueError("max_retries must be between 1 and 3")
    prompts = value("KEYWORD_SPECIFIC_SYSTEM_PROMPTS")
    system = prompts.get(main_keyword or keyword) or value("NEWS_SCORE_SYSTEM_MSG")
    if content is None or not str(content).strip():
        return record_result(ScoreResult(0, "empty_content"))
    request_messages = messages(system, value("NEWS_SCORE_PROMPT"), title, content, keyword, context)
    params, credentials = model_arguments("scoring"), model_credentials("scoring")
    for attempt in range(1, max_retries + 1):
        raw = None
        try:
            client = pool.get_client(*credentials, max_uses=100).with_options(max_retries=0)
            response = client.chat.completions.create(**params, messages=request_messages)
            raw = response.choices[0].message.content
            score = cap_score(parse_government_affairs_score(raw), context)
            usage = getattr(response, "usage", None)
            tokens = {k: getattr(usage, k, None) for k in ("prompt_tokens", "completion_tokens", "total_tokens")} if usage else None
            result = ScoreResult(score, "ok", attempts=attempt, raw_response=raw, token_usage=tokens)
        except Exception as exc:
            code = error_code(exc)
            if code in {"authentication", "quota", "context_limit"} or attempt == max_retries:
                return record_result(ScoreResult(None, "failed", code, attempt, raw))
            time.sleep(retry_interval)
            continue
        # Log I/O failures must not repeat a successful paid request.
        return record_result(result)
    raise AssertionError("unreachable")

def score_fields(result):
    return {"score": result.score, "score_status": result.status,
            "score_error": result.error_code, "score_attempts": result.attempts}

def scored_file_complete(path, keyword):
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return isinstance(rows, list) and all(
            isinstance(row, dict) and eligible(row) and type(row.get("score")) is int and 0 <= row["score"] <= 5
            and (row.get("score_status") == "ok" or
                 (row.get("score_status") == "empty_content" and row["score"] == 0))
            for row in rows)
    except (OSError, ValueError):
        return False
