"""Regression coverage for the independent review of PR #21."""
import ast
import copy
from html.parser import HTMLParser
import os
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

import pytest

import config_store
from config_migration import extract_legacy
from config_schema import ConfigError
from config_store import ConfigStore
from test_config_system import ctx, web, ROOT


@pytest.mark.parametrize('level', ['platform', 'variant'])
def test_legacy_key_types_rejected_before_init_or_save(ctx, level):
    for invalid in (1, None, False, '', ' '):
        document = copy.deepcopy(ctx.document)
        mapping = document['legacy']['models']
        if level == 'variant':
            mapping = next(iter(mapping.values()))
        mapping[invalid] = copy.deepcopy(next(iter(mapping.values())))
        target = ConfigStore(ctx.root / 'must-not-exist.sqlite3')
        with pytest.raises(ConfigError, match='归档模型'):
            target.initialize(document, {'kind': 'invalid-test'})
        assert not target.path.exists()
        with pytest.raises(ConfigError, match='归档模型'):
            ctx.store.save(document, ctx.first.token, 'invalid keys')
        assert ctx.store.read().token == ctx.first.token


@pytest.mark.parametrize('level', ['platform', 'variant'])
def test_ast_migration_rejects_integer_and_string_key_collision(ctx, level):
    source_path = ctx.source / 'config.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    models = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'NEWS_SUMMARY_MODELS' for t in n.targets))
    mapping = models if level == 'platform' else models.values[0]
    original = copy.deepcopy(mapping.values[0])
    mapping.keys.extend([ast.Constant(value='1'), ast.Constant(value=1)])
    mapping.values.extend([copy.deepcopy(original), copy.deepcopy(original)])
    source_path.write_text(ast.unparse(tree), encoding='utf-8')
    with pytest.raises(ConfigError, match='归档模型'):
        extract_legacy(ctx.source)
    target = ConfigStore(ctx.root / 'never-published.sqlite3')
    result = ctx.cli('init', '--source', ctx.source, selected_store=target)
    assert result.returncode == 1 and not target.path.exists()


def test_unreadable_serialization_is_not_published_and_retry_succeeds(ctx):
    target = ConfigStore(ctx.root / 'publication.sqlite3')
    encode = config_store.encode
    def damaged(value):
        return '{"duplicate":1,"duplicate":2}' if value is ctx.document else encode(value)
    with patch.object(config_store, 'encode', side_effect=damaged):
        with pytest.raises(ConfigError, match='重复 JSON'):
            target.initialize(ctx.document, {'kind': 'publication-test'})
    assert not target.path.exists()
    snapshot, created = target.initialize(ctx.document, {'kind': 'publication-test'})
    assert created and snapshot.document == ctx.document


@pytest.mark.parametrize('route', ['keywords', 'config-restore', 'runs/start', 'runs/rescore', 'business-types'])
def test_all_admin_writes_require_authenticated_same_session_csrf(ctx, web, route):
    import routes.admin as admin
    other = web.app.test_client()
    other.get('/admin/keywords', headers=web.headers)
    with other.session_transaction() as session:
        foreign = session['config_csrf']
    with patch.object(admin, 'get_store') as store, patch.object(admin, 'edit_keywords') as edit, patch.object(admin, 'get_connection') as db, patch.object(admin.run_mgr, 'start_run') as start, patch('subprocess.run') as process:
        for token in ('', 'wrong-token', '非 ASCII', foreign):
            result = web.client.post('/admin/' + route, headers=web.headers,
                                     data={'date': '2099-01-01', 'action': 'confirm_merge', 'config_csrf': token})
            assert result.status_code == 400
            assert result.headers['Cache-Control'] == 'no-store'
        result = web.client.post('/admin/' + route, data={'config_csrf': web.csrf})
        assert result.status_code == 401
        for effect in (store, edit, db, start, process):
            effect.assert_not_called()
    assert ctx.store.read().token == ctx.first.token


