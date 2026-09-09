"""Run the relevant offline suite in a disposable configuration and working directory."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from config_store import ConfigStore, read_document

ROOT = Path(__file__).resolve().parent
TESTS = [
    "test_home_performance.py", "test_config_store.py", "test_config_integration.py", "test_config_system.py", "test_config_review.py", "test_config_followup.py", "test_bank_news_feature.py",
    "test_historical_date_filter.py", "test_write_to_mysql_dedup.py",
    "test_business_type_feature.py", "test_news_volume_alert.py",
]


def main():
    with tempfile.TemporaryDirectory(prefix="serp-offline-tests-") as directory:
        path = Path(directory)
        store = ConfigStore(path / "runtime.sqlite3")
        store.initialize(read_document(ROOT / "config_defaults.json"), {"kind": "test-suite"})
        env = {**os.environ, "SERP_CONFIG_STORE": str(store.path), "SERP_CONFIG_REVISION": "",
               "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
               "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "1", "MYSQL_USER": "offline_test",
               "MYSQL_PASSWORD": "offline_test", "MYSQL_DB": "offline_test", "MYSQL_TABLE": "scored_news_test",
               "DEEPSEEK_API_KEY": "offline-test-key", "SERPAPI_KEY": "offline-test-key", "GNEWS_API_KEY": "offline-test-key",
               "BAILIAN_API_KEY": "offline-test-key", "FEISHU_USER_ID": "", "FEISHU_APP_ID": "", "FEISHU_APP_SECRET": "",
               "LARK_CLI_PATH": sys.executable, "ADMIN_USERNAME": "test-admin", "ADMIN_PASSWORD": "test-password",
               "FLASK_SECRET_KEY": "isolated-test-session-key", "RUN_LOG_PATH": str(path / "run.log")}
        # Block accidental network access even if a test forgets to mock an SDK.
        network_guard = """
import socket, sys
def blocked(*args, **kwargs):
    raise AssertionError('Offline test attempted network access; mock the service')
socket.socket.connect = blocked
socket.create_connection = blocked
"""
        program = network_guard + "\nimport pytest\nraise SystemExit(pytest.main(sys.argv[1:]))\n"
        result = subprocess.run([sys.executable, "-B", "-c", program, "-q", "-p", "no:cacheprovider", *[str(ROOT / name) for name in TESTS], *sys.argv[1:]], cwd=path, env=env)
        if result.returncode:
            return result.returncode
        # This older regression exits at module scope, so run it in its own process.
        legacy = network_guard + "\nimport runpy\nrunpy.run_path(sys.argv[1], run_name='__main__')\n"
        return subprocess.run([sys.executable, "-B", "-c", legacy, str(ROOT / "test_log_system.py")], cwd=path, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
