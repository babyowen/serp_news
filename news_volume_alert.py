"""新闻量波动预警模块

在 main.py 流水线全部步骤执行完成后，从 scored_news 聚合当天和过去四个
同星期（-7/-14/-21/-28 天）的全量新闻数与高分新闻数（score >= 4），
计算波动率，abs(change_rate) > 20% 时通过飞书发送一张汇总卡片。

设计要点：
- 不新增任何持久化表/文件；只读 scored_news。
- 阈值、高分线、历史窗口均硬编码（按 issue #11 要求）。
- 自身抛出的任何异常必须由上层（main.py）吞掉，不影响流水线退出码。
"""
import os
import json
import logging
import subprocess
import datetime

from db_utils import get_connection, get_table_name

logger = logging.getLogger(__name__)

# ---- 常量（硬编码，按 issue #11）----
THRESHOLD_PCT = 20                # abs(change_rate) > 20 触发（严格大于）
HIGH_SCORE_MIN = 4                # score >= 4 计为高分
BASELINE_OFFSETS_DAYS = (7, 14, 21, 28)
DATE_FMT = "%Y-%m-%d"

METRIC_ICONS = {
    "全量新闻数": "📰",
    "高分新闻数": "⭐",
}


# ============== 数据查询 ==============

def _offset_date(target, days):
    d = datetime.datetime.strptime(target, DATE_FMT).date()
    return (d - datetime.timedelta(days=days)).strftime(DATE_FMT)


