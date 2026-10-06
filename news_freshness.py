"""Conservative publication-date checks for daily platform news.

Explicit publication evidence takes precedence. A captured search date may
fill a missing publication date; it never overrides old or conflicting evidence.
"""
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from news_dates import absolute_time, parse_search_date

VERSION = 1
META_NAMES = {'article:published_time', 'og:published_time', 'pubdate',
              'publishdate', 'publish_date', 'publishtime', 'publish_time',
              'publication_date', 'datepublished', 'dc.date.issued', 'dcterms.issued'}


def parse_publication_date(raw):
    text = str(raw or '').strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T.+', text):
            stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if stamp.tzinfo:
                stamp = stamp.astimezone(ZoneInfo('Asia/Shanghai'))
            return stamp.date().isoformat()
        match = re.search(r'(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)', text)
        if match:
            return date(*map(int, match.groups())).isoformat()
    except (ValueError, TypeError):
        pass
    stamp = absolute_time(text)
    return stamp.date().isoformat() if stamp else None


def assess_html(html, target_date):
    date.fromisoformat(target_date)
    soup = BeautifulSoup(html or '', 'html.parser')
    evidence, unparsed = [], []
    def add(source, raw):
        parsed = parse_publication_date(raw)
        if parsed:
            evidence.append({'source':source, 'raw':str(raw)[:200], 'date':parsed})
        elif str(raw or '').strip():
            unparsed.append({'source':source, 'raw':str(raw)[:200]})
    for tag in soup.find_all('meta'):
        name = str(tag.get('property') or tag.get('name') or tag.get('itemprop') or '').lower()
        if name in META_NAMES:
            add('meta:'+name, tag.get('content'))
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            kinds = node.get('@type', [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and any(k in {'NewsArticle','Article','Report','BlogPosting'} for k in kinds):
                add('jsonld:datePublished', node.get('datePublished'))
            for key in ('@graph', 'mainEntity'):
                visit(node.get(key))
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            visit(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError, RecursionError):
            pass
    for tag in soup.select('[itemprop="datePublished"], .publish-time, .publish_time, .pubdate, #pubtime, .newstime'):
        text = tag.get('datetime') or tag.get('content') or tag.get_text(' ', strip=True)
        if len(text) <= 160 and not re.search(r'更新|修改|modified|updated', text, re.I):
            add('publication_element', text)
    # Only explicit, short publication labels outside article paragraphs.
    for tag in soup.find_all(['span', 'time', 'div']):
        text = tag.get_text(' ', strip=True)
        if len(text) <= 100 and re.match(r'^(?:发布时间|发布日期|刊发时间)\s*[:：]', text):
            add('publication_label', text)
    check = assess_publication_evidence(evidence, target_date)
    if unparsed:
        check['unparsed_evidence'] = unparsed
        if check['status'] == 'accepted' or check['reason'] == 'missing_publication_date':
            check.update(status='pending', reason='unparseable_publication_date')
    return check


def assess_publication_evidence(evidence, target_date):
    """Apply the same date gate to explicit HTML or validated service evidence."""
    date.fromisoformat(target_date)
    days = sorted({row['date'] for row in evidence})
    status, reason, published = 'pending', 'missing_publication_date', None
    if len(days) > 1:
        reason = 'conflicting_publication_dates'
    elif days:
        published = days[0]
        if published == target_date:
            status, reason = 'accepted', 'target_date'
        elif published < target_date:
            status, reason = 'old', 'before_target_date'
        else:
            reason = 'after_target_date'
    return {'version':VERSION, 'target_date':target_date, 'status':status,
            'reason':reason, 'published_date':published, 'evidence':evidence}


SEARCH_SOURCES = {'serp_baidunews', 'serp_googlenews', 'serp_bingnews', 'serp_duckduckgo_news'}


def search_date_evidence(row, target_date):
    observed = absolute_time(row.get('search_fetched_at'))
    raw = row.get('search_date_raw')
    if (row.get('sourceapi') not in SEARCH_SOURCES or not observed or not raw
            or row.get('date') != target_date
            or parse_search_date(raw, observed) != target_date):
        return None
    return {'source':'search:'+row['sourceapi'], 'raw':raw, 'date':target_date,
            'observed_at':observed.isoformat(), 'field':row.get('search_date_field', 'date')}


def apply_search_fallback(check, row, target_date):
    """Only absent publication evidence can use a saved search timestamp."""
    if (check.get('status') != 'pending' or check.get('reason') != 'missing_publication_date'
            or check.get('evidence') or check.get('unparsed_evidence')):
        return check
    evidence = search_date_evidence(row, target_date)
    if not evidence:
        return check
    return {**check, 'status':'accepted', 'reason':'search_date_fallback',
            'date_basis':'search_result', 'estimated_date':target_date,
            'published_date':None, 'evidence':[evidence]}


def check_current(row, target_date=None):
    check = row.get('publication_check')
    target = target_date or row.get('fetchdate')
    if isinstance(check, dict) and check.get('reason') == 'search_date_fallback':
        expected = search_date_evidence(row, target)
        if not (expected and check.get('status') == 'accepted'
                and check.get('estimated_date') == target
                and check.get('published_date') is None
                and check.get('evidence') == [expected]):
            return False
    return (isinstance(check, dict) and check.get('version') == VERSION
            and bool(target) and check.get('target_date') == target
            and check.get('link') == row.get('link')
            and check.get('status') in {'accepted','old','pending'})


def eligible(row):
    if not check_current(row):
        return False
    check = row['publication_check']
    evidence = check.get('evidence')
    if check.get('reason') == 'search_date_fallback':
        return True  # check_current already revalidated its saved search evidence.
    return (check['status'] == 'accepted' and check.get('published_date') == row.get('fetchdate')
            and isinstance(evidence, list) and bool(evidence)
            and all(isinstance(item, dict) and item.get('date') == row.get('fetchdate') for item in evidence))


def write_diagnostic(rows, target_date, path):
    from government_affairs_pipeline import atomic_json
    counts = {'accepted':0, 'old':0, 'pending':0}
    records = []
    for row in rows:
        check = row.get('publication_check', {})
        status = check.get('status', 'pending') if check_current(row, target_date) else 'pending'
        counts[status] += 1
        records.append({key:row.get(key) for key in ('title','link','search_keyword','search_date_raw','search_fetched_at','search_date_field','date','fetchdate','publication_check','date_review')})
    atomic_json(path, {'version':VERSION, 'target_date':target_date, 'counts':counts,
                       'search_fallback_count':sum(eligible(row) and row['publication_check'].get('reason') == 'search_date_fallback' for row in rows),
                       'articles':records})
