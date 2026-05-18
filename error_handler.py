# -*- coding: utf-8 -*-
# =========================================
# 错误处理和日志记录工具模块
# 主要功能：统一的错误处理、异常捕获、运行状态跟踪、日志记录
# =========================================

import os
import sys
import json
import traceback
import functools
from datetime import datetime
from typing import Any, Callable, Optional, Dict, List
import subprocess

class ErrorHandler:
    """统一的错误处理类"""
    
    def __init__(self, log_dir: str = "output"):
        self.log_dir = log_dir
        self.error_log_path = os.path.join(log_dir, "error_log.txt")
        # 优先使用环境变量指定的批次日志路径
        env_path = os.environ.get("RUN_LOG_PATH")
        if env_path:
            self.run_log_path = env_path
        else:
            self.run_log_path = os.path.join(log_dir, "run_log.txt")
        self.ensure_log_dir()
        
    def ensure_log_dir(self):
        """确保日志目录存在"""
        os.makedirs(self.log_dir, exist_ok=True)
    
    def log_error(self, error_type: str, error_msg: str, 
                  script_name: str = None, keyword: str = None, 
                  traceback_info: str = None, context: Dict = None):
        """记录错误到error_log.txt"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        error_entry = {
            "timestamp": now,
            "error_type": error_type,
            "script_name": script_name or "unknown",
            "keyword": keyword or "unknown",
            "error_message": error_msg,
            "traceback": traceback_info,
            "context": context or {}
        }
        
        # 写入结构化日志
        with open(self.error_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now}] ERROR\n")
            f.write(f"脚本: {script_name or 'unknown'}\n")
            f.write(f"关键词: {keyword or 'unknown'}\n")
            f.write(f"错误类型: {error_type}\n")
            f.write(f"错误信息: {error_msg}\n")
            if traceback_info:
                f.write(f"详细堆栈:\n{traceback_info}\n")
            if context:
                f.write(f"上下文信息: {json.dumps(context, ensure_ascii=False, indent=2)}\n")
            f.write("=" * 50 + "\n\n")
    
    def log_step_failure(self, step_name: str, cmd: str = None, 
                        error_msg: str = None, keyword: str = None):
        """记录步骤失败到run_log.txt"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        log_entry = (
            f"\n[{now}]\n"
            f"[失败] 执行失败: {step_name}\n"
            f"[关键词] 关键词: {keyword or 'unknown'}\n"
        )
        
        if cmd:
            log_entry += f"[命令] 执行命令: {cmd}\n"
        if error_msg:
            log_entry += f"[错误信息] 错误信息: {error_msg}\n"
        
        log_entry += f"==============================\n"
        
        with open(self.run_log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    
    def log_program_crash(self, program_name: str, stage: str, 
                         error_msg: str, keyword: str = None, 
                         traceback_info: str = None):
        """记录程序崩溃信息"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        crash_log = (
            f"\n[{now}]\n"
            f"[异常终止] 程序异常终止\n"
            f"[程序名] 程序名: {program_name}\n"
            f"[执行阶段] 执行阶段: {stage}\n"
            f"[关键词] 关键词: {keyword or 'unknown'}\n"
            f"[错误信息] 错误信息: {error_msg}\n"
        )
        
        if traceback_info:
            crash_log += f"[详细错误] 详细错误:\n{traceback_info}\n"
        
        crash_log += f"==============================\n"
        
        # 同时写入run_log和error_log
        with open(self.run_log_path, "a", encoding="utf-8") as f:
            f.write(crash_log)
        
        self.log_error(
            error_type="PROGRAM_CRASH",
            error_msg=error_msg,
            script_name=program_name,
            keyword=keyword,
            traceback_info=traceback_info,
            context={"stage": stage}
        )

def with_error_handling(script_name: str, stage: str = "main"):
    """装饰器：为函数添加错误处理"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            error_handler = ErrorHandler()
            try:
                return func(*args, **kwargs)
            except KeyboardInterrupt:
                error_msg = "用户中断程序执行"
                print(f"[INFO] {error_msg}")
                error_handler.log_program_crash(
                    program_name=script_name,
                    stage=stage,
                    error_msg=error_msg
                )
                sys.exit(130)  # 标准的Ctrl+C退出码
            except Exception as e:
                error_msg = str(e)
                traceback_info = traceback.format_exc()
                
                print(f"[ERROR] {script_name} 在 {stage} 阶段发生异常")
                print(f"[ERROR] 错误信息: {error_msg}")
                print(f"[ERROR] 详细信息已记录到日志文件")
                
                error_handler.log_program_crash(
                    program_name=script_name,
                    stage=stage,
                    error_msg=error_msg,
                    traceback_info=traceback_info
                )
                
                # 非致命错误，返回None继续执行
                return None
                
        return wrapper
    return decorator

def safe_subprocess_run(cmd: str, step_name: str, keyword: str = None,
                       check: bool = True, retries: int = 2) -> bool:
    """安全执行子进程，失败时自动重试"""
    error_handler = ErrorHandler()
    import time

    for attempt in range(1, retries + 1):
        print(f"[开始] {step_name}" + (f" (重试 {attempt}/{retries})" if attempt > 1 else ""))

        try:
            if sys.platform.startswith('win'):
                result = subprocess.run(cmd, shell=True, check=check,
                                      capture_output=True, text=True, encoding='gbk', errors='ignore',
                                      env=os.environ)
            else:
                result = subprocess.run(cmd, shell=True, check=check,
                                      capture_output=True, text=True, encoding='utf-8',
                                      env=os.environ)

            print(f"[完成] {step_name}")
            return True

        except subprocess.CalledProcessError as e:
            error_msg = f"命令执行失败，返回码: {e.returncode}"
            if e.stdout:
                error_msg += f"\n标准输出: {e.stdout}"
            if e.stderr:
                error_msg += f"\n错误输出: {e.stderr}"

            if attempt < retries:
                print(f"[重试] {step_name} 返回码 {e.returncode}，{3}秒后重试 ({attempt}/{retries})")
                time.sleep(3)
                continue

            print(f"[错误] {step_name} 返回码: {e.returncode} (已重试{retries}次)")
            if e.stderr:
                print(f"[ERROR] {e.stderr[:200]}")

            error_handler.log_step_failure(
                step_name=step_name, cmd=cmd, error_msg=error_msg, keyword=keyword
            )
            error_handler.log_error(
                error_type="SUBPROCESS_ERROR", error_msg=error_msg,
                script_name="main.py", keyword=keyword,
                context={"command": cmd, "step": step_name, "returncode": e.returncode, "retries": retries}
            )
            if check:
                raise
            return False

        except Exception as e:
            error_msg = f"执行命令时发生异常: {str(e)}"

            if attempt < retries:
                print(f"[重试] {step_name} 异常: {e}，3秒后重试 ({attempt}/{retries})")
                time.sleep(3)
                continue

            traceback_info = traceback.format_exc()
            print(f"[错误] {step_name} 异常: {e} (已重试{retries}次)")

            error_handler.log_step_failure(
                step_name=step_name, cmd=cmd, error_msg=error_msg, keyword=keyword
            )
            error_handler.log_error(
                error_type="SUBPROCESS_EXCEPTION", error_msg=error_msg,
                script_name="main.py", keyword=keyword,
                traceback_info=traceback_info,
                context={"command": cmd, "step": step_name, "retries": retries}
            )
            if check:
                raise
            return False

def setup_global_exception_handler():
    """设置全局异常处理器"""
    error_handler = ErrorHandler()
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            # 用户中断，正常处理
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        error_msg = str(exc_value)
        traceback_info = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        print(f"[FATAL] 程序发生未捕获的异常")
        print(f"[FATAL] 错误类型: {exc_type.__name__}")
        print(f"[FATAL] 错误信息: {error_msg}")
        print(f"[FATAL] 详细信息已记录到日志文件")
        
        error_handler.log_error(
            error_type="UNCAUGHT_EXCEPTION",
            error_msg=f"{exc_type.__name__}: {error_msg}",
            script_name=os.path.basename(sys.argv[0]) if sys.argv else "unknown",
            traceback_info=traceback_info
        )
        
        # 调用原始异常处理器
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = handle_exception

def log_script_start(script_name: str, args: List[str] = None):
    """记录脚本开始执行"""
    error_handler = ErrorHandler()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    args_str = f" {' '.join(args)}" if args else ""
    log_entry = f"\n[{now}] [开始执行] {script_name}{args_str}\n"

    os.makedirs(os.path.dirname(error_handler.run_log_path), exist_ok=True)
    with open(error_handler.run_log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)

def log_script_complete(script_name: str, success: bool = True, message: str = None):
    """记录脚本执行完成"""
    error_handler = ErrorHandler()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    status = "执行完成" if success else "执行失败"
    log_entry = f"[{now}] [{status}] {script_name}"
    if message:
        log_entry += f" — {message}"
    log_entry += "\n"

    os.makedirs(os.path.dirname(error_handler.run_log_path), exist_ok=True)
    with open(error_handler.run_log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)