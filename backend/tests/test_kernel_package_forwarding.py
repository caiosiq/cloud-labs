"""Phase 3: session kernel_packages forwarding + premade refuse."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_model.coordinator.jobs import runner as runner_mod


_EDGE_ROOT = Path(
    r"C:\Users\szist\Desktop\josh_robotic_twin_simulation\lab_automation\cloudlabs_edge"
)


class KernelPackageForwardingTests(unittest.TestCase):
    def test_closed_loop_merges_packages_into_command_params(self) -> None:
        packages = [
            {
                "kernel_id": "session.test.score.abcd1234",
                "digest": "sha256:" + ("a" * 64),
                "artifact_b64": base64.b64encode(b"x").decode("ascii"),
            }
        ]
        captured: dict = {}

        class _Opt:
            pass

        async def _fake_execute(lab, cmd):  # noqa: ANN001
            captured["cmd"] = cmd

        class _Mgr:
            def is_cancel_requested(self, _jid):  # noqa: ANN001
                return False

            def update_progress(self, *_a, **_k):  # noqa: ANN001
                return None

        spec = {
            "command": {
                "action": "OPTIMIZE",
                "target_id": "tag_20",
                "parameters": {"mode": "ensemble"},
            },
            "kernel_packages": packages,
        }

        class _Lab:
            pass

        with mock.patch.object(runner_mod, "parse_command_payload", return_value=_Opt()) as parse:
            with mock.patch.object(runner_mod, "OptimizeBody", _Opt):
                with mock.patch.object(
                    runner_mod, "execute_validated_command", side_effect=_fake_execute
                ):
                    asyncio.run(
                        runner_mod._run_closed_loop("j1", _Lab(), spec, _Mgr())
                    )

        called_cmd = parse.call_args[0][0]
        self.assertEqual(called_cmd["parameters"]["kernel_packages"], packages)

    def test_edge_provision_refuses_premade_id(self) -> None:
        if str(_EDGE_ROOT) not in sys.path:
            sys.path.insert(0, str(_EDGE_ROOT))
        from adapters import optimize as opt

        with tempfile.TemporaryDirectory() as tmp:
            kdir = Path(tmp)
            (kdir / "manifest.json").write_text(
                '{"schema_version":1,"kernels":[{"id":"builtin.roi_centroid","artifact":"x.pt"}]}',
                encoding="utf-8",
            )
            with mock.patch.object(opt, "_kernels_dir", return_value=kdir):
                with self.assertRaises(ValueError) as ctx:
                    opt.provision_session_packages(
                        [
                            {
                                "kernel_id": "builtin.roi_centroid",
                                "artifact_b64": base64.b64encode(b"abc").decode("ascii"),
                                "digest": "sha256:" + hashlib.sha256(b"abc").hexdigest(),
                            }
                        ]
                    )
                self.assertIn("session.", str(ctx.exception).lower())

    def test_edge_provision_session_ok(self) -> None:
        if str(_EDGE_ROOT) not in sys.path:
            sys.path.insert(0, str(_EDGE_ROOT))
        from adapters import optimize as opt

        data = b"not-torch-but-cached"
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            kdir = Path(tmp)
            (kdir / "manifest.json").write_text(
                '{"schema_version":1,"kernels":[]}',
                encoding="utf-8",
            )
            with mock.patch.object(opt, "_kernels_dir", return_value=kdir):
                kids = opt.provision_session_packages(
                    [
                        {
                            "kernel_id": "session.test.score.abcd1234",
                            "artifact_b64": base64.b64encode(data).decode("ascii"),
                            "digest": digest,
                            "output_kind": "scalar",
                        }
                    ]
                )
            self.assertEqual(kids, ["session.test.score.abcd1234"])
            self.assertTrue((kdir / "session.test.score.abcd1234.pt").is_file())


if __name__ == "__main__":
    unittest.main()
