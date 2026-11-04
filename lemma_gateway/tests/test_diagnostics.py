import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from lemma_gateway.app import create_app
from lemma_gateway.command_language import parse_console_line
from lemma_gateway.diagnostics import MAX_EVENTS, job_diagnostics
from lemma_gateway.service import GatewayError
from lemma_gateway.tests.test_app import StubController


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict('os.environ', {'CLOUDLAB_MUJOCO_LOG_DIR': str(self.root), 'CLOUDLABS_LEMMA_API_TOKEN': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.request_id = 'a' * 32
        self.log = self.root / 'mujoco_20261104_120000_1234.jsonl'
        error = f'move failed (sim_request_id={self.request_id}, log={self.log})'
        self.job = {
            'job_id': 'job_test', 'command': 'move tag_22 200 490.4 -90',
            'status': 'failed', 'error': error,
            'result': {'command_id': 'cmd_test', 'command_status': {'status': 'failed', 'error': error}},
        }

    def write(self, rows):
        self.log.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def test_only_matching_request_is_exported_with_traceback(self):
        self.write([
            {'request_id': 'b' * 32, 'event': 'other_move', 'error': 'unrelated'},
            {'request_id': self.request_id, 'event': 'command_received', 'command': {'target_y_mm': 490.4}},
            {'request_id': self.request_id, 'event': 'pick_and_place_failure', 'error': 'outside frame',
             'traceback': 'Traceback: original failure', 'stage_trace': ['open gripper'], 'state': {'bulky': 'not exported'}},
        ])
        report = job_diagnostics(self.job)
        self.assertTrue(report['simulator']['available'])
        self.assertEqual(len(report['simulator']['events']), 2)
        failure = report['simulator']['events'][-1]
        self.assertEqual(failure['traceback'], 'Traceback: original failure')
        self.assertEqual(failure['stage_trace'], ['open gripper'])
        self.assertNotIn('state', failure)

    def test_no_log_reference_keeps_original_error(self):
        self.job['result'] = None
        self.job['error'] = 'coordinator connection lost'
        report = job_diagnostics(self.job)
        self.assertFalse(report['simulator']['available'])
        self.assertEqual(report['error'], 'coordinator connection lost')

    def test_missing_log_is_explicit(self):
        self.assertIn('unavailable', job_diagnostics(self.job)['simulator']['reason'])

    def test_unrelated_request_is_not_used_as_fallback(self):
        self.write([{'request_id': 'b' * 32, 'event': 'pick_and_place_failure', 'error': 'other move'}])
        report = job_diagnostics(self.job)
        self.assertFalse(report['simulator']['available'])
        self.assertEqual(report['simulator']['events'], [])

    def test_log_outside_configured_root_is_not_read(self):
        error = f'failed (sim_request_id={self.request_id}, log={self.root.parent}/mujoco_20261104_120000_1234.jsonl)'
        self.job['result']['command_status']['error'] = error
        self.assertIn('outside', job_diagnostics(self.job)['simulator']['reason'])

    def test_malformed_and_partial_lines_do_not_hide_valid_failure(self):
        self.write([{'request_id': self.request_id, 'event': 'pick_and_place_failure', 'error': 'specific failure'}])
        with self.log.open('a') as handle:
            handle.write('not JSON\n{"partial":')
        self.assertTrue(job_diagnostics(self.job)['simulator']['available'])

    def test_excerpt_is_bounded_and_truncation_is_reported(self):
        self.write([{'request_id': self.request_id, 'event': 'progress', 'message': str(i)} for i in range(MAX_EVENTS + 10)])
        report = job_diagnostics(self.job)['simulator']
        self.assertTrue(report['truncated'])
        self.assertEqual(len(report['events']), MAX_EVENTS)
        self.assertEqual(report['events'][-1]['message'], str(MAX_EVENTS + 9))

    def test_scan_limit_still_keeps_recent_failure(self):
        self.log.write_text('x' * 256 + '\n' + json.dumps({
            'request_id': self.request_id, 'event': 'pick_and_place_failure', 'error': 'recent failure',
        }) + '\n')
        with patch('lemma_gateway.diagnostics.MAX_SCAN_BYTES', 220):
            report = job_diagnostics(self.job)['simulator']
        self.assertTrue(report['available'])
        self.assertTrue(report['truncated'])

    def test_endpoint_and_console_diagnostics_share_job_authentication(self):
        self.write([{'request_id': self.request_id, 'event': 'pick_and_place_failure', 'traceback': 'specific stack'}])
        controller = StubController()
        def failure(parsed, cancel_event):
            raise GatewayError(self.job['error'], result=self.job['result'])
        controller.execute_mutation = failure
        with patch.dict('os.environ', {'CLOUDLABS_LEMMA_API_TOKEN': 'test-token'}), TestClient(create_app(controller)) as api:
            headers = {'Authorization': 'Bearer test-token'}
            accepted = api.post('/v1/console', json={'command': self.job['command']}, headers=headers).json()
            job_id = accepted['job']['job_id']
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = api.get('/v1/jobs/' + job_id, headers=headers).json()
                if job['status'] == 'failed':
                    break
                time.sleep(.001)
            url = job['diagnostics_url']
            self.assertEqual(api.get(url).status_code, 401)
            report = job['diagnostics']
            self.assertEqual(report['error'], job['error'])
            self.assertEqual(report['simulator']['events'][0]['traceback'], 'specific stack')
            self.log.unlink()  # The automatic snapshot must outlive its log file.
            self.assertEqual(api.get('/v1/jobs/' + job_id, headers=headers).json()['diagnostics'], report)
            self.assertEqual(api.get(url, headers=headers).json(), report)
            console = api.post('/v1/console', json={'command': 'diagnostics ' + job_id}, headers=headers).json()
            self.assertEqual(console['result'], report)
            self.assertEqual(api.get('/v1/jobs/missing/diagnostics', headers=headers).status_code, 404)

    def test_console_parser_recognizes_diagnostics(self):
        parsed = parse_console_line('diagnostics job_test')
        self.assertEqual(parsed.kind, 'job_diagnostics')
        self.assertEqual(parsed.arguments['job_id'], 'job_test')

    def wait_for_job(self, api, accepted):
        job_id = accepted.json()['job']['job_id']
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = api.get('/v1/jobs/' + job_id).json()
            if job['status'] in {'failed', 'cancelled', 'succeeded'}:
                return job
            time.sleep(.001)
        self.fail('Job did not finish')

    def test_immediate_console_rejections_include_diagnostics(self):
        with TestClient(create_app(StubController())) as api:
            for body, code in (({'command': 'move tag_22 x 1 0'}, 400), ({'command': 'unknownverb'}, 400), ({}, 422)):
                with self.subTest(body=body):
                    response = api.post('/v1/console', json=body)
                    self.assertEqual(response.status_code, code)
                    report = response.json()['diagnostics']
                    self.assertEqual(report['status'], 'failed')
                    self.assertTrue(report['error'])
                    self.assertFalse(report['simulator']['available'])
                    self.assertIsNone(report['job_id'])

    def test_backend_refusal_and_unexpected_exception_include_original_error(self):
        for error in (GatewayError('coordinator refused move', status_code=409), RuntimeError('gateway crashed')):
            with self.subTest(error=error):
                controller = StubController()
                def failure(parsed, cancel_event):
                    raise error
                controller.execute_mutation = failure
                with TestClient(create_app(controller)) as api:
                    job = self.wait_for_job(api, api.post('/v1/console', json={'command': self.job['command']}))
                    self.assertEqual(job['status'], 'failed')
                    self.assertIn(str(error), job['error'])
                    self.assertEqual(job['diagnostics']['error'], job['error'])
                    self.assertFalse(job['diagnostics']['simulator']['available'])

    def test_missing_log_keeps_failure_and_includes_unavailability_reason(self):
        controller = StubController()
        def failure(parsed, cancel_event):
            raise GatewayError(self.job['error'], result=self.job['result'])
        controller.execute_mutation = failure
        with TestClient(create_app(controller)) as api:
            job = self.wait_for_job(api, api.post('/v1/console', json={'command': self.job['command']}))
            self.assertEqual(job['error'], self.job['error'])
            self.assertIn('unavailable', job['diagnostics']['simulator']['reason'])

    def test_diagnostic_reader_failure_never_hides_command_error(self):
        controller = StubController()
        def failure(parsed, cancel_event):
            raise GatewayError('original command failure')
        controller.execute_mutation = failure
        with patch('lemma_gateway.diagnostics.job_diagnostics', side_effect=RuntimeError('reader broke')), TestClient(create_app(controller)) as api:
            job = self.wait_for_job(api, api.post('/v1/console', json={'command': self.job['command']}))
            self.assertEqual(job['status'], 'failed')
            self.assertEqual(job['error'], 'original command failure')
            self.assertEqual(job['diagnostics']['error'], job['error'])
            self.assertIn('could not be collected', job['diagnostics']['simulator']['reason'])

    def test_failed_status_is_published_only_with_diagnostics(self):
        from lemma_gateway.diagnostics import capture_job_diagnostics
        entered, release = threading.Event(), threading.Event()
        controller = StubController()
        def failure(parsed, cancel_event):
            raise GatewayError('original failure')
        def collect(job):
            entered.set()
            if not release.wait(2):
                raise AssertionError('Test did not release diagnostic reader')
            return capture_job_diagnostics(job)
        controller.execute_mutation = failure
        with patch('lemma_gateway.app.capture_job_diagnostics', side_effect=collect), TestClient(create_app(controller)) as api:
            accepted = api.post('/v1/console', json={'command': self.job['command']})
            try:
                self.assertTrue(entered.wait(1))
                job_id = accepted.json()['job']['job_id']
                pending = api.get('/v1/jobs/' + job_id).json()
                self.assertEqual(pending['status'], 'running')
                self.assertIsNone(pending['diagnostics'])
            finally:
                release.set()
            job = self.wait_for_job(api, accepted)
            self.assertEqual(job['status'], 'failed')
            self.assertEqual(job['diagnostics']['error'], 'original failure')

    def test_successful_job_has_no_failure_diagnostics(self):
        with TestClient(create_app(StubController())) as api:
            job = self.wait_for_job(api, api.post('/v1/console', json={'command': self.job['command']}))
            self.assertEqual(job['status'], 'succeeded')
            self.assertIsNone(job['diagnostics'])
