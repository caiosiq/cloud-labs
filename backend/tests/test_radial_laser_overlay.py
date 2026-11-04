"""Radial overlay provenance, frame transform, VC and legacy isolation."""
from pathlib import Path
import copy
import importlib.util
import json
import math
import sys
import pytest

CLOUD=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(CLOUD.parent))
from lab_automation.managers.radial_laser_calibration import (
    build_tag22_placed_overlay,load_radial_laser_calibration,
    load_placed_laser_calibration,robot_tcp_x_at_y,placed_robot_x_correction_at_y,
    DEFAULT_TAG22_HENE_PLACED_CALIBRATION)
from lab_model.coordinator.state.projections import normalize_laser_lines_doc,extract_configuration
from lab_model.coordinator.state.diff import configuration_diff
from lab_model.coordinator.backends.lab_view_config import laser_line_coeffs_from_doc

def seed():
    return json.loads((CLOUD/'schemas/coordinator_seeds/real.default/laser_lines.json').read_text(encoding='utf-8'))

def test_seven_points_match_script27_and_preserve_actual_robot_y():
    line=build_tag22_placed_overlay()
    baseline=load_radial_laser_calibration()
    placed=load_placed_laser_calibration(DEFAULT_TAG22_HENE_PLACED_CALIBRATION,baseline)
    f=baseline['calibration_snapshot']['frame_transform']
    c,s=math.cos(f['lab_to_robot_rotation_rad']),math.sin(f['lab_to_robot_rotation_rad'])
    ox,oy=f['robot_origin_xy_mm']
    assert line==next(row for row in seed()['lines'] if row['id']==line['id'])
    for i,p in enumerate(line['points']):
        robot_x=c*p['x']-s*p['y']+ox; robot_y=s*p['x']+c*p['y']+oy
        expected_y=-300.+75.*i
        assert robot_y==pytest.approx(expected_y,abs=1e-9)
        assert robot_x==pytest.approx(robot_tcp_x_at_y(baseline,expected_y)+
                                     placed_robot_x_correction_at_y(placed,expected_y),abs=1e-9)
    assert line['calibration']['gripper_closed_position']==510
    assert 'z' not in line
    # Public table Y differs from the original robot Y stations.
    assert line['points'][0]['y'] != -300.

def test_vc_preserves_and_detects_interior_point_changes():
    doc=normalize_laser_lines_doc(seed())
    cfg=extract_configuration({'components':{},'laser_lines':doc})
    line=next(row for row in cfg['laser_lines']['lines'] if row['id']=='he_ne')
    assert len(line['points'])==7 and line['calibration']['placed_sha256']
    other=copy.deepcopy(cfg)
    row=next(row for row in other['laser_lines']['lines'] if row['id']==line['id'])
    row['points'][3]['x']+=.1
    assert any('he_ne' in str(item) for item in configuration_diff(cfg,other))
    assert normalize_laser_lines_doc(doc)==doc

def test_legacy_coefficients_never_fit_endpoints_of_radial_path():
    doc=seed()
    expected=laser_line_coeffs_from_doc(doc,'REAL')
    assert doc['snap_line_id']=='he_ne'
    assert expected['snap_line_id']=='diode' and expected['b']==392.6
    doc['snap_line_id']='he_ne'
    assert laser_line_coeffs_from_doc(doc,'REAL')['snap_line_id']!='he_ne'

def test_offline_import_is_idempotent_and_preserves_other_state(tmp_path):
    path=CLOUD/'scripts/ops/import_radial_hene_calibration.py'
    spec=importlib.util.spec_from_file_location('_radial_import_test',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    seed_path=tmp_path/'schemas/coordinator_seeds/real.default/laser_lines.json'
    seed_path.parent.mkdir(parents=True)
    initial=seed()
    # Simulate the old orange geometry plus separate blue calibration.
    calibrated=build_tag22_placed_overlay()
    blue=copy.deepcopy(calibrated)
    blue['id']='he_ne_radial_tag22';blue['color']='#22d3ee'
    blue['calibration'].pop('usage')
    diode=next(r for r in initial['lines'] if r['id']=='diode')
    legacy={'id':'he_ne','name':'He–Ne','color':'#ff5c33','enabled':True,
            'p1':{'x':360.,'y':-193.3},'p2':{'x':360.4,'y':206.7}}
    initial={'snap_line_id':'diode','lines':[diode,legacy,blue]}
    seed_path.write_text(json.dumps(initial),encoding='utf-8')
    state_path=tmp_path/'coordinator_data/real.default/lab_state.json'
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({'components':{'tag_1':{'unchanged':True}},'laser_lines':initial}))
    checkpoint=state_path.with_name('session_last_lab_state.json')
    checkpoint.write_text(json.dumps({'version':1,'lab_state':{'components':{'tag_1':{}},'laser_lines':initial}}))
    assert len(module.install(tmp_path))==3
    assert module.install(tmp_path)==[]
    doc=json.loads(seed_path.read_text(encoding='utf-8'))
    assert doc['snap_line_id']=='he_ne'
    assert doc['lines']==[diode,calibrated]
    assert json.loads(state_path.read_text(encoding='utf-8'))['components']=={'tag_1':{'unchanged':True}}
    saved=json.loads(checkpoint.read_text(encoding='utf-8'))
    assert saved['lab_state']['laser_lines']['lines']==[diode,calibrated]
    assert saved['lab_state']['components']=={'tag_1':{}}
    normalized=normalize_laser_lines_doc(initial)
    assert normalized==normalize_laser_lines_doc(doc)
    assert normalize_laser_lines_doc(normalized)==normalized


def test_stale_running_backend_state_upgrades_on_restart(tmp_path):
    from lab_model.coordinator.state.lab_state_store import LabStateStore
    radial=build_tag22_placed_overlay()
    blue=copy.deepcopy(radial)
    blue["id"]="he_ne_radial_tag22"
    blue["color"]="#22d3ee"
    blue["calibration"].pop("usage")
    old={"snap_line_id":"diode","lines":[
        next(row for row in seed()["lines"] if row["id"]=="diode"),
        {"id":"he_ne","p1":{"x":360.,"y":-193.3},"p2":{"x":360.4,"y":206.7}},
        blue]}
    state_path=tmp_path/"lab_state.json"
    state_path.write_text(json.dumps({"components":{},"laser_lines":old}),encoding="utf-8")
    store=LabStateStore.from_disk("real.default",str(state_path))
    assert store.snapshot()["laser_lines"]==normalize_laser_lines_doc(seed())
