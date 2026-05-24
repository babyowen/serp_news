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
        """扫描 output/ 目录获取历史运行，按日期+批次组织"""
        if not os.path.exists(OUTPUT_DIR):
            return []
        dirs = []
        for d in os.listdir(OUTPUT_DIR):
            full = os.path.join(OUTPUT_DIR, d)
            if os.path.isdir(full) and re.match(r"\d{4}-\d{2}-\d{2}", d):
                files = [f for f in os.listdir(full) if f.endswith(".json")]
                runs = sorted(
                    [f for f in os.listdir(full) if f.startswith("run_") and f.endswith(".log")],
                    reverse=True
                )
                entry = {"date": d, "file_count": len(files), "runs": runs}
                # 从批次文件名提取运行时间
                if runs:
                    # run_20260517_120500.log → 2026-05-17 12:05:00
                    first_run = runs[0]
                    try:
                        ts = first_run.replace("run_", "").replace(".log", "")
                        entry["latest_run"] = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
                    except Exception:
                        pass
                dirs.append(entry)
        dirs.sort(key=lambda x: x["date"], reverse=True)
        return dirs[:limit]

    def get_log(self, date):
        """读取指定日期的批次日志文件，合并返回"""
        date_dir = os.path.join(OUTPUT_DIR, date)
        if not os.path.isdir(date_dir):
            # fallback: 尝试从全局日志读取
            return self._read_legacy_log(date)

        run_files = sorted(
            [f for f in os.listdir(date_dir) if f.startswith("run_") and f.endswith(".log")]
        )

        if not run_files:
            return self._read_legacy_log(date)

        all_lines = []
        for rf in run_files:
            path = os.path.join(date_dir, rf)
            # 从文件名提取批次时间
            try:
                ts = rf.replace("run_", "").replace(".log", "")
                batch_label = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
            except Exception:
                batch_label = rf
            all_lines.append(f"━━━ 运行批次: {batch_label} ━━━")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.rstrip()
                        if stripped:
                            all_lines.append(stripped)
            except Exception as e:
                all_lines.append(f"[读取失败] {path}: {e}")
            all_lines.append("")

        return self._filter_log_lines(all_lines) if all_lines else ["暂无日志"]

    def _read_legacy_log(self, date):
        """从旧的全局 run_log.txt 读取（兼容旧数据）"""
        log_path = os.path.join(OUTPUT_DIR, "run_log.txt")
        if not os.path.exists(log_path):
            return ["暂无日志"]

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            date_lines = []
            for i, line in enumerate(lines):
                if date in line:
                    date_lines.append(i)

            if not date_lines:
                result = [l.rstrip() for l in lines[-80:] if l.rstrip()]
                return self._filter_log_lines(result) if result else ["暂无日志"]

            start = max(0, date_lines[0] - 2)
            end = min(len(lines), date_lines[-1] + 30)

            raw = [line.rstrip() for line in lines[start:end] if line.rstrip()]
            return self._filter_log_lines(raw) if raw else ["暂无日志"]

        except Exception as e:
            return [f"读取日志失败: {e}"]

    @staticmethod
    def _filter_log_lines(lines):
        """过滤日志行，保留关键信息"""
        important_keywords = [
            "[ERROR]", "[失败]", "错误", "异常", "失败:",
            "[WARN]", "[跳过空内容]",
            "[执行完成]", "[执行失败]", "[开始执行]",
            "导入数据库", "[统计]", "[完成]",
            "[SKIP]", "跳过",
            "━━━",
        ]

        skip_patterns = ["倒计时", "准备重试"]

        result = []
        for line in lines:
            # 重要行保留
            is_important = any(k in line for k in important_keywords)
            if is_important:
                result.append(line)
                continue

            # 噪音行过滤
            if any(p in line for p in skip_patterns):
                continue

            # 连续的分隔线只保留一条
            if "=====" in line:
                if result and "=====" in result[-1]:
                    continue

            result.append(line)

        return result if result else ["无关键日志"]


import re
