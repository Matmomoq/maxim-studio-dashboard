import re
import ssl
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dashboard.login_security import LoginLimiter
from dashboard.call_recordings_demo import CallRecordingDemo, RecordingUnavailable
from db_security import VerifiedConnection, connect, tls_context
import test_dashboard


class LoginSecurityTests(unittest.TestCase):
    setUp = test_dashboard.ApplicationSmokeTests.setUp
    tearDown = test_dashboard.ApplicationSmokeTests.tearDown
    login = test_dashboard.ApplicationSmokeTests.login

    def test_limit_survives_cookie_reset_and_allows_other_ip(self):
        for _ in range(20):
            self.client = self.app.test_client()
            self.assertEqual(self.login('wrong').status_code, 401)
        blocked = self.login()
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers['Retry-After']), 0)
        self.client.environ_base['REMOTE_ADDR'] = '192.0.2.12'
        self.assertEqual(self.login().status_code, 302)

    def test_unicode_password_is_rejected_without_server_error(self):
        self.assertEqual(self.login('пароль').status_code, 401)

    def test_csrf_still_required(self):
        self.assertEqual(self.client.post('/login', data={'username':'test-user','password':'test-password-123'}).status_code,400)

    def test_csp_nonce_matches_each_page_and_changes(self):
        self.assertEqual(self.login().status_code, 302)
        previous = None
        for path in ('/', '/verification', '/expenses', '/budgets', '/calls', '/call-recordings-demo', '/funnel', '/yclients', '/plans', '/yclients/plan-fact'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            policy = response.headers['Content-Security-Policy']
            script_policy = next(x for x in policy.split(';') if 'script-src' in x)
            self.assertNotIn('unsafe-inline', script_policy)
            nonce = re.search(r"'nonce-([^']+)'", script_policy)[1]
            scripts = re.findall(r'<script\b([^>]*)>', response.text)
            self.assertTrue(scripts, path)
            self.assertTrue(all('nonce="'+nonce+'"' in attrs for attrs in scripts),path)
            self.assertNotEqual(nonce, previous)
            previous=nonce


class LimiterTests(unittest.TestCase):
    def test_atomic_shared_counts_and_expiry(self):
        with TemporaryDirectory() as folder:
            path=str(Path(folder)/'limits.sqlite3')
            clock=[1000]
            limiter=LoginLimiter(path,'test',clock=lambda:clock[0])
            # Initialize schema once; independent instances simulate Passenger workers.
            self.assertEqual(limiter.attempt('initial','initial'),0)
            def attempt(_):
                return LoginLimiter(path,'test',clock=lambda:clock[0]).attempt('ip','user')
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(attempt,range(30)))
            self.assertEqual(results.count(0),20)
            clock[0]+=301
            self.assertEqual(limiter.attempt('ip','user'),0)

    def test_account_limit_applies_across_addresses(self):
        with TemporaryDirectory() as folder:
            limiter=LoginLimiter(str(Path(folder)/'limits.sqlite3'),'test')
            for i in range(60):
                self.assertEqual(limiter.attempt(str(i),'user'),0)
            self.assertGreater(limiter.attempt('new-ip','user'),0)


class RecordingSecurityTests(unittest.TestCase):
    def demo(self,responses):
        demo=CallRecordingDemo.__new__(CallRecordingDemo)
        demo.recording_url=lambda _: 'https://media.comagic.ru/start'
        demo._http_get=Mock(side_effect=responses)
        return demo

    def test_external_redirect_is_not_requested(self):
        response=Mock(status_code=302,headers={'Location':'http://127.0.0.1/private'})
        demo=self.demo([response])
        with self.assertRaises(RecordingUnavailable):demo.open_audio(1)
        self.assertEqual(demo._http_get.call_count,1)
        self.assertFalse(demo._http_get.call_args.kwargs['allow_redirects'])
        response.close.assert_called_once()

    def test_safe_relative_redirect_and_range_work(self):
        redirect=Mock(status_code=302,headers={'Location':'/record.mp3'})
        audio=Mock(status_code=206,url='https://media.comagic.ru/record.mp3')
        demo=self.demo([redirect,audio])
        self.assertIs(demo.open_audio(1,'bytes=0-100'),audio)
        self.assertEqual(demo._http_get.call_args.args[0],audio.url)
        self.assertEqual(demo._http_get.call_args.kwargs['headers'],{'Range':'bytes=0-100'})

    def test_redirect_loop_is_bounded(self):
        response=Mock(status_code=302,headers={'Location':'/start'})
        demo=self.demo([response]*4)
        with self.assertRaises(RecordingUnavailable):demo.open_audio(1)
        self.assertEqual(demo._http_get.call_count,4)


class DatabaseSecurityTests(unittest.TestCase):
    def test_context_validates_chain_and_hostname(self):
        for mode in ('PREFERRED','REQUIRED','VERIFY_IDENTITY'):
            context=tls_context({'AMO_DB_SSL_MODE':mode})
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)

    def test_server_without_tls_is_rejected_before_authentication(self):
        conn=VerifiedConnection.__new__(VerifiedConnection)
        conn.server_capabilities=0
        with patch('pymysql.connections.Connection._request_authentication') as auth:
            with self.assertRaises(Exception):conn._request_authentication()
            auth.assert_not_called()

    def test_unverified_context_is_rejected(self):
        context=ssl._create_unverified_context()
        with self.assertRaises(ValueError):connect(ssl=context)
