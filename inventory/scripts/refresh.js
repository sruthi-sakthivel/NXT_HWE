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

const START = new Date(2026, 4, 5); // May 5, 2026 — must match START in the dashboard HTML
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

function dayIndex(d) {
  return Math.round((d - START) / 86400000);
}

function fmtDate(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function fmtDisplayDate(d) {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
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
  const today = dayIndex(new Date(now.getFullYear(), now.getMonth(), now.getDate()));

  // Load existing data.json (if present) to preserve history and restocks
  let existing = { liveHistory: [], restocks: [] };
  if (fs.existsSync(DATA_PATH)) {
    try { existing = JSON.parse(fs.readFileSync(DATA_PATH, 'utf8')); } catch (e) { /* start fresh */ }
  }

  const earbuds = items.find(it => it.key === 'earbuds');
  const balance = earbuds.available - earbuds.backorder;

  const history = (existing.liveHistory || []).filter(h => h.day !== today);
  history.push({ date: fmtDate(now), day: today, balance });
  history.sort((a, b) => a.day - b.day);

  const payload = {
    liveStock: { fetchedAt: fmtDisplayDate(now), items },
    liveHistory: history,
    restocks: existing.restocks || [],
    lastRefreshed: now.toISOString(),
  };

  fs.writeFileSync(DATA_PATH, JSON.stringify(payload, null, 2) + '\n');
  console.log(`data.json updated — Smartbuds balance: ${balance} (as of ${fmtDate(now)})`);
}

main().catch(err => {
  console.error('Refresh failed:', err.message);
  process.exit(1);
});
