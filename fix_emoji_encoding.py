#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复emoji编码问题的脚本
将代码中的emoji字符替换为配置化的版本，避免Windows GBK编码错误
"""

import os
import re
from config import EMOJI_MAP

def fix_emoji_in_file(file_path):
    """修复单个文件中的emoji字符"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # 替换emoji字符
        for emoji, replacement in EMOJI_MAP.items():
            # 使用get_emoji函数替换直接的emoji字符
            pattern = f'"{emoji}"'
            replacement_pattern = f'get_emoji("{emoji}")'
            content = content.replace(pattern, replacement_pattern)
            
            # 处理f-string中的emoji
            pattern = f"f\"([^\"]*){emoji}([^\"]*)\""
            def replace_fstring(match):
                before = match.group(1)
                after = match.group(2)
                return f'f"{before}{{get_emoji("{emoji}")}}{after}"'
            content = re.sub(pattern, replace_fstring, content)
            
            # 处理普通字符串中的emoji
            pattern = f"(['\"])([^'\"]*){emoji}([^'\"]*)['\"]"
            def replace_string(match):
                quote = match.group(1)
                before = match.group(2)
                after = match.group(3)
                return f'{quote}{before}{{get_emoji("{emoji}")}}{after}{quote}'
            content = re.sub(pattern, replace_string, content)
        
        # 如果内容有变化，写回文件
        if content != original_content:
            # 添加import语句
            if 'from config import' in content and 'get_emoji' not in content:
                content = content.replace(
                    'from config import',
                    'from config import get_emoji,'
                )
            elif 'import' in content and 'from config import get_emoji' not in content:
                # 在第一个import语句后添加
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if line.strip().startswith('import ') or line.strip().startswith('from '):
                        lines.insert(i + 1, 'from config import get_emoji')
                        break
                content = '\n'.join(lines)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"[FIXED] {file_path}")
            return True
        else:
            print(f"[SKIP] {file_path} - 无需修改")
            return False
            
    except Exception as e:
        print(f"[ERROR] 修复 {file_path} 失败: {e}")
        return False

def main():
    """主函数：修复所有Python文件中的emoji编码问题"""
    print("开始修复emoji编码问题...")
    
    # 需要修复的Python文件列表
    python_files = [
        'main.py',
        'news_scorer.py', 
        'news_summarizer.py',
        'fetch_content.py',
        'fetch_and_filter.py',
        'error_handler.py',
        'write_to_mysql.py'
    ]
    
    fixed_count = 0
    total_count = 0
    
    for file_path in python_files:
        if os.path.exists(file_path):
            total_count += 1
            if fix_emoji_in_file(file_path):
                fixed_count += 1
        else:
            print(f"[WARN] 文件不存在: {file_path}")
    
    print(f"\n修复完成！")
    print(f"总文件数: {total_count}")
    print(f"已修复: {fixed_count}")
    print(f"无需修改: {total_count - fixed_count}")
    
    print("\n使用说明：")
    print("1. 如果在Windows上遇到GBK编码错误，请在config.py中设置：")
    print("   USE_EMOJI_OUTPUT = False")
    print("2. 在Linux/Mac上可以保持：")
    print("   USE_EMOJI_OUTPUT = True")

if __name__ == "__main__":
    main() 