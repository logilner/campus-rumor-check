#!/usr/bin/env python3
"""Best-effort scrape of the UNL Daily Crime & Fire Log into data/crimelog_raw.json.

    python scripts/scrape_crimelog.py                 # just the landing page (fast, ~1 request)
    python scripts/scrape_crimelog.py --backfill       # also walk the calendar archive (slower)
    python scripts/scrape_crimelog.py --backfill --max-days 20 --delay 1

The log lives in an ASP.NET page (scsapps.unl.edu/policereports/MainPage.aspx).
By default this only reads whatever is server-rendered into the landing page —
in practice just the most recent day or two of entries, since UNLPD's default
view is not a rolling window. Those entries scroll off within about a week, so
running this on its own only ever "builds up" one or two new cases at a time,
as new incidents get logged.

The page also has a calendar widget covering a ~60-day archive, where each day
is its own entry behind a classic ASP.NET postback (not a plain link) — pass
--backfill to drive that directly: it submits a form POST per visible calendar
day and merges whatever each one returns. This walks the currently-displayed
month (all cells the site itself renders, roughly the last 4-6 weeks), not the
full 60 days — the site paginates further history behind a "previous month"
postback this script does not follow.

Output rows are unreviewed and the parser is heuristic; if UNL changes the page
markup this may capture partial or messy text. The 'raw' field keeps the original
chunk so nothing is silently dropped. Promote anything notable into
data/incidents.json by hand.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from _common import load_json, now_iso, save_json

URL = "https://scsapps.unl.edu/policereports/MainPage.aspx"
UA = "Mozilla/5.0 (compatible; CampusRumorCheck/1.0; local research tool)"

CASE_RE = re.compile(r"(?:case\s*#?\s*)?\b(\d{8,9})\b", re.I)
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
DISPO_RE = re.compile(r"\b(open|closed|cleared|pending|inactive|unfounded|referred|exceptional)\b", re.I)

# Calendar (ASP.NET Calendar server control) markup for the day-selection widget.
_DAY_RE = re.compile(
    r"__doPostBack\('ctl00\$ContentPlaceHolder1\$DateSelection','(\d+)'\)\"[^>]*"
    r'title="([A-Za-z]+) (\d{1,2})">\d{1,2}<'
)
_HEADER_RE = re.compile(r'style="width:70%;">([A-Za-z]+ \d{4})<')
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _strip_tags(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"[ \t]+", " ", text)


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _post(url: str, fields: dict[str, str]) -> str:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch_text(url: str) -> str:
    return _strip_tags(_get(url))


def _dedupe(rows: list[dict]) -> list[dict]:
    # keep the longest 'raw' per case number (most complete capture)
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["case"])
        if cur is None or len(r["raw"]) > len(cur["raw"]):
            best[r["case"]] = r
    return list(best.values())


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
    return _dedupe(rows)


def _hidden_field(raw: str, name: str) -> str:
    m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', raw)
    return html.unescape(m.group(1)) if m else ""


def _calendar_days(raw: str) -> list[tuple[str, str]]:
    """Every calendar day cell on the rendered page, as (postback-arg, iso-date),
    most recent first, excluding future dates."""
    hm = _HEADER_RE.search(raw)
    if not hm:
        return []
    header_month, header_year_s = hm.group(1).rsplit(" ", 1)
    if header_month not in _MONTHS:
        return []
    header_mi, header_year = _MONTHS.index(header_month), int(header_year_s)
    today = datetime.now(timezone.utc).date()

    out = []
    for arg, month_name, day in _DAY_RE.findall(raw):
        if month_name not in _MONTHS:
            continue
        mi = _MONTHS.index(month_name)
        year = header_year
        if header_mi == 0 and mi == 11:
            year -= 1  # header is January, this cell is December of the prior year
        elif header_mi == 11 and mi == 0:
            year += 1  # header is December, this cell is January of the next year
        try:
            d = date(year, mi + 1, int(day))
        except ValueError:
            continue
        if d > today:
            continue  # no incidents to find in the future
        out.append((arg, d.isoformat()))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def backfill(url: str, max_days: int, delay: float) -> list[dict]:
    """Walk the calendar's day-selection postback for every visible day (most
    recent first, capped at max_days), submitting each as its own form POST and
    parsing the returned page. Returns the combined, de-duped rows."""
    raw = _get(url)
    hidden = {name: _hidden_field(raw, name)
              for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")}
    if not hidden["__VIEWSTATE"]:
        print("backfill: could not find __VIEWSTATE on the page — markup may have changed.",
              file=sys.stderr)
        return []

    days = _calendar_days(raw)[:max_days]
    print(f"backfill: walking {len(days)} calendar day(s)...")
    all_rows: list[dict] = []
    for n, (arg, iso_date) in enumerate(days, 1):
        fields = {
            "__EVENTTARGET": "ctl00$ContentPlaceHolder1$DateSelection",
            "__EVENTARGUMENT": arg,
            **hidden,
        }
        try:
            resp = _post(url, fields)
        except Exception as e:  # noqa: BLE001 - one bad day shouldn't kill the run
            print(f"  [{n}/{len(days)}] {iso_date}: request failed ({e}) — skipped", file=sys.stderr)
            continue
        rows = parse_rows(_strip_tags(resp))
        print(f"  [{n}/{len(days)}] {iso_date}: {len(rows)} row(s)")
        all_rows.extend(rows)
        if delay:
            time.sleep(delay)
    return all_rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--backfill", action="store_true",
                     help="also walk the calendar's day-by-day archive, not just the landing "
                          "page (dozens of requests; this is how you build up history)")
    ap.add_argument("--max-days", type=int, default=45,
                     help="cap on how many calendar days to walk with --backfill (default 45)")
    ap.add_argument("--delay", type=float, default=0.5,
                     help="seconds to wait between requests during --backfill (default 0.5)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        landing_rows = parse_rows(fetch_text(args.url))
    except Exception as e:  # noqa: BLE001
        print(f"fetch failed: {e}", file=sys.stderr)
        return 1

    new_rows = landing_rows
    if args.backfill:
        try:
            new_rows = _dedupe(landing_rows + backfill(args.url, args.max_days, args.delay))
        except Exception as e:  # noqa: BLE001
            print(f"backfill failed, keeping landing-page rows only: {e}", file=sys.stderr)

    if not new_rows:
        print("No case-numbered rows found. The page markup may have changed — "
              "open the URL and check, or try --backfill.")

    store = load_json("crimelog_raw.json")
    existing = {r["case"]: r for r in store.get("rows", [])}
    for r in new_rows:
        existing[r["case"]] = r
    merged = sorted(existing.values(), key=lambda r: r["case"], reverse=True)

    print(f"parsed {len(new_rows)} row(s) this run; {len(merged)} total in crimelog_raw.json")
    for r in new_rows[:10]:
        print(f"  {r['case']}  {' · '.join(r['dates'][:2]):26s} {r['disposition'] or '':12s} {r['raw'][:80]}")
    if len(new_rows) > 10:
        print(f"  ... and {len(new_rows) - 10} more")

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
