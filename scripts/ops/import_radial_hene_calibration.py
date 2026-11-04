"""Install the camera-specific radial reference into real.default only.

Offline data import: no servers, hardware or version-control history changes.
Replace the orange He-Ne with the measured radial path; preserve the diode.
"""
from pathlib import Path
import argparse
import copy
import json
import sys

CLOUD_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = CLOUD_ROOT.parent
sys.path.insert(0, str(WORKSPACE))
from lab_automation.managers.radial_laser_calibration import build_tag22_placed_overlay


def install(cloud_root=CLOUD_ROOT):
    cloud_root = Path(cloud_root).resolve()
    line = build_tag22_placed_overlay()
    paths = [
        cloud_root/'schemas/coordinator_seeds/real.default/laser_lines.json',
        cloud_root/'coordinator_data/real.default/laser_lines.json',
        cloud_root/'coordinator_data/real.default/lab_state.json',
        cloud_root/'coordinator_data/real.default/session_last_lab_state.json',
    ]
    changed = []
    for path in paths:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding='utf-8-sig'))
        state = doc.get('lab_state', {}) if path.name == 'session_last_lab_state.json' else doc
        overlay = state.setdefault('laser_lines', {'snap_line_id':None, 'lines':[]}) if path.name in ('lab_state.json','session_last_lab_state.json') else doc
        rows = overlay.setdefault('lines', [])
        previous = copy.deepcopy(overlay)
        # Replace both the historic orange geometry and its former blue duplicate.
        rows[:] = [row for row in rows if row.get('id') not in ('he_ne', 'ne_he', 'he_ne_radial_tag22')]
        rows.append(copy.deepcopy(line))
        overlay['snap_line_id'] = 'he_ne'
        if overlay != previous:
            text = json.dumps(doc, indent=2, ensure_ascii=False)+'\n'
            temporary=path.with_suffix(path.suffix+'.radial.tmp')
            temporary.write_text(text,encoding='utf-8')
            temporary.replace(path)
            changed.append(str(path.relative_to(cloud_root)))
    return changed


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true',help='Update seed and existing real.default working overlay; stop the new backend first.')
    args=parser.parse_args()
    if args.install:
        print(json.dumps(dict(updated=install()),indent=2))
    else:
        print(json.dumps(build_tag22_placed_overlay(),indent=2,ensure_ascii=False))
