"""Build wear_time/index.html from wear_time/wear_time_data.json."""
import json, os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]  # wear_time/

with open(ROOT / "wear_time_data.json", encoding="utf-8") as f:
    raw = json.load(f)

# Support both old flat format and new grouped format
if "internal" in raw and "external" in raw:
    data_internal = raw["internal"]
    data_external = raw["external"]
else:
    data_internal = raw
    data_external = {}

# ── User metadata ─────────────────────────────────────────────────────────────
INTERNAL_USERS = [
    {"name": "Akshay",    "email": "akshay@nextsense.io",                    "user_id": "aCkZozcQSvbreWqzOZOB"},
    {"name": "Bridgitte", "email": "bridgitte.chan@nextsense.io",             "user_id": "YC9CAxKduFgzLNCrjQiW1cHziTf1"},
    {"name": "Caitlin",   "email": "caitlin@nextsense.io",                   "user_id": "ZRAqCK4Vb3tdYe6Tbq9U"},
    {"name": "Chris",     "email": "christopher.rowley@nextsense.io",        "user_id": "3TatWqtmBaMpUlBWiTI6bQQBzs53"},
    {"name": "Clayton",   "email": "clayton.gentsch@nextsense.io",           "user_id": "3ha0rwXpYtNm8GzeAltwi0BmJDZ2"},
    {"name": "Duy",       "email": "duy.phan@nextsense.io / duyphanngoc@gmail.com", "user_id": "G5GtDIfe6xQFjUKT3SCfNohMqBA2"},
    {"name": "Eric",      "email": "eric@nextsense.io",                      "user_id": "Qtu8MR1DjbFEROIZe1Ee"},
    {"name": "Gillian",   "email": "gillybean2802@gmail.com",                "user_id": "0mmzi0FwbmYFlUyllvxkw92hLnQ2"},
    {"name": "Ilsim",     "email": "ilsimshin@nextsense.io",                 "user_id": "sxXWrv8EYIPhkZe4XebC5bLaRbL2"},
    {"name": "JB",        "email": "jberent@gmail.com",                      "user_id": "5G4E3DXnV2iNf1lUI3VF"},
    {"name": "KB",        "email": "borodink@gmail.com",                     "user_id": "HEh1IZPaCFMeHJv8ImLT7QaJX8f2"},
    {"name": "Jason Bach (JB)", "email": "jasonbach1998@gmail.com",           "user_id": "r4NHdzUcoEeGgbRcVFMWiwxn6723"},
    {"name": "Nhan",      "email": "anhnhancao@gmail.com",                   "user_id": "TrO02SvpM3X3OFBVejKzWj03vZ62"},
    {"name": "Nivi",      "email": "nivedithamuthukrishnan@gmail.com",        "user_id": "xMymgzQ3krPmSbUwy614bpNQZY23"},
    {"name": "Sruthi",    "email": "sruthi.sakthivel@nextsense.io",          "user_id": "b3759mWLKuYIoQiikFyssNU7cxp2"},
    {"name": "Tae",       "email": "tae.joung@nextsense.io",                "user_id": "JCpuDm5iLqOtwevfcwvdTk6fc7u1"},
]

EXTERNAL_USERS = [
    {"name": "Abrar Haroon",        "email": "8njp8t4th9@privaterelay.appleid.com", "user_id": "7Jy4mC1u9lhMeQgCL95ad93lgDP2"},
    {"name": "Allan Levey",         "email": "alevey@emory.edu",                    "user_id": "ZRdXN3blZiP9zzcU6fniYHBVJxh1"},
    {"name": "Andy Kurtzig",        "email": "khn7cytb5t@privaterelay.appleid.com", "user_id": "rkiVholKZtgYaRVe8MlhnSMDOSG3"},
    {"name": "Craig Forman",        "email": "craig.forman@gmail.com",              "user_id": "gMIZG5f9lHWOkyYjV8xq6QhAscS2"},
    {"name": "Esther Dyson",        "email": "edyson@edventure.com",                "user_id": "EWSBYa5GUHXXKLVcZWtbyvHAZ183"},
    {"name": "Jamie Evarts",        "email": "jamie.evarts@gmail.com",              "user_id": "Gbq8ByQQKfRVAUFMCMhOXpHSVUn2"},
    {"name": "Jefferson Terkhorn",  "email": "jeffterk@gmail.com",                  "user_id": "mAGGKa2R90ZRXDxfAtrdwkA0gqQ2"},
    {"name": "Jonathan Cheng",      "email": "jonathancheng77@gmail.com",           "user_id": "LunaEA99PUPlQWmBOwV0um6a5YQ2"},
    {"name": "Michael Coates",      "email": "mwcoates@gmail.com",                  "user_id": "0DC4BbXM6UOr93opFOiRUyS6ioJ3"},
    {"name": "Pamela York",         "email": "pyork@capita3.com",                   "user_id": "1Qg6CMSmHTOciqQOaOMCXdYBwHw2"},
    {"name": "R C",                 "email": "castellanomd@gmail.com",              "user_id": "MXCGWiu71TSWCUtz1jbpviF90vq2"},
    {"name": "Seal",                "email": "s@seal.com",                          "user_id": "J775bD3UCDbU4QJ9zuN0MdacEco2"},
    {"name": "TMAC",                "email": "k6xs5nmb8d@privaterelay.appleid.com", "user_id": "7sTwtsTG6oOYKhtQhNJQoUMW68E3"},
    {"name": "Tyler",               "email": "me@tylersarkisian.com",               "user_id": "IzWXcMOt68QdgmEsFAqduhvMUGr1"},
    {"name": "Zack Varner",         "email": "zackvarner@msn.com",                  "user_id": "mGvDNVAVbxbpQCTCJlctSuqwCSl2"},
]


