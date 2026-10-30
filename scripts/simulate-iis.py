"""Stand-in for the IIS /peoplemodelling application, so the sub-path deployment can be
verified before anything is changed on the shared server.

Reproduces exactly what the web.config that setup-hana-path.ps1 writes does:
  /peoplemodelling        -> 302 to /peoplemodelling/   (the add-slash rule)
  /peoplemodelling/{rest} -> proxied to 127.0.0.1:8000/{rest}   (prefix STRIPPED)
  anything else           -> 404 (the shared site would send it elsewhere)
"""
import http.client
import http.server

PREFIX = "/peoplemodelling"
BACKEND_HOST, BACKEND_PORT = "127.0.0.1", 8000
LISTEN_PORT = 8099


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"  proxy: {fmt % args}", flush=True)

    def _handle(self, method):
        path = self.path
        # Drain the request body FIRST, always. On a keep-alive connection an unread
        # body stays in the socket and is parsed as the start of the next request --
        # which showed up as a bogus 501 "Unsupported method" and made a real finding
        # look like a harness failure.
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        if path == PREFIX:
            self.send_response(302)
            self.send_header("Location", PREFIX + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not path.startswith(PREFIX + "/"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        upstream_path = path[len(PREFIX):] or "/"

        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=30)
        headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        headers["Host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"
        # IIS adds this; the app does not need it, but the real thing sends it.
        headers["X-Original-URL"] = path
        try:
            conn.request(method, upstream_path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
        except Exception as exc:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            print(f"  proxy: 502 {exc}", flush=True)
            return
        finally:
            conn.close()

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


if __name__ == "__main__":
    print(f"IIS stand-in on http://127.0.0.1:{LISTEN_PORT}{PREFIX}/ -> {BACKEND_HOST}:{BACKEND_PORT}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler).serve_forever()
