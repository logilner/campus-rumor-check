#!/usr/bin/env python3
"""Campus Rumor Check — local server.

    python server.py            # http://localhost:8000
    python server.py 8080

Serves the static app and two JSON endpoints:
  POST /api/check    {"text": "...", "useAI": true}  -> assessment (+ Claude web
                     check if 'anthropic' and credentials are available)
  POST /api/refresh                                   -> re-run source fetch + crime-log scrape

Works with no API key (heuristic matching + verified-source search links). With
credentials, the check also runs a Claude web search across the verified domains.
"""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from _common import assess, load_json, log_check  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_POST(self):
        if self.path.rstrip("/") == "/api/check":
            return self._handle_check()
        if self.path.rstrip("/") == "/api/refresh":
            return self._handle_refresh()
        self._send_json({"error": "unknown endpoint"}, 404)

    def _handle_check(self):
        payload = self._read_json()
        text = (payload.get("text") or "").strip()
        if not text:
            return self._send_json({"error": "empty text"}, 400)
        config = load_json("config.json")
        sources = load_json("sources.json")
        result = assess(text, config, sources)

        if payload.get("useAI", True):
            try:
                from check_rumor import ai_verify
                result["ai"] = ai_verify(result["claim"], result["category"], config)
            except Exception as e:  # noqa: BLE001
                result["ai"] = {"status": "error", "summary": f"AI check failed: {e}", "sources": []}
        try:
            log_check(result)
        except Exception:  # noqa: BLE001 - logging must never break a check
            pass
        self._send_json(result)

    def _handle_refresh(self):
        out = {}
        for modname in ("fetch_sources", "scrape_crimelog"):
            try:
                mod = __import__(modname)
                rc = mod.main([])
                out[modname] = "ok" if rc in (0, None) else f"exit {rc}"
            except SystemExit as e:
                out[modname] = "ok" if e.code in (0, None) else f"exit {e.code}"
            except Exception as e:  # noqa: BLE001
                out[modname] = f"error: {e}"
        self._send_json(out)


def main() -> int:
    with ThreadingHTTPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/"
        print(f"Campus Rumor Check on {url}  (Ctrl+C to stop)")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
