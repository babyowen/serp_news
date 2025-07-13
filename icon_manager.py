# -*- coding: utf-8 -*-
"""
图标管理器 - 跨平台emoji和图标解决方案
主要功能：
1. 自动检测运行环境（Windows/Mac/Linux）
2. 提供多种图标主题（emoji/text/colorful）
3. 统一的图标API，便于维护
4. 支持配置化切换
"""

import os
import sys
import platform
from enum import Enum
from typing import Dict, Optional


class IconTheme(Enum):
    """图标主题枚举"""
    EMOJI = "emoji"          # 使用emoji图标（Mac/Linux推荐）
    TEXT = "text"            # 使用纯文本标记（Windows兼容）
    COLORFUL = "colorful"    # 使用彩色文本（支持ANSI颜色）
    MINIMAL = "minimal"      # 最简化文本


class EnvironmentType(Enum):
    """环境类型枚举"""
    WINDOWS = "windows"
    MAC = "mac"
    LINUX = "linux"
    UNKNOWN = "unknown"


class IconManager:
    """图标管理器主类"""
    
    def __init__(self, theme: Optional[IconTheme] = None, force_theme: bool = False):
        """
        初始化图标管理器
        
        Args:
            theme: 指定使用的主题，如果为None则自动检测
            force_theme: 是否强制使用指定主题，忽略环境检测
        """
        self.environment = self._detect_environment()
        self.encoding = self._detect_encoding()
        
        if theme is None:
            self.theme = self._auto_select_theme()
        else:
            self.theme = theme
            
        self.force_theme = force_theme
        
        # 定义图标映射表
        self._icon_maps = self._init_icon_maps()
        
        # 输出环境信息
        self._log_environment_info()
    
    def _detect_environment(self) -> EnvironmentType:
        """检测运行环境"""
        system = platform.system().lower()
        if system == "windows":
            return EnvironmentType.WINDOWS
        elif system == "darwin":
            return EnvironmentType.MAC
        elif system == "linux":
            return EnvironmentType.LINUX
        else:
            return EnvironmentType.UNKNOWN
    
    def _detect_encoding(self) -> str:
        """检测系统编码"""
        try:
            return sys.stdout.encoding or 'utf-8'
        except:
            return 'utf-8'
    
    def _auto_select_theme(self) -> IconTheme:
        """根据环境自动选择最佳主题"""
        if self.environment == EnvironmentType.WINDOWS:
            # Windows环境，检查是否支持UTF-8
            if self.encoding.lower() in ['utf-8', 'utf8']:
                return IconTheme.COLORFUL
            else:
                return IconTheme.TEXT
        elif self.environment in [EnvironmentType.MAC, EnvironmentType.LINUX]:
            # Mac/Linux环境，优先使用emoji
            return IconTheme.EMOJI
        else:
            # 未知环境，使用最安全的文本模式
            return IconTheme.TEXT
    
    def _init_icon_maps(self) -> Dict[IconTheme, Dict[str, str]]:
        """初始化图标映射表"""
        return {
            IconTheme.EMOJI: {
                # 状态图标
                "success": "✅",
                "error": "❌", 
                "warning": "⚠️",
                "info": "ℹ️",
                "start": "🚀",
                "complete": "🎉",
                "skip": "⏭️",
                "stop": "⏹️",
                
                # 功能图标
                "time": "🕒",
                "key": "🔑",
                "search": "🔍",
                "news": "📰",
                "file": "📄",
                "folder": "📁",
                "save": "💾",
                "download": "⬇️",
                "upload": "⬆️",
                
                # 工具图标
                "ai": "🤖",
                "optimize": "🔧",
                "settings": "⚙️",
                "stats": "📊",
                "chart": "📈",
                "list": "📋",
                "target": "🎯",
                
                # 阶段图标
                "stage1": "1️⃣",
                "stage2": "2️⃣", 
                "stage3": "3️⃣",
                "stage4": "4️⃣",
                "stage5": "5️⃣",
                
                # 网络图标
                "web": "🌐",
                "api": "🔌",
                "database": "🗄️",
                "server": "🖥️",
            },
            
            IconTheme.TEXT: {
                # 状态图标
                "success": "[OK]",
                "error": "[ERROR]",
                "warning": "[WARN]", 
                "info": "[INFO]",
                "start": "[START]",
                "complete": "[DONE]",
                "skip": "[SKIP]",
                "stop": "[STOP]",
                
                # 功能图标
                "time": "[TIME]",
                "key": "[KEY]",
                "search": "[SEARCH]",
                "news": "[NEWS]",
                "file": "[FILE]",
                "folder": "[FOLDER]",
                "save": "[SAVE]",
                "download": "[DOWN]",
                "upload": "[UP]",
                
                # 工具图标
                "ai": "[AI]",
                "optimize": "[OPT]",
                "settings": "[SET]",
                "stats": "[STATS]",
                "chart": "[CHART]",
                "list": "[LIST]",
                "target": "[TARGET]",
                
                # 阶段图标
                "stage1": "[1]",
                "stage2": "[2]",
                "stage3": "[3]", 
                "stage4": "[4]",
                "stage5": "[5]",
                
                # 网络图标
                "web": "[WEB]",
                "api": "[API]",
                "database": "[DB]",
                "server": "[SERVER]",
            },
            
            IconTheme.COLORFUL: {
                # 状态图标（使用ANSI颜色）
                "success": "\033[92m[OK]\033[0m",      # 绿色
                "error": "\033[91m[ERROR]\033[0m",     # 红色
                "warning": "\033[93m[WARN]\033[0m",    # 黄色
                "info": "\033[94m[INFO]\033[0m",       # 蓝色
                "start": "\033[95m[START]\033[0m",     # 紫色
                "complete": "\033[92m[DONE]\033[0m",   # 绿色
                "skip": "\033[96m[SKIP]\033[0m",       # 青色
                "stop": "\033[91m[STOP]\033[0m",       # 红色
                
                # 功能图标
                "time": "\033[96m[TIME]\033[0m",       # 青色
                "key": "\033[93m[KEY]\033[0m",         # 黄色
                "search": "\033[94m[SEARCH]\033[0m",   # 蓝色
                "news": "\033[95m[NEWS]\033[0m",       # 紫色
                "file": "\033[97m[FILE]\033[0m",       # 白色
                "folder": "\033[97m[FOLDER]\033[0m",   # 白色
                "save": "\033[92m[SAVE]\033[0m",       # 绿色
                "download": "\033[94m[DOWN]\033[0m",   # 蓝色
                "upload": "\033[94m[UP]\033[0m",       # 蓝色
                
                # 工具图标
                "ai": "\033[96m[AI]\033[0m",           # 青色
                "optimize": "\033[93m[OPT]\033[0m",    # 黄色
                "settings": "\033[97m[SET]\033[0m",    # 白色
                "stats": "\033[92m[STATS]\033[0m",     # 绿色
                "chart": "\033[92m[CHART]\033[0m",     # 绿色
                "list": "\033[97m[LIST]\033[0m",       # 白色
                "target": "\033[91m[TARGET]\033[0m",   # 红色
                
                # 阶段图标
                "stage1": "\033[91m[1]\033[0m",        # 红色
                "stage2": "\033[93m[2]\033[0m",        # 黄色
                "stage3": "\033[92m[3]\033[0m",        # 绿色
                "stage4": "\033[94m[4]\033[0m",        # 蓝色
                "stage5": "\033[95m[5]\033[0m",        # 紫色
                
                # 网络图标
                "web": "\033[94m[WEB]\033[0m",         # 蓝色
                "api": "\033[96m[API]\033[0m",         # 青色
                "database": "\033[93m[DB]\033[0m",     # 黄色
                "server": "\033[97m[SERVER]\033[0m",   # 白色
            },
            
            IconTheme.MINIMAL: {
                # 最简化的文本标记
                "success": "OK",
                "error": "ERR",
                "warning": "WARN",
                "info": "INFO", 
                "start": "START",
                "complete": "DONE",
                "skip": "SKIP",
                "stop": "STOP",
                "time": "TIME",
                "key": "KEY",
                "search": "SEARCH",
                "news": "NEWS",
                "file": "FILE",
                "folder": "DIR",
                "save": "SAVE",
                "download": "DOWN",
                "upload": "UP",
                "ai": "AI",
                "optimize": "OPT",
                "settings": "CFG",
                "stats": "STAT",
                "chart": "CHART",
                "list": "LIST",
                "target": "TGT",
                "stage1": "1",
                "stage2": "2",
                "stage3": "3",
                "stage4": "4", 
                "stage5": "5",
                "web": "WEB",
                "api": "API",
                "database": "DB",
                "server": "SRV",
            }
        }
    
    def _log_environment_info(self):
        """输出环境信息（仅在首次初始化时）"""
        if not hasattr(self.__class__, '_logged'):
            print(f"[IconManager] 环境: {self.environment.value}")
            print(f"[IconManager] 编码: {self.encoding}")
            print(f"[IconManager] 主题: {self.theme.value}")
            self.__class__._logged = True
    
    def get_icon(self, icon_name: str, fallback: str = "") -> str:
        """
        获取图标
        
        Args:
            icon_name: 图标名称
            fallback: 当图标不存在时的回退文本
            
        Returns:
            图标字符串
        """
        icon_map = self._icon_maps.get(self.theme, {})
        return icon_map.get(icon_name, fallback or f"[{icon_name.upper()}]")
    
    def format_message(self, message: str, icon_name: str = "", prefix: str = "") -> str:
        """
        格式化消息，添加图标前缀
        
        Args:
            message: 消息内容
            icon_name: 图标名称
            prefix: 额外前缀
            
        Returns:
            格式化后的消息
        """
        parts = []
        
        if icon_name:
            parts.append(self.get_icon(icon_name))
        
        if prefix:
            parts.append(prefix)
            
        parts.append(message)
        
        return " ".join(parts)
    
    def safe_print(self, message: str, icon_name: str = "", prefix: str = "", **kwargs):
        """
        安全打印，自动处理编码问题
        
        Args:
            message: 消息内容
            icon_name: 图标名称  
            prefix: 额外前缀
            **kwargs: print函数的其他参数
        """
        formatted_msg = self.format_message(message, icon_name, prefix)
        
        try:
            print(formatted_msg, **kwargs)
        except UnicodeEncodeError:
            # 如果编码失败，降级到纯文本模式
            if self.theme != IconTheme.MINIMAL:
                backup_manager = IconManager(IconTheme.MINIMAL, force_theme=True)
                safe_msg = backup_manager.format_message(message, icon_name, prefix)
                print(safe_msg, **kwargs)
            else:
                # 最后的安全措施：移除所有非ASCII字符
                safe_msg = message.encode('ascii', 'ignore').decode('ascii')
                print(f"[{icon_name.upper()}] {prefix} {safe_msg}" if icon_name else f"{prefix} {safe_msg}", **kwargs)
    
    def get_available_icons(self) -> list:
        """获取当前主题下所有可用的图标名称"""
        return list(self._icon_maps.get(self.theme, {}).keys())
    
    def switch_theme(self, new_theme: IconTheme):
        """切换主题"""
        self.theme = new_theme
        print(f"[IconManager] 已切换到主题: {new_theme.value}")


