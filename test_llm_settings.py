"""LLM 环境变量配置与连通性检查的离线回归；禁止真实网络。"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from config_schema import ConfigError
from llm_settings import DEFAULT_TIMEOUT, all_stage_settings, check_stages, stage_profile

LLM_TEST_ENV = {
    "LLM_BASE_URL": "http://llm-test.invalid/v1",
    "LLM_API_KEY": "offline-test-key",
    "LLM_SCORING_MODEL": "offline-test-model",
    "LLM_ITEM_SUMMARIZER_MODEL": "offline-test-model",
    "LLM_REGION_MODEL": "offline-test-model",
    "LLM_SCORING_TEMPERATURE": "1",
    "LLM_REGION_TEMPERATURE": "0.2",
}


@pytest.fixture
def clean_llm_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("LLM_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def setenv_all(monkeypatch, values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_complete_env_parses_exactly(clean_llm_env):
    for key, value in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, value)
    settings = all_stage_settings()
    assert settings["scoring"] == {
        "api_key": "offline-test-key", "base_url": "http://llm-test.invalid/v1",
        "model": "offline-test-model",
        "parameters": {"stream": False, "timeout": DEFAULT_TIMEOUT, "temperature": 1}}
    assert "temperature" not in settings["item_summarizer"]["parameters"]
    assert settings["region"]["parameters"]["temperature"] == 0.2


def test_global_model_falls_back_and_stage_override_wins(clean_llm_env):
    setenv_all(clean_llm_env, {"LLM_BASE_URL": "http://llm-test.invalid/v1", "LLM_API_KEY": "k", "LLM_MODEL": "global-model"})
    assert stage_profile("scoring")["model"] == "global-model"
    clean_llm_env.setenv("LLM_REGION_MODEL", "region-override")
    assert stage_profile("region")["model"] == "region-override"


def test_stage_base_url_and_key_override_globals(clean_llm_env):
    setenv_all(clean_llm_env, {"LLM_BASE_URL": "http://global.invalid/v1", "LLM_API_KEY": "global-key",
                               "LLM_MODEL": "m", "LLM_SCORING_BASE_URL": "http://stage.invalid/v1",
                               "LLM_SCORING_API_KEY": "stage-key"})
    profile = stage_profile("scoring")
    assert profile["base_url"] == "http://stage.invalid/v1"
    assert profile["api_key"] == "stage-key"
    assert stage_profile("region")["api_key"] == "global-key"


def test_missing_model_reports_variable_name(clean_llm_env):
    setenv_all(clean_llm_env, {"LLM_BASE_URL": "http://llm-test.invalid/v1", "LLM_API_KEY": "k"})
    with pytest.raises(ConfigError, match="LLM_SCORING_MODEL"):
        stage_profile("scoring")


def test_missing_base_url_reports_variable(clean_llm_env):
    clean_llm_env.setenv("LLM_SCORING_MODEL", "m")
    with pytest.raises(ConfigError, match="LLM_BASE_URL"):
        stage_profile("scoring")


@pytest.mark.parametrize("variable,value,match", [
    ("LLM_SCORING_TEMPERATURE", "3", "0–2"),
    ("LLM_SCORING_TIMEOUT", "0", "正数"),
    ("LLM_SCORING_SEED", "abc", "必须为数值"),
    ("LLM_SCORING_MAX_TOKENS", "1.5", "必须为整数"),
    ("LLM_REGION_TOP_P", "1.5", "0–1"),
])
def test_invalid_values_fail_as_configuration_errors(clean_llm_env, variable, value, match):
    for key, val in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, val)
    stage = "region" if variable.startswith("LLM_REGION") else "scoring"
    clean_llm_env.setenv(variable, value)
    with pytest.raises(ConfigError, match=match):
        stage_profile(stage)


@pytest.mark.parametrize("variable", ["LLM_SCORING_TEMPERATUR", "LLM_SCORING_STREAM", "LLM_REGION_MODELX"])
def test_unknown_stage_variables_are_rejected_not_ignored(clean_llm_env, variable):
    for key, val in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, val)
    clean_llm_env.setenv(variable, "anything")
    stage = "region" if variable.startswith("LLM_REGION") else "scoring"
    with pytest.raises(ConfigError, match=variable):
        stage_profile(stage)


def test_empty_values_are_treated_as_unset(clean_llm_env):
    for key, val in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, val)
    clean_llm_env.setenv("LLM_SCORING_TEMPERATURE", "")
    assert "temperature" not in stage_profile("scoring")["parameters"]


def test_probe_sends_fixed_text_and_exact_parameters(clean_llm_env):
    for key, val in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, val)
    import llm_settings
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
    with patch("openai.OpenAI") as factory:
        factory.return_value.__enter__.return_value = client
        result = llm_settings._check_stage("scoring")
    assert result["ok"] is True
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs == dict(model="offline-test-model", stream=False, timeout=DEFAULT_TIMEOUT, temperature=1,
                          messages=[{"role": "user", "content": "Reply with OK."}])
    _, client_kwargs = factory.call_args
    assert client_kwargs["max_retries"] == 0 and client_kwargs["base_url"] == "http://llm-test.invalid/v1"


def test_probe_redacts_provider_exception(clean_llm_env):
    for key, val in LLM_TEST_ENV.items():
        clean_llm_env.setenv(key, val)
    import llm_settings
    with patch("openai.OpenAI", side_effect=RuntimeError("secret-key leaked in provider message")):
        result = llm_settings._check_stage("scoring")
    assert result["ok"] is False
    assert "secret-key" not in result["error"]


def test_probe_reports_missing_key_without_network(clean_llm_env):
    setenv_all(clean_llm_env, {"LLM_BASE_URL": "http://llm-test.invalid/v1", "LLM_SCORING_MODEL": "m"})
    import llm_settings
    with patch("openai.OpenAI", side_effect=AssertionError("缺 Key 时不得发起请求")):
        result = llm_settings._check_stage("scoring")
    assert result["ok"] is False and "LLM API Key" in result["error"]


def test_check_stages_survives_missing_configuration(clean_llm_env):
    results = check_stages()
    assert list(results) == ["scoring", "item_summarizer", "region"]
    assert all(result["ok"] is False and result["error"] for result in results.values())


def test_check_model_cli_loads_dotenv_and_never_opens_store(tmp_path, monkeypatch, clean_llm_env):
    import config_cli
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "LLM_BASE_URL=http://dotenv.invalid/v1\nLLM_API_KEY=dotenv-key\nLLM_SCORING_MODEL=d-model\n")
    monkeypatch.setattr(config_cli, "__file__", str(project / "config_cli.py"))
    monkeypatch.delenv("SERP_CONFIG_STORE", raising=False)
    seen = {}

    def fake_check(stages=None):
        seen["stages"] = stages
        seen["base_url"] = os.environ["LLM_BASE_URL"]
        return {"scoring": {"ok": True, "model": "d-model"}}

    with patch("llm_settings.check_stages", fake_check), \
         patch("config_cli.ConfigStore", side_effect=AssertionError("check-model 不得打开配置存储")):
        result = config_cli.run(config_cli.parser().parse_args(["check-model"]))
    assert result == {"scoring": {"ok": True, "model": "d-model"}}
    assert seen["stages"] is None
    assert seen["base_url"] == "http://dotenv.invalid/v1"


def test_check_model_cli_single_stage_dispatch(tmp_path, monkeypatch, clean_llm_env):
    import config_cli
    seen = {}
    with patch("llm_settings.check_stages", lambda stages=None: seen.setdefault("stages", stages) or {}), \
         patch("config_cli.ConfigStore", side_effect=AssertionError):
        config_cli.run(config_cli.parser().parse_args(["check-model", "--stage", "region"]))
    assert seen["stages"] == ["region"]
