# cloud-labs MoveIt sidecar

This sidecar runs inside WSL/Ubuntu with ROS 2 Jazzy + MoveIt. It exposes a
small HTTP API for the Windows MuJoCo runtime, so cloud-labs does not import ROS
packages on Windows.

## Start MoveIt

In WSL terminal 1:

```bash
cd /mnt/c/Users/joshua/Documents/robotic_twin_simulation
source /opt/ros/jazzy/setup.bash
source ~/dev_ws/install/setup.bash
ros2 launch xarm_moveit_config xarm7_moveit_fake.launch.py add_gripper:=true
```

## Start the sidecar

In WSL terminal 2:

```bash
cd /mnt/c/Users/joshua/Documents/robotic_twin_simulation
source /opt/ros/jazzy/setup.bash
source ~/dev_ws/install/setup.bash
python3 cloud-labs/tools/moveit_sidecar/moveit_http_sidecar.py
```

If Fast DDS prints shared-memory warnings, start both WSL terminals with:

```bash
export RMW_FASTRTPS_USE_SHM=0
```

The default endpoint is:

```text
http://127.0.0.1:8765
```

From Windows/cloud-labs, override it with:

```powershell
$env:CLOUDLAB_MOVEIT_URL = "http://127.0.0.1:8765"
```

The MuJoCo runtime auto-calibrates the small TCP-frame offset between the xArm
MoveIt model and the MuJoCo `link_tcp` site on the first MoveIt stage. To pin
that value manually instead, set one of these before starting `backend/main.py`:

```powershell
$env:CLOUDLAB_MOVEIT_TCP_Z_OFFSET_M = "0.118"
$env:CLOUDLAB_MOVEIT_TCP_OFFSET_M = "0,0,0.118"
```

## Health check

From WSL or Windows:

```bash
curl http://127.0.0.1:8765/health
```

The response should include:

```json
{"ok": true, "action_name": "/move_action", "move_group_action_ready": true}
```

If the action is not ready, check the action name:

```bash
ros2 action list | grep move
```

Then run the sidecar with an explicit action name if needed:

```bash
python3 cloud-labs/tools/moveit_sidecar/moveit_http_sidecar.py --action-name /move_action
```

## Diagnostics

Each MuJoCo simulator process writes a JSONL trace under:

```text
cloud-labs/logs/mujoco_sessions/
```

The backend error message includes `sim_request_id` and `log=...` when a move
fails. That file records every MoveIt stage request, the world objects, attached
object, MoveIt result code/name, controller contact stage, and MuJoCo contacts
at the failure point.

To put logs somewhere else before starting `backend/main.py`:

```powershell
$env:CLOUDLAB_MUJOCO_LOG_DIR = "C:\Users\joshua\Documents\robotic_twin_simulation\cloud-labs\logs\mujoco_sessions"
```
