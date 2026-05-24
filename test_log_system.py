"""日志系统测试 — 10个案例覆盖批次日志核心逻辑"""
import os
import sys
import json
import shutil
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(__file__))

from error_handler import ErrorHandler, log_script_start, log_script_complete
import run_manager as rm

# 替换模块级 OUTPUT_DIR 为临时目录
TEST_DIR = tempfile.mkdtemp(prefix="log_test_")
ORIGINAL_OUTPUT_DIR = rm.OUTPUT_DIR
rm.OUTPUT_DIR = TEST_DIR

TEST_DATE = "2026-05-16"
DATE_DIR = os.path.join(TEST_DIR, TEST_DATE)
os.makedirs(DATE_DIR, exist_ok=True)

passed = 0
failed = 0

def assert_test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


# ========== 案例1：无 RUN_LOG_PATH 时 fallback 到默认路径 ==========
print("\n[案例1] 无环境变量时 fallback 到默认路径")
os.environ.pop("RUN_LOG_PATH", None)
eh = ErrorHandler(log_dir=TEST_DIR)
expected = os.path.join(TEST_DIR, "run_log.txt")
assert_test("fallback 路径正确", eh.run_log_path == expected, f"期望 {expected}，实际 {eh.run_log_path}")


# ========== 案例2：有 RUN_LOG_PATH 时使用指定路径 ==========
print("\n[案例2] 设置 RUN_LOG_PATH 时写入指定路径")
batch_path = os.path.join(DATE_DIR, "run_20260517_100000.log")
os.environ["RUN_LOG_PATH"] = batch_path
eh2 = ErrorHandler(log_dir=TEST_DIR)
assert_test("使用环境变量路径", eh2.run_log_path == batch_path, f"期望 {batch_path}，实际 {eh2.run_log_path}")


# ========== 案例3：log_script_start / log_script_complete 写入批次文件 ==========
print("\n[案例3] log函数写入批次日志文件")
log_script_start("test_script", ["2026-05-16"])
log_script_complete("test_script", success=True, message="测试完成")
assert_test("批次文件已创建", os.path.exists(batch_path))
with open(batch_path, "r", encoding="utf-8") as f:
    content = f.read()
assert_test("包含开始标记", "[开始执行] test_script" in content)
assert_test("包含完成标记", "[执行完成] test_script" in content)
assert_test("包含消息", "测试完成" in content)


# ========== 案例4：同一日期多次运行生成不同批次 ==========
print("\n[案例4] 同一日期多次运行生成不同批次")
for ts in ["20260517_080000", "20260517_120000", "20260517_180000"]:
    p = os.path.join(DATE_DIR, f"run_{ts}.log")
    os.environ["RUN_LOG_PATH"] = p
    log_script_start("test", [TEST_DATE])
    log_script_complete("test", True, f"批次{ts}")
run_files = sorted([f for f in os.listdir(DATE_DIR) if f.startswith("run_")])
assert_test("生成了4个批次文件", len(run_files) == 4, f"实际 {len(run_files)} 个")


# ========== 案例5：run_manager.get_run_history 返回批次信息 ==========
print("\n[案例5] get_run_history 返回批次信息")
mgr = rm.RunManager()
history = mgr.get_run_history()
date_entry = next((h for h in history if h["date"] == TEST_DATE), None)
assert_test("找到测试日期", date_entry is not None)
assert_test("runs 列表有4个", len(date_entry["runs"]) == 4, f"实际 {len(date_entry.get('runs', []))} 个: {date_entry.get('runs', [])}")
assert_test("latest_run 有值", date_entry.get("latest_run") is not None, f"实际 {date_entry}")


# ========== 案例6：get_log 合并多个批次 ==========
print("\n[案例6] get_log 合并多个批次日志")
log_lines = mgr.get_log(TEST_DATE)
batch_markers = [l for l in log_lines if "运行批次" in l]
assert_test("有4个批次分隔标记", len(batch_markers) == 4, f"实际 {len(batch_markers)} 个")
assert_test("包含批次080000", any("08:00:00" in l for l in batch_markers))
assert_test("包含批次180000", any("18:00:00" in l for l in batch_markers))


# ========== 案例7：无批次文件时 fallback 读取旧格式日志 ==========
print("\n[案例7] 无批次文件时 fallback 到旧格式日志")
fallback_date = "2026-01-01"
fallback_dir = os.path.join(TEST_DIR, fallback_date)
os.makedirs(fallback_dir, exist_ok=True)
with open(os.path.join(fallback_dir, "dummy.json"), "w") as f:
    f.write("[]")
# 全局 run_log.txt
global_log = os.path.join(TEST_DIR, "run_log.txt")
with open(global_log, "w", encoding="utf-8") as f:
    f.write(f"[{fallback_date} 08:00:00] [开始执行] legacy_script\n")
    f.write(f"[{fallback_date} 08:00:01] [执行完成] legacy_script — 完成\n")
    f.write("[2026-05-16 12:00:00] 不相关的日志\n")
log_lines = mgr.get_log(fallback_date)
assert_test("读取到旧格式日志", len(log_lines) > 0, f"实际 {len(log_lines)} 行")
assert_test("包含旧格式日期", any(fallback_date in l for l in log_lines), f"内容: {log_lines[:3]}")


# ========== 案例8：日期目录不存在时返回提示 ==========
print("\n[案例8] 不存在的日期（无目录无匹配日志）返回提示")
# 清空全局日志，确保 fallback 也找不到内容
global_log = os.path.join(TEST_DIR, "run_log.txt")
with open(global_log, "w", encoding="utf-8") as f:
    pass  # 空文件
log_lines = mgr.get_log("2099-12-31")
assert_test("返回提示信息", len(log_lines) > 0)
assert_test("提示暂无日志", "暂无日志" in log_lines[0], f"实际: {log_lines[0]}")


# ========== 案例9：批次时间戳解析正确 ==========
print("\n[案例9] 批次文件名时间戳解析")
rf = "run_20260517_133000.log"
ts = rf.replace("run_", "").replace(".log", "")
formatted = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
assert_test("解析为 2026-05-17 13:30:00", formatted == "2026-05-17 13:30:00", f"实际 {formatted}")


# ========== 案例10：子进程通过 env 继承 RUN_LOG_PATH ==========
print("\n[案例10] 子进程继承 RUN_LOG_PATH 环境变量")
child_batch = os.path.join(DATE_DIR, "run_20260517_235900.log")
os.environ["RUN_LOG_PATH"] = child_batch
import subprocess
result = subprocess.run(
    [sys.executable, "-c",
     "import os; from error_handler import ErrorHandler; "
     "eh=ErrorHandler(); print(eh.run_log_path)"],
    capture_output=True, text=True, env=os.environ
)
child_path = result.stdout.strip()
assert_test("子进程读到环境变量", child_path == child_batch, f"期望 {child_batch}，子进程 {child_path}")


# ========== 清理 ==========
rm.OUTPUT_DIR = ORIGINAL_OUTPUT_DIR
shutil.rmtree(TEST_DIR, ignore_errors=True)
os.environ.pop("RUN_LOG_PATH", None)

print(f"\n{'='*50}")
print(f"测试结果: {passed} 通过, {failed} 失败, 共 {passed+failed} 项")
sys.exit(0 if failed == 0 else 1)
