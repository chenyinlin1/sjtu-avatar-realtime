#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PROJECT_DIR="/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
PYTHON_BIN="${PYTHON_BIN:-/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python}"
CONFIG_FILE="${CONFIG_FILE:-config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml}"
PORT="${PORT:-6006}"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/openavatarchat_${PORT}.pid"
case "$PORT" in
  6006)
    DEFAULT_PUBLIC_URL="https://u848390-a11e-6f8e472d.cqa1.seetacloud.com:8443"
    ;;
  6008)
    DEFAULT_PUBLIC_URL="https://uu848390-a11e-6f8e472d.cqa1.seetacloud.com:8443"
    ;;
  *)
    DEFAULT_PUBLIC_URL=""
    ;;
esac
PUBLIC_URL="${OPENAVATARCHAT_PUBLIC_URL:-$DEFAULT_PUBLIC_URL}"
DEFAULT_DASHSCOPE_API_KEY="sk-ws-H.RXYHLIP.Kh4Y.MEQCIACbJPegvfSq5y2Yi-Yy5qAdT37KoSv49wJXl-PUdcq6AiASAFj4NE7M4bf02RZq_dNwCt9ZK8gPkBBmIK3a-4JWMA"
DEFAULT_BOCHA_API_KEY="sk-b897bbc697594d8da78633e94513d08a"  # 可填入 Bocha API Key；外部 BOCHA_API_KEY 环境变量优先
WEB_SEARCH_MODE="${OPENAVATAR_WEB_SEARCH_MODE:-bocha}"
WEB_SEARCH_ALWAYS="${OPENAVATAR_WEB_SEARCH_ALWAYS:-true}"
WEB_SEARCH_TIMEOUT="${OPENAVATAR_WEB_SEARCH_TIMEOUT:-3.0}"
WEB_SEARCH_RESULT_LIMIT="${OPENAVATAR_WEB_SEARCH_RESULT_LIMIT:-5}"

usage() {
  cat <<'MSG'
Usage:
  ./start_realtime_human.sh [search options]

Search options:
  --web-search-mode off|dashscope|bocha
      off       Disable web search.
      dashscope Use DashScope native model search.
      bocha     Use Bocha web search and inject results into the LLM prompt.
  --enable-dashscope-search
      Shortcut for --web-search-mode dashscope.
  --enable-bocha-search
      Shortcut for --web-search-mode bocha.
  --disable-web-search
      Shortcut for --web-search-mode off.
  --web-search-always
      In bocha mode, search for every user request instead of keyword-triggered search.
  --bocha-api-key KEY
      Set BOCHA_API_KEY for Bocha mode. Overrides DEFAULT_BOCHA_API_KEY in this script.
  --web-search-timeout SECONDS
      Search request timeout. Default: 3.0.
  --web-search-result-limit N
      Max search results injected into the prompt. Default: 5.

Default:
  Bocha web search is enabled for every user request unless overridden by
  OPENAVATAR_WEB_SEARCH_MODE / OPENAVATAR_WEB_SEARCH_ALWAYS or CLI options.
MSG
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --web-search-mode)
      WEB_SEARCH_MODE="${2:-}"
      shift 2
      ;;
    --enable-dashscope-search)
      WEB_SEARCH_MODE="dashscope"
      shift
      ;;
    --enable-bocha-search)
      WEB_SEARCH_MODE="bocha"
      shift
      ;;
    --disable-web-search)
      WEB_SEARCH_MODE="off"
      shift
      ;;
    --web-search-always)
      WEB_SEARCH_ALWAYS="true"
      shift
      ;;
    --bocha-api-key)
      export BOCHA_API_KEY="${2:-}"
      shift 2
      ;;
    --web-search-timeout)
      WEB_SEARCH_TIMEOUT="${2:-}"
      shift 2
      ;;
    --web-search-result-limit)
      WEB_SEARCH_RESULT_LIMIT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$WEB_SEARCH_MODE" in
  off|dashscope|bocha) ;;
  *)
    echo "ERROR: --web-search-mode must be one of: off, dashscope, bocha" >&2
    exit 1
    ;;
