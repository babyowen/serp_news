# -*- coding: utf-8 -*-
"""
迁移示例 - 如何使用新的图标管理系统

这个文件展示了如何将现有代码迁移到新的图标管理系统，
以及新系统的各种使用方式。
"""

# ========== 旧的写法（需要迁移） ==========
def old_way_example():
    """旧的写法示例 - 容易出现GBK编码错误"""
    safe_print("开始执行任务", "start")
    safe_print("采集新闻数据", "news")
    safe_print("任务完成", "success")
    # 在Windows环境下这些emoji可能导致编码错误


# ========== 新的写法（推荐） ==========
from icon_manager import get_icon, safe_print, format_message, IconManager, IconTheme
from logger_utils import get_logger, get_news_logger, log_info, log_success, log_error

def new_way_basic_example():
    """新写法示例1：基本用法"""
    # 方式1：使用快捷函数（最简单）
    safe_print("开始执行任务", "start")
    safe_print("采集新闻数据", "news") 
    safe_print("任务完成", "success")
    
    # 方式2：只获取图标，自己组合
    start_icon = get_icon("start")
    print(f"{start_icon} 开始执行任务")

def new_way_logger_example():
    """新写法示例2：使用日志器（推荐）"""
    logger = get_logger("example")
    
    # 使用统一的日志方法
    logger.start("开始执行任务")
    logger.info("正在采集新闻数据", "数据采集")
    logger.success("任务完成")
    logger.error("发生错误")
    logger.warning("这是一个警告")
    
    # 使用特定功能的日志方法
    logger.step(1, "第一步：初始化", 3)
    logger.step(2, "第二步：处理", 3)
    logger.step(3, "第三步：完成", 3)

def new_way_news_logger_example():
    """新写法示例3：使用新闻专用日志器"""
    news_logger = get_news_logger("测试关键词", "2025-01-18")
    
    # 脚本级别的日志
    news_logger.script_start("test_script.py", ["arg1", "arg2"])
    
    # 新闻系统特定的日志
    news_logger.news_fetch("测试关键词", 25, "Google News")
    news_logger.content_fetch("这是一个测试新闻标题", True, "trafilatura")
    news_logger.ai_score("这是一个测试新闻标题", 4)
    
    # 批量操作总结
    news_logger.batch_summary(10, 8, 2, "正文抓取")
    
    news_logger.script_complete("test_script.py", True)

def theme_switching_example():
    """主题切换示例"""
    print("\n=== 主题切换演示 ===")
    
    # 测试不同主题
    themes = [IconTheme.EMOJI, IconTheme.TEXT, IconTheme.COLORFUL, IconTheme.MINIMAL]
    
    for theme in themes:
        print(f"\n--- {theme.value.upper()} 主题 ---")
        manager = IconManager(theme, force_theme=True)
        
        manager.safe_print("这是成功消息", "success")
        manager.safe_print("这是错误消息", "error")
        manager.safe_print("这是警告消息", "warning")
        manager.safe_print("这是信息消息", "info")

def migration_guide_for_existing_files():
    """现有文件的迁移指南"""
    print("\n=== 现有文件迁移指南 ===")
    
    migration_rules = [
        {
            "旧代码": 'print("[开始] 开始执行")',
            "新代码": 'safe_print("开始执行", "start")',
            "或者": 'logger.start("开始执行")'
        },
        {
            "旧代码": 'print("[成功] 操作成功")',
            "新代码": 'safe_print("操作成功", "success")',
            "或者": 'logger.success("操作成功")'
        },
        {
            "旧代码": 'print("[错误] 操作失败")',
            "新代码": 'safe_print("操作失败", "error")',
            "或者": 'logger.error("操作失败")'
        },
        {
            "旧代码": 'print(f"[新闻] 采集到{count}条")',
            "新代码": 'safe_print(f"采集到{count}条", "news")',
            "或者": 'logger.news_fetch(keyword, count, source)'
        }
    ]
    
    for i, rule in enumerate(migration_rules, 1):
        print(f"\n{i}. 迁移规则:")
        print(f"   旧代码: {rule['旧代码']}")
        print(f"   新代码: {rule['新代码']}")
        print(f"   或者:   {rule['或者']}")

def compatibility_example():
    """兼容性示例 - 新旧系统并存"""
    from config import USE_ICON_MANAGER, ENABLE_LEGACY_UNICODE_CLEAN
    
    print(f"\n=== 兼容性配置 ===")
    print(f"USE_ICON_MANAGER: {USE_ICON_MANAGER}")
    print(f"ENABLE_LEGACY_UNICODE_CLEAN: {ENABLE_LEGACY_UNICODE_CLEAN}")
    
    # 可以同时使用新旧系统
    if USE_ICON_MANAGER:
        safe_print("使用新的图标管理器", "success")
    
    if ENABLE_LEGACY_UNICODE_CLEAN:
        # 仍然可以使用旧的clean_unicode_for_console函数
        from news_summarizer import clean_unicode_for_console
        old_style_msg = clean_unicode_for_console("旧系统的消息 📰")
        print(f"旧系统: {old_style_msg}")

def error_handling_example():
    """错误处理示例"""
    print("\n=== 错误处理演示 ===")
    
    # 新系统会自动处理编码错误
    manager = IconManager()
    
    # 即使包含特殊字符，也不会崩溃
    test_messages = [
        "包含emoji的消息 🎉",
        "包含Unicode字符的消息 ©®™",
        "普通的ASCII消息",
        "中文消息测试"
    ]
    
    for msg in test_messages:
        try:
            manager.safe_print(msg, "info", "[测试]")
        except Exception as e:
            print(f"处理消息时出错: {e}")

def performance_comparison():
    """性能对比示例"""
    import time
    
    print("\n=== 性能对比 ===")
    
    # 测试消息
    test_count = 1000
    test_message = "这是一个测试消息"
    
    # 旧方式（直接print）
    start_time = time.time()
    for i in range(test_count):
        print(f"[INFO] {test_message} {i}")
    old_time = time.time() - start_time
    
    # 新方式（使用图标管理器）
    start_time = time.time()
    for i in range(test_count):
        safe_print(f"{test_message} {i}", "info")
    new_time = time.time() - start_time
    
    print(f"旧方式耗时: {old_time:.3f}秒")
    print(f"新方式耗时: {new_time:.3f}秒")
    print(f"性能比: {new_time/old_time:.2f}x")

if __name__ == "__main__":
    print("=== 新图标管理系统迁移示例 ===\n")
    
    # 运行所有示例
    print("1. 基本用法示例:")
    new_way_basic_example()
    
    print("\n2. 日志器示例:")
    new_way_logger_example()
    
    print("\n3. 新闻日志器示例:")
    new_way_news_logger_example()
    
    # 主题切换演示
    theme_switching_example()
    
    # 迁移指南
    migration_guide_for_existing_files()
    
    # 兼容性示例
    compatibility_example()
    
    # 错误处理示例
    error_handling_example()
    
    # 性能对比（可选，注释掉避免输出过多）
    # performance_comparison()
    
    print("\n=== 迁移建议 ===")
    print("1. 优先使用 logger 方式，功能最完整")
    print("2. 简单场景可使用 safe_print 快捷函数")
    print("3. 新旧系统可以并存，逐步迁移")
    print("4. 在 config.py 中可以配置图标主题")
    print("5. 系统会自动检测环境并选择最佳主题") 