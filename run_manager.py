"""运行状态管理 — 启动流水线、跟踪进度、查看历史"""
import json
import os
import subprocess
import sys
import datetime
import signal

STATUS_FILE = os.path.join("output", "run_status.json")
OUTPUT_DIR = "output"


def _read_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _write_status(data):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class RunManager:
    def start_run(self, date=None):
        """后台启动 main.py，记录状态"""
        current = _read_status()
        if current.get("status") == "running":
            # 检查进程是否还活着
            pid = current.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    return {"error": "已有运行中的任务", "status": current}
                except (ProcessLookupError, PermissionError):
                    pass

        if not date:
            date = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        cmd = [sys.executable, "main.py", date]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(__file__) or ".",
        )

        steps = {
            "fetch": {"status": "pending"},
            "content": {"status": "pending"},
            "scoring": {"status": "pending"},
            "database": {"status": "pending"},
            "item_summary": {"status": "pending"},
            "region": {"status": "pending"},
            "tobacco": {"status": "pending"},
        }

        status = {
            "pid": proc.pid,
            "date": date,
            "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "running",
            "steps": steps,
        }
        _write_status(status)
        return status

    def get_status(self):
        """获取当前运行状态，检查进程是否还活着"""
        status = _read_status()
        if not status:
            return {"status": "idle"}

        if status.get("status") == "running":
            pid = status.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, PermissionError):
                    status["status"] = "finished"
                    _write_status(status)
        return status

    def get_run_history(self, limit=30):
        """扫描 output/ 目录获取历史运行日期"""
        if not os.path.exists(OUTPUT_DIR):
            return []
        dirs = []
        for d in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, d)
            if os.path.isdir(full) and re.match(r"\d{4}-\d{2}-\d{2}", d):
                # 统计文件数量
                files = [f for f in os.listdir(full) if f.endswith(".json")]
                dirs.append({"date": d, "file_count": len(files)})
        dirs.sort(key=lambda x: x["date"], reverse=True)
        return dirs[:limit]

    def get_log(self, date):
        """读取指定日期的运行日志"""
        log_path = os.path.join(OUTPUT_DIR, "run_log.txt")
        if not os.path.exists(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 过滤出包含该日期的日志段
            result = []
            capture = False
            for line in lines:
                if date in line:
                    capture = True
                if capture:
                    result.append(line)
                if "==============" in line and capture and len(result) > 1:
                    capture = False
            return "".join(result[-200:]) if result else "".join(lines[-200:])
        except Exception:
            return ""


import re
