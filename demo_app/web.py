"""Demo web app for Auto-Remediate.

Usage:
  python -m demo_app.web
"""
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import importlib

import demo_app.tasks
from demo_app.trigger import ORDER_NULL_EMAIL


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Dynamically reload tasks module so patch changes are reflected immediately
        importlib.reload(demo_app.tasks)

        # Run task directly:
        # Before patch: raises actual unhandled AttributeError (server 500 error)
        # After patch: succeeds cleanly
        demo_app.tasks.send_receipt.run(ORDER_NULL_EMAIL)

        # On success, render ONLY 'success'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>success</title>
  <style>
    body {
      background: #0f172a;
      color: #34d399;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 4.5rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: 0;
    }
  </style>
</head>
<body>
  success
</body>
</html>"""
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run_server(host: str = "0.0.0.0", port: int = 5000):
    server = ReusableHTTPServer((host, port), DemoHandler)
    print(f"Demo Web App running on http://localhost:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo web server.")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo Web App for Auto-Remediate")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