# 创建全局图标管理器实例
_global_icon_manager = None

def get_icon_manager() -> IconManager:
    """获取全局图标管理器实例"""
    global _global_icon_manager
    if _global_icon_manager is None:
        _global_icon_manager = IconManager()
    return _global_icon_manager

def get_icon(icon_name: str, fallback: str = "") -> str:
    """快捷函数：获取图标"""
    return get_icon_manager().get_icon(icon_name, fallback)

def safe_print(message: str, icon_name: str = "", prefix: str = "", **kwargs):
    """快捷函数：安全打印"""
    get_icon_manager().safe_print(message, icon_name, prefix, **kwargs)

def format_message(message: str, icon_name: str = "", prefix: str = "") -> str:
    """快捷函数：格式化消息"""
    return get_icon_manager().format_message(message, icon_name, prefix)


if __name__ == "__main__":
    # 测试代码
    print("=== 图标管理器测试 ===")
    
    # 测试所有主题
    for theme in IconTheme:
        print(f"\n--- {theme.value.upper()} 主题 ---")
        manager = IconManager(theme, force_theme=True)
        
        # 测试常用图标
        test_icons = ["success", "error", "warning", "start", "complete", "ai", "news", "time"]
        for icon in test_icons:
            print(f"{manager.get_icon(icon)} {icon}")
    
    # 测试safe_print
    print("\n--- 安全打印测试 ---")
    safe_print("这是一个测试消息", "info", "[TEST]")
    safe_print("成功完成任务", "success")
    safe_print("发生错误", "error") 