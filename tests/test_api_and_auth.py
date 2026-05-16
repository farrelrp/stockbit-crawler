"""API and auto-auth regression tests for new task controls and token resume flow."""
import unittest
from unittest.mock import MagicMock, patch

import app as app_module
from auto_auth import AutoAuth


class TestJobsApi(unittest.TestCase):
    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def test_task_action_endpoint_calls_manager(self):
        with patch.object(app_module.job_manager, 'task_action', return_value=True) as task_action:
            response = self.client.post(
                '/api/jobs/j1/tasks/action',
                json={
                    'ticker': 'BBCA',
                    'date': '2024-01-02',
                    'action': 'retry_after',
                    'delay_seconds': 30,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'success': True})
        task_action.assert_called_once_with('j1', 'BBCA', '2024-01-02', 'retry_after', 30.0)

    def test_credentials_endpoints(self):
        with patch.object(app_module.credentials_manager, 'set_credentials', return_value={'success': True}), \
             patch.object(app_module.credentials_manager, 'get_status', return_value={'has_credentials': True, 'email': 'ab***@x.com'}):
            response = self.client.post('/api/credentials', json={'email': 'a@x.com', 'password': 'pw'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

        with patch.object(app_module.credentials_manager, 'clear_credentials', return_value={'success': True}):
            response = self.client.delete('/api/credentials')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'success': True})

    def test_job_defaults_round_trip(self):
        defaults = {'delay': 5, 'limit': 60, 'pause_on_rate_limit': True}
        with patch.object(app_module.settings_store, 'set_job_defaults', return_value={'success': True}), \
             patch.object(app_module.settings_store, 'get_job_defaults', return_value=defaults):
            response = self.client.post('/api/settings/job-defaults', json=defaults)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['defaults']['pause_on_rate_limit'])


class TestAutoAuthResume(unittest.TestCase):
    def test_success_callback_resumes_jobs(self):
        token_manager = MagicMock()
        token_manager.set_token.return_value = {'success': True, 'expires_at': '2026-01-01T00:00:00'}
        credentials_manager = MagicMock()
        auto_auth = AutoAuth(
            token_manager,
            credentials_manager=credentials_manager,
            on_login_success=lambda: 3,
        )
        auto_auth._solve_recaptcha = MagicMock(return_value='captcha')
        auto_auth._post_login = MagicMock(return_value={'data': {'access_token': 'x' * 60}})
        auto_auth._session_cookies = 'cookie=1'

        auto_auth._do_login('user@example.com', 'secret', save_credentials=True)

        self.assertEqual(auto_auth.get_status()['status'], 'success')
        self.assertEqual(auto_auth.get_status()['result']['resumed_jobs'], 3)
        credentials_manager.set_credentials.assert_called_once_with('user@example.com', 'secret')
