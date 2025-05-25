import os
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")  # GNews API Key
DEFAULT_KEYWORDS = ["公积金", "养老"]  # 可在此处修改默认关键词列表

API_KEY = os.getenv("SERPAPI_KEY") 