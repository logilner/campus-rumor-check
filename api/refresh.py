"""POST /api/refresh is a local-dev-only feature (server.py re-runs
scripts/fetch_sources.py and scrape_crimelog.py and writes the results).
That can't work on Vercel -- the filesystem is read-only at runtime -- so this
returns a clear explanation instead of a bare 404. The real replacement is
.github/workflows/refresh-sources.yml, which refreshes data on a schedule and
commits it, triggering a fresh deploy.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.dumps({
            "error": "not available on this deployment",
            "why": ("Vercel's filesystem is read-only at request time, so this endpoint can't "
                    "write data/sources.json or data/crimelog_raw.json here. Source data is "
                    "refreshed on a schedule by .github/workflows/refresh-sources.yml instead, "
                    "which commits the results to the repo and triggers a new deploy."),
        }).encode("utf-8")
        self.send_response(501)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
