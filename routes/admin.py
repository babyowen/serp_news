"""管理路由 — 关键词配置、模型配置、运行监控、重评（需认证）"""
import json
import os
import sys
from flask import render_template, request, redirect, url_for, flash, Blueprint
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash
from config_manager import read_keywords, write_keywords, read_model_config
from run_manager import RunManager
from db_utils import get_connection, get_table_name
from config import DEFAULT_KEYWORDS
from news_business_type_utils import (
    count_secondary_label_uses,
    get_business_type_alias_records,
    get_business_type_dashboard,
    get_business_type_label_stats,
    load_business_type_aliases,
    merge_secondary_labels,
    table_has_business_types_column,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
auth = HTTPBasicAuth()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
users = {ADMIN_USERNAME: generate_password_hash(ADMIN_PASSWORD)}


@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None


@admin_bp.before_request
@auth.login_required
def login_required():
    pass


run_mgr = RunManager()


@admin_bp.route("/keywords", methods=["GET", "POST"])
def keywords():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            main_kw = request.form.get("main_keyword", "").strip()
            search_kw_str = request.form.get("search_keywords", "").strip()
            if main_kw and search_kw_str:
                kw = read_keywords()
                search_list = [s.strip() for s in search_kw_str.split(",") if s.strip()]
                kw[main_kw] = search_list
                write_keywords(kw)
        elif action == "delete":
            main_kw = request.form.get("main_keyword", "").strip()
            if main_kw:
                kw = read_keywords()
                kw.pop(main_kw, None)
                write_keywords(kw)
        elif action == "edit":
            old_kw = request.form.get("old_keyword", "").strip()
            main_kw = request.form.get("main_keyword", "").strip()
            search_kw_str = request.form.get("search_keywords", "").strip()
            if main_kw and search_kw_str:
                kw = read_keywords()
                if old_kw in kw:
                    del kw[old_kw]
                search_list = [s.strip() for s in search_kw_str.split(",") if s.strip()]
                kw[main_kw] = search_list
                write_keywords(kw)
        return redirect(url_for("admin.keywords"))

    kw = read_keywords()
    return render_template("admin/keywords.html", keywords=kw)


@admin_bp.route("/models")
def models():
    config = read_model_config()
    from config import (
        NEWS_SCORE_SYSTEM_MSG, NEWS_SCORE_PROMPT,
        NEWS_SCORE_SYSTEM_MSG_ELDER_CARE, NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK,
        NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500, NEWS_ITEM_SUMMARY_USER_PROMPT_500,
        NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION, NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION,
        NEWS_ITEM_SHORT_CONTENT_SYSTEM_PROMPT_GJJ_REGION, NEWS_ITEM_SHORT_CONTENT_USER_PROMPT_GJJ_REGION,
        NEWS_BUSINESS_TYPE_SYSTEM_PROMPT_GJJ, NEWS_BUSINESS_TYPE_USER_PROMPT_GJJ,
        NEWS_REGION_SYSTEM_PROMPT_GJJ, NEWS_REGION_USER_PROMPT_GJJ,
    )
    prompt_groups = [
        {
            "group": "新闻评分",
            "prompts": [
                {"name": "通用评分 System Prompt", "var": "NEWS_SCORE_SYSTEM_MSG", "content": NEWS_SCORE_SYSTEM_MSG},
                {"name": "养老专用评分 System Prompt", "var": "NEWS_SCORE_SYSTEM_MSG_ELDER_CARE", "content": NEWS_SCORE_SYSTEM_MSG_ELDER_CARE,
                 "note": "生效关键词: 养老"},
                {"name": "银行专用评分 System Prompt", "var": "NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK", "content": NEWS_SCORE_SYSTEM_MSG_TOBACCO_SERVICE_BANK,
                 "note": "生效关键词: 烟草服务银行"},
                {"name": "评分 User Prompt", "var": "NEWS_SCORE_PROMPT", "content": NEWS_SCORE_PROMPT},
            ],
        },
        {
            "group": "单条摘要（通用）",
            "prompts": [
                {"name": "摘要 System Prompt", "var": "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500", "content": NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500},
                {"name": "摘要 User Prompt", "var": "NEWS_ITEM_SUMMARY_USER_PROMPT_500", "content": NEWS_ITEM_SUMMARY_USER_PROMPT_500},
            ],
        },
        {
            "group": "单条摘要（公积金 — 摘要+地域+业务类型一步完成）",
            "prompts": [
                {"name": "公积金摘要 System Prompt", "var": "NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION", "content": NEWS_ITEM_SUMMARY_SYSTEM_PROMPT_500_GJJ_REGION,
                 "note": "news_item_summarizer 中调用"},
                {"name": "公积金摘要 User Prompt", "var": "NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION", "content": NEWS_ITEM_SUMMARY_USER_PROMPT_500_GJJ_REGION},
            ],
        },
        {
            "group": "短正文标注（公积金 — 地域+业务类型一步完成）",
            "prompts": [
                {"name": "短正文标注 System Prompt", "var": "NEWS_ITEM_SHORT_CONTENT_SYSTEM_PROMPT_GJJ_REGION", "content": NEWS_ITEM_SHORT_CONTENT_SYSTEM_PROMPT_GJJ_REGION,
                 "note": "正文不超过 500 字时由 news_item_summarizer 调用，不生成摘要"},
                {"name": "短正文标注 User Prompt", "var": "NEWS_ITEM_SHORT_CONTENT_USER_PROMPT_GJJ_REGION", "content": NEWS_ITEM_SHORT_CONTENT_USER_PROMPT_GJJ_REGION},
            ],
        },
        {
            "group": "业务类型标注（公积金 — 历史补标）",
            "prompts": [
                {"name": "业务类型 System Prompt", "var": "NEWS_BUSINESS_TYPE_SYSTEM_PROMPT_GJJ", "content": NEWS_BUSINESS_TYPE_SYSTEM_PROMPT_GJJ,
                 "note": "news_business_type_analyzer 中调用"},
                {"name": "业务类型 User Prompt", "var": "NEWS_BUSINESS_TYPE_USER_PROMPT_GJJ", "content": NEWS_BUSINESS_TYPE_USER_PROMPT_GJJ},
            ],
        },
        {
            "group": "地域标注（公积金 — 补标已有记录）",
            "prompts": [
                {"name": "地域标注 System Prompt", "var": "NEWS_REGION_SYSTEM_PROMPT_GJJ", "content": NEWS_REGION_SYSTEM_PROMPT_GJJ,
                 "note": "news_region_analyzer 中调用"},
                {"name": "地域标注 User Prompt", "var": "NEWS_REGION_USER_PROMPT_GJJ", "content": NEWS_REGION_USER_PROMPT_GJJ},
            ],
        },
    ]
    return render_template("admin/models.html", config=config, prompt_groups=prompt_groups)


def _business_types_context(table, preview=None):
    context = {"table": table, "schema_ready": False, "stats": {}, "aliases": [], "preview": preview}
    conn = get_connection(dict_cursor=False)
    try:
        cursor = conn.cursor()
        context["schema_ready"] = table_has_business_types_column(cursor, table)
        if context["schema_ready"]:
            aliases = load_business_type_aliases(cursor, table)
            context["stats"] = get_business_type_label_stats(cursor, table, aliases)
            context["aliases"] = get_business_type_alias_records(cursor, table)
        cursor.close()
    except Exception as exc:
        context["schema_error"] = str(exc)
    finally:
        conn.close()
    return context


@admin_bp.route("/business-types", methods=["GET", "POST"])
def business_types():
    table = get_table_name()
    if request.method == "POST":
        action = request.form.get("action")
        level1 = request.form.get("level1", "").strip()
        try:
            retired_labels = json.loads(request.form.get("retired_labels", "[]"))
            if not isinstance(retired_labels, list):
                raise ValueError("待合并标签格式无效")
            target_label = request.form.get("target_new", "").strip() or request.form.get("target_existing", "").strip()
            if action == "preview_merge":
                conn = get_connection(dict_cursor=False)
                try:
                    cursor = conn.cursor()
                    aliases = load_business_type_aliases(cursor, table)
                    affected_count = count_secondary_label_uses(cursor, table, level1, retired_labels, aliases)
                    cursor.close()
                finally:
                    conn.close()
                preview = {
                    "level1": level1,
                    "retired_labels": retired_labels,
                    "target_label": target_label,
                    "affected_count": affected_count,
                }
                return render_template("admin/business_types.html", **_business_types_context(table, preview))
            if action == "confirm_merge":
                conn = get_connection(autocommit=False)
                try:
                    updated = merge_secondary_labels(conn, table, level1, retired_labels, target_label)
                finally:
                    conn.close()
                flash(f"合并完成，更新了 {updated} 条新闻记录", "success")
                return redirect(url_for("admin.business_types"))
        except (ValueError, json.JSONDecodeError) as exc:
            flash(str(exc), "danger")
        except Exception as exc:
            flash(f"业务类型操作失败：{exc}", "danger")
    return render_template("admin/business_types.html", **_business_types_context(table))


@admin_bp.route("/business-type-dashboard")
def business_type_dashboard():
    table = get_table_name()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    context = {
        "table": table,
        "date_from": date_from,
        "date_to": date_to,
        "schema_ready": False,
        "all_label_stats": {},
    }
    conn = get_connection(dict_cursor=False)
    try:
        cursor = conn.cursor()
        context["schema_ready"] = table_has_business_types_column(cursor, table)
        if context["schema_ready"]:
            aliases = load_business_type_aliases(cursor, table)
            context["dashboard"] = get_business_type_dashboard(cursor, table, date_from or None, date_to or None)
            context["all_label_stats"] = get_business_type_label_stats(cursor, table, aliases)
        cursor.close()
    except Exception as exc:
        context["schema_error"] = str(exc)
    finally:
        conn.close()
    return render_template("admin/business_type_dashboard.html", **context)


@admin_bp.route("/runs")
def runs():
    status = run_mgr.get_status()
    history = run_mgr.get_run_history(limit=30)

    # 统计最近几天无评分数量
    unscored_stats = []
    conn = get_connection(dict_cursor=True)
    try:
        cursor = conn.cursor()
        table = get_table_name()
        cursor.execute(
            f"SELECT fetchdate, COUNT(*) as cnt FROM {table} "
            "WHERE score IS NULL "
            "AND fetchdate >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) "
            "GROUP BY fetchdate ORDER BY fetchdate DESC"
        )
        for row in cursor.fetchall():
            # 同时查该日总数
            cursor.execute(
                f"SELECT COUNT(*) as total FROM {table} WHERE fetchdate = %s",
                (row["fetchdate"],)
            )
            total = cursor.fetchone()["total"]
            unscored_stats.append({
                "date": row["fetchdate"],
                "unscored": row["cnt"],
                "total": total,
            })
        cursor.close()
    finally:
        conn.close()

    return render_template("admin/runs.html",
                           status=status, history=history,
                           unscored_stats=unscored_stats,
                           project_dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           python_path=sys.executable)


@admin_bp.route("/runs/start", methods=["POST"])
def start_run():
    date = request.form.get("date", "").strip()
    result = run_mgr.start_run(date=date if date else None)
    if "error" in result:
        return render_template("admin/runs.html", status=result, history=run_mgr.get_run_history())
    return redirect(url_for("admin.runs"))


@admin_bp.route("/runs/<date>/log")
def run_log(date):
    log_lines = run_mgr.get_log(date)
    return render_template("admin/run_log.html", date=date, log_lines=log_lines)


@admin_bp.route("/runs/rescore", methods=["POST"])
def rescore():
    """重新评分：对指定日期无分/0分条目重评并更新数据库"""
    date = request.form.get("date", "").strip()
    if not date:
        return redirect(url_for("admin.runs"))

    table = get_table_name()
    updated_total = 0
    errors = []

    for kw in DEFAULT_KEYWORDS:
        scored_path = os.path.join("output", date, f"{date}_{kw}_scored.json")
        if not os.path.exists(scored_path):
            continue

        # 调用 news_scorer.py --rescore
        import subprocess
        cmd = [sys.executable, "news_scorer.py", kw, date, "--rescore"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            errors.append(f"{kw}: 评分失败")
            continue

        # 更新数据库中的分数
        try:
            conn = get_connection(autocommit=False)
            try:
                cursor = conn.cursor()
                import write_to_mysql
                write_to_mysql.conn = conn
                write_to_mysql.cursor = cursor
                write_to_mysql.TABLE_NAME = table
                updated = write_to_mysql.update_scores_from_json(scored_path, kw)
                updated_total += updated
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            errors.append(f"{kw}: 数据库更新失败 - {e}")

    msg = f"重评完成，更新了 {updated_total} 条记录"
    if errors:
        msg += f"，失败: {', '.join(errors)}"
    return render_template("admin/runs.html",
                           status=run_mgr.get_status(),
                           history=run_mgr.get_run_history(),
                           rescore_msg=msg)
