#!/usr/bin/env python3
"""Local, read-only information-flow explorer; Python standard library only."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from catalog import chapters, lesson, LESSONS

HERE = Path(__file__).resolve().parent
TRACES = HERE / 'traces'
ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/style.css': ('style.css', 'text/css')}


def read_trace(chapter):
    if chapter not in LESSONS:
        raise ValueError('unknown chapter')
    paths = sorted(TRACES.glob(chapter + '-*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not paths:
        return {'name': None, 'events': []}
    # An in-flight final line may be incomplete; completed lines remain readable.
    events = []
    with paths[0].open(encoding='utf-8') as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {'name': paths[0].name, 'events': events}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Reject foreign Host values (including DNS rebinding hostnames).
        if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            self.send_error(403)
            return
        url = urlparse(self.path)
        try:
            if url.path in ASSETS:
                name, mime = ASSETS[url.path]
                body = (HERE / 'static' / name).read_bytes()
            elif url.path == '/api/chapters':
                body, mime = json.dumps(chapters(), ensure_ascii=False).encode(), 'application/json'
            elif url.path in ('/api/lesson', '/api/trace'):
                chapter = parse_qs(url.query).get('chapter', ['s01'])[0]
                data = lesson(chapter) if url.path == '/api/lesson' else read_trace(chapter)
                body, mime = json.dumps(data, ensure_ascii=False).encode(), 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, StopIteration):
            self.send_error(400, 'Invalid chapter')
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Agent 信息流实验室 → http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
