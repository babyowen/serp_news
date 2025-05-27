import os
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")  # GNews API Key
DEFAULT_KEYWORDS = ["公积金", "养老"]  # 可在此处修改默认关键词列表

API_KEY = os.getenv("SERPAPI_KEY")

# 黑名单关键词
blacklist_keywords = [
    '星岛环球网','8world','诗华资讯','日经中文网','lianhe','南洋商报','两个至上','ntdtv','看中国','vietnamplus','swissinfo','英为财情','Wall Street Journal','法国国际广播电台','新唐人電視台','China Digital Times','大紀元','DW','文学城','禁闻网','BBC.com','自由亚洲电台','焦点新闻','美国之音','人民报','议报'
] 