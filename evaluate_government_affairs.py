"""Explicit paid evaluation of an isolated candidate; preview is the default."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from config_schema import ConfigError, digest, parse_json
from config_store import ConfigStore
from topic_config import build_topic_candidate, TOPIC
from topic_evaluation import load_samples, evaluation_manifest, evaluate_samples, scoring_profile

ROOT = Path(__file__).resolve().parent

def worker(manifest_path, output):
    from runtime_config import get_snapshot
    from news_scorer import score_news_result
    manifest = parse_json(Path(manifest_path).read_text(encoding="utf-8"))
    candidate = get_snapshot().document
    if digest(candidate) != manifest["candidate_sha256"] or digest(scoring_profile()) != manifest["scoring_profile_sha256"]:
        raise ConfigError("隔离评测候选或有效模型不匹配")
    def score(sample):
        return score_news_result(sample["title"], sample["content"], sample["search_keyword"], TOPIC)
    report = evaluate_samples(load_samples(), manifest, score)
    # Exclusive write prevents accidental loss of previous evaluation evidence.
    with Path(output).open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return 0 if report["passed"] else 1

def run_evaluation(candidate, manifest, output):
    target = Path(output).expanduser().resolve()
    if target.exists() or not target.parent.is_dir():
        raise ConfigError("评测输出须为已有目录中的新文件，不能覆盖旧证据")
    with tempfile.TemporaryDirectory(prefix="serp-government-eval-") as directory:
        temporary = Path(directory)
        store = ConfigStore(temporary / "candidate.sqlite3")
        store.initialize(candidate, {"kind": "isolated-evaluation"})
        request = temporary / "manifest.json"
        request.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "SERP_CONFIG_STORE": str(store.path), "SERP_CONFIG_REVISION": "",
               "RUN_LOG_PATH": str(temporary / "evaluation.log"), "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--run",
            "--worker-manifest", str(request), "--output", str(target)],
            cwd=temporary, env=env)
        return process.returncode

def main(argv=None):
    parser = argparse.ArgumentParser(description="机关事务固定集评测；默认仅预览，--run 会产生模型费用")
    parser.add_argument("--store")
    parser.add_argument("--output")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--worker-manifest", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        if args.worker_manifest:
            if not args.run or not args.output:
                raise ConfigError("评测子进程须显式 --run 和 --output")
            return worker(args.worker_manifest, args.output)
        snapshot = ConfigStore(args.store).read()
        candidate = build_topic_candidate(snapshot.document)["document"]
        samples = load_samples()
        manifest = evaluation_manifest(snapshot, candidate)
        if not args.run:
            print(json.dumps({**manifest, "sample_count": len(samples),
                "maximum_scoring_calls": len(samples)*3, "mode": "preview"}, ensure_ascii=False, indent=2))
            return 0
        if not args.output:
            raise ConfigError("--run 必须提供 --output")
        return run_evaluation(candidate, manifest, args.output)
    except (ConfigError, OSError) as exc:
        print(f"评测未完成: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
