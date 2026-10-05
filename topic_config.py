"""Explicit, additive topic installation. Never called during application startup."""
import copy
import json
from pathlib import Path

from config_schema import ConfigConflict, ConfigError, differences, digest, validate
from config_store import ConfigStore

TOPIC = "江苏机关事务"
OLD_TOPIC = "机关事务管理局"
PROMPT_ID = "NEWS_SCORE_SYSTEM_MSG_GOVERNMENT_AFFAIRS"
PACKAGE = Path(__file__).parent / "topic_packages" / "government_affairs.json"


def build_topic_candidate(document, topic="government-affairs"):
    if topic != "government-affairs":
        raise ConfigError("未知专题")
    validate(document)
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    searches = document["settings"]["SEARCH_KEYWORDS"]
    routes = document["keyword_prompt_ids"]
    rules = document["settings"]["NEWS_RULE_BASED_SCORING"]
    if OLD_TOPIC in searches or OLD_TOPIC in routes:
        raise ConfigConflict("旧名称机关事务管理局已存在，请核对历史配置及数据，不自动迁移")
    conflicting = [r for r in rules if r["main_keyword"] in (TOPIC, OLD_TOPIC)]
    if conflicting:
        raise ConfigConflict("本专题存在标题直接赋分规则，请人工处理：" +
                             json.dumps(conflicting, ensure_ascii=False))
    present = [TOPIC in searches, PROMPT_ID in document["prompts"], TOPIC in routes]
    expected_prompt = {"text": package["system_prompt"], "status": "active"}
    if all(present):
        if (searches[TOPIC] != package["search_keywords"] or
                document["prompts"][PROMPT_ID] != expected_prompt or routes[TOPIC] != PROMPT_ID):
            raise ConfigConflict("专题已有生产自定义内容，与初始安装包不同；保留原配置，不自动覆盖")
        status = "noop"
    elif any(present):
        raise ConfigConflict("专题配置部分存在或提示词ID被占用；保留原配置，请人工比较")
    else:
        status = "add"
    candidate = copy.deepcopy(document)
    if status == "add":
        candidate["settings"]["SEARCH_KEYWORDS"][TOPIC] = package["search_keywords"]
        candidate["prompts"][PROMPT_ID] = expected_prompt
        candidate["keyword_prompt_ids"][TOPIC] = PROMPT_ID
    validate(candidate)
    return {"document": candidate, "status": status,
            "changes": differences(document, candidate), "candidate_sha256": digest(candidate)}


def validate_evaluation_report(path, snapshot, candidate):
    # Shared implementation with the explicit evaluator; never contacts the model.
    from topic_evaluation import validate_report
    validate_report(path, snapshot, candidate)


def enable_topic(args):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    store = ConfigStore(args.store)
    current = store.read()
    if args.apply:
        for field in ("expected_version", "expected_candidate_sha256", "backup", "note"):
            if not getattr(args, field, None) or not str(getattr(args, field)).strip():
                raise ConfigError("--apply 缺少参数 --" + field.replace("_", "-"))
    if args.expected_version and args.expected_version != current.token:
        raise ConfigConflict("配置版本已变化，请重新预览")
    proposal = build_topic_candidate(current.document, args.topic)
    if args.expected_candidate_sha256 and args.expected_candidate_sha256 != proposal["candidate_sha256"]:
        raise ConfigConflict("候选配置哈希已变化，请重新评测")
    preview = {k: v for k, v in proposal.items() if k != "document"}
    preview.update(store=str(store.path), current_version=current.token, prompt_id=PROMPT_ID,
                   existing_topics=len(current.document["settings"]["SEARCH_KEYWORDS"]),
                   existing_search_keywords=sum(len(v) for v in current.document["settings"]["SEARCH_KEYWORDS"].values()),
                   added_topics=int(proposal["status"] == "add"),
                   added_search_keywords=11 if proposal["status"] == "add" else 0)
    if not args.apply or proposal["status"] == "noop":
        return preview
    if not args.evaluation_report:
        raise ConfigError("新增启用必须提供 --evaluation-report，且先完成业务复核")
    validate_evaluation_report(args.evaluation_report, current, proposal["document"])
    store.backup(args.backup)
    enabled = store.save(proposal["document"], current.token, args.note,
                         {"kind": "enable-topic", "topic": args.topic,
                          "source_version": current.token})
    return {**preview, "status": "enabled", "version": enabled.token, "backup": args.backup}
