# mock_edge

Teaching lab for Cloud Labs. Cloud-labs talks **only** to
[`cloudlabs_edge/`](./cloudlabs_edge/) (Edge Contract v1). Teaching physics
lives in [`src/mock_edge/host/`](./src/mock_edge/host/); catalog/state in
[`lab_view/`](./lab_view/).

Same door shape as a physical lab (`robot-deathray/cloudlabs_edge/`) and the
[`simulation_edge/cloudlabs_edge/`](../simulation_edge/) (`sim.default`).

## Layout

```text
mock_edge/
  cloudlabs_edge/          # Edge Contract face (certify / EdgeClient URL)
    main.py, dispatch.py, adapters/, …
  src/mock_edge/           # Python package: host physics + bootstrap
    host/, shared/, __main__.py
  lab_view/                # catalog / state / control VC
```

## Run

```powershell
# From cloud-labs root
$env:PYTHONPATH = "backend;mock_edge\src"
pip install -e ./packages/cloudlabs_edge_dev
pip install -e ./mock_edge
python -m mock_edge --port 8100
```

Or from the edge folder (with the same `PYTHONPATH`):

```powershell
cd mock_edge/cloudlabs_edge
uvicorn main:app --host 127.0.0.1 --port 8100
```

## Certify

```powershell
cloudlabs-edge doctor --path mock_edge/cloudlabs_edge
cloudlabs-edge certify http://127.0.0.1:8100 --path mock_edge/cloudlabs_edge --profile stub
```

In-process coordinator teaching still uses `MockLabCommunicator` from
`mock_edge.host` via `EdgeClient`; the HTTP edge above is the process face.
