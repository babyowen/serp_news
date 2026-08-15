"""Shared business-type validation, normalization and persistence helpers."""

import json
from collections import defaultdict


ALLOWED_BUSINESS_TYPE_TABLES = {"scored_news", "scored_news_test"}
BUSINESS_TYPE_LEVEL1 = ("缴存", "提取", "贷款", "其它")
ALIASES_TABLE = "news_business_type_aliases"


def validate_business_type_table_name(table_name):
    if table_name not in ALLOWED_BUSINESS_TYPE_TABLES:
        raise ValueError(f"Unsupported table name: {table_name}")
    return table_name


def table_has_business_types_column(cursor, table_name):
    validate_business_type_table_name(table_name)
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = 'business_types'
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def ensure_business_type_schema(cursor, table_name):
    """Create the explicit Issue #10 schema. Intended for the setup script only."""
    validate_business_type_table_name(table_name)
    if not table_has_business_types_column(cursor, table_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN business_types JSON NULL")

    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ALIASES_TABLE} (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            news_table VARCHAR(64) NOT NULL,
            level1 VARCHAR(32) NOT NULL,
            retired_level2 VARCHAR(128) NOT NULL,
            canonical_level2 VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_business_type_alias (news_table, level1, retired_level2),
            KEY idx_business_type_canonical (news_table, level1, canonical_level2)
        ) CHARACTER SET utf8mb4
        """
    )


def _clean_label(value):
    if value is None:
        return None
    label = str(value).strip()
    return label[:128] if label else None


def load_business_type_aliases(cursor, table_name):
    validate_business_type_table_name(table_name)
    cursor.execute(
        f"SELECT level1, retired_level2, canonical_level2 FROM {ALIASES_TABLE} WHERE news_table=%s",
        (table_name,),
    )
    return {(level1, retired): canonical for level1, retired, canonical in cursor.fetchall()}


def get_business_type_label_stats(cursor, table_name, aliases=None):
    """Return active labels and their number of uses, grouped by primary type."""
    validate_business_type_table_name(table_name)
    aliases = aliases or {}
    cursor.execute(
        f"SELECT business_types FROM {table_name} WHERE keyword=%s AND business_types IS NOT NULL",
        ("公积金",),
    )
    counts = defaultdict(lambda: defaultdict(int))
    for (raw_types,) in cursor.fetchall():
        for item in decode_business_types(raw_types, aliases):
            counts[item["level1"]][item["level2"]] += 1
    return {
        level1: [
            {"label": label, "count": count}
            for label, count in sorted(counts[level1].items())
        ]
        for level1 in BUSINESS_TYPE_LEVEL1
    }


def get_business_type_alias_records(cursor, table_name):
    validate_business_type_table_name(table_name)
    cursor.execute(
        f"""
        SELECT level1, retired_level2, canonical_level2, updated_at
        FROM {ALIASES_TABLE}
        WHERE news_table=%s
        ORDER BY level1, retired_level2
        """,
        (table_name,),
    )
    return cur_rows_to_dicts(cursor.fetchall())


def cur_rows_to_dicts(rows):
    return [
        {"level1": row[0], "retired_level2": row[1], "canonical_level2": row[2], "updated_at": row[3]}
        for row in rows
    ]


def count_secondary_label_uses(cursor, table_name, level1, labels, aliases=None):
    validate_business_type_table_name(table_name)
    aliases = aliases or {}
    canonical_labels = {resolve_canonical_level2(level1, label, aliases) for label in labels}
    cursor.execute(
        f"SELECT business_types FROM {table_name} WHERE keyword=%s AND business_types IS NOT NULL",
        ("公积金",),
    )
    return sum(
        any(item["level1"] == level1 and item["level2"] in canonical_labels
            for item in decode_business_types(raw_types, aliases))
        for (raw_types,) in cursor.fetchall()
    )


def resolve_canonical_level2(level1, level2, aliases):
    level2 = _clean_label(level2)
    if not level2:
        return None
    seen = set()
    while (level1, level2) in aliases and level2 not in seen:
        seen.add(level2)
        level2 = aliases[(level1, level2)]
    return level2


def normalize_business_types(raw_types, aliases=None):
    """Return no more than two valid, canonicalized and deduplicated tag objects."""
    aliases = aliases or {}
    if not isinstance(raw_types, list):
        return []

    normalized = []
    seen = set()
    for item in raw_types:
        if not isinstance(item, dict):
            continue
        level1 = _clean_label(item.get("level1"))
        level2 = _clean_label(item.get("level2"))
        if level1 not in BUSINESS_TYPE_LEVEL1 or not level2:
            continue
        level2 = resolve_canonical_level2(level1, level2, aliases)
        key = (level1, level2)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"level1": level1, "level2": level2})
        if len(normalized) == 2:
            break
    return normalized


def validate_llm_business_types(raw_types, aliases=None):
    """Validate an LLM payload strictly before any database write."""
    if not isinstance(raw_types, list) or len(raw_types) > 2:
        return None
    for item in raw_types:
        if not isinstance(item, dict):
            return None
        level1 = _clean_label(item.get("level1"))
        level2 = _clean_label(item.get("level2"))
        if level1 not in BUSINESS_TYPE_LEVEL1 or not level2:
            return None
    # 别名归并或模型重复输出会让条目数收缩；这是规范化的预期结果，
    # 不应使原本合法的 payload 连同摘要、地域一起被拒绝。
    return normalize_business_types(raw_types, aliases)


def decode_business_types(value, aliases=None):
    if value is None:
        return []
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return normalize_business_types(value, aliases)


def build_business_type_catalog(cursor, table_name, aliases=None):
    """Build the active secondary-label choices for the dynamic AI prompt."""
    validate_business_type_table_name(table_name)
    aliases = aliases or {}
    cursor.execute(
        f"""
        SELECT business_types FROM {table_name}
        WHERE keyword=%s AND score>=3 AND business_types IS NOT NULL
        """,
        ("公积金",),
    )
    catalog = defaultdict(set)
    for (raw_types,) in cursor.fetchall():
        for item in decode_business_types(raw_types, aliases):
            catalog[item["level1"]].add(item["level2"])
    return {level1: sorted(catalog.get(level1, set())) for level1 in BUSINESS_TYPE_LEVEL1}


def format_business_type_catalog(catalog):
    lines = []
    for level1 in BUSINESS_TYPE_LEVEL1:
        labels = catalog.get(level1) or []
        if labels:
            lines.append(f"- {level1}：{'、'.join(labels)}")
    return "\n".join(lines) or "（当前暂无既有二级标签；请仅在确有必要时新建。）"


def get_business_type_dashboard(cursor, table_name, date_from=None, date_to=None, recent_limit=8):
    """Return read-only coverage, distribution and recent labeled-news data for the admin dashboard."""
    validate_business_type_table_name(table_name)
    where = ["keyword=%s", "score>=3"]
    params = ["公积金"]
    if date_from:
        where.append("fetchdate >= %s")
        params.append(date_from)
    if date_to:
        where.append("fetchdate <= %s")
        params.append(date_to)
    where_sql = " AND ".join(where)

    cursor.execute(
        f"""
        SELECT COUNT(*),
               SUM(business_types IS NOT NULL),
               SUM(business_types IS NULL),
               SUM(CASE WHEN business_types IS NOT NULL AND JSON_LENGTH(business_types)=0 THEN 1 ELSE 0 END)
        FROM {table_name}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    eligible, labeled, pending, empty = cursor.fetchone()
    eligible = eligible or 0
    labeled = labeled or 0
    pending = pending or 0
    empty = empty or 0

    cursor.execute(
        f"SELECT business_types FROM {table_name} WHERE {where_sql} AND business_types IS NOT NULL",
        tuple(params),
    )
    level1_counts = defaultdict(int)
    level2_counts = defaultdict(int)
    for (raw_types,) in cursor.fetchall():
        for item in decode_business_types(raw_types):
            level1_counts[item["level1"]] += 1
            level2_counts[(item["level1"], item["level2"])] += 1

    cursor.execute(
        f"""
        SELECT fetchdate, title, score, region, business_types
        FROM {table_name}
        WHERE {where_sql} AND business_types IS NOT NULL
        ORDER BY fetchdate DESC, id DESC
        LIMIT %s
        """,
        tuple(params + [recent_limit]),
    )
    recent_items = []
    for fetchdate, title, score, region, raw_types in cursor.fetchall():
        recent_items.append({
            "fetchdate": fetchdate,
            "title": title,
            "score": score,
            "region": region,
            "business_types": decode_business_types(raw_types),
        })

    return {
        "eligible": eligible,
        "labeled": labeled,
        "pending": pending,
        "empty": empty,
        "coverage": round(labeled * 100 / eligible, 1) if eligible else 0,
        "level1_counts": [{"level1": level1, "count": level1_counts[level1]} for level1 in BUSINESS_TYPE_LEVEL1],
        "level2_counts": [
            {"level1": level1, "level2": level2, "count": count}
            for (level1, level2), count in sorted(level2_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        ],
        "recent_items": recent_items,
    }


def merge_secondary_labels(conn, table_name, level1, retired_labels, target_label):
    """Merge one or more same-level labels and update all affected news atomically."""
    validate_business_type_table_name(table_name)
    if level1 not in BUSINESS_TYPE_LEVEL1:
        raise ValueError("Invalid primary business type")
    target_label = _clean_label(target_label)
    retired_labels = sorted({_clean_label(label) for label in retired_labels if _clean_label(label)})
    if not target_label or not retired_labels or target_label in retired_labels:
        raise ValueError("请选择待合并标签，并提供不同的目标标签")

    cur = conn.cursor()
    try:
        aliases = load_business_type_aliases(cur, table_name)
        target_label = resolve_canonical_level2(level1, target_label, aliases)
        resolved_retired = {resolve_canonical_level2(level1, label, aliases) for label in retired_labels}
        resolved_retired.discard(target_label)
        if not resolved_retired:
            raise ValueError("待合并标签已指向目标标签")

        cur.execute(
            f"SELECT id, business_types FROM {table_name} WHERE keyword=%s AND business_types IS NOT NULL",
            ("公积金",),
        )
        updated = 0
        for news_id, raw_types in cur.fetchall():
            types = decode_business_types(raw_types, aliases)
            changed = False
            for item in types:
                if item["level1"] == level1 and item["level2"] in resolved_retired:
                    item["level2"] = target_label
                    changed = True
            types = normalize_business_types(types, aliases={})
            if changed:
                cur.execute(
                    f"UPDATE {table_name} SET business_types=%s WHERE id=%s",
                    (json.dumps(types, ensure_ascii=False), news_id),
                )
                updated += 1

        for source in resolved_retired:
            cur.execute(
                f"""
                INSERT INTO {ALIASES_TABLE} (news_table, level1, retired_level2, canonical_level2)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE canonical_level2=VALUES(canonical_level2)
                """,
                (table_name, level1, source, target_label),
            )
            cur.execute(
                f"""
                UPDATE {ALIASES_TABLE}
                SET canonical_level2=%s
                WHERE news_table=%s AND level1=%s AND canonical_level2=%s
                """,
                (target_label, table_name, level1, source),
            )
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
