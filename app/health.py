import time
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

last_activity = time.time()


def heartbeat():
    global last_activity
    last_activity = time.time()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        age = time.time() - last_activity
        if age > 600:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Recorder stalled")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def start_health_server():
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info("Health server started")
