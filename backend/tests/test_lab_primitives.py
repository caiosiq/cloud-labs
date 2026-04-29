import asyncio
import os
import pathlib
import re
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from lab_communicator.base import LabCommunicator
from lab_model import motor_rotation_store as mrs
from lab_primitives.dispatch import execute_validated_command, fetch_read_primitive, parse_command_payload
from lab_primitives.ids import MACRO_PRIMITIVE_IDS, PrimitiveId
from lab_primitives.protocol import LabPrimitiveSurface
from lab_primitives.registry import PRIMITIVE_REGISTRY


class _MotorOnlyLab(LabCommunicator):
    """Minimal lab for macro tests: only ``move_motor`` + rotation store side effects."""

    def __init__(self):
        self.moves: list[tuple[str, int, float]] = []

    def get_lab_state(self) -> dict:
        return {"components": {}}

    async def move_motor(self, target_id: str, motor_id: int, distance: float) -> None:
        self.moves.append((target_id, motor_id, distance))
        mrs.add_delta(target_id, motor_id, distance)


class LabPrimitivesTests(unittest.TestCase):
    def test_registry_has_every_primitive_id(self) -> None:
        for pid in PrimitiveId:
            self.assertIn(pid, PRIMITIVE_REGISTRY, msg=f"missing registry row for {pid!r}")

    def test_motor_send_home_is_macro(self) -> None:
        self.assertIn(PrimitiveId.MOTOR_SEND_HOME, MACRO_PRIMITIVE_IDS)

    def test_motor_send_home_macro_zeroes_angle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rot_path = os.path.join(tmp, "mock_motor_rotations.json")
            with patch.object(mrs, "_mode_path", return_value=rot_path):
                mrs.add_delta("t_macro", 1, 12.5)
                self.assertAlmostEqual(mrs.get_angle("t_macro", 1), 12.5)
                lab = _MotorOnlyLab()
                cmd = parse_command_payload(
                    {
                        "action": "MOTOR_SEND_HOME",
                        "target_id": "t_macro",
                        "parameters": {"motor_id": 1},
                    }
                )
                asyncio.run(execute_validated_command(lab, cmd))
                self.assertAlmostEqual(mrs.get_angle("t_macro", 1), 0.0)
                self.assertEqual(lab.moves, [("t_macro", 1, -12.5)])

    def test_move_motor_validation_error_on_bad_payload(self) -> None:
        with self.assertRaises(ValidationError):
            parse_command_payload(
                {"action": "MOVE_MOTOR", "target_id": "x", "parameters": {}}
            )

    def test_lab_primitive_surface_runtime_check(self) -> None:
        lab = _MotorOnlyLab()
        self.assertIsInstance(lab, LabPrimitiveSurface)

    def test_fetch_read_uses_return_helpers(self) -> None:
        class _SliceLab(LabCommunicator):
            def get_lab_state(self) -> dict:
                return {
                    "components": {
                        "tag_a": {
                            "tunables": {"presence": "breadboard"},
                            "measurables": {"pose": {"x": 1.0}},
                        }
                    }
                }

        lab = _SliceLab()
        self.assertEqual(
            fetch_read_primitive(lab, PrimitiveId.GET_TUNABLES, "tag_a")["presence"],
            "breadboard",
        )
        self.assertEqual(
            fetch_read_primitive(lab, PrimitiveId.GET_MEASURABLES, "tag_a")["pose"]["x"],
            1.0,
        )