def _build_table_rows(users, data_dict):
    user_meta = {u["name"]: u for u in users}
    rows = []
    for u in users:
        name = u["name"]
        if name not in data_dict:
            rows.append({"name": name, "email": u.get("email",""), "user_id": u.get("user_id",""), "no_data": True})
            continue
        d = data_dict[name]
        l = d["quality"]["left"]
        r = d["quality"]["right"]
        rows.append({
            "name":       name,
            "email":      u.get("email", ""),
            "user_id":    u.get("user_id", ""),
            "no_data":    False,
            "events":     d["events"],
            "from_date":  d["from_date"],
            "to_date":    d["to_date"],
            "l_strong":   l["strong"],
            "l_good":     l["good"],
            "l_floating": l["floating"],
            "l_bad":      l["bad"],
            "r_strong":   r["strong"],
            "r_good":     r["good"],
            "r_floating": r["floating"],
            "r_bad":      r["bad"],
            "l_high_ac":  d.get("high_ac_left_pct",  0.0),
            "r_high_ac":  d.get("high_ac_right_pct", 0.0),
        })
    return rows


internal_rows = _build_table_rows(INTERNAL_USERS, data_internal)
external_rows = _build_table_rows(EXTERNAL_USERS, data_external)

# Daily data keyed by name (both groups merged — names are unique across groups)
daily_only = {}
for data_dict in (data_internal, data_external):
    for name, d in data_dict.items():
        daily_only[name] = {"daily": d["daily"], "replacements": d.get("replacements", [])}

internal_json = json.dumps(internal_rows, separators=(",", ":"))
external_json = json.dumps(external_rows, separators=(",", ":"))
daily_json    = json.dumps(daily_only,    separators=(",", ":"))

