"""Small stdlib host. Contains no catalog, bookings, accounts or design templates."""
import argparse
import io
import json
import os
import re
from pathlib import Path
try:
    import resource
except ImportError:
    resource = None
import runpy
import sys
import socket
import socketserver
from urllib.parse import unquote
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8099)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--version', default='export')
    parser.add_argument('--project', default='export')
    parser.add_argument('--parent', default='')
    parser.add_argument('--socket', default='')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    database = root / 'data' / 'data.sqlite3'
    database.parent.mkdir(exist_ok=True)
    os.environ['LAB_DATABASE'] = str(database)
    # The worker inherits only the lab's minimal environment. OS confinement is
    # applied by the launcher before Python starts, not by model-written code.
    if resource is not None:
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    scope = runpy.run_path(str(root / 'app.py'))
    app = scope['application']
    allowed_host = f'{args.host}:{args.port}'
    origin = f'http://{allowed_host}'

    def application(environ, start_response):
        def respond(status, body, content_type='application/json'):
            start_response(status, [('Content-Type', content_type), ('Cache-Control', 'no-store')])
            return [body]
        if environ.get('HTTP_HOST') != allowed_host:
            return respond('403 Forbidden', b'{"error":"Host rejected"}')
        method = environ['REQUEST_METHOD']
        if method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
            return respond('405 Method Not Allowed', b'{}')
        if method not in {'GET', 'HEAD', 'OPTIONS'} and environ.get('HTTP_ORIGIN') != origin:
            return respond('403 Forbidden', b'{"error":"Origin rejected"}')
        path = environ['PATH_INFO']
        if path == '/__lab_health':
            return respond('200 OK', json.dumps({'pid': os.getpid(), 'version_id': args.version, 'project_id': args.project}).encode())
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return respond('400 Bad Request', b'{}')
        if not 0 <= length <= 1_000_000:
            return respond('413 Payload Too Large', b'{}')
        environ['wsgi.input'] = io.BytesIO(environ['wsgi.input'].read(length))
        # Parent app cookies and HTTP auth never reach the generated application.
        environ['HTTP_COOKIE'] = '; '.join(item.strip() for item in environ.get('HTTP_COOKIE', '').split(';') if item.strip().startswith('lab_'))
        environ.pop('HTTP_AUTHORIZATION', None)
        def secured_start(status, headers, exc_info=None):
            safe = []
            for key, value in headers:
                name = key.casefold()
                if '\r' in value or '\n' in value:
                    continue
                if name == 'set-cookie':
                    if not value.startswith('lab_') or 'domain=' in value.casefold():
                        continue
                    value += '; SameSite=Lax; HttpOnly'
                if name not in {'content-security-policy', 'access-control-allow-origin', 'access-control-allow-credentials', 'server', 'location'}:
                    safe.append((key, value))
                elif name == 'location' and value.startswith('/') and not value.startswith('//'):
                    safe.append((key, value))
            safe.extend([('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors " + (args.parent or "'none'")),
                ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store')])
            return start_response(status, safe, exc_info)
        if method in {'GET', 'HEAD'} and (path in {'/', '/index.html'} or path.startswith('/assets/')):
            relative = 'index.html' if path in {'/', '/index.html'} else unquote(path.lstrip('/'))
            target = (root / relative).resolve()
            if not target.is_relative_to(root) or (relative != 'index.html' and not re.fullmatch(r'assets/[a-zA-Z0-9_-]+\.(css|js)', relative)) or not target.is_file():
                return respond('404 Not Found', b'{}')
            content_type = 'text/html; charset=utf-8' if target.suffix == '.html' else 'text/css' if target.suffix == '.css' else 'text/javascript'
            secured_start('200 OK', [('Content-Type', content_type)])
            return [] if method == 'HEAD' else [target.read_bytes()]
        try:
            # Materialize within the error boundary, and cap the response size.
            captured = []
            def capture(status, headers, exc_info=None):
                captured[:] = [status, headers, exc_info]
                return lambda body: None
            chunks, total = [], 0
            response = app(environ, capture)
            try:
                for chunk in response:
                    if not isinstance(chunk, bytes):
                        raise ValueError('Invalid response chunk')
                    total += len(chunk)
                    if total > 4_000_000:
                        raise ValueError('Response too large')
                    chunks.append(chunk)
                output = b''.join(chunks)
            finally:
                if hasattr(response, 'close'):
                    response.close()
            if len(output) > 4_000_000 or not captured:
                raise ValueError('Invalid response')
            secured_start(*captured)
            return [] if method == 'HEAD' else [output]
        except Exception:
            return respond('500 Internal Server Error', b'{"error":"Application error"}')

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass
    class UnixServer(WSGIServer):
        address_family = socket.AF_UNIX

        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = args.host, args.port
            self.setup_environ()

        def get_request(self):
            connection, _ = self.socket.accept()
            return connection, ('127.0.0.1', 0)

    if args.socket:
        server = UnixServer(args.socket, QuietHandler)
        server.set_app(application)
        os.chmod(args.socket, 0o600)
    else:
        server = make_server('127.0.0.1', args.port, application, handler_class=QuietHandler)
    with server:
        print(f'Listening on {origin}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
