"""Local SQLite configuration revisions, atomic activation and portable exports."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from config_schema import ConfigConflict, ConfigError, ConfigVersionError, ConfigVersionNotFound, digest, encode, parse_json, validate

PROJECT_DIR = Path(__file__).resolve().parent


def store_path(value=None):
    raw = value if value is not None else os.environ.get("SERP_CONFIG_STORE")
    if not raw or not Path(raw).expanduser().is_absolute():
        raise ConfigError("请设置 SERP_CONFIG_STORE 为部署目录之外的配置文件绝对路径，并先显式初始化")
    path = Path(raw).expanduser().resolve()
    if path == PROJECT_DIR or PROJECT_DIR in path.parents:
        raise ConfigError("运行配置及备份必须位于项目部署目录之外")
    return path


def _sync_dir(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_file(temp, destination):
    """Publish a completely written file without ever replacing an existing file."""
    try:
        os.link(temp, destination)
        _sync_dir(destination.parent)
    except FileExistsError as exc:
        raise ConfigConflict(f"目标已存在，未覆盖: {destination}") from exc


def write_export(destination, text):
    target = store_path(destination)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".config-export-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        publish_file(Path(temp), target)
    finally:
        Path(temp).unlink(missing_ok=True)


@dataclass(frozen=True)
class Snapshot:
    store_id: str
    revision: int
    sha256: str
    created_at: str
    note: str
    _json: str

    @property
    def token(self):
        return f"{self.store_id}:{self.revision}"

    @property
    def document(self):
        # Callers receive an independent object, never the cached revision itself.
        return parse_json(self._json)

    def summary(self):
        return {"version": self.token, "sha256": self.sha256, "created_at": self.created_at, "note": self.note}


class ConfigStore:
    def __init__(self, path=None):
        self.path = store_path(path)

    @contextmanager
    def _connection(self, writable=False):
        if not self.path.is_file():
            raise ConfigError(f"配置存储不存在: {self.path}；请先使用 config_cli.py init，禁止自动回退默认配置")
        connection = None
        try:
            # rw prevents accidental creation but permits SQLite to recover a hot
            # rollback journal after a killed writer, including on the read path.
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ConfigError("配置存储结构不兼容或已损坏")
            if writable:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA fullfsync=ON")
            yield connection
        except sqlite3.Error as exc:
            raise ConfigError(f"配置存储操作失败，未回退默认值: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()  # An uncommitted write transaction is rolled back.

    @staticmethod
    def _decode(row):
        if row is None:
            raise ConfigError("配置版本不存在或生效指针已损坏")
        store_id, revision, text, checksum, created, note = row
        document = validate(parse_json(text))
        if digest(document) != checksum:
            raise ConfigError("配置内容校验失败，禁止使用损坏版本")
        return Snapshot(store_id, revision, checksum, created, note, text)

    def _read(self, connection, token=None):
        if token is None:
            row = connection.execute("SELECT m.store_id,r.id,r.document,r.sha256,r.created_at,r.note FROM metadata m JOIN revisions r ON r.id=m.active WHERE m.id=1").fetchone()
        else:
            try:
                store_id, revision = token.split(":")
                number = int(revision)
                if str(uuid.UUID(store_id)) != store_id or str(number) != revision or not 0 < number <= 2**63 - 1:
                    raise ValueError()
                revision = number
            except (ValueError, AttributeError) as exc:
                raise ConfigVersionError("配置版本必须使用完整的 store_id:revision") from exc
            metadata = connection.execute("SELECT store_id FROM metadata WHERE id=1").fetchone()
            if metadata is None:
                raise ConfigError("配置存储元数据缺失或已损坏")
            if metadata[0] != store_id:
                raise ConfigVersionError("指定版本来自其他配置存储")
            row = connection.execute("SELECT m.store_id,r.id,r.document,r.sha256,r.created_at,r.note FROM metadata m JOIN revisions r ON r.id=? WHERE m.id=1 AND m.store_id=?", (revision, store_id)).fetchone()
            if row is None:
                raise ConfigVersionNotFound("指定的配置版本不存在")
        return self._decode(row)

    def read(self, token=None):
        with self._connection() as connection:
            return self._read(connection, token)

    @staticmethod
    def _insert(connection, document, note, source):
        cursor = connection.execute("INSERT INTO revisions(document,sha256,created_at,note,source) VALUES (?,?,?,?,?)", (
            encode(document), digest(document), datetime.now(timezone.utc).isoformat(), note, encode(source),
        ))
        return cursor.lastrowid

    def initialize(self, document, source, note="首次初始化"):
        validate(document)
        if not isinstance(note, str) or not note.strip():
            raise ConfigError("请填写初始化说明")
        source_id = digest(source)
        if self.path.exists():
            with self._connection() as connection:
                metadata = connection.execute("SELECT initial_source,initial_sha256 FROM metadata WHERE id=1").fetchone()
                if metadata != (source_id, digest(document)):
                    raise ConfigConflict("目标已初始化且来源不同；请先 diff，再通过带版本检查的 import 创建新版本")
                return self._read(connection), False
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".config-init-", dir=self.path.parent)
        os.close(descriptor)
        temp = Path(temp_name)
        connection = None
        try:
            connection = sqlite3.connect(temp, isolation_level=None)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA fullfsync=ON")
            connection.executescript("""
                CREATE TABLE revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document TEXT NOT NULL, sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, note TEXT NOT NULL, source TEXT NOT NULL
                );
                CREATE TABLE metadata (
                    id INTEGER PRIMARY KEY CHECK(id=1), store_id TEXT NOT NULL,
                    active INTEGER NOT NULL REFERENCES revisions(id),
                    initial_source TEXT NOT NULL, initial_sha256 TEXT NOT NULL
                );
                CREATE TABLE batch_pins (
                    output_directory TEXT NOT NULL, keyword TEXT NOT NULL,
                    revision INTEGER NOT NULL REFERENCES revisions(id),
                    PRIMARY KEY(output_directory,keyword)
                );
                CREATE TRIGGER revisions_no_update BEFORE UPDATE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'Configuration revisions are immutable'); END;
                CREATE TRIGGER revisions_no_delete BEFORE DELETE ON revisions
                    BEGIN SELECT RAISE(ABORT, 'Configuration revisions are immutable'); END;
                PRAGMA user_version=1;
            """)
            connection.execute("BEGIN IMMEDIATE")
            revision = self._insert(connection, document, note, source)
            connection.execute("INSERT INTO metadata VALUES(1,?,?,?,?)", (str(uuid.uuid4()), revision, source_id, digest(document)))
            # Verify the stored representation before publishing, just as save()
            # verifies its new revision before committing the active pointer.
            self._read(connection)
            connection.commit()
            connection.close()
            connection = None
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            publish_file(temp, self.path)
        finally:
            if connection is not None:
                connection.close()
            temp.unlink(missing_ok=True)
        return self.read(), True

    def save(self, document, expected_version, note, source=None):
        validate(document)
        if not isinstance(note, str) or not note.strip():
            raise ConfigError("请填写修改说明")
        with self._connection(writable=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._read(connection)
            if expected_version != current.token:
                raise ConfigConflict("配置已被其他操作修改，请刷新并比较差异后重新提交")
            revision = self._insert(connection, document, note, source or {"kind": "edit"})
            connection.execute("UPDATE metadata SET active=? WHERE id=1 AND active=?", (revision, current.revision))
            result = self._read(connection)
            connection.commit()
            return result

    def restore(self, revision, expected_version, note):
        old = self.read(revision)
        return self.save(old.document, expected_version, note, {"kind": "restore", "restored_version": old.token})

    def history(self, limit=100, at_version=None):
        with self._connection() as connection:
            current = self._read(connection, at_version)
            rows = connection.execute("SELECT id,sha256,created_at,note FROM revisions WHERE id<=? ORDER BY id DESC LIMIT ?", (current.revision, limit)).fetchall()
            return [{"version": f"{current.store_id}:{r[0]}", "sha256": r[1], "created_at": r[2], "note": r[3], "active": r[0] == current.revision} for r in rows]

    def pin_batch(self, output_directory, keyword=None, requested_version=None, adopt_existing=False, scored_only=False):
        """Pin each output topic once, so automatic file-based resume cannot mix revisions."""
        directory = Path(output_directory).resolve()
        with self._connection(writable=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            selected = self._read(connection, requested_version)
            rows = dict(connection.execute("SELECT keyword,revision FROM batch_pins WHERE output_directory=?", (str(directory),)))
            existing = {revision for key, revision in rows.items() if keyword is None or key == keyword}
            if len(existing) > 1:
                raise ConfigConflict("当日不同关键词使用了不同版本，请分别按 --keyword 续跑")
            if existing:
                pinned = self._read(connection, f"{selected.store_id}:{existing.pop()}")
                if requested_version is not None and pinned.token != requested_version:
                    raise ConfigConflict("指定版本与已有批次绑定不同，禁止混用；请在独立输出工作目录开始新批次")
                selected = pinned
            keywords = list(selected.document["settings"]["SEARCH_KEYWORDS"])
            if keyword is not None:
                if keyword not in keywords:
                    raise ConfigError(f"此配置版本没有主关键词: {keyword}")
                keywords = [keyword]
            if scored_only:
                # Admin rescore must find actual work before creating any pins.
                keywords = [key for key in keywords if (directory / f"{directory.name}_{key}_scored.json").is_file()]
                if not keywords:
                    raise ConfigError("没有与所选配置匹配的可重评文件；未创建批次绑定，请核对日期、输出文件及历史配置版本")
            for key in keywords:
                if key in rows:
                    continue
                date = directory.name
                has_outputs = any((directory / f"{date}_{key}{suffix}.json").exists() for suffix in ("", "_scored"))
                if has_outputs and not adopt_existing:
                    raise ConfigConflict("已有输出缺少配置版本记录；核对原配置后使用 --adopt-existing-config 显式绑定")
                connection.execute("INSERT INTO batch_pins VALUES(?,?,?)", (str(directory), key, selected.revision))
            connection.commit()
            return selected, keywords

    def export(self, token=None):
        snapshot = self.read(token)
        return {"format": "serp-news-config-export-v1", "origin": snapshot.summary(), "document": snapshot.document, "sha256": snapshot.sha256}

    def backup(self, destination):
        """SQLite online backup includes every revision and the original source bytes."""
        target = store_path(destination)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".config-backup-", dir=target.parent)
        os.close(descriptor)
        try:
            with self._connection() as source:
                with sqlite3.connect(temp_name) as dest:
                    source.backup(dest)
            with open(temp_name, "rb") as handle:
                os.fsync(handle.fileno())
            publish_file(Path(temp_name), target)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def original_source(self):
        with self._connection() as connection:
            self._read(connection)
            row = connection.execute("SELECT r.source,m.initial_source FROM revisions r CROSS JOIN metadata m WHERE m.id=1 ORDER BY r.id LIMIT 1").fetchone()
            source = parse_json(row[0])
            if digest(source) != row[1]:
                raise ConfigError("原始迁移源校验失败")
            return source


def read_document(path):
    try:
        data = parse_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"无法读取配置导入文件: {exc}") from exc
    if isinstance(data, dict) and data.get("format") == "serp-news-config-export-v1":
        if set(data) != {"format", "origin", "document", "sha256"} or digest(data["document"]) != data["sha256"]:
            raise ConfigError("配置导出包字段或校验值无效")
        data = data["document"]
    return validate(data)
