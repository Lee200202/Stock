"""v73 LINE Webhook：驗簽、成功寫入 Cloud Tasks 才回 200、工作重試、圖文選單。"""
import importlib.util
import io
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('line_webhook_main', os.path.join(ROOT, 'line-webhook', 'main.py'))
lw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lw)

SECRET = '0123456789abcdef0123456789abcdef'
GAS = 'https://script.google.com/macros/s/AKfycbTESTTESTTEST/exec'


class _Res:
    def __init__(self, text):
        self.text = text.encode('utf-8')

    def read(self, n=-1):
        return self.text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append({'url': req.full_url, 'data': req.data, 'method': req.get_method(), 'headers': dict(req.headers)})
        r = self.replies.pop(0) if self.replies else {'ok': True}
        if isinstance(r, Exception):
            raise r
        return _Res(json.dumps(r) if isinstance(r, dict) else r)


class FakeRelay(lw.Relay):
    def __init__(self, secret=SECRET, gas=GAS):
        super().__init__(secret, gas, opener=FakeOpener([]), queue=FakeQueue(), task_verifier=lambda token: token == 'valid-task')
        self.submitted = []
        self.sig_counts = 0

    def enqueue(self, body_text, sig, meta):
        self.submitted.append((body_text, sig, meta))

    def count_sig_fail(self):
        self.sig_counts += 1


class FakeQueue:
    service_url = 'https://relay.example'
    service_account = 'tasks@example.iam.gserviceaccount.com'

    def __init__(self):
        self.tasks = []
        self.error = None

    def ready(self):
        return True

    def enqueue(self, envelope):
        if self.error:
            raise self.error
        self.tasks.append(envelope)
        return 'task-1'


def call(app, method, path, body=b'', headers=None):
    env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
    for k, v in (headers or {}).items():
        env['HTTP_' + k.upper().replace('-', '_')] = v
    out = {}

    def start(status, hdrs):
        out['status'] = int(status.split()[0])
        out['headers'] = dict(hdrs)
    out['body'] = b''.join(app(env, start))
    return out


