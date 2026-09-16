#!/usr/bin/env python3
"""Refresh data/sources.json: recent items from the verified sources plus a
snapshot of the emergency.unl.edu active-incident page. Standard library only.

    python scripts/fetch_sources.py
    python scripts/fetch_sources.py --days 45

Items are pulled from Google News RSS scoped to each verified domain (robust and
key-free), except the Daily Nebraskan, which has its own site RSS (TNCMS) that
returns every published article instead of whatever Google happened to index --
used directly for that domain. Nebraska Today's own feed is too shallow (fixed
~10 items, no pagination) to replace Google outright, so it's added on top as a
same-day supplement instead, de-duped by title. All items are de-duplicated by
URL (or, for the Nebraska Today supplement, by normalized title, since a native
link and a Google-redirect link for the same story never match as strings).
Domains are tagged official/news from data/config.json. Seed items already in
the file are kept.

UNL Police and Lincoln Journal Star/1011 KOLN stay on Google News RSS only --
UNL Police (Drupal) has no feed at all, and Journal Star/KOLN's own feeds are
firehoses covering their whole coverage area (obituaries, wire news) with no
working way to scope them to campus content, so Google's keyword-scoped search
is the better fit for those two.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from _common import load_json, now_iso, save_json

UA = "Mozilla/5.0 (compatible; CampusRumorCheck/1.0; local research tool)"


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def google_news_rss(query: str) -> bytes:
    return _get(
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    )


def daily_nebraskan_rss(limit: int = 100) -> bytes:
    """The Daily Nebraskan runs on TNCMS (Townnews), which exposes its own search
    RSS feed of every published article -- not just what Google News happens to
    have indexed. Dropping the site's default '#topstory' keyword filter gets
    everything, newest first."""
    return _get(
        "https://www.dailynebraskan.com/search/?"
        + urllib.parse.urlencode({"f": "rss", "t": "article", "l": limit, "s": "start_time", "sd": "desc"})
    )


def nebraska_today_rss() -> bytes:
    """Nebraska Today's own feed. Unlike the Daily Nebraskan's, this one has no
    working pagination (always ~10 items, a few days deep) so it's used only as
    a same-day supplement to the Google News pull below, not a replacement."""
    return _get("https://news.unl.edu/feed/")


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def parse_rss(xml_bytes: bytes, cutoff: datetime, domain: str, src_type: str, src_name: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for item in root.iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            dt = None
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            continue
        src_el = item.find("source")
        srcname = (src_el.text.strip() if src_el is not None and src_el.text else src_name)
        headline = title
        if srcname and title.endswith(f" - {srcname}"):
            headline = title[: -(len(srcname) + 3)]
        out.append({
            "id": f"gn-{abs(hash(link)) % (10**12)}",
            "type": src_type,
            "source": srcname,
            "domain": domain,
            "date": dt.date().isoformat(),
            "title": headline,
            "url": link,
            "summary": "",
        })
    return out


def parse_native_rss(xml_bytes: bytes, cutoff: datetime, domain: str, src_type: str, src_name: str,
                      id_prefix: str = "dn") -> list[dict]:
    """Parse a plain RSS 2.0 feed straight from the outlet's own site (real
    article links, no Google redirect, no aggregator title-mangling)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for item in root.iterfind(".//item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            dt = None
        if dt is None or not link:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            continue
        summary = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        summary = re.sub(r"\s+", " ", summary).strip()[:400]
        out.append({
            "id": f"{id_prefix}-{abs(hash(link)) % (10**12)}",
            "type": src_type,
            "source": src_name,
            "domain": domain,
            "date": dt.date().isoformat(),
            "title": title,
            "url": link,
            "summary": summary,
        })
    return out


def fetch_active_incident_page() -> dict:
    url = "https://emergency.unl.edu/"
    try:
        raw = _get(url).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"url": url, "checkedAt": now_iso(), "text": f"Could not fetch ({e})."}
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    text = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", text)  # collapse "Visit Visit Visit" nav chrome
    text = re.sub(r"\s+", " ", text).strip()
    cues = ("active incident", "no active", "no known", "all clear", "shelter in place",
            "advisory", "alert", "ongoing", "updated", "last update", "incident")
    low = text.lower()
    hit = min((low.find(c) for c in cues if c in low), default=-1)
    if hit >= 0:
        snippet = text[max(0, hit - 120): hit + 480]
        note = ""
    else:
        snippet = text[:400]
        note = " (no active-incident language detected — page may show only the standard emergency-info content)"
    return {"url": url, "checkedAt": now_iso(),
            "text": (snippet or "(page returned no readable text)") + note}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    config = load_json("config.json")
    store = load_json("sources.json")
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    kept = [it for it in store.get("items", []) if not str(it.get("id", "")).startswith(("gn-", "dn-", "nt-"))]
    seen = {it.get("url") for it in kept if it.get("url")}

    fetched: list[dict] = []
    for src in config["verifiedSources"]:
        dom = src["domain"]
        if dom == "unlalert.unl.edu":
            continue  # signup portal, nothing to index
        try:
            if dom == "dailynebraskan.com":
                # Native site RSS: every published article, not just what Google
                # News happened to index for this domain.
                items = parse_native_rss(daily_nebraskan_rss(), cutoff, dom, src["type"], src["name"])
            else:
                query = f'site:{dom} (UNL OR "University of Nebraska" OR campus OR police OR alert OR safety)'
                items = parse_rss(google_news_rss(query), cutoff, dom, src["type"], src["name"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! {src['name']}: {e}", file=sys.stderr)
            continue
        new = [it for it in items if it["url"] and it["url"] not in seen]
        for it in new:
            seen.add(it["url"])
        fetched.extend(new)
        print(f"  {src['name']:32s} +{len(new)}")

    # Nebraska Today also runs its own feed (news.unl.edu/feed/), but it's a
    # fixed ~10 items with no working pagination -- too shallow to replace the
    # Google News pull above, so it's only used to catch same-day items Google
    # hasn't indexed yet. De-duped by normalized title (not just URL), since the
    # native link and the Google-redirect link for the same story never match.
    nt_src = next((s for s in config["verifiedSources"] if s["domain"] == "news.unl.edu"), None)
    if nt_src:
        try:
            nt_items = parse_native_rss(nebraska_today_rss(), cutoff, nt_src["domain"],
                                         nt_src["type"], nt_src["name"], id_prefix="nt")
        except Exception as e:  # noqa: BLE001
            print(f"  ! Nebraska Today (native feed): {e}", file=sys.stderr)
            nt_items = []
        known_titles = {_norm_title(x["title"]) for x in kept + fetched}
        new_nt = [it for it in nt_items
                  if it["url"] not in seen and _norm_title(it["title"]) not in known_titles]
        for it in new_nt:
            seen.add(it["url"])
        fetched.extend(new_nt)
        print(f"  Nebraska Today (native feed, same-day supplement) +{len(new_nt)}")

    store["items"] = sorted(kept + fetched, key=lambda x: x.get("date", ""), reverse=True)
    store["fetchedAt"] = now_iso()
    print("  emergency.unl.edu ... fetching page snapshot")
    store["activeIncidentPage"] = fetch_active_incident_page()

    n_off = sum(1 for x in store["items"] if x.get("type") == "official")
    n_news = sum(1 for x in store["items"] if x.get("type") == "news")
    print(f"\nsources.json: {n_off} official + {n_news} news = {len(store['items'])} items")

    if args.dry_run:
        print("--dry-run: not writing.")
        return 0
    save_json("sources.json", store)
    print("wrote data/sources.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
