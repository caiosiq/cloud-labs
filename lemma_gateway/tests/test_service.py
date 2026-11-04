"""Exercise real gateway jobs against coordinator HTTP responses."""
import json
import threading
import time
import unittest

import httpx
from fastapi.testclient import TestClient

from lemma_gateway.app import create_app
from lemma_gateway.command_language import parse_console_line
from lemma_gateway.service import CoordinatorClient, GatewayError, JobCancelled


class CommandFeedbackTests(unittest.TestCase):
    def make_controller(self, statuses, *, receipt=None, state_status="IDLE", status_response=None, command_response=None):
        self.calls = []
        self.polls = 0
        self.cancel = threading.Event()
        self.state = {
            "system_status": state_status,
            "last_runtime_error": {"message": "latest simulator error", "target_id": "tag_22"},
            "components": {"tag_22": {"statecontrol": {"tunables": {
                "presence": "breadboard", "nominal_pose": {"x": 320, "y": -285, "rotation": -90},
            }}}},
        }

        def handler(request):
            self.calls.append((request.method, request.url.path))
            self.assertEqual(request.headers["X-CloudLabs-Backend"], "sim.default")
            path = request.url.path
            if path == "/api/jobs/lease/acquire":
                body = {"lease_id": "test-lease"}
            elif path == "/api/jobs/lease/release":
                body = {"released": True}
            elif path == "/api/catalog":
                body = [{"tag_id": "tag_22", "name": "test component"}]
            elif path == "/api/lab-state":
                body = self.state
            elif path == "/api/command":
                if command_response:
                    return command_response(request)
                body = receipt if receipt is not None else {"status": "queued", "command_id": "cmd_test"}
            elif path == "/api/command-queue/status":
                self.assertEqual(json.loads(request.content), {"command_ids": ["cmd_test"]})
                self.polls += 1
                if status_response:
                    return status_response(request)
                info = statuses[min(self.polls - 1, len(statuses) - 1)]
                body = {"enabled": True, "statuses": {"cmd_test": info}}
            else:
                raise AssertionError(path)
            return httpx.Response(200, json=body)

        controller = CoordinatorClient(
            session=httpx.Client(transport=httpx.MockTransport(handler)),
            operation_timeout_s=.05, poll_interval_s=.001, heartbeat_s=60,
        )
        self.addCleanup(controller.close)
        return controller

    def execute(self, controller, line="move tag_22 200 490.4 -90"):
        return controller.execute_mutation(parse_console_line(line), self.cancel)

    def test_idle_does_not_hide_failed_command(self):
        controller = self.make_controller([{"status": "failed", "error": "destination outside tool envelope"}])
        with self.assertRaisesRegex(GatewayError, "cmd_test failed: destination outside tool envelope") as caught:
            self.execute(controller)
        self.assertEqual(caught.exception.result["command_status"]["status"], "failed")
        self.assertEqual(caught.exception.result["accepted"]["command_id"], "cmd_test")
        self.assertEqual(self.polls, 1)

    def test_idle_while_queued_never_finishes_early(self):
        controller = self.make_controller([{"status": s} for s in ("queued", "queued", "running", "done")])
        result = self.execute(controller)
        self.assertEqual(self.polls, 4)
        self.assertEqual(result["command_status"]["status"], "done")
        # State was only read to resolve the component, never to infer completion.
        self.assertEqual(self.calls.count(("GET", "/api/lab-state")), 1)
        self.assertEqual(self.calls.count(("POST", "/api/command")), 1)

    def test_done_pick_in_holding_state_is_successful(self):
        controller = self.make_controller([{"status": "done"}], state_status="HOLDING")
        self.assertEqual(self.execute(controller, "pick tag_22")["command_status"]["status"], "done")

    def test_coordinator_cancellation_is_not_success(self):
        controller = self.make_controller([{"status": "cancelled", "error": "lease_released"}])
        with self.assertRaisesRegex(JobCancelled, "lease_released"):
            self.execute(controller)

    def test_gateway_cancellation_preserves_command_receipt(self):
        def cancel_during_poll(_request):
            self.cancel.set()
            return httpx.Response(200, json={"enabled": True, "statuses": {"cmd_test": {"status": "running"}}})
        controller = self.make_controller([], status_response=cancel_during_poll)
        with self.assertRaisesRegex(JobCancelled, "may still finish") as caught:
            self.execute(controller)
        self.assertEqual(caught.exception.result["command_id"], "cmd_test")

    def test_timeout_preserves_id_without_resubmitting_move(self):
        controller = self.make_controller([{"status": "running"}])
        with self.assertRaisesRegex(GatewayError, "Timed out.*cmd_test.*may still finish") as caught:
            self.execute(controller)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertEqual(caught.exception.result["command_id"], "cmd_test")
        self.assertEqual(self.calls.count(("POST", "/api/command")), 1)

    def test_unknown_missing_disabled_and_malformed_status_cannot_succeed(self):
        for payload in (
            {"enabled": True, "statuses": {"cmd_test": {"status": "unknown"}}},
            {"enabled": True, "statuses": {"cmd_test": {"status": "unexpected"}}},
            {"enabled": True, "statuses": {}},
            {"enabled": False}, {}, [],
        ):
            with self.subTest(payload=payload):
                controller = self.make_controller([], status_response=lambda _r, p=payload: httpx.Response(200, json=p))
                with self.assertRaisesRegex(GatewayError, "Cannot confirm outcome of cmd_test"):
                    self.execute(controller)

    def test_status_transport_error_retains_command_id(self):
        def disconnected(request):
            raise httpx.ConnectError("connection lost", request=request)
        controller = self.make_controller([], status_response=disconnected)
        with self.assertRaisesRegex(GatewayError, "Cannot confirm outcome of cmd_test") as caught:
            self.execute(controller)
        self.assertEqual(caught.exception.result["command_id"], "cmd_test")

    def test_invalid_json_cannot_succeed(self):
        controller = self.make_controller([], status_response=lambda r: httpx.Response(200, text="not JSON"))
        with self.assertRaisesRegex(GatewayError, "invalid JSON"):
            self.execute(controller)

    def test_synchronous_completion_without_matrix(self):
        controller = self.make_controller([], receipt={"status": "ok", "pose": {"x": 200, "y": 440}})
        self.assertEqual(self.execute(controller)["completion"], "synchronous")
        self.assertEqual(self.polls, 0)

    def test_acceptance_without_tracking_cannot_succeed(self):
        for receipt in ({"status": "queued"}, {"status": "accepted"}, {}, {"status": "error"}):
            with self.subTest(receipt=receipt):
                controller = self.make_controller([], receipt=receipt)
                with self.assertRaisesRegex(GatewayError, "outcome cannot be confirmed"):
                    self.execute(controller)

    def test_compact_state_keeps_latest_error(self):
        controller = self.make_controller([])
        self.assertEqual(controller.compact_state()["last_runtime_error"], self.state["last_runtime_error"])

    def test_immediate_rejection_preserves_error_without_polling(self):
        for code in (400, 409, 422, 502):
            with self.subTest(code=code):
                controller = self.make_controller([], command_response=lambda r, c=code: httpx.Response(c, json={"detail": "specific refusal"}))
                with self.assertRaisesRegex(GatewayError, "specific refusal"):
                    self.execute(controller)
                self.assertEqual(self.polls, 0)

    def test_submission_timeout_warns_about_uncertain_delivery(self):
        def timeout(request):
            raise httpx.ReadTimeout("response timed out", request=request)
        controller = self.make_controller([], command_response=timeout)
        with self.assertRaisesRegex(GatewayError, "may have been accepted"):
            self.execute(controller)
        self.assertEqual(self.calls.count(("POST", "/api/command")), 1)

    def test_real_gateway_job_reports_late_failure_and_details(self):
        controller = self.make_controller([{"status": "failed", "error": "destination outside tool envelope"}])
        with TestClient(create_app(controller)) as api:
            response = api.post("/v1/console", json={"command": "move tag_22 200 490.4 -90"})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["job"]["job_id"]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = api.get(f"/v1/jobs/{job_id}").json()
                if job["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(.001)
            self.assertEqual(job["status"], "failed")
            self.assertIn("destination outside tool envelope", job["error"])
            self.assertEqual(job["result"]["command_id"], "cmd_test")

    def test_real_gateway_job_preserves_coordinator_cancellation(self):
        controller = self.make_controller([{"status": "cancelled", "error": "lease released"}])
        with TestClient(create_app(controller)) as api:
            accepted = api.post('/v1/console', json={"command": "move tag_22 200 440 -90"}).json()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = api.get('/v1/jobs/' + accepted['job']['job_id']).json()
                if job['status'] in {'succeeded', 'failed', 'cancelled'}:
                    break
                time.sleep(.001)
            self.assertEqual(job['status'], 'cancelled')
            self.assertIn('lease released', job['error'])
            self.assertEqual(job['result']['command_id'], 'cmd_test')


if __name__ == "__main__":
    unittest.main()
