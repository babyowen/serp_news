# -*- coding: utf-8 -*-
# =========================================
# 新闻摘要主程序
# 主要功能：加载新闻、过滤、去重、构建prompt、调用大模型总结、保存结果、记录日志
# =========================================
import os
import sys
# 设置tokenizer并行环境变量，避免警告
os.environ["TOKENIZERS_PARALLELISM"] = "false"
# 设置transformers日志级别，抑制PyTorch相关警告
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
import json
from datetime import datetime, timedelta
from config import (
    NEWS_SUMMARY_MODELS,
    NEWS_SUMMARY_SYSTEM_PROMPT,
    NEWS_SUMMARY_USER_PROMPT,
    NEWS_SUMMARY_RESULT_FILENAME,
    NEWS_SUMMARY_PLATFORM,
    NEWS_SUMMARY_MODEL,
    DEFAULT_KEYWORDS,
    NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT,
    NEWS_SUMMARY_JUDGE_USER_PROMPT,
    NEWS_SUMMARY_OPTIMIZE_SYSTEM_PROMPT,
    NEWS_SUMMARY_OPTIMIZE_USER_PROMPT,
    NEWS_SUMMARY_HOTSPOT_SYSTEM_PROMPT,
    NEWS_SUMMARY_HOTSPOT_USER_PROMPT,
    NEWS_SUMMARY_FILTER_SOURCEAPI,
)

# ========== 新增：SSL连接池管理 ==========
class OpenAIClientPool:
    """OpenAI客户端连接池管理器，用于优化SSL连接处理"""
    
    def __init__(self):
        self._clients = {}
        self._client_usage_count = {}
        self._max_usage_per_client = 50  # 每个客户端最大使用次数
    
    def get_client(self, platform, model_name):
        """获取或创建OpenAI客户端"""
        key = f"{platform}-{model_name}"
        model_cfg = NEWS_SUMMARY_MODELS[platform][model_name]
        
        # 检查是否需要创建新客户端
        if (key not in self._clients or 
            self._client_usage_count.get(key, 0) >= self._max_usage_per_client):
            
            # 关闭旧客户端（如果存在）
            if key in self._clients:
                self._close_client(key)
            
            # 创建新客户端
            from openai import OpenAI
            self._clients[key] = OpenAI(
                api_key=model_cfg['api_key'], 
                base_url=model_cfg['base_url']
            )
            self._client_usage_count[key] = 0
        
        # 增加使用计数
        self._client_usage_count[key] += 1
        return self._clients[key]
    
    def _close_client(self, key):
        """安全关闭指定客户端"""
        if key in self._clients:
            try:
                client = self._clients[key]
                if hasattr(client, 'close'):
                    client.close()
                elif hasattr(client, '_client') and hasattr(client._client, 'close'):
                    client._client.close()
            except Exception as e:
                pass  # 静默处理关闭异常
            finally:
                del self._clients[key]
                if key in self._client_usage_count:
                    del self._client_usage_count[key]
    
    def close_all(self):
        """关闭所有客户端连接"""
        for key in list(self._clients.keys()):
            self._close_client(key)
    
    def get_stats(self):
        """获取连接池状态统计"""
        return {
            'active_clients': len(self._clients),
            'usage_counts': self._client_usage_count.copy()
        }

# 全局连接池实例
_client_pool = OpenAIClientPool()

def clean_unicode_for_console(text):
    """
    清理文本中的特殊Unicode字符，避免Windows GBK编码错误
    """
    if not text:
        return text
    
    # 常见的需要替换的Unicode字符
    replacements = {
        '\xa9': '(C)',      # 版权符号
        '\u2714': '[OK]',   # 勾选符号
        '\u261e': '[->]',   # 手指符号
        '\U0001f552': '[TIME]',  # 时钟emoji
        '\U0001f4f0': '[NEWS]',  # 新闻emoji
        '\U0001f4ca': '[CHART]', # 图表emoji
        '\U0001f4f1': '[PHONE]', # 手机emoji
        '\U0001f4bb': '[PC]',    # 电脑emoji
        '\U0001f310': '[GLOBE]', # 地球emoji
        '\U0001f4c8': '[TREND]', # 趋势图emoji
        # 添加更多需要替换的字符...
    }
    
    cleaned_text = text
    for unicode_char, replacement in replacements.items():
        cleaned_text = cleaned_text.replace(unicode_char, replacement)
    
    # 移除其他可能导致GBK编码错误的字符
    try:
        # 尝试编码为GBK，如果失败则移除有问题的字符
        cleaned_text.encode('gbk')
    except UnicodeEncodeError as e:
        # 逐字符检查，移除无法编码的字符
        safe_chars = []
        for char in cleaned_text:
            try:
                char.encode('gbk')
                safe_chars.append(char)
            except UnicodeEncodeError:
                safe_chars.append('?')  # 用?替代无法编码的字符
        cleaned_text = ''.join(safe_chars)
    
    return cleaned_text
