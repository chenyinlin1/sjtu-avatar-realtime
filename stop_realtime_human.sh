#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat"
CONFIG_FILE="config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml"
PORT="${PORT:-6006}"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$LOG_DIR/openavatarchat_${PORT}.pid"

cd "$PROJECT_DIR" 2>/dev/null || true
mkdir -p "$LOG_DIR"

pid_cmdline() {
  local pid="$1"
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
}

pid_cwd() {
  local pid="$1"
  readlink -f "/proc/$pid/cwd" 2>/dev/null || true
}

is_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

is_ours() {
  local pid="$1"
  local cmd cwd
  cmd="$(pid_cmdline "$pid")"
  cwd="$(pid_cwd "$pid")"

  [[ "$cwd" == "$PROJECT_DIR" ]] || \
    [[ "$cmd" == *"src/demo.py"* ]] || \
    [[ "$cmd" == *"$CONFIG_FILE"* ]] || \
    [[ "$cmd" == *"$(basename "$CONFIG_FILE")"* ]]
}

children_of() {
  local pid="$1"
  pgrep -P "$pid" 2>/dev/null || true
}

kill_tree() {
  local signal="$1"
  local pid="$2"
  local child

  for child in $(children_of "$pid"); do
    kill_tree "$signal" "$child"
  done

  kill "-$signal" "$pid" 2>/dev/null || true
}

kill_process_group_if_leader() {
  local signal="$1"
  local pid="$2"
  local pgid

  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
  if [[ -n "$pgid" && "$pgid" == "$pid" ]]; then
    kill "-$signal" -- "-$pid" 2>/dev/null || true
  fi
}

wait_for_exit() {
  local pid="$1"
  local seconds="${2:-10}"
  local i

  for ((i = 0; i < seconds; i++)); do
    if ! is_alive "$pid"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_pid() {
  local pid="$1"
  local label="${2:-process}"

  if ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    return 0
  fi

  if ! is_alive "$pid"; then
    return 0
  fi

  if ! is_ours "$pid"; then
    echo "Skip $label PID $pid: it does not look like this OpenAvatarChat process."
    return 0
  fi

  echo "Stopping $label PID $pid"
  kill_process_group_if_leader TERM "$pid"
  kill_tree TERM "$pid"

  if ! wait_for_exit "$pid" 10; then
    echo "Force killing $label PID $pid"
    kill_process_group_if_leader KILL "$pid"
    kill_tree KILL "$pid"
    wait_for_exit "$pid" 3 || true
  fi
}

pids_on_port() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ port"$" || $4 ~ port"," { print }' | sed -nE 's/.*pid=([0-9]+).*/\1/p'
  fi
}

unique_pids() {
  awk 'NF && !seen[$1]++'
}

PIDS=""

if [[ -f "$PID_FILE" ]]; then
  PID_FROM_FILE="$(tr -dc '0-9' < "$PID_FILE" || true)"
  if [[ -n "$PID_FROM_FILE" ]]; then
    PIDS="$PIDS
$PID_FROM_FILE"
  fi
fi

MATCHED_PIDS="$(pgrep -f "src/demo.py.*$(basename "$CONFIG_FILE")" 2>/dev/null || true)"
if [[ -n "$MATCHED_PIDS" ]]; then
  PIDS="$PIDS
$MATCHED_PIDS"
fi

PORT_PIDS="$(pids_on_port || true)"
if [[ -n "$PORT_PIDS" ]]; then
  PIDS="$PIDS
$PORT_PIDS"
fi

PIDS="$(printf '%s\n' "$PIDS" | unique_pids)"

if [[ -z "$PIDS" ]]; then
  echo "No OpenAvatarChat process found for port $PORT."
  rm -f "$PID_FILE"
  exit 0
fi

while IFS= read -r pid; do
  [[ -n "$pid" ]] || continue
  stop_pid "$pid" "OpenAvatarChat"
done <<< "$PIDS"

rm -f "$PID_FILE"
echo "OpenAvatarChat stop command finished."
