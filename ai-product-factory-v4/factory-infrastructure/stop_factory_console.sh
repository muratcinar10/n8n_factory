#!/bin/sh
set -eu

BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUNTIME_DIR="$BASE_DIR/runtime"

stop_service() {
  name=$1
  script=$2
  pid_file="$RUNTIME_DIR/$name.pid"
  [ -f "$pid_file" ] || { echo "$name is not managed by this launcher"; return; }
  pid=$(sed -n '1p' "$pid_file")
  case "$pid" in ''|*[!0-9]*) echo "$name PID file is invalid; left untouched"; return;; esac
  command_line=$(ps -p "$pid" -o command= 2>/dev/null || true)
  case "$command_line" in
    *"$script"*)
      kill "$pid"
      count=0
      while kill -0 "$pid" 2>/dev/null && [ "$count" -lt 50 ]; do sleep 0.1; count=$((count + 1)); done
      if kill -0 "$pid" 2>/dev/null; then echo "$name did not stop cleanly; no forced signal sent"; return; fi
      rm "$pid_file"
      echo "$name stopped"
      ;;
    *) echo "$name PID does not match the expected service; left untouched";;
  esac
}

stop_service director-bridge "$BASE_DIR/bridge/director_bridge.py"
stop_service factory-monitor "$BASE_DIR/monitor/factory_monitor.py"
