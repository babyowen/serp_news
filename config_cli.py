"""Explicit initialization, migration, diff, revision restore and configuration backup."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

from config_migration import extract_legacy
from config_schema import ConfigConflict, ConfigError, differences, digest
from config_store import ConfigStore, read_document, write_export


def _candidate(args):
    if args.source:
        return extract_legacy(args.source)
    path = Path(__file__).with_name("config_defaults.json") if args.defaults else Path(args.file)
    document = read_document(path)
    return document, {"kind": "defaults" if args.defaults else "import", "document_sha256": digest(document)}


def parser():
    root = argparse.ArgumentParser(description="提示词及运行配置管理（不连接业务数据库）")
    root.add_argument("--store", help="部署目录外的绝对路径；也可通过 SERP_CONFIG_STORE 设置")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("init", "import", "diff"):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--source", help="旧版 config.py 及调用代码所在目录（仅 AST 解析）")
        source.add_argument("--file", help="配置 JSON 或带校验值的导出包")
        source.add_argument("--defaults", action="store_true", help="显式使用仓库默认模板；生产应使用 --source")
        if name != "diff":
            command.add_argument("--dry-run", action="store_true", help="只预览，不创建目录或写入")
            command.add_argument("--note", default="首次迁移" if name == "init" else None, required=name == "import")
        if name == "import":
            command.add_argument("--expected-version", required=True, help="编辑时记录的完整配置版本")
    commands.add_parser("status")
    history = commands.add_parser("history")
    history.add_argument("--limit", type=int, default=100)
    export = commands.add_parser("export")
    export.add_argument("--version")
    export.add_argument("--output", required=True)
    export.add_argument("--editable", action="store_true", help="仅导出 document 对象，便于编辑后显式导入")
    restore = commands.add_parser("restore")
    restore.add_argument("--version", required=True)
    restore.add_argument("--expected-version", required=True)
    restore.add_argument("--note", required=True)
    restore.add_argument("--dry-run", action="store_true")
    backup = commands.add_parser("backup", help="一致性备份整个配置文件，包含所有历史和原始迁移源")
    backup.add_argument("--output", required=True)
    sources = commands.add_parser("export-sources", help="恢复首次迁移保存的原始源码文件")
    sources.add_argument("--directory", required=True)
    return root


def run(args):
    store = ConfigStore(args.store)
    if args.command in ("init", "import", "diff"):
        document, source = _candidate(args)
        current = store.read() if store.path.exists() else None
        if args.command == "import":
            if current is None:
                raise ConfigError("目标尚未初始化，请显式使用 init")
            if args.expected_version != current.token:
                raise ConfigConflict("配置已被其他操作修改，请刷新并比较差异后重新提交")
        preview = {"store": str(store.path), "candidate_sha256": digest(document),
                   "prompts": len(document["prompts"]), "initialized": current is not None,
                   "current": current.summary() if current else None,
                   "changes": differences(current.document, document) if current else []}
        if args.command == "diff" or args.dry_run:
            if args.command == "init" and current:
                preview["warning"] = "目标已初始化；只有完全相同的初始化源可幂等重试，不会切换当前版本"
            return preview
        if args.command == "init":
            snapshot, created = store.initialize(document, source, args.note)
            return {"created": created, **snapshot.summary()}
        if current is None:
            raise ConfigError("目标尚未初始化，请显式使用 init")
        snapshot = store.save(document, args.expected_version, args.note, source)
        return snapshot.summary()
    if args.command == "status":
        return {"store": str(store.path), **store.read().summary()}
    if args.command == "history":
        return store.history(args.limit)
    if args.command == "export":
        bundle = store.export(args.version)
        write_export(args.output, json.dumps(bundle["document"] if args.editable else bundle, ensure_ascii=False, indent=2) + "\n")
        return {"exported": args.output, "source_version": bundle["origin"]["version"]}
    if args.command == "restore":
        if args.dry_run:
            current = store.read()
            if args.expected_version != current.token:
                raise ConfigConflict("配置已被其他操作修改，请刷新并比较差异后重新提交")
            candidate = store.read(args.version)
            return {"current": current.summary(), "candidate": candidate.summary(), "changes": differences(current.document, candidate.document)}
        return store.restore(args.version, args.expected_version, args.note).summary()
    if args.command == "backup":
        store.backup(args.output)
        return {"backup": args.output}
    if args.command == "export-sources":
        source = store.original_source()
        if source.get("kind") != "legacy":
            raise ConfigError("此存储并非从旧源码迁移，没有原始源码归档")
        directory = Path(args.directory).expanduser()
        from config_store import store_path
        directory = store_path(directory)
        if directory.exists():
            raise ConfigError("源码恢复目录必须不存在，防止覆盖现有文件")
        # Decode and validate the entire archive before creating any output.
        files = {}
        for name, item in source["files"].items():
            if Path(name).name != name or name in (".", ".."):
                raise ConfigError("原始源码归档含非法路径")
            raw = base64.b64decode(item["base64"], validate=True)
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ConfigError("原始源码归档校验失败")
            files[name] = raw
        directory.mkdir(mode=0o700, parents=True)
        for name, raw in files.items():
            target = directory / name
            with target.open("xb") as handle:
                handle.write(raw)
            target.chmod(0o600)
        return {"directory": str(directory), "files": list(files)}


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ConfigError, OSError) as exc:
        print(f"配置操作失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
