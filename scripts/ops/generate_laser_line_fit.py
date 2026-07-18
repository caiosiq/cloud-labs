"""
Write repo-root laser_line_fit.npy for the UI /api/laser-line.

Lab coordinates use x = a*y + b (mm). Canvas origin is table center; grid dots are 25 mm (see frontend/js/config.js).

- Vertical laser (aligned with breadboard columns): a = 0, b = x intercept in mm.
- Typical tuning: b ≈ (hole_count) * (hole_spacing_mm). Old fit was ~389 mm (~15.5 × 25).

Examples (from project root):
  python scripts/ops/generate_laser_line_fit.py --holes 16 --spacing-mm 25
  python scripts/ops/generate_laser_line_fit.py --holes 15 --spacing-mm 25
  python scripts/ops/generate_laser_line_fit.py --inches 16          # b = 16 * 25.4 mm
  python scripts/ops/generate_laser_line_fit.py --b 400 --slope 0    # set b directly

If the UI breadboard grid is shifted by ¼\" (see BREADBOARD_GRID_OFFSET_X_MM in frontend/js/config.js), subtract the same
amount from b so the laser overlay matches: e.g. after --holes 16 --spacing-mm 25 (b=400), use --b 393.65
or run: python -c "import numpy as np; np.save('laser_line_fit.npy', np.array([0.,400-25.4/4]))"
"""

from __future__ import annotations

import argparse
import os

import numpy as np


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default_out = os.path.join(root, "laser_line_fit.npy")

    p = argparse.ArgumentParser(description="Generate laser_line_fit.npy (x = a*y + b in lab mm).")
    p.add_argument(
        "--out",
        default=default_out,
        help=f"Output path (default: {default_out})",
    )
    p.add_argument(
        "--slope",
        type=float,
        default=0.0,
        help="a in x = a*y + b (0 = vertical line in x–y plane)",
    )
    p.add_argument(
        "--b",
        type=float,
        default=None,
        help="Intercept b in mm (if omitted, derived from --holes/--spacing-mm or --inches)",
    )
    p.add_argument(
        "--holes",
        type=float,
        default=None,
        help="Hole count along +X from lab x=0 (multiplied by spacing unless --b set)",
    )
    p.add_argument(
        "--spacing-mm",
        type=float,
        default=25.0,
        help="Breadboard hole spacing in mm (matches UI grid)",
    )
    p.add_argument(
        "--inches",
        type=float,
        default=None,
        help="If set, b = inches * 25.4 (overrides --holes)",
    )
    args = p.parse_args()

    if args.b is not None:
        b = float(args.b)
    elif args.inches is not None:
        b = float(args.inches) * 25.4
    elif args.holes is not None:
        b = float(args.holes) * args.spacing_mm
    else:
        # Default: vertical at 16 holes × 25 mm (pick 15 or 16 to match the lab column)
        b = 16.0 * args.spacing_mm

    a = float(args.slope)
    arr = np.array([a, b], dtype=np.float64)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    np.save(args.out, arr)
    print(f"Wrote {args.out}")
    print(f"  x = a*y + b  with  a={a:g}  b={b:g}  (mm)")
    print("  Reload UI: click Refresh state, or hard-refresh the page (GET /api/laser-line reads this file each time).")


if __name__ == "__main__":
    main()
