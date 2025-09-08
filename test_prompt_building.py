# -*- coding: utf-8 -*-
"""
测试脚本：查看AI评分的完整prompt构建过程
使用2025-08-15的数据，每个关键词取第一条新闻进行测试
"""

import json
import os
from config import (
    NEWS_SCORE_PROMPT, 
    NEWS_SCORE_SYSTEM_MSG, 
    KEYWORD_SPECIFIC_SYSTEM_PROMPTS,
    SEARCH_KEYWORDS
)

def clean_unicode_for_console(text):
    """清理文本中的特殊Unicode字符，避免Windows GBK编码错误"""
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
    }
    
    cleaned_text = text
    for unicode_char, replacement in replacements.items():
        cleaned_text = cleaned_text.replace(unicode_char, replacement)
    
    return cleaned_text

def build_prompt_for_news(news_item, main_keyword):
    """
    构建完整的AI评分prompt，模拟news_scorer.py中的逻辑
    """
    title = news_item.get("title", "")
    content = news_item.get("content", "")
    
    # 获取搜索关键词，如果没有则使用主关键词
    search_keyword = news_item.get("search_keyword", main_keyword)
    
    # 构建user prompt
    user_prompt = NEWS_SCORE_PROMPT.format(keyword=search_keyword, title=title, content=content)
    
    # 选择system prompt
    system_prompt = KEYWORD_SPECIFIC_SYSTEM_PROMPTS.get(main_keyword, NEWS_SCORE_SYSTEM_MSG)
    
    return {
        "main_keyword": main_keyword,
        "search_keyword": search_keyword,
        "title": title,
        "content_preview": content[:200] + "..." if len(content) > 200 else content,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "full_prompt": f"System: {system_prompt}\n\nUser: {user_prompt}"
    }

def test_prompt_building():
    """测试prompt构建过程"""
    date_str = "2025-08-15"
    data_dir = f"output/{date_str}"
    
    print("=" * 80)
    print("AI评分Prompt构建测试")
    print("=" * 80)
    
    # 获取所有主关键词
    main_keywords = list(SEARCH_KEYWORDS.keys())
    
    for main_keyword in main_keywords:
        json_file = f"{date_str}_{main_keyword}.json"
        json_path = os.path.join(data_dir, json_file)
        
        if not os.path.exists(json_path):
            print(f"\n❌ 文件不存在: {json_file}")
            continue
        
        try:
            # 读取JSON文件
            with open(json_path, "r", encoding="utf-8") as f:
                news_list = json.load(f)
            
            if not news_list:
                print(f"\n❌ 文件为空: {json_file}")
                continue
            
            # 取第一条新闻
            first_news = news_list[0]
            
            # 构建prompt
            prompt_data = build_prompt_for_news(first_news, main_keyword)
            
            # 显示结果
            print(f"\n{'='*60}")
            print(f"主关键词: {prompt_data['main_keyword']}")
            print(f"搜索关键词: {prompt_data['search_keyword']}")
            print(f"新闻标题: {prompt_data['title']}")
            print(f"内容预览: {prompt_data['content_preview']}")
            print(f"{'='*60}")
            
            print("\n【System Prompt】")
            print("-" * 40)
            print(clean_unicode_for_console(prompt_data['system_prompt']))
            
            print("\n【User Prompt】")
            print("-" * 40)
            print(clean_unicode_for_console(prompt_data['user_prompt']))
            
            print(f"\n【完整Prompt Token数估算】")
            print(f"System prompt长度: {len(prompt_data['system_prompt'])} 字符")
            print(f"User prompt长度: {len(prompt_data['user_prompt'])} 字符")
            print(f"总计长度: {len(prompt_data['full_prompt'])} 字符")
            
            print(f"\n{'='*60}")
            print(f"✅ {main_keyword} 关键词测试完成")
            print(f"{'='*60}")
            
        except Exception as e:
            print(f"\n❌ 处理文件 {json_file} 时出错: {e}")
            continue
    
    print(f"\n{'='*80}")
    print("测试完成！")
    print("=" * 80)

if __name__ == "__main__":
    test_prompt_building()