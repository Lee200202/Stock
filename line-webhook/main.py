"""LINE Webhook 轉送服務（v73，2026/09/27）。

為什麼需要它
  LINE 平台要求 Webhook 在 2 秒內回 2xx，並用 x-line-signature 標頭簽名。
  Apps Script 的 doPost 讀不到請求標頭（拿不到簽章），回應也常常超過 2 秒，
  所以不能把網站直接當 LINE 的 Webhook。

這個服務只做三件事
  1. POST /callback：用頻道密鑰驗 x-line-signature（原始 body 的 HMAC-SHA256，base64），不符回 401。
     驗過且成功寫入 Cloud Tasks 後回 200；工作由 Cloud Tasks 把「原始 body＋簽章」
     原封不動 POST 到網站 /exec?action=line，失敗時由工作佇列續送。
     網站用同一把密鑰再驗一次、依 webhookEventId 去重後處理（訂閱開關、查詢、回覆）。
  2. GET /static/richmenu-*.png：圖文選單圖片，後台「建立圖文選單」時由網站來取。
  3. GET /healthz：版本與是否設定完成（不回任何密鑰）。

不存使用者資料；記錄只寫事件數、類型與 webhookEventId，不寫 userId 或訊息內容。
不能只靠回 200 後的行程內執行緒：Cloud Run 仍可能縮容，LINE 此時不會重送。
Cloud Tasks 寫入失敗則回 503，讓 LINE 的 webhook redelivery 接手；網站暫時錯誤由 Cloud Tasks 重試。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

BUILD = '2026-09-27-line-v73'
MAX_BODY = 1024 * 1024
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
STATIC_FILES = {'richmenu-query.png', 'richmenu-notify.png'}
FINAL_ERRORS = {'signature', 'destination-mismatch', 'bad-envelope', 'bad-body'}


def signature(body: bytes, secret: str) -> str:
    """LINE 的簽章算法：base64(HMAC-SHA256(頻道密鑰, 原始 body))。"""
    return base64.b64encode(hmac.new(secret.encode('utf-8'), body, hashlib.sha256).digest()).decode('ascii')


def verify(body: bytes, sig: str, secret: str) -> bool:
    if not secret or not sig:
        return False
    return hmac.compare_digest(signature(body, secret), sig.strip())


def log(severity: str, message: str, **fields) -> None:
    """Cloud Logging 讀得懂的一行 JSON。不寫 userId、訊息內容或任何密鑰。"""
    print(json.dumps(dict(severity=severity, message=message, **fields), ensure_ascii=False), flush=True)


class CloudTasksQueue:
    """Webhook 的持久交接點；create_task 成功後才能向 LINE 回 200。"""

    def __init__(self, project='', location='', queue='', service_url='', service_account='', client=None):
        self.project = project or os.environ.get('LINE_TASKS_PROJECT', '') or os.environ.get('GOOGLE_CLOUD_PROJECT', '')
        self.location = location or os.environ.get('LINE_TASKS_LOCATION', '')
        self.queue = queue or os.environ.get('LINE_TASKS_QUEUE', '')
        self.service_url = (service_url or os.environ.get('LINE_RELAY_URL', '')).rstrip('/')
        self.service_account = service_account or os.environ.get('LINE_TASK_SERVICE_ACCOUNT', '')
        self.client = client

    def ready(self):
        return all((self.project, self.location, self.queue, self.service_url.startswith('https://'), self.service_account))

    def enqueue(self, envelope):
        if not self.ready():
            raise RuntimeError('Cloud Tasks queue is not configured')
        if self.client is None:
            from google.cloud import tasks_v2
            self.client = tasks_v2.CloudTasksClient()
        parent = self.client.queue_path(self.project, self.location, self.queue)
        task = {'http_request': {
            'http_method': 'POST', 'url': self.service_url + '/tasks/forward',
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
            'oidc_token': {'service_account_email': self.service_account, 'audience': self.service_url}
        }}
        return self.client.create_task(request={'parent': parent, 'task': task}, timeout=1.5)


class Relay:
    def __init__(self, secret: str, gas_url: str, opener=None, queue=None, task_verifier=None):
        self.secret = (secret or '').strip()
        self.gas_url = (gas_url or '').strip()
        self.opener = opener or urllib.request.build_opener()
        self.queue = queue or CloudTasksQueue()
        self.task_verifier = task_verifier
        self.sig_fail = 0
        self.last_report = 0.0

    def ready(self) -> bool:
        return bool(self.secret) and self.gas_url.startswith('https://') and self.queue.ready()

    def target(self) -> str:
        return self.gas_url + ('&' if '?' in self.gas_url else '?') + 'action=line'

    def post(self, envelope: dict) -> dict:
        data = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(self.target(), data=data, method='POST',
                                     headers={'Content-Type': 'application/json; charset=utf-8'})
        # Apps Script 的 POST 會回 302 轉到 script.googleusercontent.com 取結果；urllib 會改用 GET 跟過去。
        with self.opener.open(req, timeout=40) as res:
            text = res.read(20000).decode('utf-8', 'replace')
        try:
            return json.loads(text)
        except ValueError:
            return {'ok': False, 'error': 'non-json', 'sample': text[:120]}

    def enqueue(self, body_text: str, sig: str, meta: dict):
        return self.queue.enqueue({'v': 1, 'kind': 'webhook', 'sig': sig, 'body': body_text})

    def task_authorized(self, header):
        prefix = 'Bearer '
        if not header.startswith(prefix):
            return False
        if self.task_verifier is not None:
            return bool(self.task_verifier(header[len(prefix):]))
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import id_token
            claims = id_token.verify_oauth2_token(header[len(prefix):], Request(), audience=self.queue.service_url)
            return claims.get('email') == self.queue.service_account and claims.get('email_verified') is True
        except Exception:
            return False

    def forward_once(self, envelope: dict, meta: dict):
        try:
            out = self.post(envelope)
        except Exception as exc:
            log('WARNING', 'forward failed; Cloud Tasks will retry', error=str(exc)[:160], **meta)
            return False
        if out.get('ok'):
            log('INFO', 'forwarded', handled=out.get('handled'), **meta)
            return True
        error = str(out.get('error') or '')[:120]
        if error in FINAL_ERRORS:
            log('ERROR', 'permanently rejected by site', error=error, **meta)
            return True
        log('WARNING', 'site not ok; Cloud Tasks will retry', error=error, **meta)
        return False

    def count_sig_fail(self):
        """擋下的偽造請求累計，最多每分鐘回報網站一次（用同一把頻道密鑰簽名）。"""
        self.sig_fail += 1
        due = time.time() - self.last_report >= 60
        if due and self.ready():
            n, self.sig_fail, self.last_report = self.sig_fail, 0, time.time()
            body = json.dumps({'type': 'relay-stats', 'sigFail': n, 'at': int(time.time() * 1000), 'build': BUILD}, separators=(',', ':'))
            try:
                self.queue.enqueue({'v': 1, 'kind': 'stats', 'sig': signature(body.encode('utf-8'), self.secret), 'body': body})
            except Exception as exc:
                log('WARNING', 'stats enqueue failed', error=str(exc)[:120])

def _respond(start_response, status: int, payload=None, body: bytes = None, ctype='application/json; charset=utf-8',
             extra=None):
    reasons = {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized', 404: 'Not Found', 405: 'Method Not Allowed',
               413: 'Payload Too Large', 503: 'Service Unavailable'}
    if body is None:
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode('utf-8')
    headers = [('Content-Type', ctype), ('Content-Length', str(len(body))), ('X-Content-Type-Options', 'nosniff')]
    headers += list(extra or [])
    start_response('%d %s' % (status, reasons.get(status, 'OK')), headers)
    return [body]


def make_app(relay: Relay = None):
    state = {'relay': relay}

    def current() -> Relay:
        if state['relay'] is None:
            state['relay'] = Relay(os.environ.get('LINE_CHANNEL_SECRET', ''), os.environ.get('GAS_WEBAPP_URL', ''))
        return state['relay']

    def app(environ, start_response):
        method = environ.get('REQUEST_METHOD', 'GET')
        path = environ.get('PATH_INFO', '/') or '/'
        if path == '/healthz' and method in ('GET', 'HEAD'):
            return _respond(start_response, 200, {'ok': True, 'build': BUILD, 'configured': current().ready()})
        if path.startswith('/static/') and method in ('GET', 'HEAD'):
            name = path[len('/static/'):]
            if name not in STATIC_FILES:
                return _respond(start_response, 404, {'ok': False})
            try:
                with open(os.path.join(STATIC_DIR, name), 'rb') as fh:
                    data = fh.read()
            except OSError:
                return _respond(start_response, 404, {'ok': False})
            return _respond(start_response, 200, body=data if method == 'GET' else b'', ctype='image/png',
                            extra=[('Cache-Control', 'public, max-age=3600')])
        if path == '/tasks/forward':
            if method != 'POST':
                return _respond(start_response, 405, {'ok': False})
            r = current()
            if not r.ready():
                return _respond(start_response, 503, {'ok': False, 'error': 'not-configured'})
            if not r.task_authorized(environ.get('HTTP_AUTHORIZATION', '')):
                return _respond(start_response, 401, {'ok': False})
            try:
                length = int(environ.get('CONTENT_LENGTH') or 0)
            except ValueError:
                return _respond(start_response, 400, {'ok': False})
            if length <= 0 or length > MAX_BODY:
                return _respond(start_response, 400, {'ok': False})
            try:
                envelope = json.loads(environ['wsgi.input'].read(length))
                raw = envelope['body'].encode('utf-8')
                if not verify(raw, envelope['sig'], r.secret):
                    return _respond(start_response, 400, {'ok': False})
            except (KeyError, TypeError, ValueError, UnicodeError):
                return _respond(start_response, 400, {'ok': False})
            meta = {'task': environ.get('HTTP_X_CLOUDTASKS_TASKNAME', '')[-80:]}
            if r.forward_once(envelope, meta):
                return _respond(start_response, 200, {'ok': True})
            return _respond(start_response, 503, {'ok': False, 'error': 'retry'})
        if path != '/callback':
            return _respond(start_response, 404, {'ok': False})
        if method != 'POST':
            return _respond(start_response, 405, {'ok': False})
        r = current()
        if not r.ready():
            log('ERROR', 'not configured: LINE_CHANNEL_SECRET / GAS_WEBAPP_URL')
            return _respond(start_response, 503, {'ok': False, 'error': 'not-configured'})
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return _respond(start_response, 400, {'ok': False})
        if length <= 0:
            return _respond(start_response, 400, {'ok': False})
        if length > MAX_BODY:
            return _respond(start_response, 413, {'ok': False})
        body = environ['wsgi.input'].read(length)
        sig = environ.get('HTTP_X_LINE_SIGNATURE', '')
        if not verify(body, sig, r.secret):
            r.count_sig_fail()
            log('WARNING', 'signature mismatch', length=length)
            return _respond(start_response, 401, {'ok': False})
        try:
            text = body.decode('utf-8')
            payload = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            return _respond(start_response, 400, {'ok': False})
        events = (payload.get('events') or []) if isinstance(payload, dict) else []
        if not isinstance(events, list):
            events = []
        if events:   # LINE 後台的「驗證」送的是空事件，只要回 200
            meta = {'events': len(events),
                    'types': sorted({str(e.get('type', '')) for e in events if isinstance(e, dict)})[:6],
                    'ids': [str(e.get('webhookEventId', ''))[:40] for e in events if isinstance(e, dict)][:5],
                    'redelivery': any(isinstance(e, dict) and (e.get('deliveryContext') or {}).get('isRedelivery')
                                      for e in events)}
            try:
                r.enqueue(text, sig, meta)
            except Exception as exc:
                log('ERROR', 'Cloud Tasks enqueue failed; ask LINE to redeliver', error=str(exc)[:160], **meta)
                return _respond(start_response, 503, {'ok': False, 'error': 'enqueue-failed'})
        return _respond(start_response, 200, {'ok': True})

    return app


app = make_app()
