"""Failure propagation through real subprocesses and the production CLI entry."""
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from error_handler import safe_subprocess_run


@pytest.mark.parametrize('check', [False, True])
def test_child_failure_is_retried_logged_and_propagated(tmp_path, monkeypatch, check):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'batch.log'))
    code = "from pathlib import Path; import sys; p=Path('attempts'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('db failed', file=sys.stderr); sys.exit(7)"
    cmd = shlex.join([sys.executable, '-c', code])
    with patch('time.sleep'):
        if check:
            with pytest.raises(subprocess.CalledProcessError) as exc:
                safe_subprocess_run(cmd, 'database', check=True)
            assert exc.value.returncode == 7
        else:
            assert safe_subprocess_run(cmd, 'database', check=False) is False
    assert (tmp_path / 'attempts').read_text() == 'xx'
    assert 'db failed' in (tmp_path / 'output/error_log.txt').read_text()


def test_child_retry_can_recover(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'batch.log'))
    code = "from pathlib import Path; import sys; p=Path('attempted'); prior=p.exists(); p.touch(); sys.exit(0 if prior else 7)"
    with patch('time.sleep'):
        assert safe_subprocess_run(shlex.join([sys.executable, '-c', code]), 'database', check=False)


@pytest.mark.parametrize('fails', [False, True])
def test_import_cli_exit_status_on_database_error(tmp_path, fails):
    # Execute the real module as __main__, with only DB/bootstrap dependencies
    # stubbed in a child interpreter; no production files or network access.
    script = Path(__file__).with_name('write_to_mysql.py')
    program = '''
import runpy,sys
from unittest.mock import Mock,patch
import pymysql
connection=Mock()
if sys.argv[2]=='True':
    connection.cursor.return_value.execute.side_effect=pymysql.OperationalError(2013,'simulated timeout')
script=sys.argv[1]
sys.argv=[script,'--date','2026-09-19']
with patch('db_utils.get_connection',return_value=connection):
    runpy.run_path(script,run_name='__main__')
'''
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/news_sources.txt').write_text('example.test\n')
    result = subprocess.run([sys.executable, '-c', program, str(script), str(fails)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == (1 if fails else 0), result.stdout + result.stderr
    assert ('simulated timeout' in result.stdout) is fails


@pytest.mark.parametrize('failed_stage', ['none', 'fetch', 'content', 'scoring', 'summary', 'tobacco'])
def test_pipeline_partial_failure_is_not_logged_as_success(tmp_path, monkeypatch, failed_stage):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_LOG_PATH', str(tmp_path / 'batch.log'))
    monkeypatch.setattr(main, 'execute_news_fetching', lambda *a: failed_stage != 'fetch')
    monkeypatch.setattr(main, 'execute_content_fetching', lambda *a: failed_stage != 'content')
    monkeypatch.setattr(main, 'execute_scoring_concurrent', lambda *a, **kw: (0, 1) if failed_stage == 'scoring' else (1, 0))
    monkeypatch.setattr(main, 'run_step', lambda *a: True)
    monkeypatch.setattr(main, 'safe_subprocess_run', lambda cmd, *a, **kw: not ((failed_stage == 'summary' and 'news_item_summarizer.py' in cmd) or (failed_stage == 'tobacco' and 'tobacco_gov_crawler.py' in cmd)))
    monkeypatch.setenv('ENABLE_ITEM_SUMMARIZER', '1')
    alert, complete = Mock(), Mock()
    monkeypatch.setattr(main, 'run_volume_alert', alert)
    monkeypatch.setattr(main, 'log_script_complete', complete)
    assert main.main('2026-09-19') is (failed_stage == 'none')
    assert complete.call_args.kwargs['success'] is (failed_stage == 'none')
    alert.assert_called_once()
