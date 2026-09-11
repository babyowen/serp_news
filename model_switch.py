"""Explicit, version-guarded switch of all live DeepSeek stages."""
import copy
import os

from config_schema import ConfigConflict, ConfigError, differences, validate

TARGET = 'deepseek-flash'


def probe(profile):
    """Small real request; never send production prompts or article text."""
    from openai import OpenAI
    key = os.getenv(profile['api_key_env'])
    if not key:
        raise ConfigError('缺少模型 API Key；未修改配置')
    try:
        with OpenAI(api_key=key, base_url=profile['base_url'],
                    timeout=60, max_retries=0) as client:
            response = client.chat.completions.create(
                model=profile['model'], **profile['parameters'],
                messages=[{'role': 'user', 'content': 'Reply with OK.'}])
            if not response.choices or not response.choices[0].message.content:
                raise ValueError('empty response')
    except Exception as exc:
        # Provider exception messages can contain credentials or request details.
        status = getattr(exc, 'status_code', None)
        raise ConfigError(f'模型检查失败（{type(exc).__name__}, HTTP {status}）；未修改配置') from None


def switch(store, *, apply=False, expected_version=None, backup=None, note=None):
    current = store.read()
    if expected_version and expected_version != current.token:
        raise ConfigConflict('配置版本已变化，请重新预览')
    candidate = copy.deepcopy(current.document)
    for stage, profile in candidate['models'].items():
        if (profile['platform'] != 'deepseek'
                or profile['base_url'].rstrip('/') not in
                ('https://api.deepseek.com', 'https://api.deepseek.com/v1')
                or profile['api_key_env'] != 'DEEPSEEK_API_KEY'):
            raise ConfigError(f'{stage} 不是官方 DeepSeek 配置，请先人工核对；未修改配置')
        profile['model'] = TARGET
    validate(candidate)
    result = {'current': current.token, 'target_model': TARGET,
              'changes': differences(current.document, candidate), 'applied': False}
    if not apply:
        return result
    if not expected_version or not backup or not note or not note.strip():
        raise ConfigError('--apply 必须提供 --expected-version、--backup 和 --note')
    if candidate == current.document:
        result['unchanged'] = True
        return result
    # Backup must succeed before paid checks or publishing. It never overwrites.
    store.backup(backup)
    for profile in candidate['models'].values():
        probe(profile)
    saved = store.save(candidate, expected_version, note)
    result.update(applied=True, version=saved.token, backup=str(backup))
    return result
