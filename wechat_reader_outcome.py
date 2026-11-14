"""Content-free, frozen reader statistics for acknowledged new WeChat rows."""
from collections import Counter
from copy import deepcopy

from wechat_source_context import (
    AUTONOMOUS_REGIONS, CITY_PROVINCES, MUNICIPALITIES, PROVINCES, verified_source_context,
)

SUMMARY_KEYS = (
    'ready_generated', 'ready_direct', 'ready_existing', 'ready_unknown',
    'low_score', 'body_unusable', 'score_unconfirmed', 'failed', 'pending', 'unknown',
)
REGION_LABELS = {
    **{name + '省': name for name in PROVINCES},
    **{name + '市': name for name in MUNICIPALITIES},
    **{full: name for name, full in AUTONOMOUS_REGIONS.items()},
    **{province + city + '市': province.removesuffix('省') + '·' + city
       for city, province in CITY_PROVINCES.items()},
    '中央机关': '中央机关',
}


def _count(value):
    return type(value) is int and value >= 0


def valid_reader_overview(value, inserted):
    """Reject unknown fields and arbitrary text before freezing or replaying."""
    if not isinstance(value, dict) or set(value) != {
        'version', 'inserted', 'summary', 'regions', 'region_unknown',
    }:
        return False
    if (type(value['version']) is not int or value['version'] != 1
            or not _count(inserted) or value['inserted'] != inserted
            or not _count(value['inserted']) or not _count(value['region_unknown'])):
        return False
    summary, regions = value['summary'], value['regions']
    if (not isinstance(summary, dict) or set(summary) != set(SUMMARY_KEYS)
            or not all(_count(n) for n in summary.values()) or sum(summary.values()) != inserted
            or not isinstance(regions, list)):
        return False
    labels = set()
    total = value['region_unknown']
    for region in regions:
        if (not isinstance(region, dict) or set(region) != {'label', 'count'}
                or not isinstance(region['label'], str) or region['label'] not in REGION_LABELS.values()
                or region['label'] in labels or not _count(region['count']) or region['count'] == 0):
            return False
        labels.add(region['label'])
        total += region['count']
    return total == inserted


def freeze_reader_overview(value, inserted):
    return deepcopy(value) if valid_reader_overview(value, inserted) else None


def _candidate_key(row):
    if not isinstance(row, dict):
        return None
    values = tuple(row.get(k) for k in ('title', 'link', 'article_id'))
    return values if all(isinstance(v, str) and v for v in values) else None


def _summary_bucket(row):
    status, method, reason = row.get('status'), row.get('summary_method'), row.get('reason')
    if not isinstance(status, str):
        return 'unknown'
    if status == 'succeeded':
        return ({'generated': 'ready_generated', 'direct': 'ready_direct'}.get(method, 'ready_unknown')
                if isinstance(method, str) else 'ready_unknown')
    if status == 'already_done':
        return 'ready_existing'
    score = row.get('score')
    if reason == 'low_score' and type(score) in (int, float) and 0 <= score < 3:
        return 'low_score'
    if reason == 'body_unusable':
        return 'body_unusable'
    if reason == 'score_missing' and score is None:
        return 'score_unconfirmed'
    return {'failed': 'failed', 'pending': 'pending'}.get(status, 'unknown')


def build_reader_overview(collection, reports, day):
    """Join committed inserts to actual row evidence; never requery the database.

    Legacy not_needed does not establish a score, and duplicate candidates do
    not confer their publisher or summary state on the new-row denominator.
    """
    if not isinstance(reports, dict):
        return None
    database = reports.get('database')
    if not isinstance(database, dict) or not isinstance(database.get('counts'), dict):
        return None
    inserted = database['counts'].get('inserted')
    articles = database.get('articles')
    if not _count(inserted) or not isinstance(articles, list):
        return None
    new_rows = [r for r in articles if isinstance(r, dict) and r.get('status') == 'inserted']
    if len(new_rows) != inserted:
        return None
    retained = [(r.get('stored_link'), r.get('title')) for r in new_rows]
    if any(not all(isinstance(v, str) and v for v in key) for key in retained) or len(set(retained)) != inserted:
        return None
    summary_report = reports.get('summary')
    summary_rows = summary_report.get('articles') if isinstance(summary_report, dict) else None
    if not isinstance(summary_rows, list):
        summary_rows = []
    by_candidate = {}
    row_ids = Counter(r.get('row_id') for r in summary_rows
                      if isinstance(r, dict) and type(r.get('row_id')) is int)
    for row in summary_rows:
        if not isinstance(row, dict) or type(row.get('row_id')) is not int or row['row_id'] <= 0:
            continue
        if row_ids[row['row_id']] != 1 or not isinstance(row.get('candidates'), list):
            continue
        for candidate in row['candidates']:
            key = _candidate_key(candidate)
            if key:
                by_candidate.setdefault(key, []).append(row)
    collected = collection.get('articles', []) if isinstance(collection, dict) and collection.get('date') == day else []
    if not isinstance(collected, list):
        collected = []
    source_rows = {}
    for row in collected:
        if isinstance(row, dict) and isinstance(row.get('link'), str):
            source_rows.setdefault(row['link'], []).append(row)
    summary = dict.fromkeys(SUMMARY_KEYS, 0)
    regions = Counter()
    region_unknown = 0
    used_rows = set()
    for new in new_rows:
        matches = by_candidate.get(_candidate_key(new), [])
        row = matches[0] if len(matches) == 1 else None
        if row and (row.get('stored_link', new['stored_link']) != new['stored_link']
                    or row.get('stored_date', day) != day or row['row_id'] in used_rows):
            row = None
        if row:
            used_rows.add(row['row_id'])
        summary[_summary_bucket(row) if row else 'unknown'] += 1
        sources = source_rows.get(new['stored_link'], [])
        source = None
        if len(sources) == 1 and new.get('link') == new['stored_link']:
            try:
                source = verified_source_context(sources[0])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        label = REGION_LABELS.get(source['账号辖区']) if source else None
        if label:
            regions[label] += 1
        else:
            region_unknown += 1
    result = {'version': 1, 'inserted': inserted, 'summary': summary,
              'regions': [{'label': label, 'count': n} for label, n in sorted(regions.items())],
              'region_unknown': region_unknown}
    return freeze_reader_overview(result, inserted)