def fetch_counts(keywords, target_date):
    """一次 SQL 拉取所有 keywords × (target_date + 4 个历史日期) 的计数。

    Returns: {(keyword, date_str): (total, high)}。该关键词在该日完全没有
    记录的 (keyword, date) 不会出现在返回 dict 中——视为缺失样本。
    """
    keywords = list(keywords)
    all_dates = [target_date] + [_offset_date(target_date, d) for d in BASELINE_OFFSETS_DAYS]
    table = get_table_name()

    kw_ph = ",".join(["%s"] * len(keywords))
    date_ph = ",".join(["%s"] * len(all_dates))
    sql = (
        f"SELECT keyword, fetchdate, "
        f"  COUNT(*) AS total, "
        f"  SUM(CASE WHEN score IS NOT NULL AND score >= %s THEN 1 ELSE 0 END) AS high "
        f"FROM {table} "
        f"WHERE keyword IN ({kw_ph}) AND fetchdate IN ({date_ph}) "
        f"GROUP BY keyword, fetchdate"
    )
    params = [HIGH_SCORE_MIN] + keywords + all_dates

    conn = get_connection(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    result = {}
    for kw, fd, total, high in rows:
        result[(kw, str(fd))] = (int(total), int(high or 0))
    return result


def _make_query_fn_from_counts(counts):
    """从 batch 查询结果构造 (keyword, date) -> (total, high) 闭包，复用 collect_history。"""
    def _q(keyword, date):
        return counts.get((keyword, date), (0, 0))
    return _q


# ============== 基线计算（纯函数，便于测试）==============

def compute_baseline(samples):
    """samples: list of (total, high) from dates that had data.

    Returns dict(avg_total, avg_high, sample_size) or None if no samples.
    """
    if not samples:
        return None
    n = len(samples)
    return {
        "avg_total": sum(s[0] for s in samples) / n,
        "avg_high": sum(s[1] for s in samples) / n,
        "sample_size": n,
    }


def collect_history(keyword, target_date, query_fn=None):
    """对单关键词采集最多 4 个有效历史样本。

    `query_fn(keyword, date) -> (total, high)` 用于解耦 DB（测试时注入桩件）。
    该日 total=0 视为缺失样本；total>0 但 high=0 视为有效样本的 0。
    """
    q = query_fn or _query_counts
    samples = []
    for offset in BASELINE_OFFSETS_DAYS:
        past = _offset_date(target_date, offset)
        total, high = q(keyword, past)
        if total > 0:
            samples.append((total, high))
    return compute_baseline(samples)


# ============== 异常检测（纯函数，便于测试）==============

def _change_rate(today, baseline):
    """百分比变化率。baseline 为 0 时返回 None（交由调用方区分）。"""
    if baseline == 0:
        return None
    return (today - baseline) / baseline * 100


def _evaluate_metric(keyword, metric_name, today_val, base_val, sample_size):
    """评估单个指标。

    Returns: (is_anomaly, item_dict)
    - 基线 0 且今日 >0：异常（新增）
    - 基线 0 且今日 0：正常
    - abs(change_rate) > 阈值：异常
    - 否则：正常
    """
    rate = _change_rate(float(today_val), float(base_val))
    if rate is None:
        # 基线为 0
        if today_val > 0:
            return True, {
                "keyword": keyword, "metric": metric_name,
                "today": today_val, "baseline_avg": 0.0,
                "sample_size": sample_size,
                "change_rate": None, "direction": "新增",
            }
        return False, {
            "keyword": keyword, "metric": metric_name,
            "today": today_val, "baseline_avg": 0.0,
            "sample_size": sample_size,
            "change_rate": 0.0,
        }
    if abs(rate) > THRESHOLD_PCT:  # 严格大于，恰好 20% 不触发
        return True, {
            "keyword": keyword, "metric": metric_name,
            "today": today_val, "baseline_avg": base_val,
            "sample_size": sample_size,
            "change_rate": rate,
            "direction": "上升" if rate > 0 else "下降",
        }
    return False, {
        "keyword": keyword, "metric": metric_name,
        "today": today_val, "baseline_avg": base_val,
        "sample_size": sample_size,
        "change_rate": rate,
    }


def detect(keyword, today, baseline):
    """对单关键词的两项指标做评估，返回 (anomalies, normals)。

    today: (total_today, high_today)
    baseline: dict from compute_baseline or None
    """
    if baseline is None:
        return [], []
    anomalies, normals = [], []
    today_total, today_high = today
    metrics = [
        ("全量新闻数", today_total, baseline["avg_total"]),
        ("高分新闻数", today_high, baseline["avg_high"]),
    ]
    for name, today_val, base_val in metrics:
        is_anomaly, item = _evaluate_metric(
            keyword, name, today_val, base_val, baseline["sample_size"])
        if is_anomaly:
            anomalies.append(item)
        else:
            normals.append(item)
    return anomalies, normals


def detect_anomalies(keyword, today, baseline):
    """旧接口：仅返回异常列表（保持向后兼容）。"""
    anomalies, _ = detect(keyword, today, baseline)
    return anomalies


# ============== 飞书卡片构造 ==============

def build_card(target_date, anomalies, normals=None):
    """构造飞书交互卡片 JSON 字符串。

    anomalies: 必填，异常项列表
    normals:   可选，正常项列表；提供时在卡片末尾渲染"正常项"节
    """
    title = f"新闻量波动预警（{target_date}）"

    by_kw = {}
    for a in anomalies:
        by_kw.setdefault(a["keyword"], []).append(a)

    elements = [
        {"tag": "div", "text": {"tag": "lark_md",
            "content": f"**统计日期** {target_date}  **异常项** {len(anomalies)} 个"}},
        {"tag": "hr"},
    ]
    for kw, items in by_kw.items():
        lines = [f"**{kw}**"]
        for a in items:
            icon = METRIC_ICONS.get(a["metric"], "")
            prefix = f"{icon} " if icon else ""
            if a["change_rate"] is None:
                rate_str = "基线为 0 / 新增"
            else:
                rate_str = f"{abs(a['change_rate']):.1f}% {a['direction']}"
            lines.append(
                f"- {prefix}{a['metric']}：今日 {a['today']}，"
                f"历史均值 {a['baseline_avg']:.1f}（{a['sample_size']}样本），"
                f"{rate_str}"
            )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    # 正常项汇总节
    if normals:
        normal_lines = [f"**正常项（{len(normals)} 个）**"]
        for n in normals:
            icon = METRIC_ICONS.get(n["metric"], "")
            prefix = f"{icon} " if icon else ""
            rate = n.get("change_rate")
            if rate is not None and rate != 0:
                rate_str = f"（{rate:+.1f}%）"
            else:
                rate_str = ""
            normal_lines.append(
                f"- {prefix}{n['keyword']}：今日 {n['today']} / 均值 {n['baseline_avg']:.1f}{rate_str}"
            )
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": "\n".join(normal_lines)}})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "orange",
        },
        "elements": elements,
    }
    return json.dumps(card, ensure_ascii=False)