today_str  = datetime.now().strftime("%Y-%m-%d")
all_active = [r for r in internal_rows + external_rows if not r["no_data"]]
date_range = ""
if all_active:
    min_from = min(r["from_date"] for r in all_active)
    max_to   = max(r["to_date"]   for r in all_active)
    date_range = f"{min_from} to {max_to}"

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NextSense Wear Time Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
  :root {
    --bg:#0f0f11; --surface:#1a1a20; --surface2:#22222a; --surface3:#2a2a34;
    --border:rgba(255,255,255,0.07); --border2:rgba(255,255,255,0.14);
    --text:#e2e2e9; --text-dim:#8b8ba7; --text-sub:#52526b; --accent:#5e6ad2;
    --c-strong:#2d6a2d; --c-good:#7bc47b; --c-floating:#c0392b; --c-bad:#c0392b;
    --c-nodata:#444444;
  }
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:'Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);font-size:14px;min-height:100vh;padding:32px 40px 60px;}
  .header{margin-bottom:28px;}
  .header h1{font-size:22px;font-weight:700;letter-spacing:-0.3px;margin-bottom:6px;}
  .header .meta{font-size:12px;color:var(--text-dim);}
  .summary{display:flex;gap:14px;margin-bottom:24px;flex-wrap:wrap;}
  .card{flex:1;min-width:140px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;position:relative;}
  .card-tooltip{display:none;position:absolute;bottom:calc(100% + 8px);left:0;background:#1e1e2a;border:1px solid rgba(255,255,255,0.15);border-radius:8px;padding:10px 14px;z-index:100;min-width:160px;max-width:280px;box-shadow:0 8px 24px rgba(0,0,0,0.6);}
  .card:hover .card-tooltip{display:block;}
  .card-tooltip::after{content:'';position:absolute;top:100%;left:0;right:0;height:12px;}
  .tip-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.6px;color:var(--text-sub);margin-bottom:6px;}
  .tip-name{display:block;font-size:12px;color:var(--text);line-height:1.8;cursor:pointer;padding:1px 4px;border-radius:4px;transition:background .1s;}
  .tip-name:hover{background:rgba(255,255,255,0.1);color:#a89fd8;}
  .card .card-label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.6px;color:var(--text-sub);margin-bottom:6px;}
  .card .card-value{font-size:26px;font-weight:700;line-height:1;}
  .card .card-sub{font-size:11px;color:var(--text-dim);margin-top:4px;}
  .card.warn .card-value{color:#f79009;} .card.good .card-value{color:#32d583;}
  .controls{display:flex;align-items:center;gap:12px;margin-bottom:24px;padding:14px 18px;background:var(--surface);border:1px solid var(--border);border-radius:10px;}
  .controls label{font-size:13px;color:var(--text-dim);font-weight:500;}
  .threshold-btn{padding:5px 14px;border-radius:6px;border:1px solid var(--border2);background:var(--surface2);color:var(--text-dim);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;font-family:inherit;}
  .threshold-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;}
  .threshold-btn:hover:not(.active){color:var(--text);}
  .legend{display:flex;gap:20px;margin-bottom:20px;flex-wrap:wrap;align-items:center;}
  .legend-item{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--text-dim);}
  .legend-swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0;}
  .legend-swatch.null-swatch{background:var(--surface3);border:1px dashed rgba(255,255,255,0.2);}
  .table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;}
  table{width:100%;border-collapse:collapse;}
  thead th{background:var(--surface2);padding:11px 16px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.6px;color:var(--text-sub);border-bottom:1px solid var(--border2);white-space:nowrap;}
  thead th.center{text-align:center;}
  tbody tr{border-bottom:1px solid var(--border);transition:background .1s;}
  tbody tr:last-child{border-bottom:none;}
  tbody tr:hover{background:rgba(255,255,255,0.02);}
  td{padding:10px 16px;vertical-align:middle;}
  /* Name + email + user_id cell */
  td.name-cell{min-width:160px;}
  .user-name{font-weight:600;font-size:13px;white-space:nowrap;color:var(--text);}
  .user-email{font-size:11px;color:var(--text-sub);white-space:nowrap;margin-top:2px;}
  .user-id-row{display:flex;align-items:center;gap:2px;margin-top:2px;}
  .user-id{font-family:monospace;font-size:10px;color:#555;white-space:nowrap;}
  .copy-btn{border:none;background:transparent;cursor:pointer;font-size:10px;color:#555;padding:0 2px;line-height:1;vertical-align:middle;}
  .copy-btn:hover{color:#aaa;}
  td.events{font-size:12px;color:var(--text-dim);text-align:right;white-space:nowrap;width:50px;max-width:80px;}
  td.bar-cell{min-width:220px;width:30%;padding-top:8px;padding-bottom:8px;}
  td.at-risk{text-align:left;min-width:180px;vertical-align:top;padding-top:12px;}
  .at-risk-wrap{display:flex;flex-direction:column;align-items:flex-start;gap:4px;}
  .risk-reason{font-size:10px;color:var(--text-sub);line-height:1.3;white-space:normal;}
  .risk-reason .rp-float{color:#e8a090;}
  .risk-reason .rp-bad{color:#e05c4b;}
  .warn-text{font-size:10px;line-height:1.4;white-space:normal;}
  .warn-text.orange{color:#f79009;}
  .warn-text.dim{color:var(--text-sub);}
  .warn-text.red{color:#ef4444;font-weight:600;}
  td.summary-cell{text-align:left;min-width:220px;vertical-align:top;padding-top:10px;padding-left:10px;}
  th.summary-cell{text-align:left;padding-left:10px;}
  /* Day summary pills */
  .day-label-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;padding:4px 0;}
  .day-label{font-size:11px;font-weight:700;color:var(--text);text-transform:uppercase;letter-spacing:0.7px;}
  .day-summary{display:inline-flex;align-items:center;gap:5px;flex-wrap:wrap;}
  .ds-ear{font-size:9px;font-weight:700;color:var(--text-sub);text-transform:uppercase;}
  .ds-pill{font-size:9px;font-weight:600;padding:1px 6px;border-radius:10px;white-space:nowrap;}
  .ds-pill.floating{background:rgba(232,160,144,0.2);color:#e8a090;border:1px solid rgba(232,160,144,0.3);}
  .ds-pill.bad{background:rgba(192,57,43,0.2);color:#e05c4b;border:1px solid rgba(192,57,43,0.3);}
  .ds-sep{color:var(--text-sub);font-size:9px;margin:0 2px;}
  .rep-pill{display:inline-flex;align-items:center;gap:4px;font-size:9px;font-weight:600;padding:2px 8px;border-radius:10px;background:rgba(94,106,210,0.2);color:#a89fd8;border:1px solid rgba(94,106,210,0.35);white-space:nowrap;}
  .bar-track{display:flex;height:26px;border-radius:5px;overflow:hidden;width:100%;}
  .bar-seg{display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;white-space:nowrap;overflow:hidden;text-shadow:0 1px 2px rgba(0,0,0,0.5);}
  .bar-seg.strong{background:var(--c-strong);color:rgba(255,255,255,0.9);}
  .bar-seg.good{background:var(--c-good);color:rgba(0,0,0,0.7);}
  .bar-seg.floating{background:var(--c-floating);color:rgba(0,0,0,0.7);}
  .bar-seg.bad{background:var(--c-bad);color:rgba(255,255,255,0.9);}
  .bar-nodata{display:flex;align-items:center;justify-content:center;height:26px;border-radius:5px;background:var(--c-nodata);color:rgba(255,255,255,0.4);font-size:11px;font-style:italic;}
  .badge-risk,.badge-ok{display:inline-flex;align-items:center;gap:4px;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;cursor:pointer;transition:all .15s;font-family:inherit;border:none;}
  .badge-risk{background:rgba(148,130,214,0.15);color:#a89fd8;border:1px solid rgba(148,130,214,0.35);}
  .badge-risk:hover{background:rgba(148,130,214,0.28);}
  .badge-ok{background:rgba(148,130,214,0.15);color:#a89fd8;border:1px solid rgba(148,130,214,0.35);}
  .badge-ok:hover{background:rgba(148,130,214,0.28);}
  .badge-nodata{font-size:11px;color:var(--text-sub);}
  /* Modal */
  .modal-overlay{display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.8);backdrop-filter:blur(4px);align-items:center;justify-content:center;}
  .modal-overlay.open{display:flex;}
  .modal{background:var(--surface);border:1px solid var(--border2);border-radius:14px;width:92vw;max-width:1200px;max-height:90vh;overflow-y:auto;box-shadow:0 24px 80px rgba(0,0,0,0.7);padding:28px 32px 32px;}
  .modal-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid var(--border);}
  .modal-title{font-size:18px;font-weight:700;}
  .modal-subtitle{font-size:12px;color:var(--text-dim);margin-top:3px;}
  .modal-close{width:32px;height:32px;border-radius:8px;border:1px solid var(--border2);background:var(--surface2);color:var(--text-dim);font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;flex-shrink:0;font-family:inherit;line-height:1;}
  .modal-close:hover{background:var(--surface3);color:var(--text);}
  .copy-img-btn{padding:5px 14px;border-radius:6px;border:1px solid var(--border2);background:var(--surface2);color:var(--text-dim);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;font-family:inherit;}
  .copy-img-btn:hover{color:var(--text);background:var(--surface3);}
  .copy-img-btn.copied{color:#32d583;border-color:#32d583;}
  .modal-legend{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap;align-items:center;padding:10px 14px;background:var(--surface2);border-radius:8px;}
  .modal-legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text-dim);}
  .ml-swatch{width:12px;height:12px;border-radius:2px;flex-shrink:0;}
  .ml-swatch.null-swatch{background:var(--surface3);border:1px dashed rgba(255,255,255,0.2);}
  /* Timeline */
  .day-row{margin-bottom:20px;}
  .day-row:last-child{margin-bottom:0;}
  .ears-wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
  .ear-label{font-size:10px;color:var(--text-sub);margin-bottom:3px;text-transform:uppercase;letter-spacing:0.4px;}
  .timeline-outer{position:relative;}
  .timeline-track{position:relative;height:36px;border-radius:4px;background:var(--surface3);overflow:hidden;}
  .tl-seg{position:absolute;top:0;height:100%;overflow:hidden;white-space:nowrap;cursor:default;}
  .tl-seg.strong{background:var(--c-strong);}
  .tl-seg.good{background:var(--c-good);}
  .tl-seg.floating{background:var(--c-floating);}
  .tl-seg.bad{background:var(--c-bad);}
  .tl-seg.null{background:transparent;}
  .tl-seg.__end__{background:transparent;}
  .ac-label{position:absolute;left:0;right:0;text-align:center;font-size:8px;font-weight:700;line-height:1;pointer-events:none;}
  .ac-label.top{top:3px;color:rgba(255,255,255,0.85);text-shadow:0 1px 2px rgba(0,0,0,0.6);}
  .ac-label.bottom{bottom:3px;color:rgba(0,0,0,0.75);}
  .tl-seg.good .ac-label.top,.tl-seg.floating .ac-label.top{color:rgba(0,0,0,0.7);text-shadow:none;}
  .tl-seg.good .ac-label.bottom,.tl-seg.floating .ac-label.bottom{color:rgba(0,0,0,0.7);}
  .x-axis{position:relative;height:18px;margin-top:4px;}
  .x-tick{position:absolute;font-size:9px;color:var(--text-sub);transform:translateX(-50%);}
  /* Section headers */
  .section-header{display:flex;align-items:center;gap:12px;margin:32px 0 16px;}
  .section-header h2{font-size:16px;font-weight:700;letter-spacing:-0.2px;}
  .section-header .section-meta{font-size:12px;color:var(--text-sub);}
  .section-summary{display:flex;gap:14px;margin-bottom:20px;flex-wrap:wrap;}
</style>
</head>
<body>
<div class="header">
  <h1>NextSense Wear Time Dashboard</h1>
  <div class="meta">Last updated: """ + today_str + """ &nbsp;|&nbsp; Data: """ + date_range + """ (most recent 14 usage days per user) &nbsp;|&nbsp; Source: Mixpanel <code>impedance_measurements</code></div>
</div>
<div class="controls">
  <label>At-Risk threshold (floating + bad &gt;):</label>
  <button class="threshold-btn" data-val="20" onclick="setThreshold(20)">20%</button>
  <button class="threshold-btn active" data-val="30" onclick="setThreshold(30)">30%</button>
</div>
<div class="legend">
  <div class="legend-item"><div class="legend-swatch" style="background:var(--c-strong)"></div>Strong (DC=All connected, AC &le; 10k&Omega;)</div>
  <div class="legend-item"><div class="legend-swatch" style="background:var(--c-good)"></div>Good (DC=All connected, AC &gt; 10k&Omega;)</div>
  <div class="legend-item"><div class="legend-swatch" style="background:var(--c-bad)"></div>Bad (Canal / Reference / GND lost / Floating)</div>
</div>

<!-- Internal Users section -->
<div class="section-header">
  <h2>&#x1F4BB; Internal Users</h2>
  <span class="section-meta" id="internal-meta"></span>
</div>
<div class="section-summary" id="internal-summary"></div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Left Ear (14-day Avg.)</th>
        <th>Right Ear (14-day Avg.)</th>
        <th class="center">Past 14 Days Tracking</th>
      </tr>
    </thead>
    <tbody id="internal-body"></tbody>
  </table>
</div>

<!-- External VIP Users section -->
<div class="section-header" style="margin-top:44px;">
  <h2>&#x1F31F; External Power Users (VIPs)</h2>
  <span class="section-meta" id="external-meta"></span>
</div>
<div class="section-summary" id="external-summary"></div>
<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Left Ear (14-day Avg.)</th>
        <th>Right Ear (14-day Avg.)</th>
        <th class="center">Past 14 Days Tracking</th>
      </tr>
    </thead>
    <tbody id="external-body"></tbody>
  </table>
</div>

<!-- Modal -->
<div class="modal-overlay" id="modal-overlay" onclick="closeModalOnBg(event)">
  <div class="modal" id="modal">
    <div class="modal-header">
      <div>
        <div class="modal-title" id="modal-title"></div>
        <div class="modal-subtitle">Most recent 14 usage days &mdash; unified 0&ndash;12h x-axis &mdash; minute offset from first event each night</div>
      </div>
      <button class="copy-img-btn" id="copy-img-btn" onclick="copyModalAsImage()">Copy as Image</button>
      <button class="modal-close" onclick="closeModal()">&times;</button>
    </div>
    <div class="modal-legend">
      <div class="modal-legend-item"><div class="ml-swatch" style="background:var(--c-strong)"></div>Strong (AC &le;10k&Omega;)</div>
      <div class="modal-legend-item"><div class="ml-swatch" style="background:var(--c-good)"></div>Good (AC &gt;10k&Omega;)</div>
      <div class="modal-legend-item"><div class="ml-swatch" style="background:var(--c-bad)"></div>Bad (Canal / Reference / GND lost / Floating)</div>
      <div class="modal-legend-item"><div class="ml-swatch null-swatch"></div>No DC status</div>
    </div>
    <div id="modal-body"></div>
  </div>
</div>

<script>
const INTERNAL_DATA = """ + internal_json + """;
const EXTERNAL_DATA = """ + external_json + """;
const DAILY_DATA    = """ + daily_json + """;
const TODAY_STR     = '""" + today_str + """';

const MAX_MINUTE = 720;
const X_TICKS  = [0,120,240,360,480,600,720];
const X_LABELS = ['0h','2h','4h','6h','8h','10h','12h'];
// Track ~530px; show AC label if block >= 15px
const AC_LABEL_PCT_THRESHOLD = (15 / 530) * 100;

let threshold = 30;

function setThreshold(val) {
  threshold = val;
  document.querySelectorAll('.threshold-btn').forEach(function(b) {
    b.classList.toggle('active', parseInt(b.dataset.val) === val);
  });
  render();
}

// Last-3-days time-weighted risk % (uses DAILY_DATA timelines)
function riskPct3d(name) {
  var ud = DAILY_DATA[name];
  if (!ud || !ud.daily.length) return 0;
  var days3 = ud.daily.slice(-3);
  var lBad=0, lTotal=0, rBad=0, rTotal=0;
  days3.forEach(function(day) {
    var l = timeBreakdown(day.left);
    var r = timeBreakdown(day.right);
    lBad   += (l.byStatus['floating']||0) + (l.byStatus['bad']||0);
    lTotal += l.total;
    rBad   += (r.byStatus['floating']||0) + (r.byStatus['bad']||0);
    rTotal += r.total;
  });
  var lPct = lTotal ? lBad/lTotal*100 : 0;
  var rPct = rTotal ? rBad/rTotal*100 : 0;
  return Math.max(lPct, rPct);
}

function riskPct3dEars(name) {
  var ud = DAILY_DATA[name];
  if (!ud || !ud.daily.length) return {l:0, r:0};
  var days3 = ud.daily.slice(-3);
  var lBad=0, lTotal=0, rBad=0, rTotal=0;
  days3.forEach(function(day) {
    var l = timeBreakdown(day.left);
    var r = timeBreakdown(day.right);
    lBad   += (l.byStatus['floating']||0) + (l.byStatus['bad']||0);
    lTotal += l.total;
    rBad   += (r.byStatus['floating']||0) + (r.byStatus['bad']||0);
    rTotal += r.total;
  });
  return {l: lTotal ? lBad/lTotal*100 : 0, r: rTotal ? rBad/rTotal*100 : 0};
}

function userBreakdown3d(name) {
  var ud = DAILY_DATA[name];
  if (!ud) return null;
  var days3 = ud.daily.slice(-3);
  var left  = {total:0, byStatus:{}, byDC:{}};
  var right = {total:0, byStatus:{}, byDC:{}};
  days3.forEach(function(day) {
    mergeBreakdown(left,  timeBreakdown(day.left));
    mergeBreakdown(right, timeBreakdown(day.right));
  });
  return {left: left, right: right};
}

function riskPct(u) {
  if (u.no_data) return null;
  if (DAILY_DATA[u.name]) return riskPct3d(u.name);
  // fallback to aggregate if no timeline data
  var l = (u.l_floating||0) + u.l_bad;
  var r = (u.r_floating||0) + u.r_bad;
  return Math.max(l, r);
}

function isAtRisk(u) {
  var rp = riskPct(u);
  return rp !== null && rp > threshold;
}

// ── Copy user_id to clipboard ─────────────────────────────────────────────────

function copyUid(btn) {
  var uid = btn.dataset.uid;
  var write = function() {
    btn.innerHTML = '&#x2713;';
    setTimeout(function() { btn.innerHTML = '&#x1F4CB;'; }, 1500);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(uid).then(write).catch(write);
  } else {
    var ta = document.createElement('textarea');
    ta.value = uid;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    write();
  }
}

function openModalFromBtn(event) {
  openModal(event.currentTarget.dataset.name);
}

function makeNameCell(u) {
  var uid = u.user_id || '';
  var email = u.email || '';
  return '<td class="name-cell">' +
    '<div class="user-name">' + u.name + '</div>' +
    (email ? '<div class="user-email">' + email + '</div>' : '') +
    (uid ? '<div class="user-id-row"><span class="user-id">' + uid + '</span>' +
           '<button class="copy-btn" data-uid="' + uid + '" onclick="copyUid(this)">&#x1F4CB;</button></div>' : '') +
    '</td>';
}

// ── Stacked bars ──────────────────────────────────────────────────────────────

function makeBar(u) {
  var segs = [
    {cls:'strong', val:u.l_strong},
    {cls:'good',   val:u.l_good},
    {cls:'bad',    val:(u.l_floating||0) + (u.l_bad||0)},
  ];
  return '<div class="bar-track">' + segs.map(function(s) {
    if (!s.val || s.val <= 0) return '';
    var lbl = s.val > 8 ? s.val.toFixed(1) + '%' : '';
    return '<div class="bar-seg ' + s.cls + '" style="width:' + s.val + '%" title="' + s.cls + ': ' + s.val.toFixed(1) + '%">' + lbl + '</div>';
  }).join('') + '</div>';
}

function makeBarRight(u) {
  var segs = [
    {cls:'strong', val:u.r_strong},
    {cls:'good',   val:u.r_good},
    {cls:'bad',    val:(u.r_floating||0) + (u.r_bad||0)},
  ];
  return '<div class="bar-track">' + segs.map(function(s) {
    if (!s.val || s.val <= 0) return '';
    var lbl = s.val > 8 ? s.val.toFixed(1) + '%' : '';
    return '<div class="bar-seg ' + s.cls + '" style="width:' + s.val + '%" title="' + s.cls + ': ' + s.val.toFixed(1) + '%">' + lbl + '</div>';
  }).join('') + '</div>';
}

// ── DC breakdown helpers ──────────────────────────────────────────────────────

// Time-weighted breakdown across a single timeline array
function timeBreakdown(tl) {
  var byStatus = {}, byDC = {}, total = 0;
  if (!tl) return {total:0, byStatus:byStatus, byDC:byDC};
  for (var i = 0; i < tl.length; i++) {
    var e = tl[i];
    if (e.status === '__end__') continue;
    var nextMin = (i + 1 < tl.length) ? Math.min(tl[i+1].minute, MAX_MINUTE) : MAX_MINUTE;
    var span = Math.max(nextMin - e.minute, 0);
    byStatus[e.status] = (byStatus[e.status] || 0) + span;
    if (e.dc) byDC[e.dc] = (byDC[e.dc] || 0) + span;
    total += span;
  }
  return {total: total, byStatus: byStatus, byDC: byDC};
}

function mergeBreakdown(a, b) {
  a.total += b.total;
  Object.keys(b.byStatus).forEach(function(k){ a.byStatus[k] = (a.byStatus[k]||0) + b.byStatus[k]; });
  Object.keys(b.byDC).forEach(function(k){ a.byDC[k] = (a.byDC[k]||0) + b.byDC[k]; });
}

function abbreviateDC(dc) {
  if (!dc) return '';
  if (dc.indexOf('Canal') >= 0)     return 'Canal lost';
  if (dc.indexOf('Reference') >= 0) return 'Reference lost';
  if (dc.indexOf('GND') >= 0)       return 'GND lost';
  if (dc === 'Floating')             return 'Floating';
  return dc;
}

// Aggregate all days for one user into {left, right} breakdowns
function userBreakdown(name) {
  var ud = DAILY_DATA[name];
  if (!ud) return null;
  var left  = {total:0, byStatus:{}, byDC:{}};
  var right = {total:0, byStatus:{}, byDC:{}};
  ud.daily.forEach(function(day) {
    mergeBreakdown(left,  timeBreakdown(day.left));
    mergeBreakdown(right, timeBreakdown(day.right));
  });
  return {left: left, right: right};
}

// Returns dominant issue string for one ear's breakdown, or '' if clean
function dominantIssue(bd) {
  if (!bd.total) return '';
  var floatMins = bd.byStatus['floating'] || 0;
  var badMins   = bd.byStatus['bad']      || 0;
  if (!floatMins && !badMins) return '';
  var floatPct = floatMins / bd.total * 100;
  var badPct   = badMins   / bd.total * 100;
  if (floatPct >= badPct) {
    return '<span class="rp-float">Floating ' + floatPct.toFixed(0) + '%</span>';
  }
  // find dominant bad sub-type
  var topDC = '', topMins = 0;
  Object.keys(bd.byDC).forEach(function(dc) {
    if (dc !== 'Floating' && bd.byDC[dc] > topMins) { topMins = bd.byDC[dc]; topDC = dc; }
  });
  var lbl = topDC ? abbreviateDC(topDC) : 'Bad';
  return '<span class="rp-bad">' + lbl + ' ' + badPct.toFixed(0) + '%</span>';
}

function makeAtRiskReason(u) {
  if (u.no_data || !isAtRisk(u)) return '';
  var dcb = userBreakdown3d(u.name);
  if (!dcb) return '';
  var ears = riskPct3dEars(u.name);
  var lRisk = ears.l > threshold;
  var rRisk = ears.r > threshold;
  var earLabel = (lRisk && rRisk) ? 'Both ears' : lRisk ? 'L ear' : 'R ear';
  var bd;
  if (lRisk && rRisk) {
    bd = {total: dcb.left.total + dcb.right.total, byStatus:{}, byDC:{}};
    mergeBreakdown(bd, dcb.left);
    mergeBreakdown(bd, dcb.right);
  } else {
    bd = lRisk ? dcb.left : dcb.right;
  }
  var issue = dominantIssue(bd);
  return '<div class="risk-reason">' + earLabel + (issue ? ' \xB7 ' + issue : '') +
         ' <span style="color:var(--text-sub)">(3d)</span></div>';
}

// Daily summary pills for modal
function makeDailySummaryPills(leftTl, rightTl) {
  var parts = [];
  [{side:'L', tl:leftTl}, {side:'R', tl:rightTl}].forEach(function(e) {
    var bd = timeBreakdown(e.tl);
    if (!bd.total) return;
    var pills = [];
    var fPct = ((bd.byStatus['floating'] || 0) / bd.total) * 100;
    if (fPct >= 2) pills.push('<span class="ds-pill floating">Floating ' + fPct.toFixed(0) + '%</span>');
    // bad sub-types
    Object.keys(bd.byDC).forEach(function(dc) {
      if (dc === 'Floating') return;
      var pct = (bd.byDC[dc] / bd.total) * 100;
      if (pct >= 2) pills.push('<span class="ds-pill bad">' + abbreviateDC(dc) + ' ' + pct.toFixed(0) + '%</span>');
    });
    if (pills.length) {
      parts.push('<span class="ds-ear">' + e.side + '</span>' + pills.join(''));
    }
  });
  if (!parts.length) return '';
  return '<span class="day-summary">' + parts.join('<span class="ds-sep">|</span>') + '</span>';
}

function atRiskBadge(u) {
  if (u.no_data) return '<span class="badge-nodata">&mdash;</span>';
  var hasModal = !!DAILY_DATA[u.name];
  var dataAttr = hasModal ? ' data-name="' + u.name.replace(/"/g, '&quot;') + '" onclick="openModalFromBtn(event)"' : '';
  var tip = hasModal ? ' title="Click to view timeline"' : '';

  var extras = '';
  if (u.to_date) {
    var toDate = new Date(u.to_date + 'T12:00:00');
    var today  = new Date(TODAY_STR   + 'T12:00:00');
    var daysSince = (today - toDate) / (1000*60*60*24);
    if (daysSince > 14) {
      extras += '<div class="warn-text dim">&#x23F0; No data in past 14 days &mdash; inactive?</div>';
    }
  }

  if (needsReplacement(u)) {
    extras += '<div class="warn-text red">&#x1F527; Replace sleeves/tips</div>';
  }

  if (isAtRisk(u)) {
    return '<div class="at-risk-wrap"><button class="badge-risk"' + dataAttr + tip + '>14-Day Tracking</button>' + extras + '</div>';
  }
  return '<div class="at-risk-wrap"><button class="badge-ok"' + dataAttr + tip + '>14-Day Tracking</button>' + extras + '</div>';
}

// Compute % of good events with AC > 30kΩ across last 3 usage days from timeline
function highAcLast3d(name) {
  var ud = DAILY_DATA[name];
  if (!ud || !ud.daily.length) return {l:0, r:0};
  var days3 = ud.daily.slice(-3);
  var lGoodTotal=0, lGoodHigh=0, rGoodTotal=0, rGoodHigh=0;
  days3.forEach(function(day) {
    [day.left, day.right].forEach(function(tl, idx) {
      if (!tl) return;
      for (var i=0; i<tl.length; i++) {
        var e = tl[i];
        if (e.status === 'good' || e.status === 'strong') {
          if (idx === 0) { lGoodTotal++; if (e.ac && e.ac > 30000) lGoodHigh++; }
          else           { rGoodTotal++; if (e.ac && e.ac > 30000) rGoodHigh++; }
        }
      }
    });
  });
  return {
    l: lGoodTotal ? lGoodHigh/lGoodTotal*100 : 0,
    r: rGoodTotal ? rGoodHigh/rGoodTotal*100 : 0
  };
}

const REPLACE_THRESHOLD = 70;

// Returns daily sessions to evaluate for replacement flag:
// - If a replacement event exists within the past 14 days, return only sessions AFTER it.
// - Otherwise return all sessions (full 14-day window).
function postReplacementDays(name) {
  var ud = DAILY_DATA[name];
  if (!ud) return null;
  var today     = new Date(TODAY_STR + 'T12:00:00');
  var cutoff14  = new Date(today.getTime() - 14 * 24 * 3600 * 1000);
  var cutStr    = cutoff14.toISOString().slice(0, 10);
  var reps      = (ud.replacements || []).slice().sort();
  var recent    = null;
  for (var i = reps.length - 1; i >= 0; i--) {
    if (reps[i] >= cutStr) { recent = reps[i]; break; }
  }
  if (recent) return ud.daily.filter(function(d) { return d.date > recent; });
  return ud.daily;
}

function needsReplacement(u) {
  if (u.no_data) return false;
  var days = postReplacementDays(u.name);
  if (days && days.length) {
    var lBad=0, lTotal=0, rBad=0, rTotal=0;
    var lGoodTotal=0, lGoodHigh=0, rGoodTotal=0, rGoodHigh=0;
    days.forEach(function(day) {
      var l = timeBreakdown(day.left);
      var r = timeBreakdown(day.right);
      lBad   += (l.byStatus['floating']||0) + (l.byStatus['bad']||0);
      lTotal += l.total;
      rBad   += (r.byStatus['floating']||0) + (r.byStatus['bad']||0);
      rTotal += r.total;
      [day.left, day.right].forEach(function(tl, idx) {
        if (!tl) return;
        for (var i = 0; i < tl.length; i++) {
          var e = tl[i];
          if (e.status === 'good' || e.status === 'strong') {
            if (idx === 0) { lGoodTotal++; if (e.ac && e.ac > 30000) lGoodHigh++; }
            else           { rGoodTotal++; if (e.ac && e.ac > 30000) rGoodHigh++; }
          }
        }
      });
    });
    var lDcPct = lTotal ? lBad/lTotal*100 : 0;
    var rDcPct = rTotal ? rBad/rTotal*100 : 0;
    var lAcPct = lGoodTotal ? lGoodHigh/lGoodTotal*100 : 0;
    var rAcPct = rGoodTotal ? rGoodHigh/rGoodTotal*100 : 0;
    return Math.max(lDcPct, rDcPct) >= REPLACE_THRESHOLD ||
           Math.max(lAcPct, rAcPct) >= REPLACE_THRESHOLD;
  }
  // Fallback for users without timeline data: use 14-day aggregates
  var l = (u.l_floating||0) + (u.l_bad||0);
  var r = (u.r_floating||0) + (u.r_bad||0);
  return Math.max(l, r) >= REPLACE_THRESHOLD ||
         Math.max(u.l_high_ac||0, u.r_high_ac||0) >= REPLACE_THRESHOLD;
}

function makeSummaryCell(u) {
  if (u.no_data) return '<td class="summary-cell"></td>';
  var html = makeAtRiskReason(u);

  // Elevated impedance: only warn if high AC persists in last 3 usage days
  // If AC recovered to green recently = electrode replaced = no warning
  var hac = highAcLast3d(u.name);
  if (hac.l > 30 || hac.r > 30) {
    var earLabel = (hac.l > 30 && hac.r > 30) ? 'Both ears' : (hac.l > 30 ? 'L ear' : 'R ear');
    html += '<div class="warn-text orange">&#9888; ' + earLabel + ': Elevated impedance (&gt;30k&Omega;) &mdash; check electrode</div>';
  }

  // No recent data
  if (u.to_date) {
    var toDate = new Date(u.to_date + 'T12:00:00');
    var today  = new Date(TODAY_STR  + 'T12:00:00');
    if ((today - toDate) / (1000*60*60*24) > 14) {
      html += '<div class="warn-text dim">&#x23F0; No data in past 14 days &mdash; inactive?</div>';
    }
  }

  return '<td class="summary-cell">' + html + '</td>';
}

function makeTableRows(tableData) {
  return tableData.map(function(u) {
    var lCell, rCell;
    if (u.no_data) {
      var nb = '<div class="bar-nodata">No data in past 90 days</div>';
      lCell = '<td class="bar-cell">' + nb + '</td>';
      rCell = '<td class="bar-cell">' + nb + '</td>';
    } else {
      lCell = '<td class="bar-cell">' + makeBar(u) + '</td>';
      rCell = '<td class="bar-cell">' + makeBarRight(u) + '</td>';
    }
    return '<tr>' + makeNameCell(u) + lCell + rCell +
           '<td class="at-risk">' + atRiskBadge(u) + '</td>' + '</tr>';
  }).join('');
}

function makeSummaryCards(tableData, labelPrefix) {
  var active  = tableData.filter(function(u){return !u.no_data;});
  var noData  = tableData.filter(function(u){return u.no_data;});
  var risk    = active.filter(isAtRisk);
  var healthy = active.filter(function(u){return !isAtRisk(u);});
  var riskTooltip = risk.length
    ? '<div class="card-tooltip"><div class="tip-title">At-Risk Users</div>' +
      risk.map(function(u){
        var hasModal = !!DAILY_DATA[u.name];
        var dataAttr = hasModal ? ' data-name="' + u.name.replace(/"/g, '&quot;') + '" onclick="openModal(this.dataset.name)"' : '';
        var title = hasModal ? ' title="Click to view 14-Day Tracking"' : '';
        return '<span class="tip-name"' + dataAttr + title + '>' + u.name + (hasModal ? ' &#x2197;' : '') + '</span>';
      }).join('') + '</div>'
    : '';
  var healthyTooltip = healthy.length
    ? '<div class="card-tooltip"><div class="tip-title">Healthy Users</div>' +
      healthy.map(function(u){
        var hasModal = !!DAILY_DATA[u.name];
        var dataAttr = hasModal ? ' data-name="' + u.name.replace(/"/g, '&quot;') + '" onclick="openModal(this.dataset.name)"' : '';
        var title = hasModal ? ' title="Click to view 14-Day Tracking"' : '';
        return '<span class="tip-name"' + dataAttr + title + '>' + u.name + (hasModal ? ' &#x2197;' : '') + '</span>';
      }).join('') + '</div>'
    : '';
  return '<div class="card"><div class="card-label">Total Users</div><div class="card-value">' + tableData.length + '</div><div class="card-sub">' + active.length + ' active &middot; ' + noData.length + ' no data</div></div>' +
    '<div class="card warn"><div class="card-label">At-Risk (&gt;&nbsp;' + threshold + '%)</div><div class="card-value">' + risk.length + '</div><div class="card-sub">' + (active.length ? Math.round(risk.length/active.length*100) : 0) + '% of active</div>' + riskTooltip + '</div>' +
    '<div class="card good"><div class="card-label">Healthy</div><div class="card-value">' + (active.length - risk.length) + '</div><div class="card-sub">floating+bad &le; ' + threshold + '%</div>' + healthyTooltip + '</div>';
}

function render() {
  document.getElementById('internal-body').innerHTML   = makeTableRows(INTERNAL_DATA);
  document.getElementById('external-body').innerHTML   = makeTableRows(EXTERNAL_DATA);
  document.getElementById('internal-summary').innerHTML = makeSummaryCards(INTERNAL_DATA, 'Internal');
  document.getElementById('external-summary').innerHTML = makeSummaryCards(EXTERNAL_DATA, 'VIP');
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function fmtMinute(m) {
  var h = Math.floor(m / 60);
  var min = m % 60;
  return h > 0 ? h + 'h ' + (min > 0 ? min + 'm' : '') : min + 'm';
}

function formatDate(dateStr) {
  var d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'});
}

function renderTimeline(events) {
  if (!events || !events.length) {
    return '<div style="height:36px;display:flex;align-items:center;padding:0 10px;font-size:11px;color:rgba(255,255,255,0.3);font-style:italic;">No data</div>';
  }

  var acIdx = 0;
  return events.map(function(e, i) {
    var nextMin  = i < events.length - 1 ? Math.min(events[i+1].minute, MAX_MINUTE) : MAX_MINUTE;
    var startMin = Math.min(e.minute, MAX_MINUTE);
    var leftPct  = (startMin / MAX_MINUTE) * 100;
    var widthPct = Math.max(((nextMin - startMin) / MAX_MINUTE) * 100, 0.2);

    var acNum = (e.ac !== null && e.ac !== undefined) ? (e.ac / 1000).toFixed(1) : null;
    var tip = '';
    if (e.status === 'null') {
      tip = 'No DC status logged';
    } else if (e.status !== '__end__') {
      var label = (e.dc && (e.status === 'bad' || e.status === 'floating')) ? e.dc : e.status;
      tip = label + (acNum ? ' | AC=' + acNum + 'k\u03A9' : '') + ' | @' + fmtMinute(e.minute);
    }

    var labelHtml = '';
    if (widthPct >= AC_LABEL_PCT_THRESHOLD && acNum !== null &&
        e.status !== '__end__' && e.status !== 'null') {
      var pos = (acIdx % 2 === 0) ? 'top' : 'bottom';
      labelHtml = '<span class="ac-label ' + pos + '">' + acNum + '</span>';
      acIdx++;
    }

    return '<div class="tl-seg ' + e.status + '" style="left:' + leftPct.toFixed(2) + '%;width:' + widthPct.toFixed(2) + '%"' +
           (tip ? ' title="' + tip + '"' : '') + '>' + labelHtml + '</div>';
  }).join('');
}

function renderXAxis() {
  var html = '<div class="x-axis">';
  for (var i = 0; i < X_TICKS.length; i++) {
    var pct = (X_TICKS[i] / MAX_MINUTE) * 100;
    html += '<span class="x-tick" style="left:' + pct + '%">' + X_LABELS[i] + '</span>';
  }
  html += '</div>';
  return html;
}

function openModal(name) {
  var userData = DAILY_DATA[name];
  if (!userData) return;

  document.getElementById('modal-title').textContent = name + "'s Impedance Timeline (14 Usage Days)";

  var html = '';
  var days = userData.daily;
  var replacements = userData.replacements || [];

  if (!days || !days.length) {
    html = '<div style="color:var(--text-dim);font-style:italic;padding:20px 0">No daily data available.</div>';
  } else {
    days.forEach(function(day) {
      var replaced = replacements.indexOf(day.date) !== -1;
      html += '<div class="day-row">' +
        '<div class="day-label-row">' +
          '<span class="day-label">' + formatDate(day.date) + '</span>' +
          '<span style="font-weight:400;color:var(--text-sub);font-size:10px">(' + day.date + ')</span>' +
          (replaced ? '<span class="rep-pill">&#x1F504; Tips &amp; Wings Replaced</span>' : '') +
          makeDailySummaryPills(day.left, day.right) +
        '</div>' +
        '<div class="ears-wrap">' +
          '<div><div class="ear-label">Left Ear</div>' +
            '<div class="timeline-outer">' +
              '<div class="timeline-track">' + renderTimeline(day.left) + '</div>' +
              renderXAxis() +
            '</div>' +
          '</div>' +
          '<div><div class="ear-label">Right Ear</div>' +
            '<div class="timeline-outer">' +
              '<div class="timeline-track">' + renderTimeline(day.right) + '</div>' +
              renderXAxis() +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    });
  }

  document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal-overlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function copyModalAsImage() {
  var btn = document.getElementById('copy-img-btn');
  var modal = document.getElementById('modal');
  btn.textContent = 'Copying...';
  html2canvas(modal, {
    backgroundColor: '#1a1a20',
    scale: 2,
    useCORS: true,
    logging: false
  }).then(function(canvas) {
    canvas.toBlob(function(blob) {
      var item = new ClipboardItem({'image/png': blob});
      navigator.clipboard.write([item]).then(function() {
        btn.textContent = 'Copied! \u2713';
        btn.classList.add('copied');
        setTimeout(function() {
          btn.textContent = 'Copy as Image';
          btn.classList.remove('copied');
        }, 2000);
      }).catch(function() {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'timeline.png';
        a.click();
        URL.revokeObjectURL(url);
        btn.textContent = 'Copy as Image';
      });
    });
  });
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.body.style.overflow = '';
}

function closeModalOnBg(e) {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}

document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeModal(); });

render();
</script>
</body>
</html>"""

out = ROOT / "index.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

size = os.path.getsize(out)
print(f"Written: {out} ({size/1024:.1f} KB)")
