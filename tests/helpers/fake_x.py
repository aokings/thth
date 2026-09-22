"""偽の X（loopback HTTP）。**本物の api.x.com は 1 度も叩かない。**

`initialize` → `append`（multipart・`segment_index`）→ `finalize` → STATUS →
`metadata` → `POST /2/tweets` の順序と本体をそのまま記録して、試験が wire を
見られるようにする。応答は `state` の欄で差し替える。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import urllib.parse


def parse_multipart(raw, content_type):
    """`segment_index` と `media` の中身だけを取り出す（試験用の最小の実装）。"""
    boundary = content_type.partition('boundary=')[2].encode('ascii')
    fields = {}
    for part in raw.split(b'--' + boundary):
        head, separator, body = part.partition(b'\r\n\r\n')
        if not separator:
            continue
        name = re.search(rb'name="([^"]+)"', head)
        if not name:
            continue
        fields[name.group(1).decode()] = body[:-2] if body.endswith(b'\r\n') else body
    return fields


def new_state():
    return {'calls': [], 'uploads': {}, 'segments': {}, 'status': {}, 'next_media': 100,
            'next_post': '1900000000000000001', 'reply': {}, 'on_tweet': None,
            'on_append': None, 'on_status': None, 'metadata_status': {}, 'tweets': [],
            'me': {'data': {'id': '123', 'username': 'demo'}}, 'posts': None}


class _Handler(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def reply(self, code, body):
        raw = b'' if body is None else json.dumps(body).encode()
        self.send_response(code)
        for key, value in self.state['reply'].get(self.path, {}).items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(length) if length else b''

    def do_GET(self):
        path, _, query = self.path.partition('?')
        values = urllib.parse.parse_qs(query)
        self.state['calls'].append(('GET', path, values))
        if path == '/2/media/upload':
            identifier = values.get('media_id', [''])[0]
            if self.state['on_status']:
                self.state['on_status']()
            queue = self.state['status'].get(identifier) or []
            info = queue.pop(0) if queue else {'state': 'succeeded'}
            if isinstance(info, tuple):
                return self.reply(info[0], info[1])
            return self.reply(200, {'data': {'id': identifier, 'processing_info': info}})
        if path == '/2/users/me':
            return self.reply(200, self.state['me'])
        if re.fullmatch(r'/2/users/[0-9]+/tweets', path):
            return self.reply(200, self.state['posts'] if self.state['posts'] is not None
                              else {'data': [], 'meta': {'result_count': 0}})
        return self.reply(404, {})

    def do_DELETE(self):
        self.state['calls'].append(('DELETE', self.path, {}))
        return self.reply(200, {'data': {'deleted': True}})

    def do_POST(self):
        raw = self._body()
        content = self.headers.get('Content-Type') or ''
        if content.startswith('multipart/form-data'):
            fields = parse_multipart(raw, content)
            payload = fields.get('media', b'')
            body = {'segment_index': fields.get('segment_index', b'').decode(),
                    'bytes': len(payload)}
            identifier = self.path.split('/')[-2]
            self.state['segments'].setdefault(identifier, []).append(body['segment_index'])
            self.state['uploads'][identifier] = self.state['uploads'].get(identifier, b'') + payload
            self.state['calls'].append(('POST', self.path, body))
            if self.state['on_append']:
                self.state['on_append']()
            return self.reply(200, {'data': {'id': identifier, 'expires_after_secs': 3600}})
        body = json.loads(raw) if raw else {}
        self.state['calls'].append(('POST', self.path, body))
        if self.path == '/2/media/upload/initialize':
            self.state['next_media'] += 1
            identifier = str(self.state['next_media'])
            return self.reply(200, {'data': {'id': identifier, 'expires_after_secs': 3600}})
        if self.path.endswith('/finalize'):
            identifier = self.path.split('/')[-2]
            queue = self.state['status'].get(identifier)
            info = {'state': 'pending', 'check_after_secs': 0} if queue else None
            data = {'id': identifier}
            if info is not None:
                data['processing_info'] = info
            return self.reply(200, {'data': data})
        if self.path == '/2/media/metadata':
            status = self.state['metadata_status'].get(body.get('id'), 200)
            return self.reply(status, {} if status == 200 else {'title': 'refused'})
        if self.path == '/2/tweets':
            if self.state['on_tweet']:
                self.state['on_tweet']()
            self.state['tweets'].append(body)
            return self.reply(201, {'data': {'id': self.state['next_post'], 'text': body.get('text', '')}})
        return self.reply(404, {})


def serve(monkeypatch=None):
    """試験のための server を 1 本立てて `(state, url, shutdown)` を返す。"""
    state = new_state()
    handler = type('Handler', (_Handler,), {'state': state})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)

    def shutdown():
        server.shutdown()
        server.server_close()
        thread.join(3)
    return state, url, shutdown


def calls(state, method, suffix):
    return [row for row in state['calls'] if row[0] == method and row[1].endswith(suffix)]
