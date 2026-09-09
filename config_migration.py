"""Extract legacy configuration with AST only; source files are never imported."""
import ast
import base64
import hashlib
import json
from pathlib import Path

from config_schema import ConfigError, REQUIRED_SETTINGS, REQUEST_FIELDS, validate

_ALLOWED_IMPORTS = {ast.dump(ast.parse(statement).body[0]) for statement in (
    "import os", "from dotenv import load_dotenv",
)}
_EMOJI_HELPER = ast.dump(ast.parse("""
def get_emoji(emoji_char):
    if USE_EMOJI_OUTPUT:
        return emoji_char
    else:
        return EMOJI_MAP.get(emoji_char, emoji_char)
""").body[0])

# Audited caller structure from 6e9442fbc7f578b8d90efef32a6278a0b9798ee2.
# Only already-extracted model/request literals and docstrings are normalized.
# Do not refresh these fingerprints just to accept unreviewed production code.
_STAGE_CONTRACTS = {
    "news_scorer.py": "b25454bcd201fec29db95de3ba5c2d08fc47e65116629bb033871ce278cb56d6",
    "news_item_summarizer.py": "f8b30837b762a5ad35ccf9ae5b61efc0115bf09589d7e561efdf9e698efe48f6",
    "news_region_utils.py": "9e35d31ed6ca80847d746e8cb2431aa35b412666716615caa3f5c506591a6daf",
}


def _tree(path, raw=None):
    try:
        return ast.parse(raw.decode("utf-8") if raw is not None else path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ConfigError(f"无法解析旧文件 {path.name}: {exc}") from exc


def _literal(node, label):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ConfigError(f"{label} 不是可安全提取的常量，需要先人工核对；未执行任何旧代码") from exc


def _env(node, label, default=False):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            and node.func.attr == "getenv" and not node.keywords
            and len(node.args) == (2 if default else 1)):
        name = _literal(node.args[0], label)
        return {"env": name, "default": _literal(node.args[1], label)} if default else name
    if default and isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {"value": node.value}
    raise ConfigError(f"{label} 无法按环境变量引用安全迁移；请检查是否含硬编码密钥或自定义表达式")


def _dict_nodes(node, label):
    if not isinstance(node, ast.Dict) or any(key is None for key in node.keys):
        raise ConfigError(f"{label} 必须是显式字典")
    pairs = [(_literal(k, label), v) for k, v in zip(node.keys, node.values)]
    if len({k for k, _ in pairs}) != len(pairs):
        raise ConfigError(f"{label} 包含重复字段，请先人工确认")
    return dict(pairs)


def _ast_shape(node):
    if isinstance(node, ast.AST):
        return [type(node).__name__, [(name, _ast_shape(value)) for name, value in ast.iter_fields(node)
                                     if not (name == "type_params" and not value)]]
    if isinstance(node, list):
        return [_ast_shape(value) for value in node]
    return repr(node)


def _stage_fingerprint(tree, call, model_definition=None):
    """Normalize only values whose semantics the migration has already extracted."""
    if model_definition is not None:
        model_definition.value = ast.Constant(value="<migrated-model>")
    else:
        next(kw for kw in call.keywords if kw.arg == "model").value = ast.Constant(value="<migrated-model>")
    call.keywords = [kw for kw in call.keywords if kw.arg not in REQUEST_FIELDS]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:]
    return hashlib.sha256(json.dumps(_ast_shape(tree), separators=(",", ":")).encode("utf-8")).hexdigest()


def _model(path, base_url, raw=None):
    tree = _tree(path, raw)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "create"
             and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "completions"]
    if len(calls) != 1:
        raise ConfigError(f"{path.name} 需要恰好一个可识别的模型调用，无法自动迁移自定义流程")
    call = calls[0]
    arguments = {kw.arg: kw.value for kw in call.keywords}
    if call.args or len(arguments) != len(call.keywords) or None in arguments or set(arguments) - {"model", "messages", *REQUEST_FIELDS}:
        raise ConfigError(f"{path.name} 有不支持的动态请求参数")
    if "model" not in arguments:
        raise ConfigError(f"{path.name} 缺少模型参数")
    expression = arguments["model"]
    model_definition = None
    if isinstance(expression, ast.Name):
        definitions = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == expression.id for t in n.targets)]
        if len(definitions) != 1:
            raise ConfigError(f"{path.name} 的模型选择不是唯一常量")
        model_definition = definitions[0]
        expression = model_definition.value
    # Verify endpoint/key use rather than silently assuming a modified pool call.
    pools = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "get_client"]
    if not pools or any(len(n.args) < 2 or not isinstance(n.args[0], ast.Name)
                        or n.args[0].id != "DEEPSEEK_API_KEY" or not isinstance(n.args[1], ast.Name)
                        or n.args[1].id != "DEEPSEEK_BASE_URL" for n in pools):
        raise ConfigError(f"{path.name} 的模型连接来源有自定义修改，需要人工核对")
    profile = {
        "platform": "deepseek", "model": _literal(expression, f"{path.name}.model"),
        "base_url": base_url, "api_key_env": "DEEPSEEK_API_KEY",
        "parameters": {name: _literal(value, f"{path.name}.{name}") for name, value in arguments.items() if name in REQUEST_FIELDS},
    }
    if _stage_fingerprint(tree, call, model_definition) != _STAGE_CONTRACTS.get(path.name):
        raise ConfigError(f"{path.name} 的调用源码超出已审核结构，可能覆盖提示词、接口或模型；请先人工核对并更新迁移适配器，未执行旧代码")
    return profile


