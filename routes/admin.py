"""管理路由 — 关键词配置、模型配置、运行监控（需认证）"""
import os
from flask import render_template, request, redirect, url_for, flash, Blueprint
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash
from config_manager import read_keywords, write_keywords, read_model_config
from run_manager import RunManager

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
    return render_template("admin/models.html", config=config)


@admin_bp.route("/runs")
def runs():
    status = run_mgr.get_status()
    history = run_mgr.get_run_history(limit=30)
    return render_template("admin/runs.html", status=status, history=history)


@admin_bp.route("/runs/start", methods=["POST"])
def start_run():
    date = request.form.get("date", "").strip()
    result = run_mgr.start_run(date=date if date else None)
    if "error" in result:
        return render_template("admin/runs.html", status=result, history=run_mgr.get_run_history())
    return redirect(url_for("admin.runs"))


@admin_bp.route("/runs/<date>/log")
def run_log(date):
    log_content = run_mgr.get_log(date)
    return render_template("admin/run_log.html", date=date, log_content=log_content)
