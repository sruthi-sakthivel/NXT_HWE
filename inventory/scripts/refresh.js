// Pulls current stock from Rush Order (server-side — their API blocks
// browser-origin requests, so this can never run client-side) and writes
// data.json for the dashboard to fetch on load.
//
// Required environment variables (set as GitHub Action secrets, never
// committed):
//   RUSHORDER_EMAIL  - e.g. sruthi.sakthivel@nextsense.io
//   RUSHORDER_TOKEN  - the Rush Order API token
//
// Run with: node scripts/refresh.js
// Requires Node 18+ (built-in fetch).

const fs = require('fs');
const path = require('path');

// The GitHub Actions runner's clock is UTC, but the dashboard's "day" is a
// Pacific business day (the cron schedule is anchored to 6am PT). Computing
// "today" from the runner's own timezone caused runs after ~5pm PT to be
// logged as the next day, skipping a day in the history entirely.
const TIME_ZONE = 'America/Los_Angeles';

const START = Date.UTC(2026, 4, 5); // May 5, 2026 — must match START in the dashboard HTML
const DATA_PATH = path.join(__dirname, '..', 'data.json');

// SKU -> display info. Keep in sync with the dashboard's LIVE_STOCK fallback.
const SKU_MAP = {
  'NSSB0100-ZW':   { key: 'earbuds', name: 'Smartbuds' },
  'NSEMTA0201C-W': { key: 'tipsS',   name: 'Tips — Small' },
  'NSEMTB0201C-W': { key: 'tipsM',   name: 'Tips — Medium' },
  'NSEMTC0201C-W': { key: 'tipsL',   name: 'Tips — Large' },
  'NSEMWZ0103T-W': { key: 'wingsS',  name: 'Sleeves — Small' },
  'NSEMWY0303T-W': { key: 'wingsM',  name: 'Sleeves — Medium' },
  'NSEMWX0103T-W': { key: 'wingsL',  name: 'Sleeves — Large' },
};

// Returns { year, month, day } for the given instant, as read on a Pacific
// Time wall clock — not the runner's local timezone.
function pacificDateParts(d) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(d).reduce((acc, p) => {
    if (p.type !== 'literal') acc[p.type] = Number(p.value);
    return acc;
  }, {});
  return parts;
}

function dayIndex({ year, month, day }) {
  return Math.round((Date.UTC(year, month - 1, day) - START) / 86400000);
}

function fmtDate({ year, month, day }) {
  return year + '-' + String(month).padStart(2, '0') + '-' + String(day).padStart(2, '0');
}

function fmtDisplayDate({ year, month, day }) {
  return new Date(Date.UTC(year, month - 1, day))
    .toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

async function main() {
  const email = process.env.RUSHORDER_EMAIL;
  const token = process.env.RUSHORDER_TOKEN;
  if (!email || !token) {
    throw new Error('RUSHORDER_EMAIL and RUSHORDER_TOKEN must be set as environment variables (GitHub secrets).');
  }
  const auth = Buffer.from(`${email}:${token}`).toString('base64');

  const res = await fetch('https://dream.rushorder.com/api/stock', {
    headers: {
      accept: 'application/json',
      authorization: `Basic ${auth}`,
      rushordermomcode: 'NXS',
    },
  });
  if (!res.ok) {
    throw new Error(`Rush Order API returned ${res.status}: ${await res.text()}`);
  }
  const stock = await res.json();

  const items = Object.entries(SKU_MAP).map(([sku, info]) => {
    const rec = stock.find(s => s.Sku === sku) || { Available: 0, Committed: 0, BackOrdered: 0 };
    return {
      key: info.key,
      name: info.name,
      sku,
      available: rec.Available,
      committed: rec.Committed,
      backorder: rec.BackOrdered,
    };
  });

  const now = new Date();
  const todayParts = pacificDateParts(now);
  const today = dayIndex(todayParts);

  // Load existing data.json (if present) to preserve history. Restocks are
  // NOT carried through here — they're manually-planned data that lives in
  // index.html, not something Rush Order reports. Writing them here caused
  // dashboard edits to restocks to get silently overwritten by whatever was
  // last in data.json the moment the page fetched it.
  let existing = { liveHistory: [], itemHistory: {} };
  if (fs.existsSync(DATA_PATH)) {
    try { existing = JSON.parse(fs.readFileSync(DATA_PATH, 'utf8')); } catch (e) { /* start fresh */ }
  }

  const earbuds = items.find(it => it.key === 'earbuds');
  const balance = earbuds.available - earbuds.backorder;

  const history = (existing.liveHistory || []).filter(h => h.day !== today);
  history.push({ date: fmtDate(todayParts), day: today, balance });
  history.sort((a, b) => a.day - b.day);

  // Per-SKU history for the 6 tips/sleeves consumables, same shape as
  // liveHistory above (day-indexed balance = Available - Backorder). Kept
  // separate from liveHistory, which is Smartbuds-specific and already
  // depended on by other dashboard logic (email draft, stats cards).
  const existingItemHistory = existing.itemHistory || {};
  const itemHistory = {};
  for (const [, info] of Object.entries(SKU_MAP)) {
    if (info.key === 'earbuds') continue;
    const it = items.find(i => i.key === info.key) || { available: 0, backorder: 0 };
    const itBalance = it.available - it.backorder;
    const itHistory = (existingItemHistory[info.key] || []).filter(h => h.day !== today);
    itHistory.push({ date: fmtDate(todayParts), day: today, balance: itBalance });
    itHistory.sort((a, b) => a.day - b.day);
    itemHistory[info.key] = itHistory;
  }

  const payload = {
    liveStock: { fetchedAt: fmtDisplayDate(todayParts), items },
    liveHistory: history,
    itemHistory,
    lastRefreshed: now.toISOString(),
  };

  fs.writeFileSync(DATA_PATH, JSON.stringify(payload, null, 2) + '\n');
  console.log(`data.json updated — Smartbuds balance: ${balance} (as of ${fmtDate(todayParts)} PT)`);
}

main().catch(err => {
  console.error('Refresh failed:', err.message);
  process.exit(1);
});
