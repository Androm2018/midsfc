#!/usr/bin/env python3
"""
Mid Annandale AFC - SOSL data updater.

Scrapes the SOSL LeagueRepublic public site (robots.txt-allowed pages only)
and writes data.json in the schema used by index.html.

Pages used:
  - Team page:   /team/{SEASON_ID}/{TEAM_ID}.html   (fixtures + results)
  - League home: /index.html                        (standings table)

Run locally:  python3 scripts/update_data.py
Output: data.json in repo root (only rewritten if content changed).

IDs (2026/27 season):
  SEASON_ID 373593711 = 2026/27  (2025/26 was 134996649, 2024-25 was 655098607)
  TEAM_ID   201795845 = Mid Annandale
When a new season starts, update SEASON_ID (find it in the season dropdown
on https://sosfl.leaguerepublic.com/index.html page source).
"""

import json
import re
import sys
import html as htmllib
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://sosfl.leaguerepublic.com"
SEASON_ID = "373593711"   # 2026/27
TEAM_ID = "201795845"     # Mid Annandale
TEAM_PAGE = f"{BASE}/team/{SEASON_ID}/{TEAM_ID}.html"
LEAGUE_HOME = f"{BASE}/index.html"

# LeagueRepublic's WAF rejects non-browser user agents, so we use a
# browser-style UA with an identifying suffix for transparency.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
                   "MidsFC-updater/1.0 (+https://midsfc.uk)"),
    "Accept": "text/html",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "data.json"

MIDS = "Mid Annandale"


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    if not r.text or len(r.text) < 2000:
        raise RuntimeError(f"Suspiciously small response from {url} ({len(r.text)} bytes)")
    return r.text


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s or "")).strip()


def classify(competition: str) -> str:
    c = (competition or "").lower()
    if "cup" in c or "trophy" in c or "shield" in c:
        return "cup"
    return "league"


def parse_team_matches(page: str):
    """Parse fixtures and results from the team page match table."""
    soup = BeautifulSoup(page, "lxml")
    fixtures, results = [], []

    for row in soup.select("table tr"):
        cells = [clean(td.get_text(" ")) for td in row.find_all("td")]
        if len(cells) < 4:
            continue
        text_row = " | ".join(cells)

        m_date = re.search(r"\b(\d{2}/\d{2}/\d{2})\b", text_row)
        m_time = re.search(r"\b(\d{1,2}:\d{2})\b", text_row)
        if not m_date:
            continue
        date, time = m_date.group(1), (m_time.group(1) if m_time else "14:00")

        competition = ""
        for c in cells:
            if re.search(r"league|cup|trophy|shield", c, re.I):
                competition = c
                break
        if not competition:
            img = row.find("img", alt=True)
            competition = img["alt"] if img else ""

        score = re.search(r"(\d{1,2})\s*[-\u2013]\s*(\d{1,2})", text_row)

        skip = re.compile(
            r"^(\d{2}/\d{2}/\d{2}.*|\d{1,2}:\d{2}|vs|v|[-\u2013]|"
            r"\d{1,2}\s*[-\u2013]\s*\d{1,2}|)$", re.I)
        names = [c for c in cells
                 if not skip.match(c)
                 and not re.search(r"league|cup|trophy|shield", c, re.I)
                 and not re.fullmatch(r"[\d\s:/\-\u2013]+", c)]

        venue = ""
        team_names = []
        for n in names:
            if n.startswith("@"):
                venue = n.lstrip("@ ").strip()
            else:
                team_names.append(n)
        if len(team_names) < 2:
            continue
        home, away = team_names[0], team_names[1]
        if len(team_names) >= 3 and not venue:
            venue = team_names[2]

        entry_type = classify(competition)
        if score:
            results.append({
                "date": date, "time": time, "type": entry_type,
                "homeTeam": home, "homeScore": int(score.group(1)),
                "awayScore": int(score.group(2)), "awayTeam": away,
            })
        else:
            fx = {"date": date, "time": time, "type": entry_type,
                  "homeTeam": home, "awayTeam": away}
            if venue:
                fx["venue"] = venue
            fixtures.append(fx)

    def key(m):
        d, mo, y = m["date"].split("/")
        return (y, mo, d, m["time"])

    fixtures.sort(key=key)                # soonest first
    results.sort(key=key, reverse=True)   # newest first
    return fixtures, results


def parse_standings(page: str):
    """Parse the league table from the league homepage."""
    soup = BeautifulSoup(page, "lxml")
    best = []
    for table in soup.find_all("table"):
        head = clean(table.get_text(" "))
        if not re.search(r"\bPTS\b", head):
            continue
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < 9:
                continue
            if not cells[0].isdigit():
                continue
            nums = [c for c in cells[2:] if re.fullmatch(r"[+-]?\d+", c)]
            if len(nums) < 8:
                continue
            gd_raw = int(nums[6])
            rows.append({
                "pos": int(cells[0]), "team": cells[1],
                "P": int(nums[0]), "W": int(nums[1]), "D": int(nums[2]),
                "L": int(nums[3]), "F": int(nums[4]), "A": int(nums[5]),
                "GD": f"+{gd_raw}" if gd_raw > 0 else str(gd_raw),
                "PTS": int(nums[7]),
            })
        if len(rows) > len(best):
            best = rows
    return best


def main() -> int:
    team_html = fetch(TEAM_PAGE)
    fixtures, results = parse_team_matches(team_html)

    league_html = fetch(LEAGUE_HOME)
    table = parse_standings(league_html)

    # Sanity checks - never publish an obviously broken file.
    problems = []
    if not fixtures and not results:
        problems.append("no fixtures or results parsed")
    if table and not any(MIDS.lower() in r["team"].lower() for r in table):
        problems.append("standings parsed but Mid Annandale missing")
    if problems:
        print("ABORT: " + "; ".join(problems), file=sys.stderr)
        return 1

    data = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
        "fixtures": fixtures,
        "table": table,
    }
    new = json.dumps(data, indent=2, ensure_ascii=False)

    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            old.pop("updated", None)
            cur = json.loads(new)
            cur.pop("updated", None)
            if old == cur:
                print("No changes - data.json left untouched.")
                return 0
        except Exception:
            pass

    OUT.write_text(new + "\n", encoding="utf-8")
    print(f"Wrote {OUT} - {len(results)} results, "
          f"{len(fixtures)} fixtures, {len(table)} table rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
