"""Trusted publisher geography for the government-affairs WeChat topic only."""
import hashlib
import json

from topic_config import TOPIC
from wechat_collector import _article_identity, load_registry


VERSION = "government-wechat-source-v1"
RULE = """官微来源地域使用规则：
已核验来源上下文也属于提供的资料，只说明发布账号、账号辖区和层级。正文明确讲述发布机构本级事务时，可据此补足本省、全省、本市、本局、市局等指代的省市名称；须结合正文确认指代对象。正文明确地点优先，来源辖区不等于事件发生地。
转载、跨地区交流或引用外地案例按正文明确地点表述，不得套用发布账号辖区；正文指代含糊或证据不足时不得猜测地域，不得因专题名含“江苏”而把外地新闻认作江苏新闻。账号层级也不等于正文所述事件或政策的层级。"""

CITY_PROVINCES = {
    "沈阳": "辽宁省", "大连": "辽宁省", "长春": "吉林省", "哈尔滨": "黑龙江省",
    "南京": "江苏省", "杭州": "浙江省", "厦门": "福建省", "济南": "山东省",
    "青岛": "山东省", "武汉": "湖北省", "深圳": "广东省", "成都": "四川省",
}
AUTONOMOUS_REGIONS = {
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
}
PROVINCES = {"河北", "山西", "辽宁", "江苏", "浙江", "安徽", "江西", "山东", "河南",
             "湖北", "湖南", "广东", "四川", "贵州", "云南", "陕西", "青海"}
MUNICIPALITIES = {"天津", "上海", "重庆"}


def verified_source_context(row, main_keyword=None):
    """Resolve stable IDs, never a nickname; stored DB rows need only their biz URL."""
    row = row or {}
    topic = main_keyword if main_keyword is not None else row.get("keyword") or row.get("main_keyword")
    if (topic != TOPIC or row.get("sourceapi") != "极致了api"
            or any(row.get(key) not in (None, "", TOPIC) for key in ("keyword", "main_keyword"))):
        return None
    identity = _article_identity(row.get("link"))
    if not identity:
        return None
    accounts = load_registry()["accounts"]
    account = next((a for a in accounts if a["enabled"] and a["biz"] == identity[0]), None)
    if (not account or "wechat_ghid" in row and row["wechat_ghid"] != account["ghid"]
            or "wechat_article_id" in row and row["wechat_article_id"] != "/".join(identity)):
        return None
    region, level = account.get("region"), account.get("level")
    if level == "national" and region == "国管局":
        jurisdiction, label = "中央机关", "国家级"
    elif level == "city" and region in CITY_PROVINCES:
        jurisdiction, label = CITY_PROVINCES[region] + region + "市", "市级"
    elif level == "province" and region in MUNICIPALITIES:
        jurisdiction, label = region + "市", "省级（直辖市）"
    elif level == "province" and region in AUTONOMOUS_REGIONS:
        jurisdiction, label = AUTONOMOUS_REGIONS[region], "省级（自治区）"
    elif level == "province" and region in PROVINCES:
        jurisdiction, label = region + "省", "省级"
    else:
        return None
    return {"发布账号": account["name"], "账号辖区": jurisdiction, "账号层级": label}


def add_source_context(system, user, row=None, main_keyword=None):
    source = verified_source_context(row, main_keyword)
    if not source:
        return system, user
    return (system + "\n\n" + RULE,
            "官微来源上下文：" + json.dumps(source, ensure_ascii=False) + "\n\n" + user)


def score_context_fields(row, main_keyword=None):
    """Only contextual scores get a cache marker; ordinary score caches stay intact."""
    source = verified_source_context(row, main_keyword)
    if not source:
        return {}
    payload = json.dumps([VERSION, RULE, source], ensure_ascii=False, sort_keys=True)
    return {"score_source_context": hashlib.sha256(payload.encode("utf-8")).hexdigest()}


def score_context_matches(cached, row=None, main_keyword=None):
    current = score_context_fields(row if row is not None else cached, main_keyword)
    return cached.get("score_source_context") == current.get("score_source_context")
