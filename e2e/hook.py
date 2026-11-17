"""
A pretend platform webhook endpoint for the e2e stack.

POST /<anything>   records the request; answers with the current status
GET  /received     everything recorded so far (JSON)
POST /control      {"status": 500} — make the next deliveries fail
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECEIVED: list[dict] = []
STATE = {'status': 200}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, payload=b''):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/received':
            self._reply(200, json.dumps(RECEIVED).encode())
        else:
            self._reply(404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/control':
            STATE.update(json.loads(body))
            self._reply(200, b'{}')
            return
        RECEIVED.append({
            'path': self.path,
            'answered': STATE['status'],
            'event_id': self.headers.get('KYC-Event-Id'),
            'signature': self.headers.get('KYC-Signature'),
            'body': body.decode(),
        })
        self._reply(STATE['status'], b'{}')

    def log_message(self, *args):
        pass


ThreadingHTTPServer(('0.0.0.0', 9000), Handler).serve_forever()