from openai import OpenAI
import tiktoken
import requests
import importlib
from error_handler import (
    setup_global_exception_handler,
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

# 导入图标管理系统
from icon_manager import safe_print, get_icon
from logger_utils import NewsLogger, get_news_logger

# 设置全局异常处理器
setup_global_exception_handler()

# 创建新闻日志记录器
summary_logger = NewsLogger()

ENABLE_MULTI_ROUND_SUMMARY = False

# ========== deepseek官方tokenizer加载（仅deepseek平台用） ==========
deepseek_tokenizer = None
# 获取deepseek tokenizer实例
# 若未加载则尝试加载本地tokenizer，否则fallback到tiktoken
# 返回tokenizer对象或None

def get_deepseek_tokenizer():
    global deepseek_tokenizer
    if deepseek_tokenizer is None:
        try:
            # 临时抑制transformers的警告
            import warnings
            warnings.filterwarnings("ignore", message="None of PyTorch, TensorFlow.*")
            
            from transformers import AutoTokenizer
            chat_tokenizer_dir = os.path.join(os.path.dirname(__file__), 'deepseek_v3_tokenizer')
            deepseek_tokenizer = AutoTokenizer.from_pretrained(chat_tokenizer_dir, trust_remote_code=True)
            
            # 恢复警告
            warnings.resetwarnings()
            
        except Exception as e:
            safe_print(f"[WARN] deepseek官方tokenizer加载失败: {e}")
            safe_print("[INFO] 这通常是因为未安装PyTorch。如果只需要基本功能，可以忽略此警告。")
            safe_print("[INFO] 如需精确token计数，请安装PyTorch: pip install torch")
            deepseek_tokenizer = None
    return deepseek_tokenizer

# 统计文本token数，支持deepseek和openai平台
# text: 输入文本
# platform: 平台名
# model_name: 模型名
# 返回token数量

def count_tokens(text, platform=None, model_name=None):
    platform = platform or NEWS_SUMMARY_PLATFORM
    model_name = model_name or NEWS_SUMMARY_MODEL
    if platform == 'deepseek':
        tokenizer = get_deepseek_tokenizer()
        if tokenizer:
            return len(tokenizer.encode(text))
        else:
            # fallback
            enc = tiktoken.get_encoding('cl100k_base')
            return len(enc.encode(text))
    else:
        enc = tiktoken.get_encoding('cl100k_base')
        return len(enc.encode(text))

# 加载打分后的新闻，过滤低分新闻
# json_path: 新闻json文件路径
# min_score: 最低分数阈值
# filter_sourceapi: 过滤特定来源的新闻，如'serp_googlenews'
# 返回过滤后的新闻列表

def load_scored_news(json_path, min_score=3, filter_sourceapi=None):
    safe_print(f"[INFO] 读取打分新闻文件: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        news_list = json.load(f)
    filtered = [n for n in news_list if n.get('score', 0) >= min_score]
    safe_print(f"[INFO] 3分及以上新闻数量: {len(filtered)}")
    
    # 如果指定了sourceapi过滤
    if filter_sourceapi:
        sourceapi_filtered = [n for n in filtered if n.get('sourceapi') == filter_sourceapi]
        safe_print(f"[INFO] 过滤sourceapi='{filter_sourceapi}'后数量: {len(sourceapi_filtered)}")
        return sourceapi_filtered
    
    return filtered

# 构建新闻列表prompt字符串
# news_list: 新闻列表
# 返回拼接后的prompt字符串

def build_news_list_prompt(news_list):
    lines = []
    for idx, news in enumerate(news_list, 1):
        title = news.get('title', '').strip().replace('\n', ' ')
        content = news.get('content', '').strip()
        score = news.get('score', '')
        lines.append(f"标题{idx}: {title}\n分数{idx}: {score}\n新闻{idx}: {content}")
    return '\n\n'.join(lines)

# 调用大模型对新闻列表进行总结
# news_list: 新闻列表
# platform: 平台名
# model_name: 模型名
# keyword: 关键词
# 返回prompt、总结、token统计等

def summarize_news(news_list, platform=None, model_name=None, keyword=None):
    platform = platform or NEWS_SUMMARY_PLATFORM
    model_name = model_name or NEWS_SUMMARY_MODEL
    model_cfg = NEWS_SUMMARY_MODELS[platform][model_name]
    safe_print(f"[INFO] 当前平台: {platform}")
    safe_print(f"[INFO] 当前模型: {model_name}")
    safe_print(f"[INFO] API地址: {model_cfg.get('base_url')}")
    news_list_str = build_news_list_prompt(news_list)
    user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
    system_tokens = count_tokens(NEWS_SUMMARY_SYSTEM_PROMPT, platform, model_name)
    user_tokens = count_tokens(user_prompt, platform, model_name)
    safe_print(f"[INFO] 新闻关键词: {keyword}")
    safe_print(f"[INFO] system prompt tokens: {system_tokens}")
    safe_print(f"[INFO] user prompt tokens: {user_tokens}")
    safe_print(f"[INFO] 总token数: {system_tokens + user_tokens}")
    stream_mode = model_cfg['model'] in ['qwq-plus']
    safe_print(f"[DEBUG] OpenAI SDK调用模型: {model_cfg['model']}")
    safe_print(f"[DEBUG] OpenAI SDK地址: {model_cfg['base_url']}")
    safe_print(f"[DEBUG] API Key: {'已配置' if model_cfg['api_key'] else '未配置'}")
    safe_print(f"[DEBUG] stream参数: {stream_mode}")
    result, token_limit_info = call_llm(
        NEWS_SUMMARY_SYSTEM_PROMPT,
        user_prompt,
        platform,
        model_name,
        stream_mode=stream_mode
    )
    if result is None:
        return user_prompt, None, system_tokens, user_tokens, 0, platform, model_name, token_limit_info
    result_tokens = count_tokens(result, platform, model_name)
    safe_print(f"[INFO] 返回内容tokens: {result_tokens}")
    return user_prompt, result, system_tokens, user_tokens, result_tokens, platform, model_name, token_limit_info

# 保存总结结果到json文件
# date: 日期
# keyword: 关键词
# summary: 总结内容
# output_dir: 输出目录
# model_name/platform/model_str: 模型信息
# 返回保存路径

def save_summary(date, keyword, summary, output_dir, model_name=None, platform=None, model_str=None):
    filename = NEWS_SUMMARY_RESULT_FILENAME.format(date=date, keyword=keyword)
    out_path = os.path.join(output_dir, filename)
    # 读取旧文件，兼容旧格式
    if os.path.exists(out_path):
        with open(out_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        # 兼容旧格式，迁移顶层 summary 字段
        if 'summary' in result:
            old_entry = {
                "summary": result.pop('summary'),
                "platform": result.pop('platform', 'unknown'),
                "model": result.pop('model', 'unknown')
            }
            result.setdefault('summaries', []).insert(0, old_entry)
    else:
        result = {"date": date, "keyword": keyword}
    if 'summaries' not in result:
        result['summaries'] = []
    # 只保留platform和model字段
    result['summaries'].append({
        "summary": summary,
        "platform": platform if platform else "unknown",
        "model": model_str if model_str else "unknown"
    })
    # 只保留 date, keyword, summaries
    result = {k: result[k] for k in ['date', 'keyword', 'summaries'] if k in result}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    safe_print(f"[INFO] 总结已保存: {out_path}")
    return out_path

# 追加运行日志到log文件
# 记录运行参数、token统计、合并关键词等信息

def append_log(date, keyword, model_name, prompt, summary_path, news_count, success=True, error_msg=None, system_tokens=None, user_tokens=None, result_tokens=None, platform=None, model_str=None, extra_info=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    log = (
        f"\n[{now}]\n"
        f"执行程序: news_summarizer\n"
        f"[关键词] 主关键词: {keyword}\n"
    )
    # 新增：记录涉及的搜索关键词
    if extra_info and 'search_keywords' in extra_info and extra_info['search_keywords']:
        log += f"[搜索关键词] 涉及搜索关键词: {', '.join([str(s) for s in extra_info['search_keywords'] if s])}\n"
    log += (
        f"[日期] 日期: {date}\n"
        f"[平台] 平台: {platform if platform else 'unknown'}\n"
        f"[模型] 模型: {model_str if model_str else model_name}\n"
        f"[新闻数量] 3分及以上新闻数量: {news_count}\n"
        f"[Prompt] 送给大模型的prompt前300字: {prompt[:300].replace(chr(10),' ')}\n"
        f"[总结结果文件] 总结结果文件: {summary_path if summary_path else '无'}\n"
        f"[Token统计] 平台: {platform if platform else 'unknown'}，模型: {model_str if model_str else model_name}，system: {system_tokens}, user: {user_tokens}, result: {result_tokens}\n"
        f"[运行结果] 运行结果: {'成功' if success else '失败'}\n"
    )
    # 新增：记录合并的二级关键词
    if extra_info and 'secondary_keywords' in extra_info:
        log += f"[合并关键词] 合并的二级关键词: {extra_info['secondary_keywords']}\n"
    if error_msg:
        log += f"[错误信息] 错误信息: {clean_unicode_for_console(error_msg)}\n"
    if extra_info and 'merge_status' in extra_info:
        log += f"[合并情况] 合并情况: {extra_info['merge_status']}\n"
    log += f"==============================\n"
    
    # 对整个日志字符串进行Unicode清理，避免GBK编码错误
    cleaned_log = clean_unicode_for_console(log)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(cleaned_log)
    safe_print(f"[INFO] 日志已写入: {log_path}")

def call_llm(system_prompt, user_prompt, platform, model_name, stream_mode=False, max_retries=3, timeout=300, retry_interval=10):
    model_cfg = NEWS_SUMMARY_MODELS[platform][model_name]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    token_limit_info = None
    
    for attempt in range(1, max_retries + 1):
        # 从连接池获取客户端
        client = _client_pool.get_client(platform, model_name)
        
        try:
            safe_print(f"[尝试] [尝试 {attempt}/{max_retries}] 正在调用 {platform}-{model_name} 模型...")
            safe_print(f"[超时设置] {timeout}秒，请耐心等待...")
            
            # 记录开始时间
            start_time = datetime.now()
            
            response = client.chat.completions.create(
                model=model_cfg['model'],
                messages=messages,
                stream=stream_mode,
                timeout=timeout
            )
            
            if stream_mode:
                safe_print("[流式输出] 开始接收模型响应...")
                result = ""
                chunk_count = 0
                for chunk in response:
                    if hasattr(chunk.choices[0].delta, "content") and chunk.choices[0].delta.content:
                        result += chunk.choices[0].delta.content
                        chunk_count += 1
                        # 每100个chunk显示一次进度
                        if chunk_count % 100 == 0:
                            safe_print(f"[流式输出] 已接收 {chunk_count} 个数据块，当前长度: {len(result)} 字符")
                safe_print(f"[完成] [流式输出] 完成，总共接收 {chunk_count} 个数据块")
            else:
                safe_print("[非流式] 等待模型完整响应...")
                result = response.choices[0].message.content.strip()
            
            # 计算耗时
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            safe_print(f"[成功] [API调用成功] 耗时: {duration:.1f}秒，响应长度: {len(result)} 字符")
            
            return result, token_limit_info
            
        except Exception as e:
            # 计算失败耗时
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            err_str = str(e)
            # 智能错误分类
            is_ssl_error = any(x in err_str.lower() for x in ["ssl", "connection", "socket", "handshake", "certificate"])
            is_token_limit = any(x in err_str.lower() for x in ["token", "context length", "input length", "max input limit", "too long"])
            is_timeout = any(x in err_str.lower() for x in ["timeout", "timed out", "time out"])
            is_network_error = any(x in err_str.lower() for x in ["network", "dns", "resolve", "unreachable", "connection refused"])
            is_rate_limit = any(x in err_str.lower() for x in ["rate limit", "rate_limit", "quota", "too many requests"])
            
            if is_token_limit:
                safe_print(f"[失败] [Token超限] 请求失败，耗时: {duration:.1f}秒")
                safe_print(f"[WARN] API返回token超限，prompt开头200字: {user_prompt[:200]}")
                safe_print(f"[WARN] API返回token超限，prompt结尾200字: {user_prompt[-200:]}")
                token_limit_info = {
                    "prompt_head": user_prompt[:200],
                    "prompt_tail": user_prompt[-200:],
                    "token_count": len(user_prompt)
                }
                # Token超限是不可重试的错误，直接返回None
                return None, token_limit_info
            elif is_ssl_error:
                safe_print(f"[失败] [SSL错误] 请求失败，耗时: {duration:.1f}秒")
                safe_print(f"[SSL错误] {err_str[:200]}...")
            elif is_timeout:
                safe_print(f"[失败] [超时失败] 请求超时，耗时: {duration:.1f}秒（超过{timeout}秒限制）")
            elif is_network_error:
                safe_print(f"[失败] [网络错误] 请求失败，耗时: {duration:.1f}秒")
            elif is_rate_limit:
                safe_print(f"[失败] [速率限制] 请求失败，耗时: {duration:.1f}秒")
            else:
                safe_print(f"[失败] [其他错误] 请求失败，耗时: {duration:.1f}秒")
            
            safe_print(f"[第{attempt}次失败] 错误类型: {type(e).__name__}")
            safe_print(f"[错误详情] {str(e)[:200]}...")
            
            # 详细调试信息
            response = getattr(e, 'response', None)
            if response is not None:
                try:
                    safe_print(f"[DEBUG] 原始响应状态: {response.status_code}")
                    safe_print(f"[DEBUG] 原始响应内容前500字: {response.text[:500]}")
                except Exception as ex:
                    safe_print(f"[DEBUG] 无法打印原始响应内容: {ex}")
            else:
                if hasattr(e, 'args') and e.args:
                    safe_print(f"[DEBUG] 异常args: {e.args}")
                    if isinstance(e.args[0], str):
                        safe_print(f"[DEBUG] 异常args[0]内容前500字: {e.args[0][:500]}")
        
        # 重试逻辑
        if attempt < max_retries:
            # 根据错误类型调整重试间隔
            if is_ssl_error or is_network_error:
                actual_retry_interval = retry_interval * 2  # SSL/网络错误延长重试间隔
            elif is_rate_limit:
                actual_retry_interval = retry_interval * 3  # 速率限制错误更长重试间隔
            else:
                actual_retry_interval = retry_interval
            
            safe_print(f"[准备重试] {actual_retry_interval}秒后进行第{attempt + 1}次尝试...")
            import time
            for i in range(actual_retry_interval):
                time.sleep(1)
                if i % 3 == 0:  # 每3秒显示一次倒计时
                    remaining = actual_retry_interval - i
                    safe_print(f"[倒计时] 还有 {remaining} 秒...")
        else:
            safe_print(f"[最终失败] 已达到最大重试次数({max_retries}次)")
    
    safe_print(f"[ERROR] 连续{max_retries}次请求均失败，已放弃。")
    return None, token_limit_info

# 主流程入口
# date: 日期
# keyword: 关键词
# model_name: 指定模型名
# output_dir: 输出目录

@with_error_handling("news_summarizer.py", "main")
def main(date=None, keyword=None, model_name=None, output_dir=None):
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    if keyword is None:
        raise ValueError('必须指定关键词')
    if output_dir is None:
        output_dir = os.path.join('output', date)
    scored_json = os.path.join(output_dir, f"{date}_{keyword}_scored.json")
    news_list = []
    search_keywords_set = set()
    # 只加载主关键词新闻，根据配置决定是否过滤特定来源
    # 所有关键词都使用所有来源的新闻
    filter_sourceapi_to_use = NEWS_SUMMARY_FILTER_SOURCEAPI  # 使用配置的过滤规则（现在为None，表示不过滤）
    
    if os.path.exists(scored_json):
        news_items = load_scored_news(scored_json, min_score=3, filter_sourceapi=filter_sourceapi_to_use)
        news_list += news_items
        for n in news_items:
            if 'search_keyword' in n:
                search_keywords_set.add(n['search_keyword'])
    
    # 合并去重（按link去重）
    unique_links = set()
    deduped_news = []
    for news in news_list:
        link = news.get('link')
        if link and link not in unique_links:
            deduped_news.append(news)
            unique_links.add(link)
    news_list = deduped_news
    
    # 新增：打印涉及的搜索关键词和过滤信息
    if search_keywords_set:
        safe_print(f"[INFO] 本批次涉及的搜索关键词: {', '.join([str(s) for s in search_keywords_set if s])}")
    if filter_sourceapi_to_use:
        safe_print(f"[INFO] 摘要只使用来源为 '{filter_sourceapi_to_use}' 的新闻")
    else:
        safe_print(f"[INFO] 摘要使用所有API来源的新闻")
    
    if not news_list:
        if filter_sourceapi_to_use:
            filter_msg = f"（已过滤来源：{filter_sourceapi_to_use}）"
        else:
            filter_msg = "（使用所有API来源）"
        safe_print(f"[INFO] 无3分及以上新闻{filter_msg}，无需总结。")
        append_log(date, keyword, model_name, '', '', 0, success=True, extra_info={'search_keywords': list(search_keywords_set)})
        return True
    # 后续流程保持不变
    # token超限多级预判
    platform = NEWS_SUMMARY_PLATFORM
    model = NEWS_SUMMARY_MODEL
    news_list_str = build_news_list_prompt(news_list)
    user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
    user_tokens = count_tokens(user_prompt, platform=platform, model_name=model)
    switched = False
    # 1. deepseek超限，切换到qwen-max
    if platform == 'deepseek' and user_tokens > 61000:
        safe_print(f"[WARN] token数{user_tokens}超出deepseek 64k限制，自动切换到bailian平台qwen-plus-latest模型")
        # 新增：记录超限prompt头尾
        safe_print(f"[WARN] 超限prompt开头200字: {user_prompt[:200]}")
        safe_print(f"[WARN] 超限prompt结尾200字: {user_prompt[-200:]}")
        with open(os.path.join("output", "run_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[WARN] token数超限（主动判断），已切换到bailian平台qwen-plus-latest模型，token数: {user_tokens}\n")
            f.write(f"[WARN] 超限prompt开头200字: {user_prompt[:200]}\n")
            f.write(f"[WARN] 超限prompt结尾200字: {user_prompt[-200:]}\n")
        platform = 'bailian'
        model = 'qwen-plus-latest'
        news_list_str = build_news_list_prompt(news_list)
        user_prompt = NEWS_SUMMARY_USER_PROMPT.format(news_list=news_list_str, keyword=keyword)
        user_tokens = count_tokens(user_prompt, platform=platform, model_name=model)
        switched = True
    prompt, summary, system_tokens, user_tokens, result_tokens, used_platform, used_model, token_limit_info = summarize_news(news_list, platform=platform, model_name=model, keyword=keyword)
    # ========== 新增：容错处理 ==========
    if summary is None:
        # 记录跳过日志
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join("output", "run_log.txt")
        skip_log = (
            f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 大模型API连续多次失败或超时，未能完成总结\n==============================\n"
        )
        # 对日志字符串进行Unicode清理，避免GBK编码错误
        cleaned_skip_log = clean_unicode_for_console(skip_log)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(cleaned_skip_log)
        safe_print(f"[SKIP] 跳过关键词: {keyword}，原因: 大模型API连续多次失败或超时")
        return False
    # ========== 第一轮摘要结果保存 ==========
    safe_print("\n===== 第1轮-初稿摘要 =====")
    safe_print(f"[INFO] 使用模型: {used_platform}-{used_model}")
    safe_print(f"[INFO] 摘要生成成功，长度: {len(summary)} 字符")
    safe_print(f"[INFO] 摘要token数: {result_tokens}")
    safe_print(f"[摘要内容] {clean_unicode_for_console(summary.strip()[:200])}{'...' if len(summary) > 200 else ''}")

    round1_entry = {
        "summary": summary,
        "platform": used_platform,
        "model": used_model,
        "round": 1
    }
    if not ENABLE_MULTI_ROUND_SUMMARY:
        all_rounds = [round1_entry]
        summary_path = save_summary_multi_round(date, keyword, all_rounds, output_dir)
        merge_info = {
            'search_keywords': list(search_keywords_set)
        }
        append_log(
            date,
            keyword,
            used_model,
            prompt,
            summary_path,
            len(news_list),
            success=True,
            system_tokens=system_tokens,
            user_tokens=user_tokens,
            result_tokens=result_tokens,
            platform=used_platform,
            model_str=used_model,
            extra_info=merge_info
        )
        if token_limit_info:
            log_path = os.path.join("output", "run_log.txt")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[WARN] API返回token超限，prompt token数: {token_limit_info['token_count']}\n")
                f.write(f"[WARN] prompt开头200字: {token_limit_info['prompt_head']}\n")
                f.write(f"[WARN] prompt结尾200字: {token_limit_info['prompt_tail']}\n")
        return True

    judge_user_prompt = NEWS_SUMMARY_JUDGE_USER_PROMPT.format(
        system_prompt=NEWS_SUMMARY_SYSTEM_PROMPT.strip(),
        user_prompt=prompt.strip(),
        summary=summary.strip()
    )
    judge_system_prompt = NEWS_SUMMARY_JUDGE_SYSTEM_PROMPT.strip()
    judge_user_tokens = count_tokens(judge_user_prompt, platform=used_platform, model_name=used_model)
    judge_system_tokens = count_tokens(judge_system_prompt, platform=used_platform, model_name=used_model)
    judge_platform, judge_model = used_platform, used_model
    if judge_platform == 'deepseek' and (judge_user_tokens + judge_system_tokens) > 61000:
        safe_print(f"[WARN] 评判官token数超限({judge_user_tokens + judge_system_tokens}>61000)，切换到bailian平台qwen-plus-latest模型")
        judge_platform = 'bailian'
        judge_model = 'qwen-plus-latest'
        judge_user_tokens = count_tokens(judge_user_prompt, platform=judge_platform, model_name=judge_model)
        judge_system_tokens = count_tokens(judge_system_prompt, platform=judge_platform, model_name=judge_model)
        with open(os.path.join("output", "run_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[WARN] 评判官token数超限，已切换到bailian平台qwen-plus-latest模型，token数: {judge_user_tokens + judge_system_tokens}\n")
    safe_print("\n===== 第2-1轮-评判官意见 =====")
    safe_print(f"[INFO] 使用模型: {judge_platform}-{judge_model}")
    safe_print(f"[INFO] 评判官token数: {judge_user_tokens + judge_system_tokens}")
    judge_suggestion, _ = call_llm(judge_system_prompt, judge_user_prompt, judge_platform, judge_model)
    if judge_suggestion is None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join("output", "run_log.txt")
        skip_log = (
            f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 评判官环节大模型API连续多次失败或超时，未能完成总结\n==============================\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(skip_log)
        safe_print(f"[SKIP] 跳过关键词: {keyword}，原因: 评判官环节大模型API连续多次失败或超时")
        return False
    safe_print(f"[INFO] 评判官建议生成成功，长度: {len(judge_suggestion)} 字符")
    safe_print(f"[评判建议] {clean_unicode_for_console(judge_suggestion.strip()[:200])}{'...' if len(judge_suggestion) > 200 else ''}")
    optimize_user_prompt = NEWS_SUMMARY_OPTIMIZE_USER_PROMPT.format(
        system_prompt=NEWS_SUMMARY_SYSTEM_PROMPT.strip(),
        user_prompt=prompt.strip(),
        summary=summary.strip(),
        judge_suggestion=judge_suggestion.strip()
    )
    optimize_system_prompt = NEWS_SUMMARY_OPTIMIZE_SYSTEM_PROMPT.strip()
    optimize_platform, optimize_model = used_platform, used_model
    optimize_user_tokens = count_tokens(optimize_user_prompt, platform=optimize_platform, model_name=optimize_model)
    optimize_system_tokens = count_tokens(optimize_system_prompt, platform=optimize_platform, model_name=optimize_model)
    if optimize_platform == 'deepseek' and (optimize_user_tokens + optimize_system_tokens) > 61000:
        safe_print(f"[WARN] 优化摘要token数超限({optimize_user_tokens + optimize_system_tokens}>61000)，切换到bailian平台qwen-plus-latest模型")
        optimize_platform = 'bailian'
        optimize_model = 'qwen-plus-latest'
        optimize_user_tokens = count_tokens(optimize_user_prompt, platform=optimize_platform, model_name=optimize_model)
        optimize_system_tokens = count_tokens(optimize_system_prompt, platform=optimize_platform, model_name=optimize_model)
        with open(os.path.join("output", "run_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[WARN] 优化摘要token数超限，已切换到bailian平台qwen-plus-latest模型，token数: {optimize_user_tokens + optimize_system_tokens}\n")
    safe_print("\n===== 第2-2轮-优化后摘要 =====")
    safe_print(f"[INFO] 使用模型: {optimize_platform}-{optimize_model}")
    safe_print(f"[INFO] 优化token数: {optimize_user_tokens + optimize_system_tokens}")
    improved_summary, _ = call_llm(optimize_system_prompt, optimize_user_prompt, optimize_platform, optimize_model)
    if improved_summary is None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path = os.path.join("output", "run_log.txt")
        skip_log = (
            f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 优化环节大模型API连续多次失败或超时，未能完成总结\n==============================\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(skip_log)
        safe_print(f"[SKIP] 跳过关键词: {keyword}，原因: 优化环节大模型API连续多次失败或超时")
        return False
    safe_print(f"[INFO] 优化摘要生成成功，长度: {len(improved_summary)} 字符")
    safe_print(f"[优化摘要] {clean_unicode_for_console(improved_summary.strip()[:200])}{'...' if len(improved_summary) > 200 else ''}")
    round2_entry = {
        "summary": improved_summary,
        "platform": optimize_platform,
        "model": optimize_model,
        "round": 2,
        "judge_suggestion": judge_suggestion
    }
    write_to_mysql = importlib.import_module('write_to_mysql')
    prev_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    safe_print(f"\n[热点追踪调试] 当前日期: {date}")
    safe_print(f"[热点追踪调试] 昨天日期: {prev_date}")
    safe_print(f"[热点追踪调试] 查询关键词: {keyword}")
    prev_summary, prev_round = write_to_mysql.fetch_latest_summary(prev_date, keyword)
    if prev_summary:
        safe_print(f"[成功] [热点追踪调试] 成功找到昨天的摘要，轮次: {prev_round}")
        safe_print(f"[热点追踪调试] 昨天摘要前200字: {prev_summary[:200]}...")
    else:
        safe_print(f"[失败] [热点追踪调试] 未找到昨天的摘要数据")
        safe_print(f"[热点追踪调试] 可能原因:")
        safe_print(f"   1. 昨天({prev_date})的摘要还未写入数据库")
        safe_print(f"   2. 数据库连接问题")
        safe_print(f"   3. 关键词({keyword})在昨天没有摘要记录")
    round3_entry = None
    if prev_summary:
        hotspot_system_prompt = NEWS_SUMMARY_HOTSPOT_SYSTEM_PROMPT.strip()
        hotspot_user_prompt = NEWS_SUMMARY_HOTSPOT_USER_PROMPT.format(prev_summary=prev_summary.strip(), today_summary=improved_summary.strip())
        hotspot_tokens = count_tokens(hotspot_system_prompt, optimize_platform, optimize_model) + count_tokens(hotspot_user_prompt, optimize_platform, optimize_model)
        safe_print("\n===== 第3轮-热点追踪总结 =====")
        safe_print(f"[INFO] 使用模型: {optimize_platform}-{optimize_model}")
        safe_print(f"[INFO] 热点追踪token数: {hotspot_tokens}")
        safe_print(f"[INFO] 对比昨天({prev_date})的摘要数据")
        hotspot_summary, _ = call_llm(hotspot_system_prompt, hotspot_user_prompt, optimize_platform, optimize_model)
        if hotspot_summary is None:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_path = os.path.join("output", "run_log.txt")
            skip_log = (
                f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: 热点追踪环节大模型API连续多次失败或超时，未能完成总结\n==============================\n"
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(skip_log)
            safe_print(f"[SKIP] 跳过关键词: {keyword}，原因: 热点追踪环节大模型API连续多次失败或超时")
            return False
        safe_print(f"[INFO] 热点追踪摘要生成成功，长度: {len(hotspot_summary)} 字符")
        safe_print(f"[热点追踪] {clean_unicode_for_console(hotspot_summary.strip()[:200])}{'...' if len(hotspot_summary) > 200 else ''}")
        round3_entry = {
            "summary": hotspot_summary,
            "platform": optimize_platform,
            "model": optimize_model,
            "round": 3,
            "prev_date": prev_date
        }
    else:
        safe_print(f"\n[警告] [跳过热点追踪] 无昨天({prev_date})的摘要数据，跳过第3轮热点追踪")
    all_rounds = [round1_entry, round2_entry]
    if round3_entry:
        all_rounds.append(round3_entry)
    summary_path = save_summary_multi_round(date, keyword, all_rounds, output_dir)
    optimization_prompt_path = os.path.join(output_dir, f"{date}_{keyword}_optimization_prompts.txt")
    with open(optimization_prompt_path, "w", encoding="utf-8") as f:
        import json as _json
        round1_messages = [
            {"role": "system", "content": NEWS_SUMMARY_SYSTEM_PROMPT.strip()},
            {"role": "user", "content": prompt.strip()}
        ]
        f.write("【1-1 初稿 messages】\n")
        f.write(_json.dumps(round1_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【1-1 初稿输出（摘要）】\n")
        f.write(summary.strip() + "\n\n")
        judge_messages = [
            {"role": "system", "content": judge_system_prompt.strip()},
            {"role": "user", "content": judge_user_prompt.strip()}
        ]
        f.write("【2-1 评判官 messages】\n")
        f.write(_json.dumps(judge_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【2-1 评判官输出（建议）】\n")
        f.write(judge_suggestion.strip() + "\n\n")
        optimize_messages = [
            {"role": "system", "content": optimize_system_prompt.strip()},
            {"role": "user", "content": optimize_user_prompt.strip()}
        ]
        f.write("【2-2 优化 messages】\n")
        f.write(_json.dumps(optimize_messages, ensure_ascii=False, indent=2) + "\n\n")
        f.write("【2-2 优化输出（最终摘要）】\n")
        f.write(improved_summary.strip() + "\n\n")
        if round3_entry:
            hotspot_messages = [
                {"role": "system", "content": hotspot_system_prompt},
                {"role": "user", "content": hotspot_user_prompt}
            ]
            f.write("【3-1 热点追踪 messages】\n")
            f.write(_json.dumps(hotspot_messages, ensure_ascii=False, indent=2) + "\n\n")
            f.write("【3-1 热点追踪输出】\n")
            f.write(hotspot_summary.strip() + "\n")
    safe_print(f"[INFO] 优化流程所有prompt及messages已保存: {optimization_prompt_path}")
    merge_info = {
        'search_keywords': list(search_keywords_set)
    }
    append_log(
        date,
        keyword,
        used_model,
        prompt,
        summary_path,
        len(news_list),
        success=True,
        system_tokens=system_tokens,
        user_tokens=user_tokens,
        result_tokens=result_tokens,
        platform=used_platform,
        model_str=used_model,
        extra_info=merge_info
    )
    if token_limit_info:
        log_path = os.path.join("output", "run_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[WARN] API返回token超限，prompt token数: {token_limit_info['token_count']}\n")
            f.write(f"[WARN] prompt开头200字: {token_limit_info['prompt_head']}\n")
            f.write(f"[WARN] prompt结尾200字: {token_limit_info['prompt_tail']}\n")
    return True

# 新增：多轮摘要保存，保留所有轮次和相关信息
def save_summary_multi_round(date, keyword, round_entries, output_dir):
    filename = NEWS_SUMMARY_RESULT_FILENAME.format(date=date, keyword=keyword)
    out_path = os.path.join(output_dir, filename)
    # 读取旧文件，兼容旧格式
    if os.path.exists(out_path):
        with open(out_path, 'r', encoding='utf-8') as f:
            result = json.load(f)
    else:
        result = {"date": date, "keyword": keyword}
    if 'summaries' not in result:
        result['summaries'] = []
    # 追加所有新轮次
    result['summaries'].extend(round_entries)
    # 只保留 date, keyword, summaries
    result = {k: result[k] for k in ['date', 'keyword', 'summaries'] if k in result}
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    safe_print(f"[INFO] 多轮摘要已保存: {out_path}")
    return out_path

# 命令行入口
@with_error_handling("news_summarizer.py", "main_entry")
def main_entry():
    """主程序入口"""
    import argparse
    
    # 记录脚本开始
    log_script_start("news_summarizer.py", sys.argv[1:])
    
    error_handler = ErrorHandler()
    success = True
    
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--date', type=str, help='日期, 格式YYYY-MM-DD')
        parser.add_argument('--keyword', type=str, help='关键词')
        parser.add_argument('--model', type=str, default=None, help='模型名（deepseek/bailian等）')
        args = parser.parse_args()

        # 新增：无参数时自动批量处理昨天所有关键词
        if args.keyword is None:
            date_str = args.date or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            processed_count = 0
            total_count = len(DEFAULT_KEYWORDS)
            
            for keyword in DEFAULT_KEYWORDS:
                safe_print(f"\n{'='*40}\n=== 开始总结主关键词: {keyword} ===\n{'='*40}")
                try:
                    result = main(date=date_str, keyword=keyword, model_name=args.model)
                    if result is not None:
                        processed_count += 1
                        safe_print(f"{'='*40}\n=== 总结主关键词: {keyword} 完成 ===\n{'='*40}")
                    else:
                        success = False
                        safe_print(f"{'='*40}\n=== 总结主关键词: {keyword} 失败 ===\n{'='*40}")
                except Exception as e:
                    safe_print(f"[ERROR] 总结关键词 {keyword} 失败: {e}")
                    error_handler.log_error(
                        error_type="SUMMARIZE_KEYWORD_ERROR",
                        error_msg=f"总结关键词 {keyword} 失败: {e}",
                        script_name="news_summarizer.py",
                        keyword=keyword
                    )
                    success = False
            
            completion_msg = f"批量摘要完成：{processed_count}/{total_count} 个关键词处理成功"
            safe_print(f"\n[统计] {completion_msg}")
            log_script_complete("news_summarizer.py", success=success, message=completion_msg)
            return success
        else:
            # 单关键词模式
            result = main(date=args.date, keyword=args.keyword, model_name=args.model)
            success = result is not None
            status_msg = f"关键词 {args.keyword} 摘要完成" if success else f"关键词 {args.keyword} 摘要失败"
            log_script_complete("news_summarizer.py", success=success, message=status_msg)
            return success
            
    except Exception as e:
        error_msg = f"news_summarizer.py 主程序执行过程中发生异常: {str(e)}"
        safe_print(f"[ERROR] {error_msg}")
        error_handler.log_error(
            error_type="MAIN_ENTRY_ERROR",
            error_msg=error_msg,
            script_name="news_summarizer.py"
        )
        log_script_complete("news_summarizer.py", success=False, message=error_msg)
        return False
    
    finally:
        # 程序结束时清理连接池
        try:
            stats = _client_pool.get_stats()
            safe_print(f"[连接池统计] 关闭前状态: {stats}")
            _client_pool.close_all()
            safe_print("[连接池] 所有OpenAI客户端连接已关闭")
        except Exception as e:
            safe_print(f"[WARN] 清理连接池时发生异常: {e}")

if __name__ == "__main__":
    success = main_entry()
    sys.exit(0 if success else 1) 
