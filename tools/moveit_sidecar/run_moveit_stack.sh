#!/usr/bin/env bash
set -euo pipefail

ROOT="${CLOUDLAB_ROOT:-/mnt/c/Users/joshua/Documents/robotic_twin_simulation/cloud-labs}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
XARM_SETUP="${XARM_SETUP:-$HOME/dev_ws/install/setup.bash}"
LOG_DIR="${CLOUDLAB_MOVEIT_LOG_DIR:-$ROOT/logs/moveit_sidecar}"
PID_DIR="$LOG_DIR/pids"
ROS_LOG="$LOG_DIR/ros_launch.log"
SIDECAR_LOG="$LOG_DIR/sidecar.log"
HEALTH_URL="${CLOUDLAB_MOVEIT_URL:-http://127.0.0.1:8765}/health"

mkdir -p "$PID_DIR"

source_ros() {
  set +u
  # shellcheck source=/dev/null
  source "$ROS_SETUP"
  # shellcheck source=/dev/null
  source "$XARM_SETUP"
  set -u
}

kill_pid_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
}

stop_stack() {
  kill_pid_file "$PID_DIR/sidecar.pid"
  kill_pid_file "$PID_DIR/ros_launch.pid"

  pkill -f 'moveit_http_sidecar.py' 2>/dev/null || true
  pkill -f 'python3 .*cloud-labs/tools/moveit_sidecar/moveit_http_sidecar.py' 2>/dev/null || true
  fuser -k 8765/tcp 2>/dev/null || true
  pkill -f 'ros2 launch .*xarm7_moveit_fake_headless.launch.py' 2>/dev/null || true
  pkill -f 'ros2 launch xarm_moveit_config _robot_moveit_fake.launch.py' 2>/dev/null || true
  pkill -f '/moveit_ros_move_group/move_group' 2>/dev/null || true
  pkill -f '/controller_manager/ros2_control_node' 2>/dev/null || true
  pkill -f '/robot_state_publisher/robot_state_publisher' 2>/dev/null || true
  pkill -f '/rviz2/rviz2' 2>/dev/null || true
  pkill -f '/xarm_planner/' 2>/dev/null || true
}

wait_for_move_action() {
  source_ros
  for _ in $(seq 1 90); do
    if ros2 action list 2>/dev/null | grep -qx '/move_action'; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for /move_action" >&2
  tail -120 "$ROS_LOG" >&2 || true
  return 1
}

wait_for_sidecar_health() {
  for _ in $(seq 1 30); do
    if python3 - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=1.0) as response:
    payload = json.loads(response.read().decode("utf-8"))
if not payload.get("ok") or not payload.get("move_group_action_ready"):
    raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for MoveIt sidecar health at $HEALTH_URL" >&2
  tail -120 "$SIDECAR_LOG" >&2 || true
  return 1
}

start_stack() {
  stop_stack
  : > "$ROS_LOG"
  : > "$SIDECAR_LOG"

  nohup bash -lc "
    source '$ROS_SETUP'
    source '$XARM_SETUP'
    export QT_QPA_PLATFORM=offscreen
    ros2 launch '$ROOT/tools/moveit_sidecar/xarm7_moveit_fake_headless.launch.py' \
      dof:=7 robot_type:=xarm add_gripper:=true
  " > "$ROS_LOG" 2>&1 < /dev/null &
  echo "$!" > "$PID_DIR/ros_launch.pid"

  wait_for_move_action

  nohup bash -lc "
    source '$ROS_SETUP'
    source '$XARM_SETUP'
    cd '/mnt/c/Users/joshua/Documents/robotic_twin_simulation'
    python3 cloud-labs/tools/moveit_sidecar/moveit_http_sidecar.py
  " > "$SIDECAR_LOG" 2>&1 < /dev/null &
  echo "$!" > "$PID_DIR/sidecar.pid"

  wait_for_sidecar_health
}

status_stack() {
  source_ros
  echo "ros_launch_pid=$(cat "$PID_DIR/ros_launch.pid" 2>/dev/null || true)"
  echo "sidecar_pid=$(cat "$PID_DIR/sidecar.pid" 2>/dev/null || true)"
  echo "move_action=$(ros2 action list 2>/dev/null | grep -x '/move_action' || true)"
  python3 - "$HEALTH_URL" <<'PY' 2>/dev/null || true
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=1.0) as response:
    print(response.read().decode("utf-8"))
PY
}

case "${1:-start}" in
  start)
    start_stack
    status_stack
    ;;
  stop)
    stop_stack
    ;;
  restart)
    start_stack
    status_stack
    ;;
  status)
    status_stack
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
