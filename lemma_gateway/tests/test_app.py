from __future__ import annotations

import threading
import time
import unittest

from fastapi.testclient import TestClient

from lemma_gateway.app import create_app


class StubController:
    def __init__(self) -> None:
        self.closed = False
        self.mutations: list[str] = []

    def health(self):
        return {"ok": True, "backend_id": "sim.default", "system_status": "IDLE"}

    def capabilities(self, *, detailed=False):
        return {"commands": ["move <tag> <x> <y> <rot>"], "detailed": detailed}

    def raw_state(self):
        return {"system_status": "IDLE", "components": {}}

    def resolve_component(self, component):
        return component

    def compact_state(self, *, tag_id=None):
        return {
            "backend_id": "sim.default",
            "system_status": "IDLE",
            "component_count": 0,
            "components": [],
            "tag_id": tag_id,
        }

    def show_preset(self, preset_name):
        return {
            "preset": preset_name,
            "document": {
                "schema_version": 1,
                "kind": "cloud_labs_simulation_preset",
                "base": preset_name,
                "components": {},
            },
        }

    def write_preset(self, preset_name, document, *, overwrite=False):
        return {
            "status": "ok",
            "preset": {"name": preset_name},
            "document": document,
            "overwrite": overwrite,
        }

    def execute_read(self, parsed):
        if parsed.kind == "state":
            return self.compact_state()
        if parsed.kind == "component_next_tag":
            return {"tag_id": "tag_100"}
        return {"kind": parsed.kind}

    def execute_mutation(self, parsed, cancel_event: threading.Event):
        if parsed.kind == "preset_write":
            self.mutations.append("SIMWRITE")
            return self.write_preset(
                parsed.arguments["name"],
                parsed.arguments["document"],
                overwrite=parsed.arguments.get("overwrite", False),
            )
        if parsed.kind.startswith("component_"):
            self.mutations.append(parsed.kind)
            return {"ok": True, "operation": parsed.kind}
        self.mutations.append(parsed.command["action"])
        time.sleep(0.01)
        return {"ok": True}

    def release_lease(self):
        return {"released": True}

    def close(self):
        self.closed = True


class GatewayAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StubController()
        self.client_context = TestClient(create_app(self.controller))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)

    def test_state_command_is_immediate(self) -> None:
        response = self.client.post("/v1/console", json={"command": "state"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "immediate")

    def test_preset_read_and_write_endpoints(self) -> None:
        shown = self.client.get("/v1/presets/test_random")
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.json()["document"]["base"], "test_random")

        document = {
            "schema_version": 1,
            "kind": "cloud_labs_simulation_preset",
            "base": "default",
            "components": {},
        }
        written = self.client.put(
            "/v1/presets/authored",
            json={"document": document, "overwrite": True},
        )
        self.assertEqual(written.status_code, 200)
        self.assertEqual(written.json()["preset"]["name"], "authored")
        self.assertTrue(written.json()["overwrite"])

    def test_simshow_is_immediate_and_simwrite_is_a_job(self) -> None:
        shown = self.client.post("/v1/console", json={"command": "simshow test_random"})
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.json()["mode"], "immediate")

        document = (
            '{"schema_version":1,"kind":"cloud_labs_simulation_preset",'
            '"base":"default","components":{"tag_13":{"presence":"breadboard",'
            '"pose":{"x":-300,"y":0,"rotation":-90}}}}'
        )
        accepted = self.client.post(
            "/v1/console",
            json={"command": f"simwrite authored {document}"},
        )
        self.assertEqual(accepted.status_code, 202)

    def test_mutation_returns_pollable_job(self) -> None:
        response = self.client.post(
            "/v1/console",
            json={"command": "move tag_13 1 2 3", "request_id": "move-1"},
        )
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job"]["job_id"]
        for _ in range(100):
            job = self.client.get(f"/v1/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(self.controller.mutations, ["MOVE_COMPONENT"])

    def test_request_id_is_idempotent(self) -> None:
        body = {"command": "move tag_13 1 2 3", "request_id": "same-request"}
        first = self.client.post("/v1/console", json=body)
        second = self.client.post("/v1/console", json=body)
        self.assertEqual(first.json()["job"]["job_id"], second.json()["job"]["job_id"])
        self.assertFalse(second.json()["created"])

    def test_unknown_command_is_400(self) -> None:
        response = self.client.post("/v1/console", json={"command": "nope"})
        self.assertEqual(response.status_code, 400)

    def test_component_library_read_and_define_job(self) -> None:
        listed = self.client.post(
            "/v1/console", json={"command": "simcomponent list"}
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["mode"], "immediate")

        defined = self.client.post(
            "/v1/console",
            json={
                "command": (
                    'simcomponent define tag_23 '
                    '{"name":"Paper lens","type":"OPTICAL_LENS"}'
                )
            },
        )
        self.assertEqual(defined.status_code, 202)

        next_tag = self.client.post(
            "/v1/console", json={"command": "simcomponent nexttag"}
        )
        self.assertEqual(next_tag.status_code, 200)
        self.assertEqual(next_tag.json()["result"], {"tag_id": "tag_100"})


if __name__ == "__main__":
    unittest.main()
