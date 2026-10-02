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
    identity = [{key: row.get(key) for key in ("title", "link", "keyword", "main_keyword", "search_keyword")}
                for row in rows if "tv.cctv.com" not in str(row.get("link") or "")]
    for row in identity:
        link = row.get("link")
        if link and link.startswith("https://") and ".people.com.cn" in link:
            row["link"] = "http://" + link[len("https://"):]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def collect(date, terms, run_search):
    folder = Path("output") / date
    merged = folder / f"{date}_{TOPIC}.json"
    diagnostic = folder / "diagnostics" / "government_affairs_fetch.json"
    version = get_snapshot().token
    if merged.exists():
        try:
            record = json.loads(diagnostic.read_text(encoding="utf-8"))
            complete = (record["config_version"] == version
                        and [r["search_keyword"] for r in record["searches"]] == list(terms)
                        and all(r["status"] in ("ok", "empty") for r in record["searches"])
                        and record["identity_sha256"] == collection_identity(json.loads(merged.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError, TypeError):
            complete = False
        print("[INFO] 江苏机关事务采集已完成" if complete else
              "[ERROR] 江苏机关事务已有不完整/缺少诊断的结果，请人工核对后恢复失败检索")
        return complete
    folder.mkdir(parents=True, exist_ok=True)
    searches, unique = [], {}
    for term in terms:
        # A random temporary filename prevents custom search terms becoming paths.
        descriptor, filename = tempfile.mkstemp(prefix="tmp_gov_", suffix=".json", dir=folder)
        os.close(descriptor)
        path = Path(filename)
        path.unlink()
        row = {"search_keyword": term, "status": "failed", "count": 0, "error": None}
        try:
            process_ok = run_search(term, str(path))
            if not process_ok:
                row["error"] = "search_process_failed"
            if not path.is_file():
                row["error"] = "missing_output"
            else:
                news = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(news, list) or not all(isinstance(n, dict) for n in news):
                    raise ValueError("invalid news list")
                row.update(status=("ok" if news else "empty") if process_ok else "failed", count=len(news))
                for item in news:
                    item.update(keyword=TOPIC, main_keyword=TOPIC, search_keyword=term)
                    key = (str(item.get("title") or "").strip(), str(item.get("link") or "").strip())
                    if key not in unique:
                        unique[key] = item
        except (OSError, ValueError, TypeError):
            row.update(status="failed", error="invalid_search_output")
        finally:
            path.unlink(missing_ok=True)
        searches.append(row)
        print(f"[GOV_FETCH] {term}: {row['status']} count={row['count']} error={row['error']}")
    atomic_json(merged, list(unique.values()))
    atomic_json(diagnostic, {"date": date, "config_version": version, "searches": searches,
                             "identity_sha256": collection_identity(list(unique.values()))})
    return all(r["status"] in ("ok", "empty") for r in searches)
