#!/bin/bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
project_dir="$(cd "$script_dir/../.." && pwd)"
venv_dir="$script_dir/.venv"
proxy_port=8765
proxy_start_timeout=60
proxy_log="$script_dir/logs/proxy.log"
proxy_health=""
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
  proxy_health="$(curl -sS --noproxy '*' --connect-timeout 1 --max-time 2 \
    "http://127.0.0.1:$proxy_port/api/health" 2>/dev/null || true)"
  printf '%s' "$proxy_health" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}

proxy_missing_config() {
  printf '%s' "$proxy_health" | grep -Eq '"configured"[[:space:]]*:[[:space:]]*false'
}

show_proxy_log() {
  echo ""
  echo "代理日志：$proxy_log"
  if [ -s "$proxy_log" ]; then
    echo "最近的启动信息："
    tail -n 60 "$proxy_log"
  else
    echo "代理尚未输出日志，可能仍在加载 Python 或依赖。"
  fi
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
  elif proxy_missing_config; then
    echo "[ERROR] 已运行的表情识别代理缺少 AIE_AK 或 AIE_SK。"
    echo "请补全 $script_dir/.env，关闭现有代理后重新启动。"
    pause_on_error
    exit 1
  else
    echo "[ERROR] 端口 $proxy_port 已被其他程序占用，或现有代理配置无效。"
    echo "请先关闭占用该端口的程序，再重新运行此启动器。"
    pause_on_error
    exit 1
  fi
else
  if [ ! -x "$venv_dir/bin/python" ]; then
    echo "首次运行：正在创建表情识别代理环境..."
    if ! command -v uv >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi
    if ! command -v uv >/dev/null 2>&1; then
      echo "[ERROR] 无法安装 uv，摄像头环境未创建。"
      pause_on_error
      exit 1
    fi
    uv python install 3.11
    uv venv --python 3.11 "$venv_dir"
    uv pip install --python "$venv_dir/bin/python" -r "$script_dir/requirements.txt"
  fi

  mkdir -p "$script_dir/logs"
  echo "正在启动表情识别代理，最多等待 $proxy_start_timeout 秒..."
  echo "代理日志：$proxy_log"
  "$venv_dir/bin/python" -u "$script_dir/app.py" >"$proxy_log" 2>&1 &
  proxy_pid=$!
  owns_proxy=true

  proxy_started=false
  proxy_failure="timeout"
  proxy_exit_code=0
  start_seconds=$SECONDS
  next_progress=10
  while (( SECONDS - start_seconds < proxy_start_timeout )); do
    if ! kill -0 "$proxy_pid" 2>/dev/null; then
      wait "$proxy_pid" || proxy_exit_code=$?
      proxy_failure="exited"
      break
    fi
    if proxy_ready; then
      proxy_started=true
      break
    fi
    if proxy_missing_config; then
      proxy_failure="config"
      break
    fi
    if (( SECONDS - start_seconds >= next_progress )); then
      echo "仍在等待代理就绪（已等待 $((SECONDS - start_seconds)) 秒）..."
      next_progress=$((next_progress + 10))
    fi
    sleep 0.4
  done

  if [ "$proxy_started" != true ]; then
    case "$proxy_failure" in
      exited)
        echo "[ERROR] 表情识别代理已提前退出，退出码：$proxy_exit_code。"
        ;;
      config)
        echo "[ERROR] 表情识别代理已启动，但缺少 AIE_AK 或 AIE_SK。"
        echo "请补全 $script_dir/.env 后重新启动。"
        ;;
      *)
        echo "[ERROR] 等待 $proxy_start_timeout 秒后，表情识别代理仍未就绪。"
        echo "请查看下方日志，确认是否卡在依赖加载或服务启动阶段。"
        ;;
    esac
    show_proxy_log
    pause_on_error
    exit 1
  fi
  echo "表情识别代理已就绪。"
fi

echo "正在调用项目原有主启动器..."
echo ""
export AI_COMPANION_CAMERA=1
"$project_dir/启动.command"
