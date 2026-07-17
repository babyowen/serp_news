"""Schema support for keyword-scoped news deduplication."""

import re


TARGET_INDEX_NAME = "keyword_title_link"
TARGET_COLUMNS = ("keyword", "title", "link")
LEGACY_COLUMNS = ("title", "link")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def ensure_keyword_scoped_dedup_index(cursor, table_name):
    """Replace the legacy global title/link index with a keyword-scoped one.

    Returns True only when a schema change was applied.  The migration is
    idempotent so normal batch runs only perform the metadata query.
    """
    if not _TABLE_NAME_RE.fullmatch(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")

    cursor.execute(
        """
        SELECT index_name, column_name, seq_in_index
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND non_unique = 0
          AND index_name <> 'PRIMARY'
        ORDER BY index_name, seq_in_index
        """,
        (table_name,),
    )
    indexes = {}
    for index_name, column_name, _ in cursor.fetchall():
        indexes.setdefault(index_name, []).append(column_name)

    target_index_is_correct = tuple(indexes.get(TARGET_INDEX_NAME, ())) == TARGET_COLUMNS
    legacy_indexes = [
        index_name
        for index_name, columns in indexes.items()
        if tuple(columns) == LEGACY_COLUMNS
    ]
    if target_index_is_correct and not legacy_indexes:
        return False

    indexes_to_drop = []
    if TARGET_INDEX_NAME in indexes and not target_index_is_correct:
        indexes_to_drop.append(TARGET_INDEX_NAME)
    indexes_to_drop.extend(legacy_indexes)
    indexes_to_drop = list(dict.fromkeys(indexes_to_drop))

    clauses = [f"DROP INDEX `{index_name}`" for index_name in indexes_to_drop]
    if not target_index_is_correct:
        clauses.append(
            "ADD UNIQUE INDEX `keyword_title_link` "
            "(`keyword`(100), `title`, `link`(255))"
        )
    cursor.execute(f"ALTER TABLE `{table_name}` " + ", ".join(clauses))
    return True
