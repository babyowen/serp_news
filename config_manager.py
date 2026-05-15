"""配置文件读写工具 — 供前端管理页面调用，直接读写 config.py"""
import re
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.py")


def read_keywords():
    """读取 config.py 中的 SEARCH_KEYWORDS，返回 {main: [search, ...]}"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # 提取 SEARCH_KEYWORDS = { ... }
    match = re.search(r"SEARCH_KEYWORDS\s*=\s*\{", content)
    if not match:
        return {}
    start = match.end()
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    dict_str = content[start - 1 : i]
    try:
        return eval(dict_str)
    except Exception:
        return {}


def write_keywords(keywords_dict):
    """修改 config.py 中的 SEARCH_KEYWORDS 定义"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # 格式化新字典
    lines = []
    for main_kw, search_list in keywords_dict.items():
        lines.append(f'    "{main_kw}": {search_list},')
    new_dict = "SEARCH_KEYWORDS = {\n" + "\n".join(lines) + "\n}"
    pattern = r"SEARCH_KEYWORDS\s*=\s*\{[^}]*\}"
    content = re.sub(pattern, new_dict, content, count=1)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def read_model_config():
    """读取模型相关配置，返回结构化数据"""
    from config import (
        DEEPSEEK_API_KEY,
        DEEPSEEK_BASE_URL,
    )

    return {
        "scoring": {
            "platform": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": DEEPSEEK_BASE_URL,
            "api_key_set": bool(DEEPSEEK_API_KEY),
        },
        "item_summarizer": {
            "platform": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": DEEPSEEK_BASE_URL,
            "api_key_set": bool(DEEPSEEK_API_KEY),
        },
        "region": {
            "platform": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": DEEPSEEK_BASE_URL,
            "api_key_set": bool(DEEPSEEK_API_KEY),
        },
    }
