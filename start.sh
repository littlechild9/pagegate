#!/bin/bash
# PageGate 启动脚本
# 用法: bash start.sh

cd "$(dirname "$0")"

# 关掉之前运行的实例
if [ -f .pid ]; then
    OLD_PID=$(cat .pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "停止旧进程 (PID: $OLD_PID)..."
        kill "$OLD_PID"
        sleep 1
    fi
    rm -f .pid
fi
lsof -ti:8888 | xargs kill 2>/dev/null

# 找 Python >= 3.10
PYTHON=""
for p in python3.13 python3.14 python3.12 python3.11 python3.10; do
    if command -v "$p" &>/dev/null; then
        PYTHON="$(command -v "$p")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

echo "使用 Python: $PYTHON ($($PYTHON --version))"

# 创建 venv（首次）
if [ ! -d venv ]; then
    echo "创建虚拟环境..."
    "$PYTHON" -m venv venv
fi

source venv/bin/activate

# 先升级 pip，再装依赖
if ! python -c "import fastapi" 2>/dev/null; then
    echo "安装依赖..."
    pip install --upgrade pip -q
    pip install -q -r requirements.txt
fi

echo "启动 PageGate..."
python server.py &
echo $! > .pid
echo "PID: $(cat .pid)"
echo "访问: http://localhost:8888"
echo "Dashboard: http://localhost:8888/dashboard?token=$(grep admin_token config.yaml | head -1 | sed 's/.*: *"\(.*\)"/\1/')"
