"""Conservative local rejection of known non-article text; no length-only gate."""
import re

VERSION = 1


def body_rejection_reason(text, title=""):
    if not isinstance(text, str) or not text.strip():
        return None  # Empty content retains its existing, distinct status.
    compact = re.sub(r"\s+", " ", text).strip()
    if re.fullmatch(r"Was this page helpful\? Yes No Thank you for your feedback!", compact, re.I):
        return "feedback_only"
    if re.match(r"^(?:Operations too frequent\.|Page not found[, .]|Access denied[.\s]|ERROR: ACCESS DENIED|Verify you are human|您访问的页面不存在|页面不存在)", compact, re.I):
        return "error_page"
    # Require an explicit reader-access barrier, not a passing mention of subscriptions.
    if (re.search(r"(?:この記事は(?:有料|会員限定)|本文は会員限定)", compact)
            and re.search(r"(?:残り\s*\d+\s*文字|全文\s*\d+\s*文字)", compact)):
        return "paywall_excerpt"
    if re.search(r"(?:剩余|剩餘)\s*\d+\s*字", compact) and re.search(r"(?:订阅|訂閱|会员|會員).{0,12}(?:阅读全文|閱讀全文|解锁|解鎖)", compact):
        return "paywall_excerpt"
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    navigation = {"首页", "新闻", "财经", "体育", "娱乐", "汽车", "科技", "登录", "注册", "关于我们", "联系我们", "返回首页"}
    if len(lines) >= 3 and all(x in navigation for x in lines):
        return "navigation_only"
    if len(compact) > 50 and re.sub(r"\s+", "", compact) == re.sub(r"\s+", "", title or ""):
        return "headline_only"
    return None


def quality_status(reason):
    return "incomplete" if reason == "paywall_excerpt" else "invalid"


def annotate_content_quality(row):
    reason = body_rejection_reason(row.get("content"), row.get("title"))
    if reason:
        row["content_quality"] = {"version": VERSION, "status": quality_status(reason), "reason": reason}
    else:
        row.pop("content_quality", None)


def content_skipped(row):
    reason = body_rejection_reason(row.get("content"), row.get("title"))
    return bool(reason and row.get("score") is None
                and row.get("score_status") == quality_status(reason) + "_content"
                and row.get("score_error") == reason)
