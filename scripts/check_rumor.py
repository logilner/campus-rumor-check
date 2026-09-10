#!/usr/bin/env python3
"""Check a rumor from the command line, with optional Claude web-search verification.

    python scripts/check_rumor.py "I heard there's a lockdown at Abel Hall"
    python scripts/check_rumor.py            # then type/paste the rumor
    python scripts/check_rumor.py --no-ai "..."   # heuristic + search links only

Heuristic matching always runs (against data/sources.json). If the 'anthropic'
package and credentials are available and --no-ai is not set, Claude also searches
the verified sources and returns a status. Every run is appended to data/checks.json
and shows up in the app's "Recent checks" view.

Credentials: set ANTHROPIC_API_KEY, or run `ant auth login` first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _common import STATUS_LABEL, assess, load_json, log_check

SYSTEM = """You verify campus rumors for students at {school}.
You can search the web, restricted to official university channels and established \
local news outlets. Decide whether the SPECIFIC claim is substantiated right now.

Return ONLY a JSON object, no prose:
{{"status": "confirmed_official" | "reported_news" | "no_match",
  "summary": "1-2 neutral, non-alarmist sentences describing what the sources say",
  "official_statement_found": true | false,
  "sources": [{{"title": "...", "url": "...", "type": "official" | "news"}}]}}

Rules:
- "confirmed_official" only if an official university or police source addresses this
  specific claim.
- "reported_news" if only local news covers it (no official confirmation).
- "no_match" if neither addresses it. Do not speculate or fill gaps.
- For safety claims with status "no_match", state in the summary that the absence of
  an official alert is not confirmation that an area is safe.
- Keep the summary factual and attributed; no editorializing."""


def ai_verify(claim: str, category: str, config: dict) -> dict | None:
    """Return an AI verdict dict, or None if the SDK/credentials are unavailable."""
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
    except Exception:  # noqa: BLE001
        return None

    domains = [s["domain"] for s in config.get("verifiedSources", []) if s.get("domain")]
    model = config.get("model", "claude-opus-5")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SYSTEM.format(school=config.get("school", "the university")),
            tools=[{
                "type": "web_search_20260209",
                "name": "web_search",
                "allowed_domains": domains,
                "max_uses": 6,
            }],
            messages=[{
                "role": "user",
                "content": f"Claim: {claim}\nCategory: {category}\n"
                           f"Search the verified sources and classify per the schema.",
            }],
        )
    except Exception as e:  # noqa: BLE001 - network/model errors shouldn't crash the check
        return {"status": "error", "summary": f"AI check failed: {e}", "sources": []}

    found: list[dict] = []
    text_parts: list[str] = []
    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for r in content:
                    url = getattr(r, "url", None)
                    if url:
                        found.append({"title": getattr(r, "title", url), "url": url})

    text = "".join(text_parts).strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    m0, m1 = text.find("{"), text.rfind("}")
    verdict = {}
    if m0 != -1 and m1 != -1:
        try:
            verdict = json.loads(text[m0:m1 + 1])
        except json.JSONDecodeError:
            pass
    if not verdict:
        return {"status": "unclear", "summary": text[:500] or "No parseable verdict.",
                "sources": found[:8]}
    if not verdict.get("sources"):
        verdict["sources"] = found[:8]
    return verdict


def render(result: dict) -> str:
    h = result["heuristic"]
    lines = [
        f'Claim:    {result["claim"]}',
        f'Category: {result["category"]}' + ("  [SAFETY]" if result["isSafety"] else ""),
        "",
        f'Heuristic (cached sources): {STATUS_LABEL.get(h["status"], h["status"])}',
    ]
    for kind, rows in (("official", h["officialMatches"]), ("news", h["newsMatches"])):
        for r in rows:
            lines.append(f'  [{kind}] {r["title"]}  {r["url"]}')
    if result.get("ai"):
        ai = result["ai"]
        lines += ["", f'AI web check: {STATUS_LABEL.get(ai.get("status"), ai.get("status"))}',
                  f'  {ai.get("summary", "")}']
        for s in ai.get("sources", [])[:8]:
            lines.append(f'  - {s.get("title", "")}  {s.get("url", "")}')
    if result["isSafety"]:
        lines += ["", "SAFETY NOTE: this tool is not a substitute for official channels.",
                  "  emergency.unl.edu | UNL Alert texts/email | call 911 for anything active.",
                  "  No alert != confirmed safe."]
    lines += ["", "Search these verified sources yourself:"]
    for s in result["searches"]:
        lines.append(f'  {s["label"]}: {s["url"]}')
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*", help="the rumor text")
    ap.add_argument("--no-ai", action="store_true", help="skip the Claude web-search check")
    ap.add_argument("--json", action="store_true", help="print the raw result object")
    args = ap.parse_args()

    raw = " ".join(args.text).strip() or input("Paste the rumor:\n> ").strip()
    if not raw:
        print("nothing to check")
        return 1

    config = load_json("config.json")
    sources = load_json("sources.json")
    result = assess(raw, config, sources)

    if not args.no_ai:
        verdict = ai_verify(result["claim"], result["category"], config)
        result["ai"] = verdict
        if verdict is None:
            print("(AI check skipped: install 'anthropic' and set credentials to enable it)\n",
                  file=sys.stderr)

    log_check(result)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