def test_valid_csrf_still_allows_start_and_business_merge(ctx, web):
    import routes.admin as admin
    with patch.object(admin.run_mgr, 'start_run', return_value={}) as start:
        response = web.client.post('/admin/runs/start', data={'date': '2099-01-01', 'config_csrf': web.csrf}, headers=web.headers)
    assert response.status_code == 302
    start.assert_called_once_with(date='2099-01-01')
    with patch.object(admin, 'get_connection', return_value=Mock()), patch.object(admin, 'merge_secondary_labels', return_value=1) as merge:
        response = web.client.post('/admin/business-types', data={
            'action': 'confirm_merge', 'level1': '业务', 'retired_labels': '["旧标签"]',
            'target_new': '新标签', 'config_csrf': web.csrf,
        }, headers=web.headers)
    assert response.status_code == 302
    merge.assert_called_once()


def test_rendered_run_and_merge_forms_include_csrf(ctx, web):
    import routes.admin as admin
    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms = []
            self.current = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'form' and attrs.get('method', '').lower() == 'post':
                self.current = []
                self.forms.append(self.current)
            if tag == 'input' and self.current is not None and attrs.get('name') == 'config_csrf':
                self.current.append(attrs.get('value'))
        def handle_endtag(self, tag):
            if tag == 'form':
                self.current = None
    connection = Mock()
    connection.cursor.return_value.fetchall.return_value = []
    context = {'table': 'scored_news_test', 'schema_ready': True, 'stats': {}, 'aliases': [],
               'preview': {'level1': '业务', 'retired_labels': ['旧标签'], 'target_label': '新标签', 'affected_count': 1}}
    with patch.object(admin, 'get_connection', return_value=connection), patch.object(admin, '_business_types_context', return_value=context), patch.object(admin.run_mgr, 'get_status', return_value={}), patch.object(admin.run_mgr, 'get_run_history', return_value=[]):
        for route in ('runs', 'business-types'):
            response = web.client.get('/admin/' + route, headers=web.headers)
            assert response.status_code == 200
            parsed = Forms()
            parsed.feed(response.get_data(as_text=True))
            assert len(parsed.forms) == 2
            assert all(tokens == [web.csrf] for tokens in parsed.forms)