class StageCInvariantsTests(unittest.TestCase):
    """Guard rails for ``fixing.md`` §9 Stage C (refined by Phase 2A).

    Stage C sealed the state-ownership boundary between cloud-labs and
    ``lab_automation``:
      * ``.inventory_location`` attribute access is forbidden in
        ``real.py`` -- cloud-labs ignores the field entirely and lets
        ``lab_automation`` own it (see ``fixing.md`` §6.1 and
        ``labautomation_new_primitives.md`` §5).
      * Writes to ``.current_location`` happen only inside
        ``_apply_loaded_pose_to_hardware`` (Phase 2A tightened this from
        ``set_lab_state``: the snapshot orchestrator now lives on the
        base class, and the per-component hardware push is the lone
        site that touches ``OpticalComponent.current_location``).
      * Writes to ``.is_placed`` happen only inside
        ``_apply_loaded_pose_to_hardware`` and ``affirm_placed_at_current``
        (the manual-place flow -- see ``fixing.md`` §9 Stage C6 option (a)).

    Any future PR that tries to re-open one of those leaks will fail this
    test and route the author back to the fixing.md / refactor discussion.
    """

    _CURRENT_LOCATION_WRITE_ALLOWED = frozenset({"_apply_loaded_pose_to_hardware"})
    # Phase 2C migrated ``affirm_placed_at_current`` and the move-family
    # primitives to base.py; the canonical write site for
    # :class:`OpticalComponent.is_placed` on real is now the
    # :meth:`_apply_is_placed_flag` virtual hook (called by the base
    # orchestrators after a successful state commit). The historical
    # write inside :meth:`_apply_loaded_pose_to_hardware` (Stage C6
    # exemption) remains valid for snapshot-load propagation.
    _IS_PLACED_WRITE_ALLOWED = frozenset(
        {"_apply_loaded_pose_to_hardware", "_apply_is_placed_flag"}
    )

    # Matches ``def <name>(`` / ``async def <name>(`` at one level of
    # indentation (4 spaces) -- i.e. RealLabCommunicator's method bodies.
    _METHOD_HEADER_RE = re.compile(r"^    (?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)

    @classmethod
    def setUpClass(cls) -> None:
        # backend/tests/test_lab_primitives.py
        #   -> backend/lab_communicator/real/communicator.py
        # (Phase 1 of the communicator refactor moved real.py into the
        # ``real/`` subpackage; see ``communicator_refactor.md`` §10.)
        here = pathlib.Path(__file__).resolve()
        cls.real_py_path = (
            here.parents[1] / "lab_communicator" / "real" / "communicator.py"
        )
        cls.real_py_src = cls.real_py_path.read_text(encoding="utf-8")

    def _iter_methods(self):
        """Yield ``(method_name, method_body)`` pairs from ``real.py``.

        Uses a simple indentation-based heuristic (not the ``ast`` module)
        so the test stays cheap and does not depend on ``lab_automation``
        being importable. Good enough for the CI lint we need.
        """
        src = self.real_py_src
        matches = list(self._METHOD_HEADER_RE.finditer(src))
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
            yield m.group(1), src[start:end]

    @staticmethod
    def _strip_line_comments(body: str) -> str:
        """Drop ``# ...`` trailing comments so prose mentions of the names
        we enforce don't trip the lint. Intentionally does not strip
        docstrings -- we don't currently assign to these attributes inside
        docstrings, and handling that properly needs the ``tokenize``
        module."""
        return "\n".join(line.split("#", 1)[0] for line in body.splitlines())

    def test_no_inventory_location_attribute_access_in_real_py(self) -> None:
        pattern = re.compile(r"\.inventory_location\b")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code):
                self.fail(
                    f"real.py::{name} contains ``.inventory_location`` "
                    f"attribute access, forbidden by fixing.md §9 Stage C. "
                    f"Use ``.current_location`` instead."
                )

    def test_current_location_writes_only_in_set_lab_state(self) -> None:
        # ``=`` without a following ``=`` = real assignment, not ``==``.
        pattern = re.compile(r"\.current_location\s*=(?!=)")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code) and name not in self._CURRENT_LOCATION_WRITE_ALLOWED:
                self.fail(
                    f"real.py::{name} writes ``.current_location``; that is "
                    f"only allowed inside "
                    f"{sorted(self._CURRENT_LOCATION_WRITE_ALLOWED)} "
                    f"(fixing.md §9 Stage C6). Treat lab_automation state "
                    f"as owned by lab_automation -- our snapshot loader is "
                    f"the single cross-wall sync point."
                )

    def test_is_placed_writes_only_in_allowed_sites(self) -> None:
        pattern = re.compile(r"\.is_placed\s*=(?!=)")
        for name, body in self._iter_methods():
            code = self._strip_line_comments(body)
            if pattern.search(code) and name not in self._IS_PLACED_WRITE_ALLOWED:
                self.fail(
                    f"real.py::{name} writes ``.is_placed``; that is only "
                    f"allowed inside "
                    f"{sorted(self._IS_PLACED_WRITE_ALLOWED)} "
                    f"(fixing.md §9 Stage C6, option (a) keeps the manual-"
                    f"place flow)."
                )


