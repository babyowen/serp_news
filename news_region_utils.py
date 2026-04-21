import json
import re
import time

from openai import OpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION,
    NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION,
    NEWS_REGION_SYSTEM_PROMPT_GJJ,
    NEWS_REGION_USER_PROMPT_GJJ,
)
from icon_manager import safe_print


ALLOWED_REGION_TABLES = {"scored_news", "scored_news_test"}
REGION_EMPTY_VALUES = {
    "",
    "null",
    "none",
    "n/a",
    "na",
    "未知",
    "无法判断",
    "无法确定",
    "不确定",
    "未提及",
    "未明确",
    "不详",
    "无",
}
AUTONOMOUS_REGION_MAP = {
    "内蒙古自治区": "内蒙古",
    "广西壮族自治区": "广西",
    "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}
ETHNIC_GROUP_PATTERN = (
    r"(?:藏族羌族|土家族苗族|蒙古族藏族|哈尼族彝族|傣族景颇族|"
    r"苗族侗族|布依族苗族|朝鲜族|哈萨克族|柯尔克孜|景颇族|"
    r"傈僳族|土家族|布依族|蒙古族|壮族|藏族|羌族|回族|彝族|"
    r"傣族|白族|哈尼族|苗族|侗族|满族)+"
)


class DeepSeekClientPool:
    def __init__(self):
        self._client = None
        self._usage = 0
        self._max_usage = 200

    def get(self):
        if self._client is None or self._usage >= self._max_usage:
            self._client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            self._usage = 0
        self._usage += 1
        return self._client


_pool = DeepSeekClientPool()


def validate_region_table_name(table_name):
    if table_name not in ALLOWED_REGION_TABLES:
        raise ValueError(f"Unsupported table name: {table_name}")
    return table_name


def table_has_region_column(cursor, table_name):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = 'region'
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _call_llm(system_prompt, user_prompt, max_retries=3):
    safe_print(f"[模型] deepseek-chat @ {DEEPSEEK_BASE_URL}")
    safe_print(f"【LLM system前120字】 {system_prompt.strip()[:120].replace(chr(10), ' ')}")
    safe_print(f"【LLM user前200字】 {user_prompt.strip()[:200].replace(chr(10), ' ')}")
    backoffs = [5, 10, 20]

    for attempt in range(max_retries):
        client = _pool.get()
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                temperature=0.2,
                timeout=60,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            safe_print(f"[LLM错误] {str(exc)}")
            if attempt < max_retries - 1:
                time.sleep(backoffs[attempt])
    return None


def _extract_json_payload(raw_text):
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _normalize_region_token(token):
    if token is None:
        return None

    token = str(token).strip()
    token = token.strip("\"'")
    token = re.sub(r"[（(].*?[)）]", "", token).strip()
    token = token.replace("，", "").replace(",", "")

    if not token:
        return None

    lowered = token.lower()
    if lowered in REGION_EMPTY_VALUES:
        return None

    if token in {"中国", "国内", "全国性", "国家级", "国家"} or "全国" in token:
        return "全国"

    for full_name, normalized in AUTONOMOUS_REGION_MAP.items():
        if full_name in token:
            if "市" in token[token.index(full_name) + len(full_name) :]:
                city_part = token[token.index(full_name) + len(full_name) :]
                city_name = city_part.split("市", 1)[0].strip()
                return city_name or normalized
            return normalized

    if "省" in token and "市" in token and token.index("省") < token.index("市"):
        city_name = token[token.index("省") + 1 : token.index("市")].strip()
        if city_name:
            return city_name

    if token.endswith("特别行政区"):
        return token.replace("特别行政区", "").strip() or None

    if token.endswith("自治区"):
        return token.replace("自治区", "").strip() or None

    if "地区" in token:
        prefix = token.split("地区", 1)[0].strip()
        if prefix:
            return f"{prefix}地区"

    if "自治州" in token:
        matched = re.match(rf"^(.+?){ETHNIC_GROUP_PATTERN}自治州", token)
        if matched:
            return f"{matched.group(1)}州"
        return token.split("自治州", 1)[0].strip() + "州"

    if "州" in token:
        prefix = token.split("州", 1)[0].strip()
        if prefix:
            return f"{prefix}州"

    if "盟" in token:
        prefix = token.split("盟", 1)[0].strip()
        if prefix:
            return f"{prefix}盟"

    if "市" in token:
        city_name = token.split("市", 1)[0].strip()
        if city_name:
            return city_name

    if token.endswith("省"):
        return token[:-1].strip() or None

    return token or None


def normalize_region(raw_region):
    if raw_region is None:
        return None

    if isinstance(raw_region, list):
        items = raw_region
    else:
        text = str(raw_region).strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = parsed
                else:
                    items = [text]
            except json.JSONDecodeError:
                items = re.split(r"[|｜、，,;/；\n]+", text)
        else:
            items = re.split(r"[|｜、，,;/；\n]+", text)

    normalized_items = []
    seen = set()
    for item in items:
        normalized = _normalize_region_token(item)
        if not normalized:
            continue
        if normalized == "全国":
            return "全国"
        if normalized not in seen:
            seen.add(normalized)
            normalized_items.append(normalized)

    if not normalized_items:
        return None
    return "|".join(normalized_items)


def call_summary_and_region_llm(title, content, max_retries=3):
    user_prompt = NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION.format(
        title=(title or "").strip(),
        content=(content or "").strip(),
    )
    raw_text = _call_llm(
        NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION,
        user_prompt,
        max_retries=max_retries,
    )
    payload = _extract_json_payload(raw_text)
    if not isinstance(payload, dict):
        return None

    short_summary = str(payload.get("short_summary", "")).strip()
    if not short_summary:
        return None

    return {
        "short_summary": short_summary,
        "region": normalize_region(payload.get("region")),
    }


def call_region_llm(title, content, max_retries=3):
    user_prompt = NEWS_REGION_USER_PROMPT_GJJ.format(
        title=(title or "").strip(),
        content=(content or "").strip(),
    )
    raw_text = _call_llm(
        NEWS_REGION_SYSTEM_PROMPT_GJJ,
        user_prompt,
        max_retries=max_retries,
    )

    payload = _extract_json_payload(raw_text)
    if isinstance(payload, dict) and "region" in payload:
        raw_region = payload.get("region")
    else:
        raw_region = raw_text

    return normalize_region(raw_region)
