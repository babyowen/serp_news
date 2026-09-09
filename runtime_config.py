"""Process-pinned and request-scoped access to the external configuration store."""
from contextvars import ContextVar
import os
from pathlib import Path
import sys

from config_store import ConfigStore

_request_snapshot = ContextVar("configuration_snapshot", default=None)
_process_snapshot = None
_process_key = None
_environment_loaded = False


def load_environment():
    global _environment_loaded
    if not _environment_loaded:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
        _environment_loaded = True


def get_store():
    load_environment()
    return ConfigStore()


def get_snapshot():
    global _process_key, _process_snapshot
    request = _request_snapshot.get()
    if request is not None:
        return request
    store = get_store()
    token = os.environ.get("SERP_CONFIG_REVISION") or None
    key = (str(store.path), token)
    if _process_snapshot is None or key != _process_key:
        _process_snapshot = store.read(token)
        _process_key = key
        print(f"[CONFIG] version={_process_snapshot.token} sha256={_process_snapshot.sha256}", file=sys.stderr)
    return _process_snapshot


def bind_process(snapshot):
    global _process_snapshot, _process_key
    store = get_store()
    # Prevent accidentally pinning a snapshot from a different environment.
    store.read(snapshot.token)
    os.environ["SERP_CONFIG_STORE"] = str(store.path)
    os.environ["SERP_CONFIG_REVISION"] = snapshot.token
    _process_snapshot = snapshot
    _process_key = (str(store.path), snapshot.token)


def begin_request():
    return _request_snapshot.set(get_store().read())


def end_request(token):
    _request_snapshot.reset(token)


def child_environment(snapshot=None):
    snapshot = snapshot or get_snapshot()
    store = get_store()
    store.read(snapshot.token)
    return {**os.environ, "SERP_CONFIG_STORE": str(store.path), "SERP_CONFIG_REVISION": snapshot.token}


def value(name):
    document = get_snapshot().document
    if name in document["prompts"]:
        return document["prompts"][name]["text"]
    if name in document["settings"]:
        return document["settings"][name]
    if name == "DEFAULT_KEYWORDS":
        return list(document["settings"]["SEARCH_KEYWORDS"])
    if name == "DEFAULT_KEYWORD":
        return next(iter(document["settings"]["SEARCH_KEYWORDS"]))
    if name == "KEYWORD_SPECIFIC_SYSTEM_PROMPTS":
        return {keyword: document["prompts"][prompt_id]["text"] for keyword, prompt_id in document["keyword_prompt_ids"].items()}
    if name == "NEWS_SUMMARY_MODELS":
        return {platform: {model: {"api_key": os.getenv(profile["api_key_env"]), "base_url": profile["base_url"], "model": profile["model"]}
                           for model, profile in variants.items()} for platform, variants in document["legacy"]["models"].items()}
    if name in ("NEWS_SUMMARY_PLATFORM", "NEWS_SUMMARY_MODEL"):
        selector = document["legacy"]["selection"]["platform" if name.endswith("PLATFORM") else "model"]
        return selector["value"] if "value" in selector else os.getenv(selector["env"], selector["default"])
    raise AttributeError(name)


def model_profile(stage):
    return get_snapshot().document["models"][stage]


def model_credentials(stage):
    profile = model_profile(stage)
    return os.getenv(profile["api_key_env"]), profile["base_url"]


def model_arguments(stage):
    profile = model_profile(stage)
    return {"model": profile["model"], **profile["parameters"]}