class CommunicatorArchitectureLints(unittest.TestCase):
    """Architectural rules from ``communicator_refactor.md`` §5.1 / §10.

    Phase 2A introduced four cross-cutting lints alongside the
    template-method migration:

    1. ``shared/`` modules MUST NOT import ``lab_automation``,
       ``lab_communicator.real``, ``lab_communicator.mock``, or
       ``lab_communicator.base`` (the four cross-cutting bans).
    2. ``real/`` and ``mock/`` MUST NOT import each other.
    3. ``base.py`` MUST NOT import ``real/`` or ``mock/``.
    4. ``_primitive_*`` class methods (in ``real/communicator.py`` /
       ``mock/communicator.py``) and the matching ``primitive_*`` free
       functions in ``real/primitives.py`` / ``mock/primitives.py`` MUST
       NOT read or write ``self.current_state`` /
       ``communicator.current_state``. Primitives only see state through
       their arguments and (for long-running primitives like
       ``primitive_optimize_component``, Phase 2D) a
       ``progress_callback``. Primitives that violate this rule either
       bypass the orchestrator's locking discipline (subtle races) or,
       for long-running primitives, deadlock.

    A failure here means the new code is in the wrong file or the
    wrong layer; route the author to ``communicator_refactor.md`` §5.1
    (folder rules) or §6.2 (progress callback rule).
    """

    _BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
    _COMM_ROOT = _BACKEND_ROOT / "lab_communicator"

    _SHARED_BANNED_IMPORTS = (
        "lab_automation",
        "lab_communicator.real",
        "lab_communicator.mock",
        "lab_communicator.base",
    )

    @classmethod
    def _iter_py_files(cls, root: pathlib.Path):
        for path in root.rglob("*.py"):
            yield path

    @classmethod
    def _read(cls, path: pathlib.Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_shared_does_not_import_banned_modules(self) -> None:
        shared = self._COMM_ROOT / "shared"
        for path in self._iter_py_files(shared):
            src = self._read(path)
            for banned in self._SHARED_BANNED_IMPORTS:
                # Match ``import X`` or ``from X``. We allow the dotted
                # prefix to extend (e.g. forbidding ``lab_communicator.base``
                # also forbids ``lab_communicator.base.LabCommunicator``).
                pattern = re.compile(
                    rf"^(?:from|import)\s+{re.escape(banned)}(?:\s|\.|$)",
                    re.MULTILINE,
                )
                if pattern.search(src):
                    self.fail(
                        f"shared/ file {path.relative_to(self._COMM_ROOT)} "
                        f"imports banned module {banned!r}. ``shared/`` is "
                        f"the cross-lab building block layer; it must stay "
                        f"hardware-agnostic and free of base coupling "
                        f"(communicator_refactor.md §5.1 rule 5; the "
                        f"``base`` ban prevents the circular-import trap "
                        f"in §7.3)."
                    )

    def test_real_and_mock_do_not_import_each_other(self) -> None:
        for backend in ("real", "mock"):
            other = "mock" if backend == "real" else "real"
            backend_root = self._COMM_ROOT / backend
            for path in self._iter_py_files(backend_root):
                src = self._read(path)
                pattern = re.compile(
                    rf"^(?:from|import)\s+lab_communicator\.{other}(?:\s|\.|$)",
                    re.MULTILINE,
                )
                if pattern.search(src):
                    self.fail(
                        f"{backend}/ file "
                        f"{path.relative_to(self._COMM_ROOT)} imports "
                        f"lab_communicator.{other}; cross-backend imports "
                        f"are banned. Shared logic belongs in "
                        f"``shared/`` (communicator_refactor.md §5.1 rule 4)."
                    )

    def test_base_does_not_import_concrete_backends(self) -> None:
        base_path = self._COMM_ROOT / "base.py"
        src = self._read(base_path)
        for backend in ("lab_communicator.real", "lab_communicator.mock"):
            pattern = re.compile(
                rf"^(?:from|import)\s+{re.escape(backend)}(?:\s|\.|$)",
                re.MULTILINE,
            )
            if pattern.search(src):
                self.fail(
                    f"base.py imports {backend!r}; the template class "
                    f"must not depend on its concrete subclasses "
                    f"(communicator_refactor.md §5.1 rule 3 -- the "
                    f"orchestrator-vs-hook split breaks if base reaches "
                    f"into a backend)."
                )

    @staticmethod
    def _strip_comments_and_docstrings(body: str) -> str:
        """Strip docstrings + line comments so prose mentions of
        ``self.current_state`` / ``communicator.current_state`` in
        commentary don't trip the state-purity lint. Uses a simple
        approximation -- triple-quoted strings are stripped via a
        non-greedy match.
        """
        stripped = re.sub(r'"""[\s\S]*?"""', "", body)
        stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
        stripped = "\n".join(
            line.split("#", 1)[0] for line in stripped.splitlines()
        )
        return stripped

    def test_primitive_hooks_do_not_touch_current_state(self) -> None:
        """``_primitive_*`` class methods MUST NOT touch ``self.current_state``.

        Scans ``real/communicator.py`` and ``mock/communicator.py`` for
        method headers; bodies of the ones whose name starts with
        ``_primitive_`` are extracted (slice between the header and
        the next method header) and scanned for the forbidden
        ``self.current_state`` access pattern. After the
        Phase 4 split (primitives moved into ``primitives.py``) these
        methods should be one-line delegations -- the test is now
        primarily a regression guard against regressions
        re-inlining hook bodies in the wrong place.
        """
        any_method_header = re.compile(
            r"^    (?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE
        )
        forbidden = re.compile(r"\bself\.current_state\b")

        for backend in ("real", "mock"):
            comm_path = self._COMM_ROOT / backend / "communicator.py"
            if not comm_path.exists():
                continue
            src = comm_path.read_text(encoding="utf-8")
            matches = list(any_method_header.finditer(src))
            for i, m in enumerate(matches):
                name = m.group(1)
                if not name.startswith("_primitive_"):
                    continue
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
                body = src[start:end]
                stripped = self._strip_comments_and_docstrings(body)
                if forbidden.search(stripped):
                    self.fail(
                        f"{backend}/communicator.py::{name} reads or "
                        f"writes ``self.current_state``; ``_primitive_*`` "
                        f"hook bodies must not touch state directly. "
                        f"The orchestrator passes you what you need via "
                        f"arguments; long-running primitives use "
                        f"``progress_callback`` (communicator_refactor.md "
                        f"§6.2). Bypassing this either races the lock or "
                        f"deadlocks under the orchestrator's lock."
                    )

    def test_primitive_free_functions_do_not_touch_current_state(self) -> None:
        """``primitive_*`` free functions MUST NOT touch ``communicator.current_state``.

        Scans ``real/primitives.py`` and ``mock/primitives.py`` for
        top-level ``def`` / ``async def`` headers; bodies of the ones
        named ``primitive_*`` are extracted and scanned for the
        forbidden access pattern (``communicator.current_state`` is
        the through-arg form; ``self.current_state`` doesn't apply
        because these are free functions, not methods).
        """
        top_level_def = re.compile(
            r"^(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE
        )
        forbidden = re.compile(
            r"\b(?:communicator|self)\.current_state\b"
        )

        for backend in ("real", "mock"):
            primitives_path = self._COMM_ROOT / backend / "primitives.py"
            if not primitives_path.exists():
                continue
            src = primitives_path.read_text(encoding="utf-8")
            matches = list(top_level_def.finditer(src))
            for i, m in enumerate(matches):
                name = m.group(1)
                if not name.startswith("primitive_"):
                    continue
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
                body = src[start:end]
                stripped = self._strip_comments_and_docstrings(body)
                if forbidden.search(stripped):
                    self.fail(
                        f"{backend}/primitives.py::{name} reads or "
                        f"writes ``communicator.current_state``; "
                        f"``primitive_*`` free functions must not touch "
                        f"state directly. Take what you need as "
                        f"arguments; for long-running primitives use "
                        f"``progress_callback`` (communicator_refactor.md "
                        f"§6.2)."
                    )


class InAirPrimitivesMockRoundTripTests(unittest.TestCase):
    """Phase 2B regression tests: mock end-to-end through every in-air
    primitive's full template-method path.

    Each test runs ``MockLabCommunicator`` instances on a temp state
    file so we don't clobber the developer's working state. We exercise:

    - ``pick_component``: status IDLE -> HOLDING, holding.tag_id stamped,
      ``measurables.pose.z`` set to ``DEFAULT_HOVER_Z_MM``.
    - ``hover_component``: HOLDING preserved, commanded pose lands on
      ``tunables.nominal_pose`` and ``state['holding'].nominal_pose``,
      mock's noise lands on ``measurables.pose`` only (commit_hover
      ``actual_pose`` parameter).
    - ``scan_rotate_in_place`` (held): final rotation = ``theta_max``,
      stays HOLDING.
    - ``place_from_hover``: clears HOLDING, status IDLE, ``z`` stripped
      from poses.
    - ``scan_rotate_in_place`` (placed): IDLE before/after, rotation
      committed.
    - Refusals: pick-while-holding, hover-when-not-holding,
      hover-with-z-out-of-bounds.

    These tests don't import ``lab_automation`` (mock backend has no
    real-lab dependency); they verify the orchestrator + commits +
    refusal-helpers stack works cohesively.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Seed a known-good mock state file in a temp directory so
        # the test isolates from the developer's working snapshot.
        import shutil
        cls._tmp = tempfile.mkdtemp(prefix="phase2b_")
        backend_root = pathlib.Path(__file__).resolve().parents[1]
        seed_state = backend_root.parents[0] / "schemas" / "mock_lab_state.json"
        seed_catalog = backend_root.parents[0] / "schemas" / "component_catalog.mock.json"
        cls._state_file = os.path.join(cls._tmp, "mock_lab_state.json")
        cls._catalog_file = os.path.join(cls._tmp, "component_catalog.mock.json")
        shutil.copy(str(seed_state), cls._state_file)
        shutil.copy(str(seed_catalog), cls._catalog_file)

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _new_lab(self):
        # Patch the module-level constants so the mock instance points
        # at the temp state file. Each test gets a fresh instance, but
        # the underlying file persists across tests in the class -- which
        # matches how mock works in production (state is a session, not
        # a per-call thing).
        import lab_communicator.mock.communicator as mc
        return self._make_lab_with(mc)

    def _make_lab_with(self, mc):
        with patch.object(mc, "LAB_STATE_FILE", self._state_file), \
             patch.object(mc, "CATALOG_FILE", self._catalog_file):
            from lab_communicator.mock.communicator import MockLabCommunicator
            return MockLabCommunicator()

    @staticmethod
    def _first_breadboard_tag(lab) -> str:
        for tag_id, comp in (lab.get_lab_state().get("components") or {}).items():
            if (comp.get("tunables") or {}).get("presence") == "breadboard":
                return tag_id
        raise AssertionError("no on-breadboard component in seed state")

    def test_pick_then_hover_then_place_round_trip(self) -> None:
        from lab_model.holding import (
            DEFAULT_HOVER_Z_MM,
            SYSTEM_STATUS_HOLDING,
            SYSTEM_STATUS_IDLE,
            held_tag,
            is_holding,
        )
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)

        # First, force a clean IDLE start (the seed file may have
        # accumulated test residue from a previous in-process run).
        with lab._state_lock:
            lab.current_state["system_status"] = SYSTEM_STATUS_IDLE
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        lab._persist_state()

        # PICK
        asyncio.run(lab.pick_component(target, {}))
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(held_tag(st), target)
        self.assertEqual(
            st["components"][target]["measurables"]["pose"]["z"],
            float(DEFAULT_HOVER_Z_MM),
        )

        # HOVER
        asyncio.run(
            lab.hover_component(
                target,
                {"target_x": 50.0, "target_y": 25.0, "rotation": 30.0, "z": 35.0},
            )
        )
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], SYSTEM_STATUS_HOLDING)
        nominal = st["holding"]["nominal_pose"]
        self.assertEqual(nominal["x"], 50.0)
        self.assertEqual(nominal["y"], 25.0)
        self.assertEqual(nominal["rotation"], 30.0)
        self.assertEqual(nominal["z"], 35.0)
        # Mock noise lands on measurables.pose only (within +/- 0.3 mm).
        m_pose = st["components"][target]["measurables"]["pose"]
        self.assertAlmostEqual(m_pose["x"], 50.0, delta=1.0)
        self.assertAlmostEqual(m_pose["y"], 25.0, delta=1.0)
        self.assertEqual(m_pose["rotation"], 30.0)

        # SCAN_ROTATE held mode: theta_max wins, status stays HOLDING.
        asyncio.run(
            lab.scan_rotate_in_place(
                target,
                {
                    "theta_min": 30.0,
                    "theta_max": 75.0,
                    "speed_deg_per_s": 90.0,
                    "axis": "z",
                },
            )
        )
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], SYSTEM_STATUS_HOLDING)
        self.assertEqual(st["holding"]["nominal_pose"]["rotation"], 75.0)

        # PLACE_FROM_HOVER
        asyncio.run(
            lab.place_from_hover(
                target, {"target_x": 60.0, "target_y": 35.0, "rotation": 75.0}
            )
        )
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], SYSTEM_STATUS_IDLE)
        self.assertFalse(is_holding(st))
        placed = st["components"][target]["measurables"]["pose"]
        self.assertEqual(placed["x"], 60.0)
        self.assertEqual(placed["y"], 35.0)
        self.assertEqual(placed["rotation"], 75.0)
        self.assertNotIn(
            "z", placed,
            "place_from_hover must strip z from measurables.pose"
        )

        # SCAN_ROTATE placed mode
        asyncio.run(
            lab.scan_rotate_in_place(
                target,
                {
                    "theta_min": 75.0,
                    "theta_max": 0.0,
                    "speed_deg_per_s": 90.0,
                    "axis": "z",
                },
            )
        )
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], SYSTEM_STATUS_IDLE)
        self.assertEqual(
            st["components"][target]["measurables"]["pose"]["rotation"], 0.0
        )

    def test_pick_refused_when_already_holding(self) -> None:
        from lab_model.holding import SYSTEM_STATUS_IDLE, held_tag
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        with lab._state_lock:
            lab.current_state["system_status"] = SYSTEM_STATUS_IDLE
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        asyncio.run(lab.pick_component(target, {}))
        # Now the second pick of a *different* on-breadboard part must
        # be refused (single-gripper invariant).
        other = next(
            t for t, c in (lab.get_lab_state().get("components") or {}).items()
            if (c.get("tunables") or {}).get("presence") == "breadboard"
            and t != target
        )
        asyncio.run(lab.pick_component(other, {}))
        # Original tag still held.
        self.assertEqual(held_tag(lab.get_lab_state()), target)
        # Cleanup so other tests start IDLE.
        asyncio.run(
            lab.place_from_hover(target, {"target_x": 0.0, "target_y": 0.0, "rotation": 0.0})
        )

    def test_hover_refused_when_not_holding(self) -> None:
        from lab_model.holding import SYSTEM_STATUS_IDLE
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        with lab._state_lock:
            lab.current_state["system_status"] = SYSTEM_STATUS_IDLE
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        # Hover without holding: orchestrator must refuse, status
        # stays IDLE, no exception.
        asyncio.run(
            lab.hover_component(
                target, {"target_x": 0.0, "target_y": 0.0, "rotation": 0.0, "z": 30.0}
            )
        )
        self.assertEqual(lab.get_lab_state()["system_status"], SYSTEM_STATUS_IDLE)

    def test_hover_refused_when_z_out_of_bounds(self) -> None:
        from lab_model.holding import SYSTEM_STATUS_HOLDING, SYSTEM_STATUS_IDLE
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        with lab._state_lock:
            lab.current_state["system_status"] = SYSTEM_STATUS_IDLE
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        asyncio.run(lab.pick_component(target, {}))
        # Now request hover with z way above max_safe_hover_z_lab_mm (200 default).
        asyncio.run(
            lab.hover_component(
                target,
                {"target_x": 0.0, "target_y": 0.0, "rotation": 0.0, "z": 999.0},
            )
        )
        # Refusal happens before any status flip, so we stay HOLDING.
        self.assertEqual(lab.get_lab_state()["system_status"], SYSTEM_STATUS_HOLDING)
        # Cleanup.
        asyncio.run(
            lab.place_from_hover(target, {"target_x": 0.0, "target_y": 0.0, "rotation": 0.0})
        )

    def test_place_from_hover_refused_into_storage_quadrant(self) -> None:
        from lab_model.holding import SYSTEM_STATUS_HOLDING, SYSTEM_STATUS_IDLE
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        with lab._state_lock:
            lab.current_state["system_status"] = SYSTEM_STATUS_IDLE
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        asyncio.run(lab.pick_component(target, {}))
        # Storage quadrant is bottom-left in the breadboard frame
        # (negative x, negative y). Pick a point well inside it.
        asyncio.run(
            lab.place_from_hover(
                target, {"target_x": -200.0, "target_y": -200.0, "rotation": 0.0}
            )
        )
        # Refusal: still HOLDING (orchestrator never flipped).
        self.assertEqual(lab.get_lab_state()["system_status"], SYSTEM_STATUS_HOLDING)
        # Cleanup at a non-storage location.
        asyncio.run(
            lab.place_from_hover(target, {"target_x": 0.0, "target_y": 100.0, "rotation": 0.0})
        )


class HeavyStatePrimitivesMockRoundTripTests(unittest.TestCase):
    """Phase 2C regression tests: mock end-to-end through the heavy-state
    primitives.

    Exercises the move-on-table family (``move_component``,
    ``store_component``, ``place_from_storage``, ``repack_storage_slot``,
    ``recenter_stored_in_inventory``), the no-hardware state mutators
    (``affirm_placed_at_current``, ``add_component_to_state``,
    ``remove_component``), and the observe template
    (``observe_measurables_for_tag``). Each test runs against a temp
    state file so the developer's working snapshot is preserved.

    Like the Phase 2B tests, these don't import ``lab_automation``;
    they verify the orchestrator + commits + refusal-helpers stack
    cohere under realistic input dicts and presence/slot transitions.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        cls._tmp = tempfile.mkdtemp(prefix="phase2c_")
        backend_root = pathlib.Path(__file__).resolve().parents[1]
        seed_state = backend_root.parents[0] / "schemas" / "mock_lab_state.json"
        seed_catalog = backend_root.parents[0] / "schemas" / "component_catalog.mock.json"
        cls._state_file = os.path.join(cls._tmp, "mock_lab_state.json")
        cls._catalog_file = os.path.join(cls._tmp, "component_catalog.mock.json")
        shutil.copy(str(seed_state), cls._state_file)
        shutil.copy(str(seed_catalog), cls._catalog_file)

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _new_lab(self):
        import lab_communicator.mock.communicator as mc
        with patch.object(mc, "LAB_STATE_FILE", self._state_file), \
             patch.object(mc, "CATALOG_FILE", self._catalog_file):
            from lab_communicator.mock.communicator import MockLabCommunicator
            return MockLabCommunicator()

    @staticmethod
    def _first_breadboard_tag(lab) -> str:
        for tag_id, comp in (lab.get_lab_state().get("components") or {}).items():
            if (comp.get("tunables") or {}).get("presence") == "breadboard":
                return tag_id
        raise AssertionError("no on-breadboard component in seed state")

    def _force_breadboard(self, lab, tag_id: str, x: float = 0.0, y: float = 0.0) -> None:
        """Reset a tag to BREADBOARD/MANUAL at a known pose."""
        with lab._state_lock:
            comp = lab.current_state["components"][tag_id]
            tun = comp.setdefault("tunables", {})
            tun["presence"] = "breadboard"
            tun["nominal_pose"] = {"x": x, "y": y, "rotation": 0.0}
            tun["placement"] = {"mode": "MANUAL"}
            tun["storage"] = {"in_storage": False, "slot": None}
            comp.setdefault("measurables", {})["pose"] = {
                "x": x, "y": y, "rotation": 0.0
            }
            lab.current_state["system_status"] = "IDLE"
            lab.current_state["holding"] = {
                "tag_id": None,
                "nominal_pose": {"x": 0.0, "y": 0.0, "rotation": 0.0, "z": 0.0},
                "requires_operator_confirm": False,
            }
        lab._persist_state()

    def test_move_component_breadboard_to_breadboard(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=100.0, y=100.0)

        asyncio.run(
            lab.move_component(
                target,
                {"target_x": -50.0, "target_y": 250.0, "rotation": 30.0},
            )
        )
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], "IDLE")
        comp = st["components"][target]
        nominal = comp["tunables"]["nominal_pose"]
        self.assertEqual(nominal["x"], -50.0)
        self.assertEqual(nominal["y"], 250.0)
        self.assertEqual(nominal["rotation"], 30.0)
        self.assertEqual(comp["tunables"]["presence"], "breadboard")
        self.assertEqual(comp["tunables"]["placement"]["mode"], "MANUAL")
        # Mock noise lands on measurables.pose only.
        m_pose = comp["measurables"]["pose"]
        self.assertAlmostEqual(m_pose["x"], -50.0, delta=1.0)
        self.assertAlmostEqual(m_pose["y"], 250.0, delta=1.0)
        self.assertEqual(m_pose["rotation"], 30.0)

    def test_move_component_refused_when_target_in_storage_quadrant(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=100.0, y=100.0)

        # Storage Q3 = negative x AND negative y.
        asyncio.run(
            lab.move_component(
                target,
                {"target_x": -200.0, "target_y": -200.0, "rotation": 0.0},
            )
        )
        comp = lab.get_lab_state()["components"][target]
        # Refusal: pose unchanged.
        self.assertEqual(comp["tunables"]["nominal_pose"]["x"], 100.0)
        self.assertEqual(comp["tunables"]["nominal_pose"]["y"], 100.0)

    def test_store_then_place_from_storage_round_trip(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=120.0, y=120.0)

        # STORE: BREADBOARD -> STORAGE
        asyncio.run(lab.store_component(target))
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(comp["tunables"]["presence"], "storage")
        self.assertEqual(comp["tunables"]["placement"]["mode"], "STORAGE")
        self.assertTrue(comp["tunables"]["storage"]["in_storage"])
        slot = comp["tunables"]["storage"]["slot"]
        self.assertIsInstance(slot, dict)
        self.assertIn("i", slot)
        self.assertIn("j", slot)

        # PLACE_FROM_STORAGE: STORAGE -> BREADBOARD
        asyncio.run(
            lab.place_from_storage(
                target,
                {"target_x": 50.0, "target_y": 200.0, "rotation": 45.0},
            )
        )
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(comp["tunables"]["presence"], "breadboard")
        self.assertEqual(comp["tunables"]["placement"]["mode"], "MANUAL")
        self.assertFalse(comp["tunables"]["storage"]["in_storage"])
        self.assertEqual(comp["tunables"]["nominal_pose"]["x"], 50.0)
        self.assertEqual(comp["tunables"]["nominal_pose"]["y"], 200.0)
        self.assertEqual(comp["tunables"]["nominal_pose"]["rotation"], 45.0)

    def test_place_from_storage_refused_when_target_in_q3(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=120.0, y=120.0)
        asyncio.run(lab.store_component(target))
        # Now try to place into storage Q3 -- must refuse.
        asyncio.run(
            lab.place_from_storage(
                target,
                {"target_x": -300.0, "target_y": -300.0, "rotation": 0.0},
            )
        )
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(
            comp["tunables"]["presence"], "storage",
            "place_from_storage into Q3 must be refused (still STORED)"
        )

    def test_repack_storage_slot_changes_slot(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=120.0, y=120.0)
        asyncio.run(lab.store_component(target))
        old_slot = lab.get_lab_state()["components"][target]["tunables"]["storage"]["slot"]

        asyncio.run(lab.repack_storage_slot(target))
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(comp["tunables"]["presence"], "storage")
        self.assertTrue(comp["tunables"]["storage"]["in_storage"])
        new_slot = comp["tunables"]["storage"]["slot"]
        self.assertIsInstance(new_slot, dict)
        # Slot may legitimately stay if the part is already in the
        # first-free cell -- the important invariant is that we
        # remained STORED with valid slot metadata.
        self.assertIn("i", new_slot)
        self.assertIn("j", new_slot)

    def test_recenter_stored_in_inventory_keeps_storage(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=120.0, y=120.0)
        asyncio.run(lab.store_component(target))

        asyncio.run(lab.recenter_stored_in_inventory(target))
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(comp["tunables"]["presence"], "storage")
        self.assertEqual(comp["tunables"]["placement"]["mode"], "STORAGE")
        self.assertTrue(comp["tunables"]["storage"]["in_storage"])

    def test_affirm_placed_at_current_lifts_storage(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target, x=120.0, y=120.0)
        asyncio.run(lab.store_component(target))

        asyncio.run(lab.affirm_placed_at_current(target))
        comp = lab.get_lab_state()["components"][target]
        self.assertEqual(comp["tunables"]["presence"], "breadboard")
        self.assertEqual(comp["tunables"]["placement"]["mode"], "MANUAL")
        self.assertFalse(comp["tunables"]["storage"]["in_storage"])
        self.assertIsNone(comp["tunables"]["storage"]["slot"])

    def test_add_then_remove_component(self) -> None:
        lab = self._new_lab()
        before = set((lab.get_lab_state().get("components") or {}).keys())
        # Pick a fresh tag id not in the seed.
        new_tag = "tag_phase2c_test"
        i = 0
        while new_tag in before:
            new_tag = f"tag_phase2c_test_{i}"
            i += 1
        asyncio.run(
            lab.add_component_to_state(
                {"tag_id": new_tag, "type": "OPTICAL_MIRROR", "placement_mode": "breadboard"}
            )
        )
        self.assertIn(new_tag, lab.get_lab_state()["components"])
        # Duplicate adds are refused (no second insert).
        n_before = len(lab.get_lab_state()["components"])
        asyncio.run(lab.add_component_to_state({"tag_id": new_tag, "type": "OPTICAL_MIRROR"}))
        self.assertEqual(len(lab.get_lab_state()["components"]), n_before)
        # Remove returns us to a clean baseline.
        asyncio.run(lab.remove_component(new_tag))
        self.assertNotIn(new_tag, lab.get_lab_state()["components"])

    def test_observe_measurables_for_unknown_tag_returns_empty(self) -> None:
        lab = self._new_lab()
        meas = asyncio.run(lab.observe_measurables_for_tag("never-existed"))
        self.assertEqual(meas, {})


