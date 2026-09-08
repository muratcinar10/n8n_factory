#!/bin/sh
set -eu

BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUNTIME_DIR="$BASE_DIR/runtime"
mkdir -p "$RUNTIME_DIR"
FACTORY_MONITOR_PORT="${FACTORY_MONITOR_PORT:-9787}"
export FACTORY_MONITOR_PORT
TOKEN_FILE="$RUNTIME_DIR/.bridge-token"
if [ -f "$TOKEN_FILE" ]; then
  BRIDGE_TOKEN=$(sed -n '1p' "$TOKEN_FILE")
else
  umask 077
  BRIDGE_TOKEN=$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  printf '%s\n' "$BRIDGE_TOKEN" > "$TOKEN_FILE"
fi

start_service() {
  name=$1
  script=$2
  pid_file="$RUNTIME_DIR/$name.pid"
  log_file="$RUNTIME_DIR/$name.log"
  if [ -f "$pid_file" ]; then
    pid=$(sed -n '1p' "$pid_file")
    if case "$pid" in ''|*[!0-9]*) false;; *) kill -0 "$pid" 2>/dev/null;; esac; then
      command_line=$(ps -p "$pid" -o command= 2>/dev/null || true)
      case "$command_line" in *"$script"*) echo "$name already running (PID $pid)"; return;; esac
    fi
    mv "$pid_file" "$pid_file.stale.$(date +%s)"
  fi
  if [ "$name" = "director-bridge" ]; then
    FACTORY_EXECUTION_ENABLED=true FACTORY_BRIDGE_TOKEN="$BRIDGE_TOKEN" nohup /usr/bin/python3 "$script" >>"$log_file" 2>&1 </dev/null &
  else
    FACTORY_MONITOR_PORT="$FACTORY_MONITOR_PORT" FACTORY_BRIDGE_TOKEN="$BRIDGE_TOKEN" nohup /usr/bin/python3 "$script" >>"$log_file" 2>&1 </dev/null &
  fi
  pid=$!
  printf '%s\n' "$pid" > "$pid_file"
  echo "$name started (PID $pid)"
}

start_service director-bridge "$BASE_DIR/bridge/director_bridge.py"
start_service factory-monitor "$BASE_DIR/monitor/factory_monitor.py"
FACTORY_BRIDGE_TOKEN="$BRIDGE_TOKEN" start_service cursor-developer-bridge "$BASE_DIR/bridge/cursor_developer_bridge.py"
printf 'FACTORY_CURSOR_BRIDGE_TOKEN=%s\n' "$BRIDGE_TOKEN" > "$RUNTIME_DIR/n8n-cursor.env"
echo "Cursor Developer Bridge:             http://127.0.0.1:8766"
echo "Director Control + Factory Monitor: http://127.0.0.1:${FACTORY_MONITOR_PORT}"
echo "Director Bridge backend:            http://127.0.0.1:8765"
