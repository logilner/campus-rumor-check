"""Shared logic for Campus Rumor Check: claim extraction, categorization, and
matching a claim against cached verified-source items. Standard library only.

Used by server.py (the in-app check) and scripts/check_rumor.py (the CLI, which
can also add Claude web-search verification).
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

_HEDGES = re.compile(
    r"^\s*(?:i\s+(?:just\s+)?(?:heard|saw|read)|someone\s+(?:said|told\s+me)|apparently|"
    r"rumor\s+has\s+it|people\s+are\s+saying|is\s+it\s+true\s+that|did\s+you\s+hear|"
    r"word\s+is|supposedly|my\s+friend\s+said|there'?s\s+a\s+rumor|allegedly)\b[\s:,-]*",
    re.I,
)
_STOP = set(
    "a an the this that these those is are was were be been being of to in on at for and or "
    "but if then than so as by with from into near about over under it its they them their "
    "there here we you i he she his her our your has have had will would can could may might "
    "just now today tonight yesterday tomorrow campus unl university nebraska lincoln".split()
)
_STOPWORD_LOCATIONS = {"campus", "unl", "university", "nebraska", "lincoln"}
LOCATION_HINTS = [
    "union", "east campus", "city campus", "abel", "sandoz", "cather", "harper", "schramm",
    "kauffman", "knoll", "selleck", "dairy store", "rec center", "recreation", "memorial stadium",
    "love library", "adele hall", "nebraska union", "east union", "greek", "fraternity",
    "sorority", "the mall", "17th", "vine", "r street", "q street", "holdrege",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def save_json(name: str, obj: dict) -> None:
    (DATA / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_claim(raw: str) -> str:
    """Strip hedging preamble and keep the first sentence or two of the actual claim."""
    text = re.sub(r"\s+", " ", (raw or "").strip())
    prev = None
    while prev != text:  # peel stacked hedges ("I heard someone said ...")
        prev = text
        text = _HEDGES.sub("", text).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    claim = " ".join(parts[:2]).strip() if parts else text
    return claim[:400] or text[:400]


def categorize(claim: str, config: dict) -> tuple[str, bool]:
    low = claim.lower()
    if any(k in low for k in config.get("safetyKeywords", [])):
        return "safety", True
    if re.search(r"\b(fired|resign|resigned|laid off|suspended|placed on leave|tenure)\b", low) and \
       re.search(r"\b(professor|instructor|dean|faculty|staff|coach|chancellor|ta|lecturer)\b", low):
        return "personnel", False
    if re.search(r"\b(police|arrest|arrested|ice|immigration|raid|detain|detained|citation|charged)\b", low):
        return "enforcement", False
    if re.search(r"\b(closed|closing|cancel|cancelled|canceled|shut down|evacuat)\b", low):
        return "operations", False
    return "general", False


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def _location_terms(s: str) -> set[str]:
    low = (s or "").lower()
    return {h for h in LOCATION_HINTS if h in low}


def score_match(claim: str, item: dict) -> int:
    hay = f"{item.get('title', '')} {item.get('summary', '')}"
    shared = _tokens(claim) & _tokens(hay)
    score = len(shared)
    if _location_terms(claim) & _location_terms(hay):
        score += 2
    return score


def _recent(item_date: str, lookback_days: int) -> bool:
    try:
        d = datetime.fromisoformat(item_date[:10]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True  # undated seed items still count
    return (datetime.now(timezone.utc) - d).days <= lookback_days


def generated_searches(claim: str, config: dict) -> list[dict]:
    q = urllib.parse.quote_plus(claim)
    out = [{
        "label": "emergency.unl.edu (active incidents)",
        "url": "https://emergency.unl.edu/",
    }]
    for src in config.get("verifiedSources", []):
        dom = src.get("domain")
        if not dom or dom == "unlalert.unl.edu":
            continue
        out.append({
            "label": f"Search {src['name']}",
            "url": f"https://www.google.com/search?q={q}+site:{dom}",
        })
    return out


def assess(raw_text: str, config: dict, sources: dict) -> dict:
    claim = extract_claim(raw_text)
    category, is_safety = categorize(claim, config)
    lookback = int(config.get("lookbackDays", 30))
    threshold = 2

    scored = []
    for it in sources.get("items", []):
        s = score_match(claim, it)
        if s >= threshold and _recent(it.get("date", ""), lookback if it.get("id", "").startswith("gn-") else 3650):
            scored.append({**it, "_score": s})
    scored.sort(key=lambda x: x["_score"], reverse=True)

    official = [x for x in scored if x.get("type") == "official"]
    news = [x for x in scored if x.get("type") == "news"]
    if official:
        status = "confirmed_official"
    elif news:
        status = "reported_news"
    else:
        status = "no_match"

    latest_alert = None
    off_items = sorted(
        [x for x in sources.get("items", []) if x.get("type") == "official"],
        key=lambda x: x.get("date", ""), reverse=True,
    )
    if off_items:
        a = off_items[0]
        latest_alert = {"title": a.get("title"), "url": a.get("url"), "date": a.get("date")}

    def trim(rows):
        return [{"title": r.get("title"), "url": r.get("url"), "source": r.get("source"),
                 "date": r.get("date"), "score": r.get("_score")} for r in rows[:5]]

    return {
        "claim": claim,
        "category": category,
        "isSafety": is_safety,
        "checkedAt": now_iso(),
        "heuristic": {
            "status": status,
            "officialMatches": trim(official),
            "newsMatches": trim(news),
        },
        "ai": None,
        "searches": generated_searches(claim, config),
        "activeIncidentPage": sources.get("activeIncidentPage"),
        "latestOfficialAlert": latest_alert,
        "sourcesFetchedAt": sources.get("fetchedAt"),
    }


STATUS_LABEL = {
    "confirmed_official": "Confirmed by an official source",
    "reported_news": "Being reported by local news — no official statement matched",
    "no_match": "No matching official statement or news coverage found",
}


def log_check(result: dict, limit: int = 200) -> None:
    store = load_json("checks.json")
    entry = {
        "checkedAt": result.get("checkedAt"),
        "claim": result.get("claim"),
        "category": result.get("category"),
        "isSafety": result.get("isSafety"),
        "heuristicStatus": result.get("heuristic", {}).get("status"),
        "aiStatus": (result.get("ai") or {}).get("status"),
        "aiSummary": (result.get("ai") or {}).get("summary"),
    }
    store["checks"] = ([entry] + store.get("checks", []))[:limit]
    save_json("checks.json", store)
