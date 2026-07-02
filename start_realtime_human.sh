#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat"
PYTHON_BIN="/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python"
CONFIG_FILE="config/chat_with_openai_compatible_bailian_qwen_realtime_flashhead_6006.yaml"
PORT="${PORT:-6006}"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/openavatarchat_${PORT}.pid"
PUBLIC_URL="https://u848390-b73d-1dabc33d.cqa1.seetacloud.com:8443"
DEFAULT_DASHSCOPE_API_KEY="sk-2416366818d84babbd9cde7992d126cf"

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
echo
echo "Following log. Press Ctrl+C to stop tail only; the service keeps running."
tail -f "$LOG_FILE"