@pytest.mark.parametrize('files', ['absent', 'empty-directory', 'unscored-only'])
def test_empty_rescore_does_not_pin_or_freeze_next_run(ctx, web, monkeypatch, files):
    import routes.admin as admin
    project = ctx.root / 'empty-project'
    directory = project / 'output' / '2099-01-01'
    monkeypatch.setattr(admin, '__file__', str(project / 'routes/admin.py'))
    if files != 'absent':
        directory.mkdir(parents=True)
    if files == 'unscored-only':
        (directory / '2099-01-01_养老.json').write_text('[]')
    with patch.object(admin, 'get_connection') as db, patch('subprocess.run') as process:
        response = web.client.post('/admin/runs/rescore', data={'date': '2099-01-01', 'config_csrf': web.csrf}, headers=web.headers)
    assert response.status_code == 302
    db.assert_not_called()
    process.assert_not_called()
    with web.client.session_transaction() as session:
        assert any('未创建批次绑定' in text for _, text in session['_flashes'])
    with sqlite3.connect(ctx.store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batch_pins').fetchone()[0] == 0
    current = ctx.store.save(ctx.document, ctx.first.token, 'new configuration')
    if files != 'unscored-only':
        assert ctx.store.pin_batch(directory)[0].token == current.token


def test_rescore_selects_only_existing_topics_and_retains_their_revision(ctx, web, monkeypatch):
    import routes.admin as admin
    project = ctx.root / 'partial-project'
    directory = project / 'output' / '2099-01-01'
    directory.mkdir(parents=True)
    monkeypatch.setattr(admin, '__file__', str(project / 'routes/admin.py'))
    ctx.store.pin_batch(directory, '养老')
    (directory / '2099-01-01_养老_scored.json').write_text('[]')
    ctx.store.save(ctx.document, ctx.first.token, 'new active version')
    with patch.object(admin.run_mgr, 'get_status', return_value={}), patch.object(admin.run_mgr, 'get_run_history', return_value=[]), patch('subprocess.run', return_value=SimpleNamespace(returncode=1)) as process:
        response = web.client.post('/admin/runs/rescore', data={'date': '2099-01-01', 'config_csrf': web.csrf}, headers=web.headers)
    assert response.status_code == 200
    process.assert_called_once()
    assert process.call_args.kwargs['env']['SERP_CONFIG_REVISION'] == ctx.first.token
    with sqlite3.connect(ctx.store.path) as connection:
        assert connection.execute('SELECT keyword,revision FROM batch_pins').fetchall() == [('养老', ctx.first.revision)]


def test_rescore_does_not_implicitly_adopt_legacy_files(ctx):
    directory = ctx.root / 'output' / '2099-01-01'
    directory.mkdir(parents=True)
    (directory / '2099-01-01_养老_scored.json').write_text('[]')
    with pytest.raises(ConfigError, match='adopt-existing-config'):
        ctx.store.pin_batch(directory, scored_only=True)
    with sqlite3.connect(ctx.store.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM batch_pins').fetchone()[0] == 0


@pytest.mark.parametrize('route', ['config-history', 'config-export'])
def test_version_input_errors_are_distinct_from_corrupt_revision(ctx, web, route):
    for token, status in [('garbage', 400), (ctx.first.store_id + ':9999999999999999999999', 400),
                          (str(uuid.uuid4()) + ':1', 400), (ctx.first.store_id + ':999', 404)]:
        response = web.client.get('/admin/' + route, query_string={'version': token}, headers=web.headers)
        assert response.status_code == status
        assert response.headers['Cache-Control'] == 'no-store'
    ctx.store.save(ctx.document, ctx.first.token, 'keep active revision healthy')
    with sqlite3.connect(ctx.store.path) as connection:
        connection.execute('DROP TRIGGER revisions_no_update')
        connection.execute('UPDATE revisions SET sha256=? WHERE id=?', ('damaged', ctx.first.revision))
    response = web.client.get('/admin/' + route, query_string={'version': ctx.first.token}, headers=web.headers)
    assert response.status_code == 503
    assert response.headers['Cache-Control'] == 'no-store'


def test_admin_success_auth_failure_and_store_failure_are_not_cacheable(ctx, web, monkeypatch):
    for route in ('keywords', 'models', 'config-history', 'config-export'):
        response = web.client.get('/admin/' + route, headers=web.headers)
        assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    denied = web.client.get('/admin/models')
    assert denied.status_code == 401 and denied.headers['Cache-Control'] == 'no-store'
    missing = ctx.root / 'missing.sqlite3'
    monkeypatch.setenv('SERP_CONFIG_STORE', str(missing))
    failure = web.client.get('/admin/models', headers=web.headers)
    assert failure.status_code == 503 and failure.headers['Cache-Control'] == 'no-store'
    assert not missing.exists()


@pytest.mark.parametrize('secret', [None, '', 'test-only-stable-session-secret'])
def test_missing_or_empty_secret_warns_while_explicit_secret_is_used(ctx, secret):
    env = {**os.environ, 'PYTHONPATH': str(ROOT)}
    if secret is None:
        env.pop('FLASK_SECRET_KEY', None)
    else:
        env['FLASK_SECRET_KEY'] = secret
    code = '''
import os, runpy, socket, sys
from unittest.mock import patch
def blocked(*a, **k):
    raise AssertionError('No network allowed')
socket.socket.connect = blocked
socket.create_connection = blocked
with patch('dotenv.load_dotenv'):
    app = runpy.run_path(sys.argv[1], run_name='secret_probe')['app']
assert app.secret_key
if os.getenv('FLASK_SECRET_KEY'):
    assert app.secret_key == os.environ['FLASK_SECRET_KEY']
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'app.py')], env=env, cwd=ctx.root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert ('未设置 FLASK_SECRET_KEY' in result.stderr) == (not secret)
