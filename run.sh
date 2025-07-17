#!/bin/bash

# 获取脚本所在的目录，确保路径的正确性
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 使用 caffeinate 命令来包裹 python 命令
# caffeinate 会在 python 程序执行期间，阻止系统进入休眠状态
# -s 参数：当电脑连接交流电源时，阻止系统空闲休眠。对于夜间任务非常适用。
caffeinate -s "$DIR/venv/bin/python" "$DIR/main.py"
