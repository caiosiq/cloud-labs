# mock_edge

Teaching **Edge Contract v1** implementation for Cloud Labs. Same skeleton as
`cloudlabs-edge init`, with every adapter **filled** against the mock teaching
host (`host/`). Physical benches use `robot-deathray/cloudlabs_edge/` instead.

## Layout

```text
src/mock_edge/
  capabilities.json    # full supported_primitives
  contract.py, latch.py, kernel_host.py, dispatch.py
  adapters/            # filled (motion, motors, vision, live_feed, …)
  server/app.py        # thin HTTP → dispatch
  host/                # teaching physics (MockLabCommunicator)
lab_view/              # catalog / state / control VC
```

## Run

```powershell
# From cloud-labs root
$env:PYTHONPATH = "backend;mock_edge\src"
pip install -e ./packages/cloudlabs_edge_dev
pip install -e ./mock_edge
python -m mock_edge --port 8100
```

## Prove the skeleton

```powershell
cloudlabs-edge doctor --path mock_edge/src/mock_edge
cloudlabs-edge certify http://127.0.0.1:8100 --path mock_edge/src/mock_edge --profile stub
```