esac

if [[ -z "${BOCHA_API_KEY:-}" && -n "$DEFAULT_BOCHA_API_KEY" ]]; then
  export BOCHA_API_KEY="$DEFAULT_BOCHA_API_KEY"
fi

if [[ "$WEB_SEARCH_MODE" == "bocha" && -z "${BOCHA_API_KEY:-}" ]]; then
  cat >&2 <<'MSG'
ERROR: Bocha web search is enabled, but BOCHA_API_KEY is not set.

Set it like this:
  export BOCHA_API_KEY='your Bocha API key'
  ./start_realtime_human.sh --enable-bocha-search
MSG
  exit 1
fi

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  export DASHSCOPE_API_KEY="$DEFAULT_DASHSCOPE_API_KEY"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Python executable not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Config file not found: $CONFIG_FILE" >&2
  exit 1
fi

# The container can inherit local SOCKS/HTTP proxy variables from an
# interactive shell. DashScope and Bocha are reachable directly here; stale
# proxy values cause httpx/OpenAI requests to fail with "Connection error".
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  cat >&2 <<'MSG'
ERROR: DASHSCOPE_API_KEY is not set, and DEFAULT_DASHSCOPE_API_KEY is empty.

Set it like this:
  export DASHSCOPE_API_KEY='your Bailian API key'
  ./start_realtime_human.sh
MSG
  exit 1
fi

if ! "$PYTHON_BIN" - <<'PY'
import os
import sys

key = os.environ.get("DASHSCOPE_API_KEY", "")
try:
    key.encode("ascii")
except UnicodeEncodeError:
    print("ERROR: DASHSCOPE_API_KEY contains non-ASCII characters.", file=sys.stderr)
    sys.exit(2)

if not key.startswith("sk-"):
    print("ERROR: DASHSCOPE_API_KEY looks invalid; it should start with sk-.", file=sys.stderr)
    sys.exit(2)

print("API key validation passed")
PY
then
  exit 1
fi

if [[ -x "$PROJECT_DIR/stop_realtime_human.sh" ]]; then
  "$PROJECT_DIR/stop_realtime_human.sh"
fi

LOG_FILE="$LOG_DIR/openavatarchat_${PORT}_$(date +%Y%m%d_%H%M%S).log"
export DASHSCOPE_API_KEY
export OPENAVATARCHAT_PUBLIC_URL="$PUBLIC_URL"
export OPENAVATAR_WEB_SEARCH_MODE="$WEB_SEARCH_MODE"
export OPENAVATAR_WEB_SEARCH_ALWAYS="$WEB_SEARCH_ALWAYS"
export OPENAVATAR_WEB_SEARCH_TIMEOUT="$WEB_SEARCH_TIMEOUT"
export OPENAVATAR_WEB_SEARCH_RESULT_LIMIT="$WEB_SEARCH_RESULT_LIMIT"

nohup setsid "$PYTHON_BIN" src/demo.py \
  --config "$CONFIG_FILE" \
  > "$LOG_FILE" 2>&1 &

PID="$!"
echo "$PID" > "$PID_FILE"

sleep 2
if ! kill -0 "$PID" 2>/dev/null; then
  echo "ERROR: OpenAvatarChat exited during startup." >&2
  echo "LOG: $LOG_FILE" >&2
  tail -n 120 "$LOG_FILE" >&2 || true
  rm -f "$PID_FILE"
  exit 1
fi

echo "PID: $PID"
echo "PID_FILE: $PID_FILE"
echo "LOG: $LOG_FILE"
echo "URL: $PUBLIC_URL"
echo "WEB_SEARCH_MODE: $WEB_SEARCH_MODE"
echo
echo "Following log. Press Ctrl+C to stop tail only; the service keeps running."
tail -f "$LOG_FILE"