class SignatureTest(unittest.TestCase):
    def test_same_constant_as_gas_test(self):
        # tests/test_line_v73_gas.js 用同一組固定值核對 Apps Script 那一端
        self.assertEqual(lw.signature(b'{"destination":"U0","events":[]}', 'test-secret-0000'),
                         '88t9n7lM1rSAZdyd8H/IaeJjTn6YXVIOF4vlXUdexq8=')
        body = json.dumps({'destination': 'U0', 'events': [{'type': 'message', 'message': {'type': 'text', 'text': '台積電 2330'}}]},
                          ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertEqual(lw.signature(body, 'test-secret-0000'), 'Iu8xT8wmtjA5NAmU8qXIM96awNAFAisJbnLjYiTnHQY=')

    def test_verify(self):
        body = b'{"events":[]}'
        self.assertTrue(lw.verify(body, lw.signature(body, SECRET), SECRET))
        self.assertTrue(lw.verify(body, ' ' + lw.signature(body, SECRET) + ' ', SECRET))
        self.assertFalse(lw.verify(body + b' ', lw.signature(body, SECRET), SECRET))
        self.assertFalse(lw.verify(body, '', SECRET))
        self.assertFalse(lw.verify(body, lw.signature(body, SECRET), ''))


class CallbackTest(unittest.TestCase):
    def setUp(self):
        self.relay = FakeRelay()
        self.app = lw.make_app(self.relay)

    def post(self, payload, sig=None, raw=None):
        body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return call(self.app, 'POST', '/callback', body, {'X-Line-Signature': sig if sig is not None else lw.signature(body, SECRET)})

    def test_valid_events_are_forwarded_verbatim_and_answered_immediately(self):
        payload = {'destination': 'U' + 'b' * 32, 'events': [
            {'type': 'message', 'webhookEventId': '01ABC', 'message': {'type': 'text', 'text': '台積電最近講什麼'},
             'source': {'type': 'user', 'userId': 'U' + 'a' * 32}, 'deliveryContext': {'isRedelivery': True}}]}
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        r = self.post(None, raw=body)
        self.assertEqual(r['status'], 200)
        self.assertEqual(len(self.relay.submitted), 1)
        text, sig, meta = self.relay.submitted[0]
        self.assertEqual(text.encode('utf-8'), body, '原始 body 一個位元組都不能變（網站要再驗一次簽章）')
        self.assertEqual(sig, lw.signature(body, SECRET))
        self.assertEqual(meta['types'], ['message'])
        self.assertEqual(meta['ids'], ['01ABC'])
        self.assertTrue(meta['redelivery'])
        self.assertNotIn('Uaaaa', json.dumps(meta), '記錄不寫 userId')
        self.assertNotIn('台積電', json.dumps(meta), '記錄不寫訊息內容')

    def test_verify_button_empty_events(self):
        r = self.post({'destination': 'U0', 'events': []})
        self.assertEqual(r['status'], 200)
        self.assertEqual(self.relay.submitted, [])

    def test_enqueue_failure_does_not_acknowledge_webhook(self):
        self.relay.enqueue = lambda *a: (_ for _ in ()).throw(OSError('queue unavailable'))
        r = self.post({'events': [{'type': 'follow', 'webhookEventId': 'e1'}]})
        self.assertEqual(r['status'], 503)

    def test_bad_signature_is_rejected_and_counted(self):
        r = self.post({'events': [{'type': 'follow'}]}, sig='AAAA')
        self.assertEqual(r['status'], 401)
        self.assertEqual(self.relay.submitted, [])
        self.assertEqual(self.relay.sig_counts, 1)

    def test_other_rejections(self):
        self.assertEqual(call(self.app, 'GET', '/callback')['status'], 405)
        self.assertEqual(call(self.app, 'POST', '/nope', b'{}')['status'], 404)
        self.assertEqual(self.post(None, raw=b'')['status'], 400)
        self.assertEqual(self.post(None, raw=b'not json')['status'], 400)
        self.assertEqual(self.post(None, raw=b'[1,2]')['status'], 200)   # 驗簽通過但不是物件：不轉送
        self.assertEqual(self.relay.submitted, [])
        big = b'{"events":[]}' + b' ' * (lw.MAX_BODY + 1)
        self.assertEqual(call(self.app, 'POST', '/callback', big, {'X-Line-Signature': lw.signature(big, SECRET)})['status'], 413)

    def test_not_configured(self):
        app = lw.make_app(FakeRelay(secret='', gas=GAS))
        self.assertEqual(call(app, 'POST', '/callback', b'{}', {'X-Line-Signature': 'x'})['status'], 503)
        app = lw.make_app(FakeRelay(secret=SECRET, gas='http://insecure/exec'))
        self.assertEqual(call(app, 'POST', '/callback', b'{}', {'X-Line-Signature': 'x'})['status'], 503)

    def test_healthz_does_not_leak_secrets(self):
        r = call(self.app, 'GET', '/healthz')
        self.assertEqual(r['status'], 200)
        data = json.loads(r['body'])
        self.assertEqual(data, {'ok': True, 'build': lw.BUILD, 'configured': True})
        self.assertNotIn(SECRET, r['body'].decode())

    def test_static_rich_menu_images(self):
        for name in ('richmenu-query.png', 'richmenu-notify.png'):
            r = call(self.app, 'GET', '/static/' + name)
            self.assertEqual(r['status'], 200)
            self.assertEqual(r['headers']['Content-Type'], 'image/png')
            self.assertTrue(r['body'].startswith(b'\x89PNG'))
            self.assertLess(len(r['body']), 1024 * 1024, 'LINE 圖文選單圖片上限 1MB')
        self.assertEqual(call(self.app, 'GET', '/static/../main.py')['status'], 404)
        self.assertEqual(call(self.app, 'GET', '/static/other.png')['status'], 404)

    def test_rich_menu_image_size_matches_menu_definition(self):
        with open(os.path.join(ROOT, 'line-webhook', 'static', 'richmenu-query.png'), 'rb') as fh:
            head = fh.read(24)
        width, height = int.from_bytes(head[16:20], 'big'), int.from_bytes(head[20:24], 'big')
        self.assertEqual((width, height), (2500, 1686), '與 Line.gs 的 lineRichMenuDefs_ 同尺寸')


class ForwardTest(unittest.TestCase):
    def relay(self, replies):
        r = lw.Relay(SECRET, GAS, opener=FakeOpener(replies), queue=FakeQueue(), task_verifier=lambda token: token == 'valid-task')
        return r

    def test_envelope_and_target(self):
        r = self.relay([{'ok': True, 'handled': 1}])
        text = '{"destination":"U0","events":[{"type":"follow"}]}'
        self.assertTrue(r.forward_once({'v': 1, 'kind': 'webhook', 'sig': 'SIG', 'body': text}, {'events': 1}))
        call0 = r.opener.calls[0]
        self.assertEqual(call0['url'], GAS + '?action=line')
        self.assertEqual(call0['method'], 'POST')
        env = json.loads(call0['data'].decode('utf-8'))
        self.assertEqual(env, {'v': 1, 'kind': 'webhook', 'sig': 'SIG', 'body': text})

    def test_network_error_returns_retryable_to_cloud_tasks(self):
        r = self.relay([OSError('timeout')])
        self.assertFalse(r.forward_once({'body': '{}'}, {}))
        self.assertEqual(len(r.opener.calls), 1)

    def test_recovers_after_temporary_error_page(self):
        r = self.relay(['<html>Google Apps Script error</html>', {'ok': True}])
        self.assertFalse(r.forward_once({'body': '{}'}, {}))
        self.assertTrue(r.forward_once({'body': '{}'}, {}))
        self.assertEqual(len(r.opener.calls), 2)

    def test_final_rejections_are_not_retried(self):
        for err in ('signature', 'destination-mismatch'):
            r = self.relay([{'ok': False, 'error': err}])
            self.assertTrue(r.forward_once({'body': '{}'}, {}))
            self.assertEqual(len(r.opener.calls), 1, err)

    def test_not_configured_is_retryable(self):
        r = self.relay([{'ok': False, 'error': 'not-configured'}])
        self.assertFalse(r.forward_once({'body': '{}'}, {}))

    def test_task_endpoint_requires_oidc_and_retries_transient_failure(self):
        r = self.relay([OSError('timeout'), {'ok': True, 'handled': 1}])
        app = lw.make_app(r)
        body = b'{"destination":"U0","events":[]}'
        env = {'v': 1, 'kind': 'webhook', 'sig': lw.signature(body, SECRET), 'body': body.decode('utf-8')}
        raw = json.dumps(env).encode('utf-8')
        self.assertEqual(call(app, 'POST', '/tasks/forward', raw)['status'], 401)
        headers = {'Authorization': 'Bearer valid-task'}
        self.assertEqual(call(app, 'POST', '/tasks/forward', raw, headers)['status'], 503)
        self.assertEqual(call(app, 'POST', '/tasks/forward', raw, headers)['status'], 200)

    def test_cloud_tasks_envelope_uses_oidc(self):
        class Client:
            def queue_path(self, *parts):
                return '/'.join(parts)

            def create_task(self, **kwargs):
                self.request = kwargs['request']
                self.timeout = kwargs['timeout']
                return {'name': 'task-1'}

        client = Client()
        q = lw.CloudTasksQueue('project', 'asia-east1', 'line', 'https://relay.example',
                               'task@example.iam.gserviceaccount.com', client)
        q.enqueue({'body': '{}', 'sig': 'x'})
        target = client.request['task']['http_request']
        self.assertEqual(target['url'], 'https://relay.example/tasks/forward')
        self.assertEqual(target['oidc_token']['audience'], 'https://relay.example')
        self.assertEqual(target['oidc_token']['service_account_email'], 'task@example.iam.gserviceaccount.com')
        self.assertLessEqual(client.timeout, 1.5)

    def test_url_with_query(self):
        r = lw.Relay(SECRET, GAS + '?x=1', queue=FakeQueue())
        self.assertEqual(r.target(), GAS + '?x=1&action=line')


if __name__ == '__main__':
    unittest.main()
