// Cloudflare Pages Function — /api/mids
// Scrapes SOSL LeagueRepublic for Mid Annandale fixtures, results & standings

const TEAM_URL      = 'https://sosfl.leaguerepublic.com/team/134996649/201795845.html';
const STANDINGS_URL = 'https://sosfl.leaguerepublic.com/standingsForDate/178710391/2/-1/-1.html';
const UA            = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const BROWSER_HEADERS = {
  'User-Agent': UA,
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-GB,en;q=0.9',
  'Accept-Encoding': 'gzip, deflate, br',
  'Cache-Control': 'no-cache',
  'Pragma': 'no-cache',
};

// Decode common HTML entities then strip all tags
function clean(html) {
  return html
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&#\d+;/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function href(html) {
  const m = html.match(/href="([^"]+)"/);
  if (!m) return null;
  return m[1].startsWith('http') ? m[1] : 'https://sosfl.leaguerepublic.com' + m[1];
}

// Extract all <tr> rows as arrays of cell objects
function rows(html) {
  const out = [];
  for (const [, tr] of html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells = [...tr.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map(([, c]) => ({
      text: clean(c),
      lower: clean(c).toLowerCase(),
      link: href(c),
    }));
    if (cells.length >= 4) out.push(cells);
  }
  return out;
}

// Split "28/03/26  14:00" into ["28/03/26", "14:00"]
// Works whether whitespace is spaces, &nbsp; (decoded), or mixed
function splitDT(str) {
  const s = str.trim();
  // Match date pattern at start, then grab everything after it as time
  const m = s.match(/^(\d{1,2}\/\d{2}\/\d{2,4})\s+(.+)$/);
  return m ? [m[1].trim(), m[2].trim()] : [s, ''];
}

// Parse "3 - 1" or "0 - 2   (HT 0-0)" → [3, 1]
function parseScore(str) {
  // Only look at the part before any bracket "(HT...)"
  const core = str.split('(')[0];
  const m = core.match(/(\d+)\s*-\s*(\d+)/);
  return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
}

function parseTeam(html) {
  const rIdx = html.search(/<h2[^>]*>[^<]*Results[^<]*<\/h2>/i);
  const mIdx = html.search(/<h2[^>]*>[^<]*Matches[^<]*<\/h2>/i);

  const resultsHtml = rIdx > -1 ? html.slice(rIdx, mIdx > rIdx ? mIdx : html.length) : '';
  const matchesHtml = mIdx > -1 ? html.slice(mIdx) : '';

  const results = rows(resultsHtml)
    .filter(r => /\d\s*-\s*\d/.test(r[3]?.text))
    .map(r => {
      const [hs, as] = parseScore(r[3].text);
      const [date, time] = splitDT(r[1].text);
      return {
        date,
        time,
        type:      r[0].lower.includes('cup') ? 'cup' : 'league',
        homeTeam:  r[2].text,
        homeScore: hs,
        awayScore: as,
        awayTeam:  r[4].text,
      };
    });

  const fixtures = rows(matchesHtml)
    .filter(r => /\bvs\b/i.test(r[3]?.text))
    .map(r => {
      const [date, time] = splitDT(r[1].text);
      return {
        date,
        time,
        type:     r[0].lower.includes('cup') ? 'cup' : 'league',
        homeTeam: r[2].text,
        awayTeam: r[4].text,
        venue:    r[5]?.text || '',
      };
    });

  return { results, fixtures };
}

function parseStandings(html) {
  return rows(html)
    .filter(r => /^\d+$/.test(r[0]?.text) && r.length >= 9)
    .map(r => ({
      pos:  parseInt(r[0].text),
      team: r[1].text,
      P:    parseInt(r[2].text) || 0,
      W:    parseInt(r[3].text) || 0,
      D:    parseInt(r[4].text) || 0,
      L:    parseInt(r[5].text) || 0,
      F:    parseInt(r[6].text) || 0,
      A:    parseInt(r[7].text) || 0,
      GD:   r[8]?.text || '0',
      PTS:  parseInt(r[9]?.text) || 0,
    }));
}

export async function onRequest() {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': 'public, s-maxage=1800, stale-while-revalidate=300',
  };

  try {
    const [teamRes, standingsRes] = await Promise.all([
      fetch(TEAM_URL,      { headers: BROWSER_HEADERS }),
      fetch(STANDINGS_URL, { headers: BROWSER_HEADERS }),
    ]);

    if (!teamRes.ok)      throw new Error(`Team page: ${teamRes.status}`);
    if (!standingsRes.ok) throw new Error(`Standings: ${standingsRes.status}`);

    const [teamHtml, standingsHtml] = await Promise.all([
      teamRes.text(),
      standingsRes.text(),
    ]);

    const { results, fixtures } = parseTeam(teamHtml);
    const table = parseStandings(standingsHtml);

    return new Response(
      JSON.stringify({ results, fixtures, table, updated: new Date().toISOString() }),
      { headers }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: err.message, stack: err.stack }),
      { status: 500, headers }
    );
  }
}
