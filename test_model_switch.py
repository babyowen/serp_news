import copy
from unittest.mock import patch

import pytest

from config_schema import ConfigConflict, ConfigError
from config_store import ConfigStore, read_document
from model_switch import switch
from pathlib import Path


@pytest.fixture
def setup(tmp_path):
    store = ConfigStore(tmp_path / 'live.sqlite3')
    doc = read_document(Path(__file__).with_name('config_defaults.json'))
    for profile in doc['models'].values():
        profile['model'] = 'deepseek-v4-flash'
    first, _ = store.initialize(doc, {'kind': 'test'})
    return store, first, tmp_path / 'backup.sqlite3'


def test_preview_is_offline_and_read_only(setup):
    store, first, backup = setup
    with patch('model_switch.probe') as probe:
        result = switch(store)
    assert result['changes'] and not result['applied']
    probe.assert_not_called()
    assert store.read().token == first.token
    assert not backup.exists()


def test_apply_preserves_everything_except_model_and_old_batch(setup):
    store, first, backup = setup
    root = backup.parent / '2099-01-01'
    store.pin_batch(root)
    with patch('model_switch.probe') as probe:
        result = switch(store, apply=True, expected_version=first.token, backup=backup, note='switch')
    assert result['applied'] and probe.call_count == 3
    expected = copy.deepcopy(first.document)
    for profile in expected['models'].values():
        profile['model'] = 'deepseek-flash'
    assert store.read().document == expected
    assert ConfigStore(backup).read().document == first.document
    assert store.pin_batch(root)[0].token == first.token
    assert store.pin_batch(backup.parent / '2099-01-02')[0].token == result['version']
    with patch('model_switch.probe') as probe:
        switch(store, apply=True, expected_version=result['version'], backup=backup, note='again')
    probe.assert_not_called()
    assert store.read().token == result['version']


def test_failed_probe_does_not_publish(setup):
    store, first, backup = setup
    with patch('model_switch.probe', side_effect=ConfigError('failed')):
        with pytest.raises(ConfigError):
            switch(store, apply=True, expected_version=first.token, backup=backup, note='switch')
    assert store.read().token == first.token
    assert backup.exists()


def test_concurrent_edit_is_not_overwritten(setup):
    store, first, backup = setup
    def concurrent(_):
        if store.read().token == first.token:
            doc = first.document
            doc['models']['scoring']['parameters']['temperature'] = 0.5
            store.save(doc, first.token, 'concurrent')
    with patch('model_switch.probe', side_effect=concurrent):
        with pytest.raises(ConfigConflict):
            switch(store, apply=True, expected_version=first.token, backup=backup, note='switch')
    assert store.read().document['models']['scoring']['model'] == 'deepseek-v4-flash'
    assert store.read().document['models']['scoring']['parameters']['temperature'] == 0.5


def test_backup_collision_prevents_calls(setup):
    store, first, backup = setup
    backup.write_text('keep')
    with patch('model_switch.probe') as probe:
        with pytest.raises(ConfigError):
            switch(store, apply=True, expected_version=first.token, backup=backup, note='switch')
    probe.assert_not_called()
    assert backup.read_text() == 'keep'
    assert store.read().token == first.token


def test_missing_apply_guards(setup):
    store, first, _ = setup
    with pytest.raises(ConfigError):
        switch(store, apply=True)
    assert store.read().token == first.token


def test_foreign_endpoint_is_rejected(setup):
    store, first, _ = setup
    doc = first.document
    doc['models']['scoring']['base_url'] = 'https://example.com'
    store.save(doc, first.token, 'other provider')
    with pytest.raises(ConfigError):
        switch(store)


def test_probe_sends_fixed_text_and_exact_parameters(setup, monkeypatch):
    from model_switch import probe
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    profile = setup[1].document['models']['scoring']
    profile['model'] = 'deepseek-flash'
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='OK'))])
    with patch('openai.OpenAI') as factory:
        factory.return_value.__enter__.return_value = client
        probe(profile)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs == dict(model='deepseek-flash', **profile['parameters'],
                          messages=[{'role': 'user', 'content': 'Reply with OK.'}])


def test_probe_redacts_provider_exception(setup, monkeypatch):
    from model_switch import probe
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'secret')
    with patch('openai.OpenAI', side_effect=RuntimeError('secret in provider exception')):
        with pytest.raises(ConfigError) as error:
            probe(setup[1].document['models']['scoring'])
    assert 'secret' not in str(error.value)


def test_cli_preview_without_network(setup):
    import config_cli
    store, first, _ = setup
    args = config_cli.parser().parse_args(['--store', str(store.path), 'switch-model'])
    with patch('model_switch.probe') as probe:
        assert config_cli.run(args)['current'] == first.token
    probe.assert_not_called()


@pytest.mark.parametrize('selection', ['dotenv', 'environment', 'explicit'])
def test_cli_store_selection_loads_real_dotenv_first(setup, monkeypatch, selection):
    import os
    import config_cli
    store, first, backup = setup
    project = backup.parent / 'project'
    project.mkdir()
    other = ConfigStore(backup.parent / 'other.sqlite3')
    other_first, _ = other.initialize(first.document, {'kind': 'other'})
    (project / '.env').write_text(
        f'SERP_CONFIG_STORE={store.path}\nDEEPSEEK_API_KEY=dotenv-test-key\n')
    monkeypatch.setattr(config_cli, '__file__', str(project / 'config_cli.py'))
    monkeypatch.delenv('SERP_CONFIG_STORE', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    # Invocation from another directory must still read the project's .env.
    monkeypatch.chdir(backup.parent)
    argv = ['switch-model']
    expected = first.token
    if selection == 'environment':
        monkeypatch.setenv('SERP_CONFIG_STORE', str(other.path))
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'environment-test-key')
        expected = other_first.token
    elif selection == 'explicit':
        argv = ['--store', str(other.path), 'switch-model']
        expected = other_first.token
    with patch('model_switch.probe') as probe:
        result = config_cli.run(config_cli.parser().parse_args(argv))
    assert result['current'] == expected
    assert os.environ['DEEPSEEK_API_KEY'] == (
        'environment-test-key' if selection == 'environment' else 'dotenv-test-key')
    probe.assert_not_called()
    assert store.read().token == first.token
    assert other.read().token == other_first.token