# ============== 飞书发送（复用 tobacco_gov_crawler 模式）==============

def send_feishu(card_json):
    user_id = os.getenv("FEISHU_USER_ID")
    lark_cli = os.getenv("LARK_CLI_PATH",
                         "/Users/babyowen/.nvm/versions/node/v24.11.0/bin/lark-cli")
    if not user_id:
        logger.info("NewsVolumeAlert | skipped (FEISHU_USER_ID not set)")
        print("[INFO] [预警] FEISHU_USER_ID 未配置，跳过通知")
        return False
    try:
        result = subprocess.run(
            [lark_cli, "im", "+messages-send", "--user-id", user_id,
             "--msg-type", "interactive", "--content", card_json],
            capture_output=True, text=True, timeout=30, env=os.environ
        )
        if result.returncode == 0:
            logger.info(f"NewsVolumeAlert | sent to {user_id}")
            print(f"[INFO] [预警] 飞书通知已发送给 {user_id}")
            return True
        logger.error(f"NewsVolumeAlert | lark-cli exit={result.returncode}: "
                     f"{result.stderr[:200]}")
        print(f"[WARN] [预警] lark-cli 退出码 {result.returncode}")
        return False
    except Exception as e:
        logger.error(f"NewsVolumeAlert | failed: {type(e).__name__}: {e}")
        print(f"[WARN] [预警] 飞书发送失败: {type(e).__name__}: {e}")
        return False


# ============== 主入口 ==============

def run(target_date, keywords, query_fn=None, send_fn=None, counts_fn=None):
    """主入口：聚合所有关键词的异常，发送一张汇总卡片。

    Args:
        target_date: YYYY-MM-DD
        keywords: iterable of main keywords
        query_fn: 用于测试注入的逐次查询桩件；提供时优先使用
        send_fn:  用于测试注入的发送桩件，默认走 send_feishu
        counts_fn: 测试注入的批量查询桩件；提供时优先于真实 fetch_counts

    Returns: list of anomaly dicts
    """
    keywords = list(keywords)
    print(f"\n[预警] 开始新闻量波动预警，target_date={target_date}, "
          f"keywords={keywords}")

    # 默认走真实批量 SQL（一次连接查全部）；测试可注入 query_fn 或 counts_fn
    if query_fn is None:
        counts = counts_fn(keywords, target_date) if counts_fn else fetch_counts(keywords, target_date)
        query_fn = _make_query_fn_from_counts(counts)
    sender = send_fn or send_feishu

    anomalies, normals = [], []
    for kw in keywords:
        try:
            today = query_fn(kw, target_date)
            baseline = collect_history(kw, target_date, query_fn=query_fn)
            a, n = detect(kw, today, baseline)
            anomalies.extend(a)
            normals.extend(n)
        except Exception as e:
            logger.error(f"NewsVolumeAlert | keyword={kw} 计算失败: {e}")
            print(f"[WARN] [预警] 关键词 {kw} 计算失败: {e}")
            continue

    print(f"[INFO] [预警] 共发现 {len(anomalies)} 个异常项，{len(normals)} 个正常项")

    if anomalies:
        card_json = build_card(target_date, anomalies, normals)
        sender(card_json)
    else:
        print("[INFO] [预警] 无异常，不发送飞书通知")

    return anomalies
