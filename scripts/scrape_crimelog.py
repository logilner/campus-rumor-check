#!/usr/bin/env python3
"""Best-effort scrape of the UNL Daily Crime & Fire Log into data/crimelog_raw.json.

    python scripts/scrape_crimelog.py
    python scripts/scrape_crimelog.py --url "<a 60-day archive URL you found>"

The log lives in an ASP.NET page (scsapps.unl.edu/policereports/MainPage.aspx).
The most recent entries are server-rendered into the landing HTML, so this parses
those. It does NOT drive the calendar/archive postbacks — run it on a schedule to
accumulate history, and promote anything notable into data/incidents.json by hand.
Output rows are unreviewed and the parser is heuristic; if UNL changes the page
markup this may capture partial or messy text. The 'raw' field keeps the original
chunk so nothing is silently dropped.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request

from _common import load_json, now_iso, save_json

URL = "https://scsapps.unl.edu/policereports/MainPage.aspx"
UA = "Mozilla/5.0 (compatible; CampusRumorCheck/1.0; local research tool)"

CASE_RE = re.compile(r"(?:case\s*#?\s*)?\b(\d{8,9})\b", re.I)
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
DISPO_RE = re.compile(r"\b(open|closed|cleared|pending|inactive|unfounded|referred|exceptional)\b", re.I)


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"[ \t]+", " ", text)


def parse_rows(text: str) -> list[dict]:
    # Anchor on case numbers; take the text window that follows each one.
    anchors = list(CASE_RE.finditer(text))
    rows = []
    for i, m in enumerate(anchors):
        start = m.start()
        end = anchors[i + 1].start() if i + 1 < len(anchors) else min(len(text), start + 400)
        chunk = re.sub(r"\s+", " ", text[start:end]).strip()
        if len(chunk) < 12:
            continue
        dates = DATE_RE.findall(chunk)
        dispo = DISPO_RE.search(chunk)
        rows.append({
            "case": m.group(1),
            "dates": dates[:4],
            "disposition": dispo.group(1).lower() if dispo else None,
            "raw": chunk[:400],
        })
    # de-dupe by case number, keep the longest raw
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["case"])
        if cur is None or len(r["raw"]) > len(cur["raw"]):
            best[r["case"]] = r
    return list(best.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        text = fetch_text(args.url)
    except Exception as e:  # noqa: BLE001
        print(f"fetch failed: {e}", file=sys.stderr)
        return 1

    new_rows = parse_rows(text)
    if not new_rows:
        print("No case-numbered rows found. The page markup may have changed — "
              "open the URL and check, or pass --url with an archive link.")
    store = load_json("crimelog_raw.json")
    existing = {r["case"]: r for r in store.get("rows", [])}
    for r in new_rows:
        existing[r["case"]] = r
    merged = sorted(existing.values(), key=lambda r: r["case"], reverse=True)

    print(f"parsed {len(new_rows)} row(s) this run; {len(merged)} total in crimelog_raw.json")
    for r in new_rows[:10]:
        print(f"  {r['case']}  {' · '.join(r['dates'][:2]):26s} {r['disposition'] or '':12s} {r['raw'][:80]}")

    if args.dry_run:
        print("--dry-run: not writing.")
        return 0
    store["rows"] = merged
    store["scrapedAt"] = now_iso()
    save_json("crimelog_raw.json", store)
    print("wrote data/crimelog_raw.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
