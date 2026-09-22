"""Vercel serverless function for POST /api/check -- same logic as
server.py's _handle_check, ported to Vercel's Python runtime (a BaseHTTPRequestHandler
subclass named `handler` per file, one file per route under api/).

Vercel's filesystem is read-only at runtime: data/config.json and data/sources.json
are read fine (bundled with the deployment via vercel.json's includeFiles), but
log_check()'s write is expected to fail here and is swallowed the same way
server.py already swallows it -- the check still succeeds, it just is not logged.
Keeping data/sources.json current is handled out of band, by
.github/workflows/refresh-sources.yml committing to the repo on a schedule
(which triggers a fresh Vercel deploy), not by a write at request time.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import assess, load_json, log_check  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
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
            log_check(result)  # best-effort; no-ops on Vercel's read-only filesystem
        except Exception:  # noqa: BLE001
            pass

        self._send_json(result)
