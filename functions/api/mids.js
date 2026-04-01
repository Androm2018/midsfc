// Cloudflare Pages Function — /api/mids
// Scrapes the South of Scotland FL site for Mid Annandale fixtures, results & standings
// Deployed automatically by Cloudflare Pages alongside index.html

const TEAM_URL      = 'https://sosfl.leaguerepublic.com/team/134996649/201795845.html';
const STANDINGS_URL = 'https://sosfl.leaguerepublic.com/standingsForDate/178710391/2/-1/-1.html';
const UA            = 'Mozilla/5.0 (compatible; MidsAFC-Website/1.0)';

// ── HTML helpers ─────────────────────────────────────────────────────────────

function stripHtml(html) {
  // Preserve alt text from images before stripping tags
  return html
    .replace(/<img[^>]+alt="([^"]*)"[^>]*\/?>/gi, ' $1 ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function getHref(html) {
  const m = html.match(/href="([^"]+)"/);
  if (!m) return null;
  return m[1].startsWith('http') ? m[1] : 'https://sosfl.leaguerepublic.com' + m[1];
}

function getRows(html) {
  const rows = [];
  for (const [, rowHtml] of html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells = [...rowHtml.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map(([, ch]) => ({
      text: stripHtml(ch),
      raw:  ch.toLowerCase(),
      href: getHref(ch),
    }));
    if (cells.length >= 4) rows.push(cells);
  }
  return rows;
}

// ── Parsers ──────────────────────────────────────────────────────────────────

function parseTeam(html) {
  // Locate the Results and Matches sections by their h2 headings
  const rIdx = html.search(/<h2[^>]*>\s*Results\s*</i);
  const mIdx = html.search(/<h2[^>]*>\s*Matches\s*</i);

  const resultsHtml = (rIdx > -1 && mIdx > -1) ? html.slice(rIdx, mIdx) : '';
  const matchesHtml = mIdx > -1 ? html.slice(mIdx) : '';

  function splitDateTime(raw) {
    // "28/03/26   14:00" — two or more spaces separate date from time
    const i = raw.search(/\s{2,}/);
    return i > -1
      ? [raw.slice(0, i).trim(), raw.slice(i).trim()]
      : [raw.trim(), ''];
  }

  const results = getRows(resultsHtml)
    .filter(r => / - /.test(r[3]?.text))
    .map(r => {
      const parts = r[3].text.split('-').map(s => parseInt(s.trim()));
      const [date, time] = splitDateTime(r[1].text);
      return {
        date,
        time,
        type:      r[0].raw.includes('cup') ? 'cup' : 'league',
        homeTeam:  r[2].text,
        homeScore: isNaN(parts[0]) ? 0 : parts[0],
        awayScore: isNaN(parts[1]) ? 0 : parts[1],
        awayTeam:  r[4].text,
        url:       r[2].href || r[4].href || null,
      };
    });

  const fixtures = getRows(matchesHtml)
    .filter(r => /\bvs\b/i.test(r[3]?.text))
    .map(r => {
      const [date, time] = splitDateTime(r[1].text);
      return {
        date,
        time,
        type:     r[0].raw.includes('cup') ? 'cup' : 'league',
        homeTeam: r[2].text,
        awayTeam: r[4].text,
        venue:    r[5]?.text || '',
        url:      r[2].href || r[4].href || null,
      };
    });

  return { results, fixtures };
}

function parseStandings(html) {
  return getRows(html)
    .filter(r => !isNaN(parseInt(r[0]?.text)) && r.length >= 9)
    .map(r => ({
      pos:  parseInt(r[0].text),
      team: r[1].text,
      url:  r[1].href,
      P:    parseInt(r[2].text)  || 0,
      W:    parseInt(r[3].text)  || 0,
      D:    parseInt(r[4].text)  || 0,
      L:    parseInt(r[5].text)  || 0,
      F:    parseInt(r[6].text)  || 0,
      A:    parseInt(r[7].text)  || 0,
      GD:   r[8]?.text           || '0',
      PTS:  parseInt(r[9]?.text) || 0,
    }));
}

// ── Handler ──────────────────────────────────────────────────────────────────

export async function onRequest(context) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    // Cache for 30 minutes on Cloudflare's edge
    'Cache-Control': 'public, s-maxage=1800, stale-while-revalidate=300',
  };

  try {
    const [teamRes, standingsRes] = await Promise.all([
      fetch(TEAM_URL,      { headers: { 'User-Agent': UA } }),
      fetch(STANDINGS_URL, { headers: { 'User-Agent': UA } }),
    ]);

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
      JSON.stringify({ error: err.message }),
      { status: 500, headers }
    );
  }
}
