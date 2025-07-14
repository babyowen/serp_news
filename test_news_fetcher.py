import json
from news_fetcher import (
    fetch_serpapi_google_news,
    fetch_serpapi_baidu_news,
    fetch_serpapi_bing_news,
    fetch_serpapi_duckduckgo_news,
)
from datetime import datetime
import os

# --- 配置测试 ---
TEST_KEYWORD = "养老"  # 你可以换成任何想测试的关键词
SAVE_RESULTS = True     # 是否将测试结果保存到文件
OUTPUT_DIR = "output/test_results" # 测试结果保存目录

def run_test(fetch_function, source_name, keyword):
    """
    通用测试函数，用于执行单个API的采集并打印结果。
    """
    print(f"---[ 开始测试: {source_name} ]---")
    try:
        # 调用API采集函数
        data = fetch_function(keyword)
        
        # 从返回的数据中提取新闻列表
        if 'news_results' in data:
            results = data.get('news_results', [])
        elif 'organic_results' in data:
            results = data.get('organic_results', [])
        else:
            results = []
            
        news_count = len(results)
        print(f"[成功] {source_name} 采集到 {news_count} 条新闻。")
        
        if news_count > 0:
            print("  - 第一条新闻标题:", results[0].get('title'))

        if SAVE_RESULTS:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{source_name}.json"
            filepath = os.path.join(OUTPUT_DIR, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"  - 测试结果已保存到: {filepath}")

        # 基本断言，确保返回了正确的数据结构
        assert isinstance(data, dict), f"{source_name} 未返回字典"
        assert isinstance(results, list), f"{source_name} 的新闻列表不是 list"
        
        print(f"---[ 测试通过: {source_name} ]---\n")
        return True

    except Exception as e:
        print(f"[失败] {source_name} 测试过程中出现错误: {e}")
        print(f"---[ 测试失败: {source_name} ]---\n")
        return False

if __name__ == "__main__":
    print(f"=== 开始 API 参数修改效果测试 ===")
    print(f"测试关键词: '{TEST_KEYWORD}'")
    print("目标: 验证每个新闻源是否能在优化参数后正常返回数据。\n")
    
    # 依次测试四个新闻源
    run_test(fetch_serpapi_google_news, "Google News", TEST_KEYWORD)
    run_test(fetch_serpapi_baidu_news, "Baidu News", TEST_KEYWORD)
    run_test(fetch_serpapi_bing_news, "Bing News", TEST_KEYWORD)
    run_test(fetch_serpapi_duckduckgo_news, "DuckDuckGo News", TEST_KEYWORD)
    
    print("=== 所有测试执行完毕 ===")
    if SAVE_RESULTS:
        print(f"详细的JSON结果已保存在 '{OUTPUT_DIR}' 目录下，请查看。") 