# -*- coding: utf-8 -*-
"""
日志工具 - 统一的日志输出系统
主要功能：
1. 集成图标管理器，提供跨平台图标支持
2. 提供多种日志级别（info/warning/error/success等）
3. 支持文件日志和控制台日志
4. 自动处理编码问题
5. 统一的API，便于维护
"""

import os
import sys
from datetime import datetime
from typing import Optional, Union
from icon_manager import IconManager, IconTheme, get_icon_manager


class LogLevel:
    """日志级别常量"""
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Logger:
    """统一日志类"""
    
    def __init__(self, 
                 name: str = "main",
                 icon_manager: Optional[IconManager] = None,
                 log_file: Optional[str] = None,
                 console_output: bool = True):
        """
        初始化日志器
        
        Args:
            name: 日志器名称
            icon_manager: 图标管理器实例，如果为None则使用全局实例
            log_file: 日志文件路径，如果为None则不写入文件
            console_output: 是否输出到控制台
        """
        self.name = name
        self.icon_manager = icon_manager or get_icon_manager()
        self.log_file = log_file
        self.console_output = console_output
        
        # 创建日志目录
        if self.log_file:
            log_dir = os.path.dirname(self.log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
    
    def _format_timestamp(self) -> str:
        """格式化时间戳"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def _write_to_file(self, message: str, level: str):
        """写入日志文件"""
        if not self.log_file:
            return
            
        try:
            timestamp = self._format_timestamp()
            # 文件日志使用纯文本格式，不包含图标和颜色
            log_entry = f"[{timestamp}] [{level.upper()}] [{self.name}] {message}\n"
            
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            # 如果文件写入失败，至少要在控制台显示
            print(f"[ERROR] 日志文件写入失败: {e}")
    
    def _log(self, message: str, level: str, icon_name: str = "", prefix: str = ""):
        """内部日志方法"""
        # 控制台输出（带图标）
        if self.console_output:
            formatted_prefix = f"[{prefix}]" if prefix else ""
            self.icon_manager.safe_print(message, icon_name, formatted_prefix)
        
        # 文件输出（不带图标）
        self._write_to_file(f"{prefix} {message}" if prefix else message, level)
    
    def debug(self, message: str, prefix: str = ""):
        """调试信息"""
        self._log(message, LogLevel.DEBUG, "info", prefix)
    
    def info(self, message: str, prefix: str = ""):
        """一般信息"""
        self._log(message, LogLevel.INFO, "info", prefix)
    
    def success(self, message: str, prefix: str = ""):
        """成功信息"""
        self._log(message, LogLevel.SUCCESS, "success", prefix)
    
    def warning(self, message: str, prefix: str = ""):
        """警告信息"""
        self._log(message, LogLevel.WARNING, "warning", prefix)
    
    def error(self, message: str, prefix: str = ""):
        """错误信息"""
        self._log(message, LogLevel.ERROR, "error", prefix)
    
    def critical(self, message: str, prefix: str = ""):
        """严重错误信息"""
        self._log(message, LogLevel.CRITICAL, "error", prefix)
    
    # 特定功能的快捷方法
    def step(self, step_num: int, message: str, total_steps: Optional[int] = None):
        """步骤信息"""
        if total_steps:
            prefix = f"步骤{step_num}/{total_steps}"
        else:
            prefix = f"步骤{step_num}"
        self._log(message, LogLevel.INFO, f"stage{min(step_num, 5)}", prefix)
    
    def start(self, message: str):
        """开始任务"""
        self._log(message, LogLevel.INFO, "start", "开始执行")
    
    def complete(self, message: str):
        """完成任务"""
        self._log(message, LogLevel.SUCCESS, "complete", "执行完成")
    
    def skip(self, message: str, reason: str = ""):
        """跳过任务"""
        full_message = f"{message} - {reason}" if reason else message
        self._log(full_message, LogLevel.INFO, "skip", "跳过")
    
    def progress(self, current: int, total: int, message: str = ""):
        """进度信息"""
        progress_msg = f"进度 {current}/{total}"
        if message:
            progress_msg += f" - {message}"
        self._log(progress_msg, LogLevel.INFO, "stats")
    
    # 新闻系统特定的日志方法
    def news_fetch(self, keyword: str, count: int, source: str = ""):
        """新闻采集日志"""
        source_info = f" 来源: {source}" if source else ""
        self._log(f"采集关键词: {keyword}, 数量: {count}{source_info}", LogLevel.INFO, "news", "新闻采集")
    
    def content_fetch(self, title: str, success: bool, method: str = ""):
        """正文抓取日志"""
        status = "成功" if success else "失败"
        method_info = f" 方法: {method}" if method else ""
        short_title = title[:50] + "..." if len(title) > 50 else title
        icon = "success" if success else "error"
        self._log(f"正文抓取{status}: {short_title}{method_info}", LogLevel.SUCCESS if success else LogLevel.ERROR, icon, "正文抓取")
    
    def ai_score(self, title: str, score: int):
        """AI评分日志"""
        short_title = title[:50] + "..." if len(title) > 50 else title
        self._log(f"评分: {score}分 - {short_title}", LogLevel.INFO, "ai", "AI评分")
    
    def file_operation(self, operation: str, file_path: str, success: bool = True):
        """文件操作日志"""
        status = "成功" if success else "失败"
        icon = "success" if success else "error"
        self._log(f"{operation}{status}: {file_path}", LogLevel.SUCCESS if success else LogLevel.ERROR, icon, "文件操作")


class NewsLogger(Logger):
    """新闻系统专用日志器"""
    
    def __init__(self, 
                 keyword: str = "unknown",
                 date: str = "",
                 log_file: Optional[str] = None):
        """
        初始化新闻日志器
        
        Args:
            keyword: 关键词
            date: 日期
            log_file: 日志文件路径
        """
        if not log_file:
            # 默认日志文件路径
            log_file = os.path.join("output", "run_log.txt")
        
        # 名称包含关键词和日期
        name = f"news-{keyword}"
        if date:
            name += f"-{date}"
            
        super().__init__(name=name, log_file=log_file)
        self.keyword = keyword
        self.date = date
    
    def script_start(self, script_name: str, args: list = None):
        """脚本开始执行"""
        args_str = " ".join(args) if args else ""
        self._log(f"开始执行: {script_name} {args_str}".strip(), LogLevel.INFO, "start", "脚本执行")
    
    def script_complete(self, script_name: str, success: bool = True, message: str = ""):
        """脚本执行完成"""
        status = "成功" if success else "失败"
        icon = "complete" if success else "error"
        full_message = f"执行{status}: {script_name}"
        if message:
            full_message += f" - {message}"
        self._log(full_message, LogLevel.SUCCESS if success else LogLevel.ERROR, icon, "脚本执行")
    
    def batch_summary(self, total: int, success: int, failed: int, operation: str = ""):
        """批量操作总结"""
        operation_name = operation or "操作"
        self._log(f"批量{operation_name}完成: 总计{total}, 成功{success}, 失败{failed}", 
                 LogLevel.INFO, "stats", "批量操作")
    
    def content_fetch_start(self, keyword: str, title: str, url: str):
        """开始正文抓取日志"""
        short_title = title[:50] + "..." if len(title) > 50 else title
        self._log(f"正在抓取: {short_title}", LogLevel.INFO, "process", f"[{keyword}]")
        self._log(f"URL: {url}", LogLevel.DEBUG, "info", f"[{keyword}]")
    
    def content_fetch(self, keyword: str, wordcount: int, grab_type: str, status: str, error_msg: str = None):
        """正文抓取结果日志"""
        if status == "success":
            self._log(f"抓取成功 ({grab_type}): {wordcount} 字", LogLevel.SUCCESS, "success", f"[{keyword}]")
        elif status == "failed":
            self._log(f"抓取失败: 未获取到正文", LogLevel.ERROR, "error", f"[{keyword}]")
        elif status == "error":
            self._log(f"抓取异常: {error_msg}", LogLevel.ERROR, "error", f"[{keyword}]")
        else:
            self._log(f"抓取状态: {status}", LogLevel.INFO, "info", f"[{keyword}]")


# 创建全局日志器实例
_global_logger = None
_news_loggers = {}

def get_logger(name: str = "main", **kwargs) -> Logger:
    """获取日志器实例"""
    global _global_logger
    if name == "main":
        if _global_logger is None:
            _global_logger = Logger(name=name, **kwargs)
        return _global_logger
    else:
        return Logger(name=name, **kwargs)

def get_news_logger(keyword: str, date: str = "") -> NewsLogger:
    """获取新闻日志器实例"""
    global _news_loggers
    key = f"{keyword}-{date}"
    
    if key not in _news_loggers:
        _news_loggers[key] = NewsLogger(keyword=keyword, date=date)
    
    return _news_loggers[key]

# 快捷函数，使用全局日志器
def log_info(message: str, prefix: str = ""):
    """快捷函数：信息日志"""
    get_logger().info(message, prefix)

def log_success(message: str, prefix: str = ""):
    """快捷函数：成功日志"""
    get_logger().success(message, prefix)

def log_warning(message: str, prefix: str = ""):
    """快捷函数：警告日志"""
    get_logger().warning(message, prefix)

def log_error(message: str, prefix: str = ""):
    """快捷函数：错误日志"""
    get_logger().error(message, prefix)

def log_step(step_num: int, message: str, total_steps: Optional[int] = None):
    """快捷函数：步骤日志"""
    get_logger().step(step_num, message, total_steps)

def log_start(message: str):
    """快捷函数：开始日志"""
    get_logger().start(message)

def log_complete(message: str):
    """快捷函数：完成日志"""
    get_logger().complete(message)


if __name__ == "__main__":
    # 测试代码
    print("=== 日志工具测试 ===")
    
    # 创建测试日志器
    logger = Logger("test", log_file="test_log.txt")
    
    # 测试各种日志级别
    logger.info("这是一个信息消息")
    logger.success("操作成功完成")
    logger.warning("这是一个警告")
    logger.error("这是一个错误")
    
    # 测试特定功能
    logger.start("开始执行任务")
    logger.step(1, "第一步：初始化", 3)
    logger.step(2, "第二步：处理数据", 3)
    logger.step(3, "第三步：保存结果", 3)
    logger.complete("任务执行完成")
    
    # 测试新闻日志器
    news_logger = NewsLogger("测试关键词", "2025-01-18")
    news_logger.script_start("test_script.py", ["arg1", "arg2"])
    news_logger.news_fetch("测试", 10, "Google News")
    news_logger.content_fetch("测试新闻标题", True, "trafilatura")
    news_logger.ai_score("测试新闻标题", 4)
    news_logger.script_complete("test_script.py", True, "测试完成")
    
    # 测试快捷函数
    log_info("使用快捷函数的信息日志")
    log_success("使用快捷函数的成功日志")
    
    print("\n测试完成，请检查 test_log.txt 文件") 