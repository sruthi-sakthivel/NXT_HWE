"""
Longitudinal impedance report — per-user start dates, all sessions to today.
  JB    : from 2026-06-02
  Akshay: from 2026-05-21
  Tae   : from 2026-05-21
  Duy   : from 2026-05-21
  Chris : from 2026-05-21
Writes frontend/wear_time_data_longitudinal.json as a list (preserves order).
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from integrations.mixpanel_client import MixpanelClient

AC_STRONG_THRESHOLD    = 10_000
GOOD_HIGH_AC_THRESHOLD = 30_000
MAX_MINUTE             = 720

# Fetch from one day before the earliest start date; no USAGE_DAYS cap.
FETCH_FROM = "2026-05-20"
FETCH_TO   = "2026-07-02"

PST       = timezone(timedelta(hours=-8))
REPO_ROOT = Path(__file__).resolve().parents[1]  # wear_time/

USERS = [
    {"name": "JB",    "user_id":  "5G4E3DXnV2iNf1lUI3VF",            "start_date": "2026-06-02"},
    {"name": "Akshay","user_id":  "aCkZozcQSvbreWqzOZOB",             "start_date": "2026-05-21"},
    {"name": "Tae",   "user_id":  "JCpuDm5iLqOtwevfcwvdTk6fc7u1",    "start_date": "2026-05-21"},
    {"name": "Duy",   "user_ids": ["G5GtDIfe6xQFjUKT3SCfNohMqBA2",
                                   "UH36z0Ijs3SkjaOdP1bzgBsyl5S2"],   "start_date": "2026-05-21"},
    {"name": "Chris", "user_id":  "3TatWqtmBaMpUlBWiTI6bQQBzs53",    "start_date": "2026-05-21"},
]

QUALITY_STATUSES = ["strong", "good", "floating", "bad"]


def _to_float(v):
    if v is None or v == "<null>":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(dc_status, ac_value):
    if dc_status is None or dc_status == "<null>":
        return "null"
    if dc_status == "All electrodes connected":
        return "strong" if (ac_value is not None and ac_value <= AC_STRONG_THRESHOLD) else "good"
    if dc_status == "Floating":
        return "floating"
    return "bad"


def _zero_counts():
    return {s: 0 for s in QUALITY_STATUSES + ["null"]}


def _to_pct(counts):
    n = sum(counts[s] for s in QUALITY_STATUSES)
    if n == 0:
        return {s: 0.0 for s in QUALITY_STATUSES}
    return {s: round(counts[s] / n * 100, 1) for s in QUALITY_STATUSES}


def _parse_sleep_sessions(events, user_id_set):
    sessions = {}
    for evt in events:
        if evt.get("event") != "sleep_session_end":
            continue
        props = evt.get("properties", {})
        uid = props.get("$user_id")
        if uid not in user_id_set:
            continue
        sid = props.get("sessionId")
        if not sid:
            continue
        onset_str = props.get("sleepOnSet")
        onset_ts = None
        if onset_str and onset_str != "<null>":
            try:
                onset_dt = datetime.fromisoformat(onset_str).replace(tzinfo=timezone.utc)
                onset_ts = onset_dt.timestamp()
            except (ValueError, TypeError):
                pass
        cal_day = props.get("calendarDay")
        date_str = cal_day[:10] if (cal_day and cal_day != "<null>") else None
        sessions[(uid, sid)] = {"onset_ts": onset_ts, "date_str": date_str}
    return sessions


def _group_by_session(imp_events, user_id_set):
    grouped = defaultdict(list)
    for evt in imp_events:
        props = evt.get("properties", {})
        uid = props.get("$user_id")
        if uid not in user_id_set:
            continue
        sid = props.get("session_id")
        if not sid:
            continue
        left_dc  = props.get("left_dc_impedance_check_status")
        right_dc = props.get("right_dc_impedance_check_status")
        if left_dc  == "<null>": left_dc  = None
        if right_dc == "<null>": right_dc = None
        grouped[(uid, sid)].append({
            "ts":       props.get("time", 0),
            "left_dc":  left_dc,
            "right_dc": right_dc,
            "left_ac":  _to_float(props.get("left_ac_impedance_value")),
            "right_ac": _to_float(props.get("right_ac_impedance_value")),
        })
    return grouped


def _build_timeline(evts_sorted, onset_ts):
    if not evts_sorted:
        return [], []
    left_tl, right_tl = [], []
    seen_l, seen_r = {}, {}
    for e in evts_sorted:
        minute = round((e["ts"] - onset_ts) / 60)
        if minute < 0:
            continue
        if minute > MAX_MINUTE:
            break
        ls = _classify(e["left_dc"],  e["left_ac"])
        rs = _classify(e["right_dc"], e["right_ac"])
        if minute not in seen_l or seen_l[minute] != ls:
            entry = {"minute": minute, "status": ls,
                     "ac": round(e["left_ac"]) if e["left_ac"] is not None else None}
            if ls in ("bad", "floating") and e["left_dc"]:
                entry["dc"] = e["left_dc"]
            left_tl.append(entry)
            seen_l[minute] = ls
        if minute not in seen_r or seen_r[minute] != rs:
            entry = {"minute": minute, "status": rs,
                     "ac": round(e["right_ac"]) if e["right_ac"] is not None else None}
            if rs in ("bad", "floating") and e["right_dc"]:
                entry["dc"] = e["right_dc"]
            right_tl.append(entry)
            seen_r[minute] = rs
    for tl in (left_tl, right_tl):
        if tl:
            last = tl[-1]
            sentinel = min(last["minute"] + 30, MAX_MINUTE)
            if sentinel > last["minute"]:
                tl.append({"minute": sentinel, "status": "__end__", "ac": None})
    return left_tl, right_tl


def query_all(users):
    uid_to_name = {}
    name_to_start = {}
    for u in users:
        uids = u.get("user_ids") or [u["user_id"]]
        for uid in uids:
            uid_to_name[uid] = u["name"]
        name_to_start[u["name"]] = u["start_date"]
    user_id_set = set(uid_to_name)

    client = MixpanelClient()

    print(f"Fetching impedance_measurements {FETCH_FROM} to {FETCH_TO} ...")
    imp_events = client._export_events(["impedance_measurements"], FETCH_FROM, FETCH_TO)
    print(f"  {len(imp_events)} impedance events exported")

    print(f"Fetching sleep_session_end {FETCH_FROM} to {FETCH_TO} ...")
    end_events = client._export_events(["sleep_session_end"], FETCH_FROM, FETCH_TO)
    print(f"  {len(end_events)} sleep_session_end events exported")

    sleep_sessions = _parse_sleep_sessions(end_events, user_id_set)
    print(f"  {len(sleep_sessions)} sessions matched to users")

    print(f"Fetching tips_wings_replaced {FETCH_FROM} to {FETCH_TO} ...")
    rep_events = client._export_events(["tips_wings_replaced"], FETCH_FROM, FETCH_TO)
    print(f"  {len(rep_events)} replacement events exported")

    replacements_by_uid = defaultdict(set)
    for evt in rep_events:
        props = evt.get("properties", {})
        uid = props.get("$user_id")
        if uid not in user_id_set:
            continue
        rd = props.get("replacement_date")
        if rd and rd != "<null>":
            replacements_by_uid[uid].add(rd[:10])

    grouped = _group_by_session(imp_events, user_id_set)

    name_to_uids = defaultdict(list)
    for uid, name in uid_to_name.items():
        name_to_uids[name].append(uid)

    results = []
    for u in users:
        name  = u["name"]
        uids  = u.get("user_ids") or [u["user_id"]]
        start = name_to_start[name]

        # Collect ALL sessions on/after start_date
        all_sessions = []
        for uid in uids:
            for (usr, sid), evts in grouped.items():
                if usr != uid:
                    continue
                info = sleep_sessions.get((uid, sid))
                if info and info["date_str"] and info["onset_ts"]:
                    date_str = info["date_str"]
                    onset_ts = info["onset_ts"]
                else:
                    first_ts = min(e["ts"] for e in evts)
                    pst_dt = datetime.fromtimestamp(first_ts, tz=PST)
                    date_str = pst_dt.strftime("%Y-%m-%d")
                    onset_ts = first_ts
                if start <= date_str <= FETCH_TO:
                    all_sessions.append((uid, sid, date_str, onset_ts))

        if not all_sessions:
            results.append({"name": name, "start_date": start, "no_data": True})
            continue

        all_sessions.sort(key=lambda x: x[3])

        left_acc  = _zero_counts()
        right_acc = _zero_counts()
        lg_total = 0; lg_high = 0
        rg_total = 0; rg_high = 0
        daily = []

        for uid, sid, date_str, onset_ts in all_sessions:
            evts = sorted(grouped[(uid, sid)], key=lambda e: e["ts"])
            left_tl, right_tl = _build_timeline(evts, onset_ts)

            for e in evts:
                minute = round((e["ts"] - onset_ts) / 60)
                if minute < 0: continue
                if minute > MAX_MINUTE: break
                ls = _classify(e["left_dc"],  e["left_ac"])
                rs = _classify(e["right_dc"], e["right_ac"])
                left_acc[ls]  += 1
                right_acc[rs] += 1
                if ls == "good":
                    lg_total += 1
                    if e["left_ac"] is not None and e["left_ac"] > GOOD_HIGH_AC_THRESHOLD:
                        lg_high += 1
                if rs == "good":
                    rg_total += 1
                    if e["right_ac"] is not None and e["right_ac"] > GOOD_HIGH_AC_THRESHOLD:
                        rg_high += 1

            daily.append({"date": date_str, "left": left_tl, "right": right_tl})

        total_events = sum(left_acc[s] for s in QUALITY_STATUSES + ["null"])
        dates = [d["date"] for d in daily]

        all_replacements: set = set()
        for uid in uids:
            all_replacements.update(replacements_by_uid.get(uid, set()))

        results.append({
            "name":       name,
            "start_date": start,
            "no_data":    False,
            "events":     total_events,
            "from_date":  dates[0],
            "to_date":    dates[-1],
            "quality": {
                "left":  _to_pct(left_acc),
                "right": _to_pct(right_acc),
            },
            "high_ac_left_pct":  round(lg_high / lg_total * 100, 1) if lg_total else 0.0,
            "high_ac_right_pct": round(rg_high / rg_total * 100, 1) if rg_total else 0.0,
            "daily":        daily,
            "replacements": sorted(all_replacements),
        })

    return results


def print_results(results):
    hdr = (f"{'Name':<8} {'Sessions':>8}  "
           f"{'L-Str':>6} {'L-Gd':>6} {'L-Flt':>6} {'L-Bd':>5}  "
           f"{'R-Str':>6} {'R-Gd':>6} {'R-Flt':>6} {'R-Bd':>5}  At-Risk")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        if r.get("no_data"):
            print(f"{r['name']:<8} {'no data':>8}")
            continue
        l, rv = r["quality"]["left"], r["quality"]["right"]
        l_risk = l["floating"] + l["bad"]
        r_risk = rv["floating"] + rv["bad"]
        flag = "YES" if l_risk > 30 or r_risk > 30 else ""
        print(f"{r['name']:<8} {len(r['daily']):>8}  "
              f"{l['strong']:>5.1f}% {l['good']:>5.1f}% {l['floating']:>5.1f}% {l['bad']:>4.1f}%  "
              f"{rv['strong']:>5.1f}% {rv['good']:>5.1f}% {rv['floating']:>5.1f}% {rv['bad']:>4.1f}%  {flag}")


if __name__ == "__main__":
    results = query_all(USERS)
    print(f"\n-- Longitudinal Report ({FETCH_FROM} to {FETCH_TO}) --")
    print_results(results)

    out_path = REPO_ROOT / "wear_time_data_longitudinal.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, separators=(",", ":"))
    print(f"\nData saved to: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
