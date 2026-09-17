# Campus Rumor Check (UNL)

Paste a rumor you saw about the University of Nebraska–Lincoln. The tool extracts
the core claim, categorizes it, and checks it against **verified sources** —
official university channels (`emergency.unl.edu`, Nebraska Today, UNL Police, the
UNL Daily Crime & Fire Log) and established local news (Daily Nebraskan, 1011
News/KOLN, Lincoln Journal Star) — then returns a status:

- **Confirmed by an official source**
- **Being reported by local news** (no official statement matched)
- **No matching official statement or news coverage found**

For **safety** rumors (shooter, gun, lockdown, threat…), the official emergency
links are pushed to the top of the result, right under the status, with a
standing reminder that *no alert is not an all-clear*.

There's also a **Past incidents** timeline (curated + best-effort UNL Police
crime-log scrape) and a **Recent checks** log.

Crime-log rows aren't just displayed — `scripts/scrape_crimelog.py`'s output
(`data/crimelog_raw.json`) is also matched against your claim like any other
source, so a claim can come back "confirmed by an official source" off a raw
police-log line, not just news/official-feed items.

## Run it

```bash
python server.py
```

Opens <http://localhost:8000/>. The page needs the server — it calls `/api/check`.

Works with **no API key**: claim extraction, matching against the cached source
feed, and verified-source search links. Add an Anthropic key to also run a live
**AI web check** (Claude searches the verified domains and classifies):

```bash
pip install anthropic
# then set ANTHROPIC_API_KEY, or run: ant auth login
```

The AI check is on by default when available; the checkbox on the form turns it
off per check.

## Keep the data fresh

| Script | Does | Writes |
| --- | --- | --- |
| `scripts/fetch_sources.py` | Pulls recent items per verified domain (Google News RSS, key-free) + a snapshot of `emergency.unl.edu`. Daily Nebraskan uses its own site RSS instead (every article, not just what Google indexed); Nebraska Today's own feed supplements Google with same-day items | `data/sources.json` |
| `scripts/scrape_crimelog.py` | Parse of the UNL Daily Crime & Fire Log landing page; `--backfill` also walks the calendar archive | `data/crimelog_raw.json` (feeds both the Past-incidents panel and rumor matching) |
| `scripts/check_rumor.py "..."` | Run a check from the terminal (heuristic + optional AI) | appends `data/checks.json` |

```bash
python scripts/fetch_sources.py --days 45
python scripts/scrape_crimelog.py --backfill   # walks the crime log's calendar archive
```

`POST /api/refresh` (or a button-free `curl -X POST localhost:8000/api/refresh`)
runs both fetchers with their default settings. Each result shows the cache age,
so a stale run is visible, not silent.

### Automated refresh (GitHub Actions)

`.github/workflows/refresh-sources.yml` runs both scripts on a schedule (every 6
hours by default) on GitHub's own servers and commits the results — no need to
run anything by hand, or have your machine on. It also has a manual "Run
workflow" button on the repo's Actions tab. Both scripts are standard-library
only, so the workflow needs no dependencies.

This doubles as the data-refresh story for a Vercel deploy: Vercel's filesystem
is read-only at runtime, so instead of writing `data/*.json` from a live
request, this workflow refreshes and commits it on a schedule, and a Vercel
project connected to the repo auto-redeploys on every push — no database
needed for the read side. (It doesn't cover `data/checks.json`, which is
written by live `/api/check` calls — that part still needs real storage if it
should persist on Vercel.)

Since the bot pushes to `main` on its own, get in the habit of `git pull`
before you push local changes, so you don't hit a rejected push.

## Data files

| File | Contents |
| --- | --- |
| `data/config.json` | School, verified-source list, safety keywords, emergency panel text, model |
| `data/sources.json` | Cached verified-source items the checker matches against (seeded) |
| `data/incidents.json` | Curated timeline of notable campus-safety incidents (seeded) |
| `data/crimelog_raw.json` | Unreviewed crime-log rows from the scraper — promote notable ones into `incidents.json` |
| `data/checks.json` | Log of past checks |

## Design choices / limits

- **"No match" is not "safe."** Official alerts can lag a real event, and the
  tool only sees what has been published to the sources above. In a possible
  emergency: `emergency.unl.edu`, your UNL Alert texts/email, and 911.
- **Verified-source allowlist.** The AI check passes `allowed_domains` so Claude
  can only cite the sources in `config.json`. Widen or narrow that list there.
- **Heuristic claim extraction.** The result shows "we read your rumor as …" —
  if it misread, rephrase and re-run.
- **Crime-log scrape is best-effort.** The log is an ASP.NET page; the default
  run only parses the server-rendered landing entries, but `--backfill` drives
  the calendar's day-by-day archive directly (the currently-visible month, not
  the site's full 60-day window). Rows are leads, not confirmed facts, even when
  the checker surfaces one as a "confirmed" match — the title/summary are
  auto-extracted from raw log text, not written up like the curated incidents.
- **Not affiliated** with the University of Nebraska–Lincoln or any news outlet.
- Some seeded incident details are marked *unverified* — confirm against the
  linked sources before relying on them.
