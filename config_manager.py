"""Management operations on versioned data; never rewrites Python source files."""
import os

from config_schema import ConfigError
from runtime_config import get_snapshot, get_store


def read_keywords():
    return get_snapshot().document["settings"]["SEARCH_KEYWORDS"]


def write_keywords(keywords_dict, expected_version):
    document = get_store().read(expected_version).document
    document["settings"]["SEARCH_KEYWORDS"] = keywords_dict
    return get_store().save(document, expected_version, "更新关键词配置")


def edit_keywords(action, main_kw, search_list, expected_version, old_kw=None):
    document = get_store().read(expected_version).document
    keywords = document["settings"]["SEARCH_KEYWORDS"]
    routes = document["keyword_prompt_ids"]
    if action == "add":
        if main_kw in keywords:
            raise ConfigError("主关键词已存在，请使用编辑操作")
        keywords[main_kw] = search_list
    elif action == "delete":
        if main_kw not in keywords:
            raise ConfigError("主关键词已不存在，请刷新")
        del keywords[main_kw]
        # Retain its prompt binding so re-adding a topic never loses its tuned rules.
    elif action == "edit":
        if old_kw not in keywords:
            raise ConfigError("原主关键词不存在，请刷新")
        retained_rules = document["settings"]["NEWS_RULE_BASED_SCORING"]
        if main_kw != old_kw and (main_kw in keywords or main_kw in routes or any(rule["main_keyword"] == main_kw for rule in retained_rules)):
            raise ConfigError("目标主关键词、专用提示词映射或保留的评分规则已存在，请先核对冲突")
        document["settings"]["SEARCH_KEYWORDS"] = {
            main_kw if key == old_kw else key: search_list if key == old_kw else value
            for key, value in keywords.items()
        }
        if old_kw in routes:
            document["keyword_prompt_ids"] = {main_kw if key == old_kw else key: value for key, value in routes.items()}
        for rule in document["settings"]["NEWS_RULE_BASED_SCORING"]:
            if rule["main_keyword"] == old_kw:
                rule["main_keyword"] = main_kw
    else:
        raise ConfigError("未知关键词操作")
    return get_store().save(document, expected_version, f"关键词 {action}: {main_kw}")


def read_model_config():
    return {stage: {**profile, "api_key_set": bool(os.getenv(profile["api_key_env"]))}
            for stage, profile in get_snapshot().document["models"].items()}
