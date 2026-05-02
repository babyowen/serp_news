import subprocess
import sys
import datetime
import os
import json
import concurrent.futures
from config import DEFAULT_KEYWORDS, SEARCH_KEYWORDS
from error_handler import (
    setup_global_exception_handler, 
    safe_subprocess_run, 
    with_error_handling,
    log_script_start,
    log_script_complete,
    ErrorHandler
)

# 设置全局异常处理器
setup_global_exception_handler()

def run_step(cmd, step_name, script_name=None, desc=None, keyword=None):
    """安全运行步骤，使用新的错误处理机制"""
    if script_name or desc:
        print(f"\n------ 即将执行: {script_name or ''} ------")
        if desc:
            print(f"功能说明: {desc}")
        print(f"-----------------------------------\n")
    
    # 使用safe_subprocess_run替代原来的subprocess.run
    return safe_subprocess_run(cmd, step_name, keyword=keyword, check=True)

def all_news_has_content(json_path):
    """判断json文件中所有新闻条目都已存在非空content字段"""
    if not os.path.exists(json_path):
        return False
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            news_list = json.load(f)
        if not isinstance(news_list, list) or not news_list:
            return False
        return all(item.get('content') and len(str(item.get('content')).strip()) > 0 for item in news_list)
    except Exception as e:
        error_handler = ErrorHandler()
        error_handler.log_error(
            error_type="FILE_READ_ERROR",
            error_msg=f"检查content时读取失败: {json_path}, 错误: {e}",
            script_name="main.py",
            context={"file_path": json_path, "function": "all_news_has_content"}
        )
        print(f"[WARN] 检查content时读取失败: {json_path}, 错误: {e}")
        return False

