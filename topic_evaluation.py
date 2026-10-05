"""Frozen evaluation dataset and report contract shared by installer and evaluator."""
from collections import Counter
from datetime import datetime
import hashlib
import math
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit

from config_schema import ConfigError, digest, parse_json
from llm_settings import stage_profile
from topic_config import PROMPT_ID

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "tests/fixtures/government_affairs_eval.jsonl"
IMPLEMENTATION_FILES = ("government_affairs_scoring.py", "news_scorer.py",
    "runtime_config.py", "llm_settings.py", "llm_client_pool.py", "topic_config.py",
    "topic_evaluation.py", "evaluate_government_affairs.py")
FORMAT = "government-affairs-evaluation-v1"

def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def load_samples():
    samples = [parse_json(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = set()
    counts = Counter()
    for sample in samples:
        required = {"id","category","base","title","content","search_keyword",
                    "expected_min","expected_max","reason","source"}
        if not isinstance(sample, dict) or not required <= set(sample):
            raise ConfigError("评测样本缺少字段")
        if not all(isinstance(sample[k], str) and sample[k].strip() for k in
                   ("id","category","title","search_keyword","reason")) or not isinstance(sample["content"], str):
            raise ConfigError("评测样本文本无效")
        if sample["id"] in ids or type(sample["base"]) is not bool:
            raise ConfigError("样本ID重复或base标志无效")
        ids.add(sample["id"])
        low, high = sample["expected_min"], sample["expected_max"]
        if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 5:
            raise ConfigError("样本预期区间无效")
        source = sample["source"]
        if not isinstance(source, dict) or source.get("kind") not in ("synthetic", "public"):
            raise ConfigError("样本须说明合成或公开来源")
        if source["kind"] == "public" and not all(source.get(k) for k in ("url","fetched_at")):
            raise ConfigError("公开来源须冻结来源URL和获取时间")
        if sample["base"]:
            counts[sample["category"]] += 1
            if not sample["content"].strip():
                raise ConfigError("基础样本不得使用空正文凑数")
    if any(counts[k] < n for k,n in (("direct",20),("unrelated",10),("ambiguous",10))):
        raise ConfigError("基础样本须至少20相关、10不相关、10易混淆")
    for sample in samples:
        if sample.get("variant_of") and sample["variant_of"] not in ids:
            raise ConfigError("稳定性变体引用了不存在的样本")
    return samples

def scoring_profile():
    profile = stage_profile("scoring")
    parts = urlsplit(profile["base_url"])
    host = parts.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if parts.port:
        host += ":" + str(parts.port)
    clean = urlunsplit((parts.scheme, host, parts.path, "", ""))
    return {"base_url": clean, "model": profile["model"], "parameters": profile["parameters"]}

def evaluation_manifest(snapshot, candidate):
    profile = scoring_profile()
    process = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True)
    implementation = {name: text_hash((ROOT/name).read_text(encoding="utf-8"))
                      for name in IMPLEMENTATION_FILES}
    return {"format": FORMAT, "source_version": snapshot.token,
            "candidate_sha256": digest(candidate), "scoring_profile": profile,
            "scoring_profile_sha256": digest(profile),
            "system_prompt_sha256": text_hash(candidate["prompts"][PROMPT_ID]["text"]),
            "user_prompt_sha256": text_hash(candidate["prompts"]["NEWS_SCORE_PROMPT"]["text"]),
            "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "implementation_sha256": digest(implementation),
            "code_revision": process.stdout.strip() if process.returncode == 0 else "unavailable",
            "working_tree_dirty": bool(status.stdout.strip()),
            "created_at": datetime.now().astimezone().isoformat()}

def result_passes(sample, result):
    if not isinstance(result, dict) or result.get("id") != sample["id"]:
        return False
    score, attempts = result.get("score"), result.get("attempts")
    duration = result.get("duration_seconds")
    if (type(score) is not int or not sample["expected_min"] <= score <= sample["expected_max"]
            or result.get("error_code") is not None
            or type(duration) not in (float,int) or not math.isfinite(duration) or duration < 0):
        return False
    if not sample["content"].strip():
        return (result.get("status") == "empty_content" and score == 0 and attempts == 0
                and result.get("raw_response") is None)
    raw = result.get("raw_response")
    return (result.get("status") == "ok" and type(attempts) is int and 1 <= attempts <= 3
            and isinstance(raw,str) and raw.strip() == str(score) and 0 <= score <= 5)

def evaluate_samples(samples, manifest, score_fn):
    results = []
    for sample in samples:
        start = time.monotonic()
        result = score_fn(sample)
        row = {"id": sample["id"], "score": result.score, "status": result.status,
               "error_code": result.error_code, "attempts": result.attempts,
               "raw_response": result.raw_response, "duration_seconds": time.monotonic()-start,
               "token_usage": getattr(result, "token_usage", None)}
        row["passed"] = result_passes(sample, row)
        results.append(row)
    return {**manifest, "results": results, "passed": all(r["passed"] for r in results),
            "summary": {"samples": len(results),
                        "failed": sum(r["status"] == "failed" for r in results),
                        "out_of_range_or_invalid": sum(not r["passed"] for r in results),
                        "valid_zero": sum(r["status"] == "ok" and r["score"] == 0 for r in results)},
            "human_review": {"approved": False, "reviewer": "", "reviewed_at": "", "reviewed_ids": []}}

def validate_report(path, snapshot, candidate):
    try:
        report = parse_json(Path(path).read_text(encoding="utf-8"))
        expected = evaluation_manifest(snapshot, candidate)
        for key in ("format","source_version","candidate_sha256","scoring_profile_sha256",
                    "system_prompt_sha256","user_prompt_sha256","dataset_sha256","implementation_sha256"):
            if report.get(key) != expected[key]:
                raise ConfigError(f"评测报告不匹配：{key}；请重新评测")
        if report.get("scoring_profile") != expected["scoring_profile"]:
            raise ConfigError("有效评分模型或参数已变化")
        samples = load_samples()
        results = report["results"]
        if not isinstance(results, list) or len(results) != len(samples):
            raise ConfigError("评测结果数量不完整")
        if [r.get("id") for r in results] != [s["id"] for s in samples]:
            raise ConfigError("评测样本顺序/ID不完整或重复")
        if report.get("passed") is not True or not all(result_passes(s,r) for s,r in zip(samples,results)):
            raise ConfigError("评测有异常、非法输出或分数越界，不能启用")
        review = report["human_review"]
        if (review.get("approved") is not True or not str(review.get("reviewer") or "").strip()
                or set(review.get("reviewed_ids", [])) != {s["id"] for s in samples}):
            raise ConfigError("尚未完成逐条业务复核")
        reviewed_at = datetime.fromisoformat(review["reviewed_at"])
        if reviewed_at.tzinfo is None:
            raise ConfigError("业务复核时间须包含时区")
    except ConfigError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ConfigError("评测报告格式或业务复核信息无效") from exc