def extract_legacy(source_dir):
    """Return a validated document and byte-exact source archive for explicit init."""
    root = Path(source_dir).resolve()
    config_path = root / "config.py"
    stage_files = {"scoring": "news_scorer.py", "item_summarizer": "news_item_summarizer.py", "region": "news_region_utils.py"}
    try:
        originals = {name: (root / name).read_bytes() for name in ["config.py", *stage_files.values(), "config_grab_rules.py"]}
    except OSError as exc:
        raise ConfigError(f"无法读取完整迁移源: {exc}") from exc
    tree = _tree(config_path, originals["config.py"])
    assignments = {}
    helpers = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name) or target.id in assignments:
                    raise ConfigError("旧配置含无法识别或重复的赋值，请先人工核对")
                assignments[target.id] = node.value
        elif isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "load_dotenv" and not node.value.args and not node.value.keywords):
                continue
            raise ConfigError("旧配置含额外顶层执行逻辑，无法确认实际生效值；未执行任何代码，请人工核对")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if ast.dump(node) not in _ALLOWED_IMPORTS:
                raise ConfigError("旧配置含自定义导入，可能改变实际生效值，请先人工核对")
        elif isinstance(node, ast.FunctionDef):
            # Even unused definitions can execute decorators/default expressions.
            # Only the known pure compatibility helper can be migrated unchanged.
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                node.body = node.body[1:]
            if node.name in helpers or ast.dump(node) != _EMOJI_HELPER:
                raise ConfigError("旧配置含自定义函数或装饰器，无法确认实际生效值，请先人工核对")
            helpers.add(node.name)
        else:
            raise ConfigError("旧配置含非声明式逻辑，需要人工核对")
    handled = {"DEFAULT_KEYWORDS", "DEFAULT_KEYWORD", "KEYWORD_SPECIFIC_SYSTEM_PROMPTS", "NEWS_SUMMARY_MODELS", "NEWS_SUMMARY_PLATFORM", "NEWS_SUMMARY_MODEL"}
    missing = (REQUIRED_SETTINGS | handled) - set(assignments)
    if missing:
        raise ConfigError(f"旧配置缺少字段（或已经是新读取入口）: {', '.join(sorted(missing))}")
    prompts = {}
    settings = {}
    for name, expression in assignments.items():
        if name in {"API_KEY", "SERPAPI_KEY", "GNEWS_API_KEY", "DEEPSEEK_API_KEY"}:
            expected = "SERPAPI_KEY" if name == "API_KEY" else name
            if _env(expression, name) != expected:
                raise ConfigError(f"{name} 的环境变量引用与当前兼容入口不一致")
        elif name in handled:
            continue
        elif "PROMPT" in name or "SYSTEM_MSG" in name:
            prompts[name] = {"text": _literal(expression, name), "status": "archived" if name.startswith("NEWS_SUMMARY_") else "active"}
        elif name in REQUIRED_SETTINGS:
            settings[name] = _literal(expression, name)
        else:
            raise ConfigError(f"发现未支持的旧配置项 {name}，拒绝静默遗漏")
    routes = {}
    for keyword, value in _dict_nodes(assignments["KEYWORD_SPECIFIC_SYSTEM_PROMPTS"], "专用提示词映射").items():
        if not isinstance(value, ast.Name) or value.id not in prompts:
            raise ConfigError(f"{keyword} 专用提示词需引用已命名提示词")
        routes[keyword] = value.id
    legacy_models = {}
    for platform, variants in _dict_nodes(assignments["NEWS_SUMMARY_MODELS"], "归档模型").items():
        legacy_models[platform] = {}
        for name, expression in _dict_nodes(variants, platform).items():
            profile = _dict_nodes(expression, name)
            if set(profile) != {"api_key", "base_url", "model"}:
                raise ConfigError(f"{name} 存在未支持的归档模型字段")
            legacy_models[platform][name] = {
                "api_key_env": _env(profile["api_key"], name),
                "base_url": _literal(profile["base_url"], name), "model": _literal(profile["model"], name),
            }
    document = validate({
        "schema_version": 1, "prompts": prompts, "settings": settings,
        "keyword_prompt_ids": routes,
        "models": {stage: _model(root / name, settings["DEEPSEEK_BASE_URL"], originals[name]) for stage, name in stage_files.items()},
        "legacy": {"models": legacy_models, "selection": {
            "platform": _env(assignments["NEWS_SUMMARY_PLATFORM"], "NEWS_SUMMARY_PLATFORM", default=True),
            "model": _env(assignments["NEWS_SUMMARY_MODEL"], "NEWS_SUMMARY_MODEL", default=True),
        }},
    })
    files = {}
    for name in ["config.py", *stage_files.values(), "config_grab_rules.py"]:
        path = root / name
        if not path.is_file():
            raise ConfigError(f"缺少迁移源文件 {name}")
        raw = originals[name]
        if path.read_bytes() != raw:
            raise ConfigError(f"迁移期间 {name} 发生变化，请重新获取一致快照")
        files[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode("ascii")}
    # Existing primary list derivations must not hide a custom production selection.
    expected = {"DEFAULT_KEYWORDS": "list(SEARCH_KEYWORDS.keys())", "DEFAULT_KEYWORD": "DEFAULT_KEYWORDS[0]"}
    for name, expression in expected.items():
        if name not in assignments or ast.dump(assignments[name]) != ast.dump(ast.parse(expression, mode="eval").body):
            raise ConfigError(f"{name} 有自定义逻辑，不能按默认派生规则迁移")
    return document, {"kind": "legacy", "files": files}
