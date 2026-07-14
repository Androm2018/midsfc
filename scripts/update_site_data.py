#!/usr/bin/env python3
"""
Auto-update Mid Annandale AFC website data (results, fixtures, league table).

Fetches from SOSL LeagueRepublic using headless Chromium (Playwright) because
the matchHub and standings pages sit behind an AWS WAF JavaScript challenge
that blocks plain HTTP requests. The team/index pages respond to normal
requests, but the pages we need (all matches + standings) require a real
browser.

Rewrites the block between AUTO-DATA-START and AUTO-DATA-END markers in
index.html. If anything looks wrong (no matches parsed, no table rows),
the script exits non-zero WITHOUT touching index.html, so a site outage
or redesign can never wipe the live data.

Run locally:  pip install playwright && playwright install chromium
              python3 scripts/update_site_data.py
"""

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ──────────────────────────────────────────────────────────────────
BASE = "https://sosfl.leaguerepublic.com"
SITE_ID = "373593711"          # SOSL LeagueRepublic site ID
TEAM_ID = "201795845"          # Mid Annandale team ID (2026/27)
FIXTURE_GROUP = "542578549"    # League fixture group (2026/27)

MATCH_HUB_URL = f"{BASE}/matchHub/{SITE_ID}/-1_-1/{TEAM_ID}/-1/-1/-1/1/true.html"
STANDINGS_URL = f"{BASE}/standingsForDate/{FIXTURE_GROUP}/2/-1/-1.html"

INDEX_HTML = Path(__file__).resolve().parent.parent / "index.html"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

LEAGUE_NAME = "South of Scotland Football League"

START_MARK = "// ── AUTO-DATA-START"
END_MARK = "// ── AUTO-DATA-END"


# ── Fetch ───────────────────────────────────────────────────────────────────
def fetch_pages():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        page.goto(MATCH_HUB_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_selector("tr[data-match-href]", timeout=30000)
        hub_html = page.content()

        page.goto(STANDINGS_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_selector("table tbody tr", timeout=30000)
        standings_html = page.content()

        browser.close()
    return hub_html, standings_html


# ── Parse helpers ───────────────────────────────────────────────────────────
def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def parse_matches(hub_html: str):
    """Return (results, fixtures) lists in the site's FALLBACK shape."""
    results, fixtures = [], []
    rows = re.findall(r"<tr data-match-href[^>]*>(.*?)</tr>", hub_html, re.S)

    for row in rows:
        cells = [strip_tags(c) for c in
                 re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 5:
            continue

        datetime_cell, home, middle, away, details = cells[:5]

        dt = re.search(r"(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})", datetime_cell)
        if not dt:
            continue
        date, time = dt.group(1), dt.group(2)

        # Competition & venue live in the details cell: "Competition @ Venue"
        comp, _, venue = details.partition("@")
        comp, venue = comp.strip(), venue.strip()
        mtype = "league" if comp.startswith(LEAGUE_NAME) else "cup"

        score = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", middle)
        if score:
            results.append({
                "date": date, "time": time, "type": mtype,
                "homeTeam": home, "homeScore": int(score.group(1)),
                "awayScore": int(score.group(2)), "awayTeam": away,
            })
        elif middle.upper() == "VS":
            fixtures.append({
                "date": date, "time": time, "type": mtype,
                "homeTeam": home, "awayTeam": away, "venue": venue,
            })
        # Anything else (P-P postponed, A-A abandoned, etc.) is skipped.

    def sort_key(m):
        return datetime.strptime(f"{m['date']} {m['time']}", "%d/%m/%y %H:%M")

    results.sort(key=sort_key, reverse=True)   # newest first
    fixtures.sort(key=sort_key)                # soonest first
    return results, fixtures


def parse_table(standings_html: str):
    """Return standings rows in the site's FALLBACK shape."""
    table = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", standings_html, re.S)
    for row in rows:
        cells = [strip_tags(c) for c in
                 re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        pos, team = int(cells[0]), cells[1]
        p, w, d, l, f, a, gd, pts = (cells[2], cells[3], cells[4], cells[5],
                                     cells[6], cells[7], cells[8], cells[9])
        try:
            gd_val = int(gd.replace("+", ""))
        except ValueError:
            gd_val = 0
        table.append({
            "pos": pos, "team": team,
            "P": int(p), "W": int(w), "D": int(d), "L": int(l),
            "F": int(f), "A": int(a),
            "GD": f"+{gd_val}" if gd_val > 0 else str(gd_val),
            "PTS": int(pts),
        })
    return table


# ── Emit JS ─────────────────────────────────────────────────────────────────
def js_obj(d: dict) -> str:
    """Compact JS object literal with unquoted keys, matching the hand style."""
    parts = []
    for k, v in d.items():
        parts.append(f"{k}:{json.dumps(v, ensure_ascii=False)}")
    return "{" + ",".join(parts) + "}"


def build_fallback_js(results, fixtures, table) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"{START_MARK} — generated {stamp} by scripts/update_site_data.py — do not edit by hand ──",
        "const FALLBACK = {",
        "  results: [",
        *[f"    {js_obj(r)}," for r in results],
        "  ],",
        "  fixtures: [",
        *[f"    {js_obj(f)}," for f in fixtures],
        "  ],",
        "  table: [",
        *[f"    {js_obj(t)}," for t in table],
        "  ]",
        "};",
        f"{END_MARK} ──",
    ]
    return "\n".join(lines)


def patch_index(new_block: str):
    src = INDEX_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START_MARK) + r".*?" + re.escape(END_MARK) + r"[^\n]*",
        re.S,
    )
    if not pattern.search(src):
        sys.exit("ERROR: AUTO-DATA markers not found in index.html")
    out = pattern.sub(lambda _: new_block, src, count=1)
    INDEX_HTML.write_text(out, encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    hub_html, standings_html = fetch_pages()

    results, fixtures = parse_matches(hub_html)
    table = parse_table(standings_html)

    # Safety rails: never write empty data over the live site.
    if not (results or fixtures):
        sys.exit("ERROR: no matches parsed — aborting without changes")
    if len(table) < 6:
        sys.exit("ERROR: standings table looks wrong — aborting without changes")

    patch_index(build_fallback_js(results, fixtures, table))
    print(f"OK: {len(results)} results, {len(fixtures)} fixtures, "
          f"{len(table)} table rows written to {INDEX_HTML.name}")


if __name__ == "__main__":
    main()