def write_skip_log(keyword, reason, file_path):
    """记录跳过信息到日志"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    skip_log = (
        f"[{now}]\n[SKIP] 跳过关键词: {keyword}\n原因: {reason} {file_path}\n==============================\n"
    )
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(skip_log)
    except Exception as e:
        print(f"[WARN] 无法写入跳过日志: {e}")

@with_error_handling("main.py", "新闻采集阶段")
def execute_news_fetching(date, main_kw):
    """执行新闻采集阶段"""
    merged_file = f"output/{date}/{date}_{main_kw}.json"
    if os.path.exists(merged_file):
        print(f"[INFO] {merged_file} 已存在，跳过 {main_kw}")
        write_skip_log(main_kw, "已存在，新闻列表已抓取", merged_file)
        return True
    
    print(f"[INFO] [步骤1] 抓取API: {main_kw}")
    all_news = []
    
    for search_kw in SEARCH_KEYWORDS[main_kw]:
        # 调用采集脚本，采集结果临时存储
        tmp_file = f"output/{date}/tmp_{date}_{main_kw}_{search_kw}.json"
        cmd = f'python fetch_and_filter.py "{search_kw}" {date} --output "{tmp_file}"'
        
        # 使用安全的subprocess调用
        success = safe_subprocess_run(
            cmd, 
            f"采集新闻-{search_kw}", 
            keyword=search_kw,
            check=False  # 不直接抛异常，让程序继续
        )
        
        if not success:
            print(f"[WARN] 采集 {search_kw} 失败，跳过此搜索关键词")
            continue
        
        # 读取采集结果，添加 search_keyword 字段
        if os.path.exists(tmp_file):
            try:
                with open(tmp_file, 'r', encoding='utf-8') as f:
                    news_list = json.load(f)
                for item in news_list:
                    item['search_keyword'] = search_kw
                    item['main_keyword'] = main_kw
                    item['keyword'] = main_kw  # 确保keyword字段为主关键词
                all_news.extend(news_list)
            except Exception as e:
                error_handler = ErrorHandler()
                error_handler.log_error(
                    error_type="JSON_READ_ERROR",
                    error_msg=f"读取临时采集文件失败: {tmp_file}, 错误: {e}",
                    script_name="main.py",
                    keyword=main_kw,
                    context={"file_path": tmp_file, "search_keyword": search_kw}
                )
                print(f"[WARN] 读取临时采集文件失败: {tmp_file}, 错误: {e}")
            finally:
                # 清理临时文件
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception as e:
                    print(f"[WARN] 删除临时文件失败: {tmp_file}, 错误: {e}")
    
    # 合并去重（按 title+link）
    unique = {}
    for item in all_news:
        key = (item.get('title', '').strip(), item.get('link', '').strip())
        if key not in unique:
            unique[key] = item
    deduped_news = list(unique.values())
    
    # 保存合并结果
    try:
        os.makedirs(f"output/{date}", exist_ok=True)
        with open(merged_file, 'w', encoding='utf-8') as f:
            json.dump(deduped_news, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 合并去重后已保存: {merged_file}，数量：{len(deduped_news)}")
        return True
    except Exception as e:
        error_handler = ErrorHandler()
        error_handler.log_error(
            error_type="FILE_WRITE_ERROR",
            error_msg=f"保存合并文件失败: {merged_file}, 错误: {e}",
            script_name="main.py",
            keyword=main_kw,
            context={"file_path": merged_file, "news_count": len(deduped_news)}
        )
        print(f"[ERROR] 保存合并文件失败: {merged_file}, 错误: {e}")
        return False

@with_error_handling("main.py", "正文抓取阶段")
def execute_content_fetching(date, kw):
    """执行正文抓取阶段"""
    kw = kw.strip()
    merged_file = f"output/{date}/{date}_{kw}.json"
    
    if not os.path.exists(merged_file):
        print(f"[WARN] {merged_file} 不存在，无法抓正文，跳过 {kw}")
        write_skip_log(kw, "新闻列表文件不存在，无法抓正文", merged_file)
        return False
    
    if all_news_has_content(merged_file):
        print(f"[INFO] {merged_file} 所有新闻正文已抓取，跳过 {kw}")
        write_skip_log(kw, "所有新闻正文已抓取", merged_file)
        return True
    
    print(f"[INFO] [步骤2] 抓正文: {kw}")
    cmd = f'python fetch_content.py {kw} {date}'
    
    return safe_subprocess_run(cmd, f"抓取正文-{kw}", keyword=kw, check=False)

@with_error_handling("main.py", "AI评分阶段")
def execute_scoring(date, kw):
    """执行AI评分阶段"""
    kw = kw.strip()
    merged_file = f"output/{date}/{date}_{kw}.json"
    scored_file = f"output/{date}/{date}_{kw}_scored.json"
    
    if not os.path.exists(merged_file):
        print(f"[WARN] {merged_file} 不存在，无法评分，跳过 {kw}")
        write_skip_log(kw, "新闻列表文件不存在，无法评分", merged_file)
        return False
    
    if os.path.exists(scored_file):
        print(f"[INFO] {scored_file} 已存在，跳过 {kw}")
        write_skip_log(kw, "已存在，已完成打分", scored_file)
        return True
    
    print(f"[INFO] [步骤3] 评分: {kw}")
    cmd = f'python news_scorer.py {kw} {date}'
    
    return safe_subprocess_run(cmd, f"AI评分-{kw}", keyword=kw, check=False)

@with_error_handling("main.py", "AI评分并发处理")
def execute_scoring_concurrent(date, keywords, max_workers=3):
    """并发执行AI评分阶段"""
    print(f"[INFO] 开始并发AI评分，最大并发数: {max_workers}")
    
    # 记录开始时间
    start_time = datetime.datetime.now()
    
    # 使用ThreadPoolExecutor进行并发处理
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_keyword = {
            executor.submit(execute_scoring, date, kw): kw 
            for kw in keywords
        }
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_keyword):
            kw = future_to_keyword[future]
            try:
                result = future.result()
                results[kw] = result
                status = "成功" if result else "失败"
                print(f"[INFO] [{kw}] 评分完成: {status}")
            except Exception as e:
                print(f"[ERROR] [{kw}] 评分异常: {str(e)}")
                results[kw] = False
                
                # 记录错误
                error_handler = ErrorHandler()
                error_handler.log_error(
                    error_type="CONCURRENT_SCORING_ERROR",
                    error_msg=f"并发评分时发生异常: {str(e)}",
                    script_name="main.py",
                    keyword=kw,
                    context={"date": date, "stage": "concurrent_scoring"}
                )
    
    # 计算统计信息
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    
    success_count = sum(1 for result in results.values() if result)
    fail_count = len(results) - success_count
    
    print(f"[INFO] 并发AI评分完成，耗时: {duration.total_seconds():.1f}秒")
    print(f"[INFO] 成功: {success_count}，失败: {fail_count}")
    
    # 记录详细结果到日志
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join("output", "run_log.txt")
    
    concurrent_log = f"\n[{now}]\n"
    concurrent_log += f"执行程序: main.py (并发AI评分)\n"
    concurrent_log += f"并发处理关键词: {', '.join(keywords)}\n"
    concurrent_log += f"最大并发数: {max_workers}\n"
    concurrent_log += f"总耗时: {duration.total_seconds():.1f}秒\n"
    concurrent_log += f"成功: {success_count}，失败: {fail_count}\n"
    concurrent_log += f"详细结果:\n"
    
    for kw, result in results.items():
        status = "成功" if result else "失败"
        concurrent_log += f"  - {kw}: {status}\n"
    
    concurrent_log += f"==============================\n"
    
    # 清理Unicode字符
    def clean_unicode_for_log(text):
        if not text:
            return text
        try:
            text.encode('gbk')
        except UnicodeEncodeError:
            safe_chars = []
            for char in text:
                try:
                    char.encode('gbk')
                    safe_chars.append(char)
                except UnicodeEncodeError:
                    safe_chars.append('?')
            text = ''.join(safe_chars)
        return text
    
    cleaned_log = clean_unicode_for_log(concurrent_log)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(cleaned_log)
    
    return success_count, fail_count

@with_error_handling("main.py", "main")
def main(date=None):
    """主函数，控制整个新闻处理流程"""
    # 记录脚本开始执行
    script_args = [date] if date else []
    log_script_start("main.py", script_args)
    
    try:
        # 如果未指定日期，自动赋值为昨天日期
        if not date:
            date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        print(f"[INFO] 本次批量处理主关键词: {DEFAULT_KEYWORDS}")
        
        # 输出所有搜索关键词
        all_search_keywords = set()
        for main_kw in DEFAULT_KEYWORDS:
            all_search_keywords.update(SEARCH_KEYWORDS[main_kw])
        print(f"[INFO] 本次所有搜索关键词: {sorted(all_search_keywords)}")
        
        # 统计成功失败情况
        fetch_success = 0
        fetch_failed = 0
        content_success = 0
        content_failed = 0
        score_success = 0
        score_failed = 0
        
        # 步骤1：抓取API
        print(f"\n[步骤1] 开始执行步骤1：新闻采集阶段")
        for main_kw in DEFAULT_KEYWORDS:
            if execute_news_fetching(date, main_kw):
                fetch_success += 1
            else:
                fetch_failed += 1
        
        print(f"\n[统计] 步骤1完成统计：成功 {fetch_success}，失败 {fetch_failed}")
        
        # 步骤2：抓正文
        print(f"\n[步骤2] 开始执行步骤2：正文抓取阶段")
        for kw in DEFAULT_KEYWORDS:
            if execute_content_fetching(date, kw):
                content_success += 1
            else:
                content_failed += 1
        
        print(f"\n[统计] 步骤2完成统计：成功 {content_success}，失败 {content_failed}")
        
        # 步骤3：评分（并发处理）
        print(f"\n[步骤3] 开始执行步骤3：AI评分阶段（并发处理）")
        score_success, score_failed = execute_scoring_concurrent(date, DEFAULT_KEYWORDS, max_workers=3)
        
        print(f"\n[统计] 步骤3完成统计：成功 {score_success}，失败 {score_failed}")
        
        print("[INFO] 全部关键词处理完成。")

        # 步骤4：自动总结主关键词（可通过环境变量 ENABLE_SUMMARIZER=0 关闭）
        date_arg = f'--date {date}' if date else ''
        enable_summarizer = os.getenv("ENABLE_SUMMARIZER", "1") == "1"
        if enable_summarizer:
            print("\n[步骤4] 开始执行步骤4：智能摘要阶段")
            summarize_success = run_step(
                f'python news_summarizer.py {date_arg}',
                '自动总结主关键词',
                'news_summarizer.py',
                '对所有主关键词进行总结，自动合并搜索关键词新闻'
            )
        else:
            print("\n[步骤4] 已通过 ENABLE_SUMMARIZER=0 关闭，跳过智能摘要阶段")
            summarize_success = True  # 跳过即视为"无失败"，防止统计/日志栏报错
        
        # 步骤5：自动写入数据库
        print("\n[步骤5] 开始执行步骤5：数据库写入阶段")
        database_success = run_step(
            f'python write_to_mysql.py {date_arg}', 
            '自动写入数据库', 
            'write_to_mysql.py', 
            '将scored和summary结果写入数据库'
        )
        
        # 统计整体执行情况
        total_steps = 5
        successful_steps = sum([
            1 if fetch_failed == 0 else 0,
            1 if content_failed == 0 else 0, 
            1 if score_failed == 0 else 0,
            1 if summarize_success else 0,
            1 if database_success else 0
        ])
        
        success_rate = successful_steps / total_steps * 100
        
        completion_message = (
            f"总体完成情况：{successful_steps}/{total_steps} 步骤成功 ({success_rate:.1f}%)\n"
            f"新闻采集：{fetch_success}成功/{fetch_failed}失败\n"
            f"正文抓取：{content_success}成功/{content_failed}失败\n"
            f"AI评分：{score_success}成功/{score_failed}失败\n"
            f"智能摘要：{'成功' if summarize_success else '失败'}\n"
            f"数据库写入：{'成功' if database_success else '失败'}"
        )
        
        print(f"\n[完成] 全部流程执行完成！")
        print(f"[统计] {completion_message}")
        
        # 记录脚本完成
        log_script_complete("main.py", success=True, message=completion_message)
        
        return True
        
    except KeyboardInterrupt:
        print(f"\n[INFO] 用户中断程序执行")
        log_script_complete("main.py", success=False, message="用户中断执行")
        sys.exit(130)
    except Exception as e:
        error_msg = f"main函数执行过程中发生异常: {str(e)}"
        print(f"[ERROR] {error_msg}")
        log_script_complete("main.py", success=False, message=error_msg)
        return False

if __name__ == "__main__":
    import sys
    date = None
    if len(sys.argv) > 1:
        date = sys.argv[1]
    
    # 执行主函数
    success = main(date)
    
    # 根据执行结果设置退出码
    if success:
        sys.exit(0)
    else:
        sys.exit(1) 