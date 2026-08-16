#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$script_dir/../.." && pwd)"
venv_dir="$script_dir/.venv"
proxy_port=8765
proxy_pid=""
owns_proxy=false

pause_on_error() {
  echo ""
  read -r -p "按回车键关闭窗口..." || true
}

cleanup() {
  if [ "$owns_proxy" = true ] && [ -n "$proxy_pid" ] && kill -0 "$proxy_pid" 2>/dev/null; then
    echo ""
    echo "正在停止本次启动的表情识别代理..."
    kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
  fi
}

proxy_ready() {
  curl -fsS "http://127.0.0.1:$proxy_port/api/health" 2>/dev/null \
    | grep -q '"ok":true'
}

trap cleanup EXIT INT TERM

if [ ! -x "$project_dir/启动.command" ]; then
  echo "[ERROR] 找不到项目主启动器：$project_dir/启动.command"
  pause_on_error
  exit 1
fi

echo "启动 Open-LLM-VTuber 与表情识别..."
echo ""

if lsof -tiTCP:"$proxy_port" -sTCP:LISTEN >/dev/null 2>&1; then
  if proxy_ready; then
    echo "表情识别代理已在运行，直接复用。"
  else
    echo "[ERROR] 端口 $proxy_port 已被其他程序占用，或现有代理配置无效。"
    echo "请先关闭占用该端口的程序，再重新运行此启动器。"
    pause_on_error
    exit 1
  fi
else
  if [ ! -x "$venv_dir/bin/python" ]; then
    echo "首次运行：正在创建表情识别代理环境..."
    python3 -m venv "$venv_dir"
    "$venv_dir/bin/python" -m pip install -r "$script_dir/requirements.txt"
  fi

  echo "正在启动表情识别代理..."
  "$venv_dir/bin/python" "$script_dir/app.py" &
  proxy_pid=$!
  owns_proxy=true

  proxy_started=false
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if proxy_ready; then
      proxy_started=true
      break
    fi
    if ! kill -0 "$proxy_pid" 2>/dev/null; then
      break
    fi
    sleep 0.4
  done

  if [ "$proxy_started" != true ]; then
    echo "[ERROR] 表情识别代理启动失败，请检查上方错误信息和 .env 配置。"
    pause_on_error
    exit 1
  fi
  echo "表情识别代理已就绪。"
fi

echo "正在调用项目原有主启动器..."
echo ""
"$project_dir/启动.command"