class OptimizePrimitiveMockRoundTripTests(unittest.TestCase):
    """Phase 2D regression tests: mock end-to-end through ``optimize_component``.

    Exercises:

    - status flip IDLE -> OPTIMIZING -> IDLE,
    - ``optimization_step`` ticks through hook progress callbacks,
    - ``last_optimization_score`` + ``last_optimized_pose`` land via
      :func:`commit_optimization_complete`,
    - ``placement.mode`` reflects the strategy name,
    - refusal: STORED parts cannot be optimized,
    - refusal: tags missing from the catalog cannot be optimized.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        cls._tmp = tempfile.mkdtemp(prefix="phase2d_")
        backend_root = pathlib.Path(__file__).resolve().parents[1]
        seed_state = backend_root.parents[0] / "schemas" / "mock_lab_state.json"
        seed_catalog = backend_root.parents[0] / "schemas" / "component_catalog.mock.json"
        cls._state_file = os.path.join(cls._tmp, "mock_lab_state.json")
        cls._catalog_file = os.path.join(cls._tmp, "component_catalog.mock.json")
        shutil.copy(str(seed_state), cls._state_file)
        shutil.copy(str(seed_catalog), cls._catalog_file)

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _new_lab(self):
        import lab_communicator.mock.communicator as mc
        with patch.object(mc, "LAB_STATE_FILE", self._state_file), \
             patch.object(mc, "CATALOG_FILE", self._catalog_file):
            from lab_communicator.mock.communicator import MockLabCommunicator
            return MockLabCommunicator()

    @staticmethod
    def _first_breadboard_tag(lab) -> str:
        for tag_id, comp in (lab.get_lab_state().get("components") or {}).items():
            if (comp.get("tunables") or {}).get("presence") == "breadboard":
                return tag_id
        raise AssertionError("no on-breadboard component in seed state")

    def _force_breadboard(self, lab, tag_id: str) -> None:
        with lab._state_lock:
            comp = lab.current_state["components"][tag_id]
            tun = comp.setdefault("tunables", {})
            tun["presence"] = "breadboard"
            tun["nominal_pose"] = {"x": 100.0, "y": 100.0, "rotation": 0.0}
            tun["placement"] = {"mode": "MANUAL"}
            tun["storage"] = {"in_storage": False, "slot": None}
            comp.setdefault("measurables", {})["pose"] = {
                "x": 100.0, "y": 100.0, "rotation": 0.0,
            }
            lab.current_state["system_status"] = "IDLE"
            lab.current_state["optimization_step"] = 0
            lab.current_state["optimization_run_dir"] = None
        lab._persist_state()

    def test_optimize_component_round_trip_commits_score_and_pose(self) -> None:
        lab = self._new_lab()
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target)

        asyncio.run(lab.optimize_component(target, "NEWTON", {}))
        st = lab.get_lab_state()
        # Status returned to IDLE; step/run_dir cleared.
        self.assertEqual(st["system_status"], "IDLE")
        self.assertEqual(st["optimization_step"], 0)
        self.assertIsNone(st["optimization_run_dir"])
        comp = st["components"][target]
        # Strategy mode + score + last_optimized_pose committed.
        self.assertEqual(comp["tunables"]["placement"]["mode"], "NEWTON")
        self.assertAlmostEqual(
            comp["measurables"]["last_optimization_score"], 0.99, places=5
        )
        last_pose = comp["measurables"].get("last_optimized_pose") or {}
        self.assertIn("x", last_pose)
        self.assertIn("y", last_pose)
        self.assertIn("rotation", last_pose)
        # Mock's simulated rotation drift lands within +/- 1 deg.
        self.assertAlmostEqual(last_pose["rotation"], 0.0, delta=1.5)

    def test_optimize_component_refused_when_stored(self) -> None:
        lab = self._new_lab()
        # Seed a STORED part by storing a breadboard one.
        target = self._first_breadboard_tag(lab)
        self._force_breadboard(lab, target)
        asyncio.run(lab.store_component(target))
        self.assertEqual(
            lab.get_lab_state()["components"][target]["tunables"]["presence"],
            "storage",
        )

        # Snapshot pre-call so we can assert the refusal didn't mutate
        # any of the optimization summary fields. Tests in this class
        # share a state file (setUpClass scope), so a *prior* test may
        # have left score=0.99 / mode=NEWTON in place -- the refusal
        # invariant we want is "unchanged", not "absent".
        before = lab.get_lab_state()["components"][target]
        before_mode = (before.get("tunables") or {}).get("placement", {}).get("mode")
        before_score = (before.get("measurables") or {}).get("last_optimization_score")

        asyncio.run(lab.optimize_component(target, "NEWTON", {}))
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], "IDLE")
        comp = st["components"][target]
        # Refusal MUST NOT touch any of the post-optimize commit fields.
        self.assertEqual(
            (comp.get("tunables") or {}).get("placement", {}).get("mode"),
            before_mode,
        )
        self.assertEqual(
            (comp.get("measurables") or {}).get("last_optimization_score"),
            before_score,
        )

    def test_optimize_component_refused_when_unknown_tag(self) -> None:
        lab = self._new_lab()
        # Tag id deliberately absent from the catalog.
        asyncio.run(lab.optimize_component("never-existed-tag", "NEWTON", {}))
        st = lab.get_lab_state()
        self.assertEqual(st["system_status"], "IDLE")
        self.assertEqual(st["optimization_step"], 0)


if __name__ == "__main__":
    unittest.main()
