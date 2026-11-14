"""Per-search diagnostic for the government affairs topic; no scheduling or configuration writes."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from runtime_config import get_snapshot
from topic_config import TOPIC


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def collection_identity(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("invalid collected rows")
    # The content stage enriches rows and removes tv.cctv.com entries.
    # Bind every other row's immutable provenance, while allowing those documented changes.
    identity = []
    for original in rows:
        if "tv.cctv.com" in str(original.get("link") or ""):
            continue
        row = {key: original.get(key) for key in ("title", "link", "keyword", "main_keyword", "search_keyword")}
        if original.get("wechat_article_id"):
            row.update(wechat_article_id=original["wechat_article_id"], wechat_ghid=original.get("wechat_ghid"),
                       wechat_search_sha256=collection_identity(original.get("wechat_search_matches", [])))
        identity.append(row)
        link = row.get("link")
        if link and link.startswith("https://") and ".people.com.cn" in link:
            row["link"] = "http://" + link[len("https://"):]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _merge_wechat(rows, report, date):
    # Source-specific verification is authoritative, including conflicts. Retain
    # search matches for provenance and to restore them if the source is removed.
    from wechat_collector import _article_identity

    if report["date"] != date:
        raise ValueError("WeChat report belongs to another business day")
    articles = {}
    for row in report["articles"]:
        key = _article_identity(row.get("link"))
        if not key or row["wechat_article_id"] != "/".join(key):
            raise ValueError("invalid collected WeChat identity")
        articles.setdefault(key, dict(row, wechat_search_matches=[]))
    search_rows = [original for row in rows for original in
                   (row.get("wechat_search_matches", []) if row.get("wechat_article_id") else [row])]
    merged = []
    for original in _deduplicate_search(search_rows):
        matched = articles.get(_article_identity(original.get("link")))
        if matched is not None:
            matched["wechat_search_matches"].append(original)
        else:
            merged.append(original)
    return merged + list(articles.values())


def _save_collection(merged, diagnostic, rows, record, wechat):
    if wechat is not None:
        rows = _merge_wechat(rows, wechat, record["date"])
        record["wechat"] = {key: wechat[key] for key in (
            "registry_sha256", "complete", "collected_count", "reused_count", "api_cost", "total_api_cost")}
        record["wechat"]["accounts"] = {status: sum(a["status"] == status for a in wechat["accounts"])
                                          for status in ("ok", "empty", "failed")}
        record["wechat"]["articles"] = sum(bool(row.get("wechat_article_id")) for row in rows)
    record["identity_sha256"] = collection_identity(rows)
    pending = diagnostic.with_suffix(".pending")
    if wechat is not None:
        atomic_json(pending, record)
    atomic_json(merged, rows)
    atomic_json(diagnostic, record)
    if wechat is not None:
        pending.unlink(missing_ok=True)


def _validated_fetch_record(diagnostic, date, version, terms, identity):
    try:
        record = json.loads(diagnostic.read_text(encoding="utf-8"))
        if (record["date"] == date and record["config_version"] == version
                and [row["search_keyword"] for row in record["searches"]] == terms
                and all(row["status"] in {"ok", "empty", "failed"} for row in record["searches"])
                and record["identity_sha256"] == identity):
            return record
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _collect_search(folder, term, run_search):
    # A random temporary filename prevents custom search terms becoming paths.
    descriptor, filename = tempfile.mkstemp(prefix="tmp_gov_", suffix=".json", dir=folder)
    os.close(descriptor)
    path = Path(filename)
    path.unlink()
    record = {"search_keyword": term, "status": "failed", "count": 0, "error": None}
    news = []
    try:
        process_ok = run_search(term, str(path))
        if not process_ok:
            record["error"] = "search_process_failed"
        if not path.is_file():
            record.update(error="missing_output")
        else:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, list) or not all(isinstance(n, dict) for n in parsed):
                raise ValueError("invalid news list")
            news = parsed
            record.update(status=("ok" if news else "empty") if process_ok else "failed", count=len(news))
            for item in news:
                item.update(keyword=TOPIC, main_keyword=TOPIC, search_keyword=term)
    except (OSError, ValueError, TypeError):
        record.update(status="failed", error="invalid_search_output")
    finally:
        path.unlink(missing_ok=True)
    print(f"[GOV_FETCH] {term}: {record['status']} count={record['count']} error={record['error']}")
    return record, news


def _deduplicate_search(rows):
    unique = {}
    for row in rows:
        key = (str(row.get("title") or "").strip(), str(row.get("link") or "").strip())
        unique.setdefault(key, row)
    return list(unique.values())


def collect(date, terms, run_search, *, collect_wechat=None):
    folder = Path("output") / date
    merged = folder / f"{date}_{TOPIC}.json"
    diagnostic = folder / "diagnostics" / "government_affairs_fetch.json"
    terms = list(terms)
    successful = {"ok", "empty"}
    version = get_snapshot().token
    if merged.exists():
        record = None
        try:
            rows = json.loads(merged.read_text(encoding="utf-8"))
            identity = collection_identity(rows)
            if collect_wechat is not None:
                # Recover a replacement interrupted between the two atomic writes.
                record = _validated_fetch_record(diagnostic.with_suffix(".pending"), date, version, terms, identity)
            record = record or _validated_fetch_record(diagnostic, date, version, terms, identity)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
        if record is None:
            print("[ERROR] 江苏机关事务已有采集结果无法验证，请人工核对诊断文件")
            return False
        searches = record["searches"]
        if collect_wechat is None:
            complete = all(row["status"] in successful for row in searches)
            print("[INFO] 江苏机关事务采集已完成" if complete else
                  "[ERROR] 江苏机关事务已有不完整的结果，请人工核对后恢复失败检索")
            return complete
    else:
        folder.mkdir(parents=True, exist_ok=True)
        rows, searches = [], []
        record = {"date": date, "config_version": version, "searches": searches}

    # For valid partial runs, retain the existing rows and retry only failed terms.
    # Previously enriched matches are retained in preference to identical fresh hits.
    retry = [idx for idx, row in enumerate(searches) if row["status"] == "failed"]
    retry.extend(range(len(searches), len(terms)))
    for index in retry:
        result, news = _collect_search(folder, terms[index], run_search)
        searches[index:index + 1] = [result]
        rows.extend(news)
    wechat = collect_wechat(date) if collect_wechat is not None else None
    if wechat is None:
        rows = _deduplicate_search(rows)
    _save_collection(merged, diagnostic, rows, record, wechat)
    return all(row["status"] in successful for row in searches) and (wechat is None or wechat["complete"])
