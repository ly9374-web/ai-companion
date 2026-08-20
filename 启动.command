#!/bin/bash
cd "$(dirname "$0")"

PORT=12393

# Replace any existing backend that is listening on our port.
LISTENER_PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u)
if [ -n "$LISTENER_PIDS" ]; then
    echo "检测到端口 $PORT 已被占用，正在停止旧进程：$LISTENER_PIDS"
    kill $LISTENER_PIDS 2>/dev/null

    # Give the old process a moment to clean up gracefully.
    for _ in 1 2 3 4 5; do
        if ! lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    REMAINING_PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sort -u)
    if [ -n "$REMAINING_PIDS" ]; then
        echo "旧进程未正常退出，正在强制停止：$REMAINING_PIDS"
        kill -9 $REMAINING_PIDS 2>/dev/null
        sleep 1
    fi

    if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[ERROR] 无法释放端口 $PORT，请手动关闭占用该端口的进程。"
        read -r -p "按回车键关闭窗口..."
        exit 1
    fi

    echo "端口 $PORT 已释放。"
    echo ""
fi

echo "启动 Open-LLM-VTuber ai陪伴..."
echo ""
./run.sh
