#!/usr/bin/env python3
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("MOCK_HTMLHUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOCK_HTMLHUB_PORT", "18888"))
ADMIN_TOKEN = os.environ.get("MOCK_HTMLHUB_TOKEN", "mock-admin-token")

PENDING = [
    {
        "slug": "mock-secret-page",
        "page_title": "Mock 审批页面",
        "visitor_id": "mock_visitor_001",
        "visitor_name": "Mock User",
        "provider": "钉钉",
        "requested_at": "2026-04-08T19:00:00+08:00",
    }
]
SUBSCRIBERS = []


def broadcast(event):
    dead = []
    for q in SUBSCRIBERS:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            SUBSCRIBERS.remove(q)
        except ValueError:
            pass


class Handler(BaseHTTPRequestHandler):
    def _auth_ok(self):
        return self.headers.get("Authorization", "") == f"Bearer {ADMIN_TOKEN}"

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if not self._auth_ok() and self.path.startswith("/api/"):
            self._json({"error": "unauthorized"}, 401)
            return

        if self.path.startswith("/api/pending"):
            self._json({"pending": PENDING, "count": len(PENDING)})
            return

        if self.path.startswith("/api/events/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = queue.Queue()
            SUBSCRIBERS.append(q)
            try:
                # startup event after short delay
                startup_event = {
                    "id": f"mock-{int(time.time())}",
                    "type": "access_requested",
                    "page": {"slug": "mock-secret-page-2", "title": "Mock SSE 页面"},
                    "visitor": {"id": "mock_visitor_002", "name": "Mock SSE User", "provider_name": "钉钉"},
                    "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                }
                q.put(startup_event)
                while True:
                    try:
                        event = q.get(timeout=20)
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    SUBSCRIBERS.remove(q)
                except ValueError:
                    pass
            return

        self._json({"ok": True, "paths": ["/api/pending", "/api/events/stream"]})

    def do_POST(self):
        if self.path == "/api/mock/emit":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                event = json.loads(body.decode("utf-8"))
            except Exception:
                self._json({"error": "invalid json"}, 400)
                return
            broadcast(event)
            self._json({"ok": True})
            return
        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Mock HTML Hub server listening on http://{HOST}:{PORT}")
    server.serve_forever()
