import subprocess
import sys
import datetime
import os
import json
import argparse
import shlex
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
from news_volume_alert import run as run_volume_alert

STATUS_FILE = os.path.join("output", "run_status.json")

def _update_step(step_name, step_status):
    """更新 run_status.json 中某个步骤的状态"""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "steps" in data and step_name in data["steps"]:
                data["steps"][step_name]["status"] = step_status
                with open(STATUS_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _finish_run():
    """标记运行完成"""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = "finished"
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# 设置全局异常处理器
setup_global_exception_handler()

def run_step(cmd, step_name, script_name=None, desc=None, keyword=None):
    """安全运行步骤，使用新的错误处理机制"""
    _update_step(step_name, "running")
    try:
        result = safe_subprocess_run(cmd, step_name, keyword=keyword, check=True)
        _update_step(step_name, "success")
        return result
    except Exception as e:
        _update_step(step_name, "failed")
        raise

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

def _run_log_path():
    return os.environ.get("RUN_LOG_PATH", os.path.join("output", "run_log.txt"))

def write_skip_log(keyword, reason, file_path):
    """记录跳过信息到日志"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = _run_log_path()
    skip_log = f"[{now}] [SKIP] {keyword}: {reason}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(skip_log)
    except Exception:
        pass

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
        cmd = f'{sys.executable} fetch_and_filter.py "{search_kw}" {date} --output "{tmp_file}"'
        
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
    cmd = f'{sys.executable} fetch_content.py {kw} {date}'
    
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
    cmd = f'{sys.executable} news_scorer.py {kw} {date}'
    
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
    log_path = _run_log_path()

    results_summary = ", ".join(f"{kw}={'成功' if r else '失败'}" for kw, r in results.items())
    concurrent_log = f"[{now}] 并发AI评分完成: 成功{success_count}, 失败{fail_count}, 耗时{duration.total_seconds():.0f}秒\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(concurrent_log)
    
    return success_count, fail_count

def get_active_keywords(keyword=None):
    """返回本次流水线处理的主关键词，并校验单关键词参数。"""
    if keyword is None:
        return list(DEFAULT_KEYWORDS)
    if keyword not in SEARCH_KEYWORDS:
        raise ValueError(f"未知主关键词: {keyword}")
    return [keyword]


@with_error_handling("main.py", "main")
def main(date=None, keyword=None):
    """主函数，控制整个新闻处理流程"""
    # 记录脚本开始执行
    script_args = [arg for arg in (date, "--keyword" if keyword else None, keyword) if arg]
    log_script_start("main.py", script_args)

    main_success = True
    active_keywords = None
    try:
        # 如果未指定日期，自动赋值为昨天日期
        if not date:
            date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        active_keywords = get_active_keywords(keyword)

        # 生成批次ID，设置批次日志路径
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_log_path = os.path.join("output", date, f"run_{run_id}.log")
        os.environ["RUN_LOG_PATH"] = batch_log_path
        os.makedirs(os.path.dirname(batch_log_path), exist_ok=True)
        with open(batch_log_path, "w", encoding="utf-8") as f:
            pass  # 创建空日志文件，确保子进程首次 open("a") 不因目录不存在而崩溃
        print(f"[INFO] 本次运行批次: {run_id}，日志文件: {batch_log_path}")
        print(f"[INFO] 本次批量处理主关键词: {active_keywords}")
        
        # 输出所有搜索关键词
        all_search_keywords = set()
        for main_kw in active_keywords:
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
        for main_kw in active_keywords:
            if execute_news_fetching(date, main_kw):
                fetch_success += 1
            else:
                fetch_failed += 1
        
        print(f"\n[统计] 步骤1完成统计：成功 {fetch_success}，失败 {fetch_failed}")
        
        # 步骤2：抓正文
        print(f"\n[步骤2] 开始执行步骤2：正文抓取阶段")
        for kw in active_keywords:
            if execute_content_fetching(date, kw):
                content_success += 1
            else:
                content_failed += 1
        
        print(f"\n[统计] 步骤2完成统计：成功 {content_success}，失败 {content_failed}")
        
        # 步骤3：评分（并发处理）
        print(f"\n[步骤3] 开始执行步骤3：AI评分阶段（并发处理）")
        score_success, score_failed = execute_scoring_concurrent(date, active_keywords, max_workers=3)
        
        print(f"\n[统计] 步骤3完成统计：成功 {score_success}，失败 {score_failed}")
        
        print("[INFO] 全部关键词处理完成。")

        # 步骤4：自动写入数据库
        date_arg = f'--date {shlex.quote(date)}'
        keyword_arg = f' --keyword {shlex.quote(keyword)}' if keyword else ''
        print("\n[步骤4] 开始执行步骤4：数据库写入阶段")
        database_success = run_step(
            f'{sys.executable} write_to_mysql.py {date_arg}{keyword_arg}',
            '自动写入数据库',
            'write_to_mysql.py',
            '将scored结果写入数据库'
        )

        # 步骤5：单条500字摘要（受环境变量控制）
        enable_item_summarizer = os.getenv("ENABLE_ITEM_SUMMARIZER", "1") == "1"
        if enable_item_summarizer:
            print(f"\n[步骤5] 开始执行步骤5：单条新闻摘要阶段")
            item_summary_success = safe_subprocess_run(
                f'{sys.executable} news_item_summarizer.py {shlex.quote(date)}{keyword_arg}',
                '单条新闻摘要',
                keyword='all',
                check=False
            )
        else:
            print(f"\n[步骤5] 已通过 ENABLE_ITEM_SUMMARIZER=0 关闭，跳过")
            item_summary_success = True

        # 步骤6：地域分析 — 已由 news_item_summarizer.py 在生成摘要时一并处理
        # news_region_analyzer.py 保留为独立脚本，可手动运行作为补充/应急
        region_success = True

        # 步骤7：烟草官网爬取（仅中国烟草关键词，条件触发）
        has_tobacco = "中国烟草" in active_keywords
        if has_tobacco:
            print(f"\n[步骤7] 开始执行步骤7：烟草官网爬取阶段")
            _update_step("tobacco", "running")
            try:
                tobacco_success = safe_subprocess_run(
                    f'{sys.executable} tobacco_gov_crawler.py --date {date}',
                    '烟草官网爬取',
                    keyword='中国烟草',
                    check=True
                )
            except Exception as e:
                print(f"[WARN] 烟草官网爬取失败: {e}")
                tobacco_success = False
            _update_step("tobacco", "success" if tobacco_success else "failed")
        else:
            print(f"\n[步骤7] 关键词不含'中国烟草'，跳过")
            tobacco_success = True

        # 步骤8：新闻量波动预警已挪到 main() 末尾的收尾路径，
        # 确保前置步骤任一异常仍能触发预警（issue #11 验收第 1 条）

        # 统计整体执行情况
        total_steps = 7
        successful_steps = sum([
            1 if fetch_failed == 0 else 0,
            1 if content_failed == 0 else 0,
            1 if score_failed == 0 else 0,
            1 if database_success else 0,
            1 if item_summary_success else 0,
            1 if region_success else 0,
            1 if tobacco_success else 0,
        ])

        success_rate = successful_steps / total_steps * 100

        completion_message = (
            f"总体完成情况：{successful_steps}/{total_steps} 步骤成功 ({success_rate:.1f}%)\n"
            f"新闻采集：{fetch_success}成功/{fetch_failed}失败\n"
            f"正文抓取：{content_success}成功/{content_failed}失败\n"
            f"AI评分：{score_success}成功/{score_failed}失败\n"
            f"数据库写入：{'成功' if database_success else '失败'}\n"
            f"单条摘要：{'成功' if item_summary_success else '失败'}\n"
            f"地域分析：{'成功' if region_success else '跳过'}\n"
            f"烟草爬取：{'成功' if tobacco_success else '跳过'}"
        )
        
        print(f"\n[完成] 全部流程执行完成！")
        print(f"[统计] {completion_message}")
        
        # 记录脚本完成
        log_script_complete("main.py", success=True, message=completion_message)

    except KeyboardInterrupt:
        print(f"\n[INFO] 用户中断程序执行")
        log_script_complete("main.py", success=False, message="用户中断执行")
        sys.exit(130)
    except Exception as e:
        error_msg = f"main函数执行过程中发生异常: {str(e)}"
        print(f"[ERROR] {error_msg}")
        log_script_complete("main.py", success=False, message=error_msg)
        main_success = False

    # 步骤8：新闻量波动预警 —— 收尾路径，任何前置异常后仍执行
    # KeyboardInterrupt 已通过 sys.exit(130) 提前退出，不会走到这里
    # 预警自身异常被吞，不影响退出码
    if active_keywords:
        print(f"\n[步骤8] 开始执行步骤8：新闻量波动预警阶段")
        try:
            run_volume_alert(target_date=date, keywords=active_keywords)
        except Exception as e:
            print(f"[WARN] 新闻量波动预警失败: {e}")
    else:
        print("\n[步骤8] 未确定有效主关键词，跳过新闻量波动预警")

    return main_success

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行新闻处理流水线")
    parser.add_argument("date", nargs="?", default=None, help="目标日期，格式 YYYY-MM-DD")
    parser.add_argument("--keyword", choices=DEFAULT_KEYWORDS, help="只处理指定主关键词")
    args = parser.parse_args()
    
    # 执行主函数
    success = main(args.date, keyword=args.keyword)
    _finish_run()
    # 根据执行结果设置退出码
    if success:
        sys.exit(0)
    else:
        sys.exit(1)
