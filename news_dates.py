"""Search timestamps parsed against a captured clock, always in Beijing time."""
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo
from dateutil import parser
from dateutil.relativedelta import relativedelta

BEIJING = ZoneInfo("Asia/Shanghai")
RELATIVE = re.compile(
    r"^(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w|months?|years?|y|秒钟?|分鐘|分钟|小時|小时|天|周|週|个月|個月|月|年)\s*(?:ago|前)?$", re.I)


def reference_time(value=None):
    value = value or datetime.now(BEIJING)
    return value.replace(tzinfo=BEIJING) if value.tzinfo is None else value.astimezone(BEIJING)


def absolute_time(value):
    text = str(value or '').strip()
    # Require all three calendar components before invoking dateutil. In
    # particular, '5 years ago' must never become the fifth of this month.
    if not (re.search(r"\b\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text)
            or re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
            or re.search(r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b", text)):
        return None
    text = text.replace('年','-').replace('月','-').replace('日','')
    # Google may append a zone name after a numeric offset. The offset is
    # authoritative; remove only that redundant suffix, not arbitrary words.
    text = re.sub(r'([+-]\d{4})\s+[A-Z]{2,5}$', r'\1', text)
    try:
        return reference_time(parser.parse(text, fuzzy=False))
    except (ValueError, TypeError, OverflowError):
        return None


def is_relative(value):
    text = str(value or '').strip()
    return bool(RELATIVE.fullmatch(text) or re.fullmatch(
        r'(昨天|昨日|前天|今天|今日|yesterday|today)(?:\s+\d{1,2}:\d{2})?', text, re.I))


def parse_search_date(value, reference_now=None):
    text = str(value or '').strip()
    now = reference_time(reference_now)
    named = re.fullmatch(r'(昨天|昨日|前天|今天|今日|yesterday|today)(?:\s+\d{1,2}:\d{2})?', text, re.I)
    if named:
        days = {'昨天':1,'昨日':1,'前天':2,'yesterday':1}.get(named.group(1).lower(),0)
        return (now-timedelta(days=days)).date().isoformat()
    match = RELATIVE.fullmatch(text)
    if match:
        amount, unit = int(match[1]), match[2].lower()
        units = {
            'seconds': {'second','seconds','sec','secs','s','秒','秒钟'},
            'minutes': {'minute','minutes','min','mins','m','分钟','分鐘'},
            'hours': {'hour','hours','hr','hrs','h','小时','小時'},
            'days': {'day','days','d','天'},
            'weeks': {'week','weeks','w','周','週'},
            'months': {'month','months','个月','個月','月'},
            'years': {'year','years','y','年'},
        }
        key = next(k for k,v in units.items() if unit in v)
        try:
            delta = relativedelta(**{key:amount}) if key in {'years','months'} else timedelta(**{key:amount})
            return (now-delta).date().isoformat()
        except (ValueError, OverflowError):
            return None
    stamp = absolute_time(text)
    return stamp.date().isoformat() if stamp else None
