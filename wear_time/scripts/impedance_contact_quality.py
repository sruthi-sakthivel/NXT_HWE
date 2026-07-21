"""
Impedance contact quality analyzer.

Groups impedance_measurements by sleep session (via session_id matching
sleep_session_end.sessionId), not calendar date.  This avoids the
midnight-split problem where a 10pm-6am session would be cut into two
calendar days.

Each session's x-axis minute 0 = sleepOnSet from sleep_session_end.
If no matching sleep_session_end exists, falls back to first impedance
event timestamp.  The "date" label uses calendarDay from
sleep_session_end (which encodes the user's local midnight in UTC).

4-status contact quality classification (per ear):
  strong   -> DC="All electrodes connected" AND ac <= 10kOhm
  good     -> DC="All electrodes connected" AND ac >  10kOhm
  floating -> DC="Floating"
  bad      -> Canal/Reference/GND lost connection
  null     -> DC status absent/null (shown as blank in timeline)

Usage:
    python working_scripts/scripts/impedance_contact_quality.py
    -> writes frontend/wear_time_data.json
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from integrations.mixpanel_client import MixpanelClient

AC_STRONG_THRESHOLD   = 10_000
GOOD_HIGH_AC_THRESHOLD = 30_000  # warn when good events avg >30kΩ
MAX_MINUTE             = 720     # clip timeline at 12h
USAGE_DAYS             = 14      # most recent unique calendar days to show per user
LOOKBACK_DAYS          = 90      # how many calendar days back to query from Mixpanel

PST      = timezone(timedelta(hours=-8))
REPO_ROOT = Path(__file__).resolve().parents[1]  # wear_time/

INTERNAL_USERS = [
    {"name": "Tae",             "user_id": "JCpuDm5iLqOtwevfcwvdTk6fc7u1"},
    {"name": "Nhan",            "user_ids": ["UUQiOO5Abfck4cYi4NGVTCV8KHv1", "TrO02SvpM3X3OFBVejKzWj03vZ62"]},
    {"name": "Robert",          "user_id": "xdDHJZRgEHYlms26yAsiw0TcCny1"},
    {"name": "Gillian",         "user_id": "0mmzi0FwbmYFlUyllvxkw92hLnQ2"},
    {"name": "Chris",           "user_id": "3TatWqtmBaMpUlBWiTI6bQQBzs53"},
    {"name": "Eric",            "user_id": "Qtu8MR1DjbFEROIZe1Ee"},
    {"name": "Devansh",         "user_id": "yw3E9lJ8dPTeu9DJikP7mSc34BH3"},
    {"name": "Ilsim",           "user_id": "sxXWrv8EYIPhkZe4XebC5bLaRbL2"},
    {"name": "Clayton",         "user_id": "3ha0rwXpYtNm8GzeAltwi0BmJDZ2"},
    {"name": "Ankit",           "user_id": "dAHpK6u2yabUJe7OvfOgfg1hTNo2"},
    {"name": "Duy",             "user_ids": ["G5GtDIfe6xQFjUKT3SCfNohMqBA2", "UH36z0Ijs3SkjaOdP1bzgBsyl5S2"]},
    {"name": "Pranav",          "user_id": "4WbObLuGzFdEXeP4iTOuqAlZIqg2"},
    {"name": "Shekhar",         "user_id": "KkrPvE04gueEgVtoU6zgcfT7bP23"},
    {"name": "Akshay",          "user_id": "aCkZozcQSvbreWqzOZOB"},
    {"name": "Caitlin",         "user_id": "ZRAqCK4Vb3tdYe6Tbq9U"},
    {"name": "Mayur",           "user_id": "YACQnWhzloSzp2iHFQXC"},
    {"name": "Bridgitte",       "user_id": "YC9CAxKduFgzLNCrjQiW1cHziTf1"},
    {"name": "JB",              "user_id": "5G4E3DXnV2iNf1lUI3VF"},
    {"name": "Nivi",            "user_id": "xMymgzQ3krPmSbUwy614bpNQZY23"},
    {"name": "KB",              "user_id": "HEh1IZPaCFMeHJv8ImLT7QaJX8f2"},
    {"name": "Jason Bach (JB)", "user_id": "r4NHdzUcoEeGgbRcVFMWiwxn6723"},
    {"name": "Sruthi",          "user_id": "b3759mWLKuYIoQiikFyssNU7cxp2"},
]

EXTERNAL_USERS = [
    {"name": "Abrar Haroon",        "user_id": "7Jy4mC1u9lhMeQgCL95ad93lgDP2"},
    {"name": "Allan Levey",         "user_id": "ZRdXN3blZiP9zzcU6fniYHBVJxh1"},
    {"name": "Andy Kurtzig",        "user_id": "rkiVholKZtgYaRVe8MlhnSMDOSG3"},
    {"name": "Craig Forman",        "user_id": "gMIZG5f9lHWOkyYjV8xq6QhAscS2"},
    {"name": "Esther Dyson",        "user_id": "EWSBYa5GUHXXKLVcZWtbyvHAZ183"},
    {"name": "Jamie Evarts",        "user_id": "Gbq8ByQQKfRVAUFMCMhOXpHSVUn2"},
    {"name": "Jefferson Terkhorn",  "user_id": "mAGGKa2R90ZRXDxfAtrdwkA0gqQ2"},
    {"name": "Jonathan Cheng",      "user_id": "LunaEA99PUPlQWmBOwV0um6a5YQ2"},
    {"name": "Michael Coates",      "user_id": "0DC4BbXM6UOr93opFOiRUyS6ioJ3"},
    {"name": "Pamela York",         "user_id": "1Qg6CMSmHTOciqQOaOMCXdYBwHw2"},
    {"name": "R C",                 "user_id": "MXCGWiu71TSWCUtz1jbpviF90vq2"},
    {"name": "Seal",                "user_id": "J775bD3UCDbU4QJ9zuN0MdacEco2"},
    {"name": "TMAC",                "user_id": "7sTwtsTG6oOYKhtQhNJQoUMW68E3"},
    {"name": "Tyler",               "user_id": "IzWXcMOt68QdgmEsFAqduhvMUGr1"},
    {"name": "Zack Varner",         "user_id": "mGvDNVAVbxbpQCTCJlctSuqwCSl2"},
]

QUALITY_STATUSES = ["strong", "good", "floating", "bad"]  # null excluded from %


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(v):
    if v is None or v == "<null>":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify(dc_status, ac_value):
    """4-status classifier. Returns null for missing DC status."""
    if dc_status is None or dc_status == "<null>":
        return "null"
    if dc_status == "All electrodes connected":
        if ac_value is not None and ac_value <= AC_STRONG_THRESHOLD:
            return "strong"
        return "good"
    if dc_status == "Floating":
        return "floating"
    # Canal / Reference hook / GND lost connection → bad
    return "bad"


def _pst_date_range(days):
    """Return (from_date, to_date) including today."""
    now       = datetime.now(PST)
    to_date   = now                          # include today
    from_date = to_date - timedelta(days=days - 1)
    return from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")


def _zero_counts():
    return {s: 0 for s in QUALITY_STATUSES + ["null"]}


def _to_pct(counts):
    """Percentages over quality statuses only (exclude null from denominator)."""
    n = sum(counts[s] for s in QUALITY_STATUSES)
    if n == 0:
        return {s: 0.0 for s in QUALITY_STATUSES}
    return {s: round(counts[s] / n * 100, 1) for s in QUALITY_STATUSES}


# ── Core processing ───────────────────────────────────────────────────────────

def _parse_sleep_sessions(events, user_id_set):
    """Parse sleep_session_end events into a lookup: (uid, sessionId) -> info.

    Each entry has:
      onset_ts  - unix timestamp of sleepOnSet (minute 0 for x-axis)
      date_str  - local date derived from calendarDay
    """
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

        # Parse sleepOnSet (UTC ISO string like "2026-03-25T07:40:17")
        onset_str = props.get("sleepOnSet")
        onset_ts = None
        if onset_str and onset_str != "<null>":
            try:
                onset_dt = datetime.fromisoformat(onset_str).replace(tzinfo=timezone.utc)
                onset_ts = onset_dt.timestamp()
            except (ValueError, TypeError):
                pass

        # Derive local date from calendarDay (encodes local midnight in UTC)
        # e.g. "2026-03-25T07:00:00" means PST midnight = March 25 local
        # e.g. "2026-03-25T04:00:00" means EST midnight = March 25 local
        cal_day = props.get("calendarDay")
        date_str = None
        if cal_day and cal_day != "<null>":
            # calendarDay is always "YYYY-MM-DDT..." — the date part IS the local date
            date_str = cal_day[:10]

        sessions[(uid, sid)] = {
            "onset_ts": onset_ts,
            "date_str": date_str,
        }
    return sessions


def _group_by_session(imp_events, user_id_set):
    """Group impedance_measurements by (uid, session_id)."""
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


def _select_sessions(grouped, uid, sleep_sessions, max_days=USAGE_DAYS):
    """Select sessions from the most recent unique calendar days for a user.

    Selects by unique day (not session count) so that multiple sessions on
    the same night don't crowd out more recent days. Returns all sessions
    that fall within the most recent max_days unique dates, sorted by onset_ts.
    """
    user_sessions = []
    for (u, sid), evts in grouped.items():
        if u != uid:
            continue
        info = sleep_sessions.get((uid, sid))
        if info and info["date_str"] and info["onset_ts"]:
            date_str = info["date_str"]
            onset_ts = info["onset_ts"]
        else:
            # Fallback: no matching sleep_session_end
            first_ts = min(e["ts"] for e in evts)
            pst_dt = datetime.fromtimestamp(first_ts, tz=PST)
            date_str = pst_dt.strftime("%Y-%m-%d")
            onset_ts = first_ts
        user_sessions.append((sid, date_str, onset_ts))

    # Find the most recent max_days unique calendar dates
    unique_dates = sorted({s[1] for s in user_sessions})
    keep_dates = set(unique_dates[-max_days:])

    # Return all sessions for those dates, in chronological order
    selected = [(sid, date_str, onset_ts)
                for sid, date_str, onset_ts in user_sessions
                if date_str in keep_dates]
    selected.sort(key=lambda x: x[2])
    return selected


def _build_timeline(evts_sorted, onset_ts):
    """Deduplicated (minute, status, ac) timeline clipped at MAX_MINUTE.

    minute 0 = onset_ts (sleepOnSet from sleep_session_end, or first
    impedance event as fallback). Events before onset get negative
    minutes and are skipped. Last block extends +30 min (or to
    MAX_MINUTE) so it's visible.
    """
    if not evts_sorted:
        return [], []

    left_tl, right_tl = [], []
    seen_l, seen_r = {}, {}

    for e in evts_sorted:
        minute = round((e["ts"] - onset_ts) / 60)
        if minute < 0:
            continue   # event before sleep onset — skip
        if minute > MAX_MINUTE:
            break

        ls = _classify(e["left_dc"],  e["left_ac"])
        rs = _classify(e["right_dc"], e["right_ac"])

        if minute not in seen_l or seen_l[minute] != ls:
            entry = {
                "minute": minute,
                "status": ls,
                "ac": round(e["left_ac"]) if e["left_ac"] is not None else None,
            }
            if ls in ("bad", "floating") and e["left_dc"]:
                entry["dc"] = e["left_dc"]
            left_tl.append(entry)
            seen_l[minute] = ls

        if minute not in seen_r or seen_r[minute] != rs:
            entry = {
                "minute": minute,
                "status": rs,
                "ac": round(e["right_ac"]) if e["right_ac"] is not None else None,
            }
            if rs in ("bad", "floating") and e["right_dc"]:
                entry["dc"] = e["right_dc"]
            right_tl.append(entry)
            seen_r[minute] = rs

    # Add sentinel so last block renders with natural width (30 min cap)
    for tl in (left_tl, right_tl):
        if tl:
            last = tl[-1]
            sentinel = min(last["minute"] + 30, MAX_MINUTE)
            if sentinel > last["minute"]:
                tl.append({"minute": sentinel, "status": "__end__", "ac": None})

    return left_tl, right_tl


# ── Main query ────────────────────────────────────────────────────────────────

def query_all(users, usage_days=USAGE_DAYS):
    uid_to_name = {}
    for u in users:
        uids = u.get("user_ids") or [u["user_id"]]
        for uid in uids:
            uid_to_name[uid] = u["name"]
    user_id_set = set(uid_to_name)

    client = MixpanelClient()
    now_pst   = datetime.now(PST)
    to_date   = now_pst.strftime("%Y-%m-%d")
    from_date = (now_pst - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    print(f"Fetching impedance_measurements {from_date} to {to_date} ...")
    imp_events = client._export_events(["impedance_measurements"], from_date, to_date)
    print(f"  {len(imp_events)} impedance events exported")

    print(f"Fetching sleep_session_end {from_date} to {to_date} ...")
    end_events = client._export_events(["sleep_session_end"], from_date, to_date)
    print(f"  {len(end_events)} sleep_session_end events exported")

    sleep_sessions = _parse_sleep_sessions(end_events, user_id_set)
    print(f"  {len(sleep_sessions)} sessions matched to users")

    print(f"Fetching tips_wings_replaced {from_date} to {to_date} ...")
    rep_events = client._export_events(["tips_wings_replaced"], from_date, to_date)
    print(f"  {len(rep_events)} replacement events exported")

    # Build per-uid replacement date lists
    replacements_by_uid = defaultdict(set)
    for evt in rep_events:
        props = evt.get("properties", {})
        uid = props.get("$user_id")
        if uid not in user_id_set:
            continue
        rd = props.get("replacement_date")
        if rd and rd != "<null>":
            replacements_by_uid[uid].add(rd[:10])  # "YYYY-MM-DD"

    grouped = _group_by_session(imp_events, user_id_set)
    results = {}

    # Group user_ids by name so multi-account users are merged into one entry
    name_to_uids = defaultdict(list)
    for uid, name in uid_to_name.items():
        name_to_uids[name].append(uid)

    for name, uids in name_to_uids.items():
        # Collect sessions from all user_ids for this person, then pick the
        # most recent usage_days unique calendar dates across all accounts.
        all_sessions = []
        for uid in uids:
            for sid, date_str, onset_ts in _select_sessions(
                    grouped, uid, sleep_sessions, usage_days * len(uids)):
                all_sessions.append((uid, sid, date_str, onset_ts))

        if not all_sessions:
            continue

        unique_dates = sorted({s[2] for s in all_sessions})
        keep_dates   = set(unique_dates[-usage_days:])
        selected     = [(uid, sid, date_str, onset_ts)
                        for uid, sid, date_str, onset_ts in all_sessions
                        if date_str in keep_dates]
        selected.sort(key=lambda x: x[3])

        left_acc  = _zero_counts()
        right_acc = _zero_counts()
        left_good_total  = 0;  left_good_high_ac  = 0
        right_good_total = 0;  right_good_high_ac = 0
        daily     = []

        for uid, sid, date_str, onset_ts in selected:
            evts = sorted(grouped[(uid, sid)], key=lambda e: e["ts"])
            left_tl, right_tl = _build_timeline(evts, onset_ts)

            for e in evts:
                minute = round((e["ts"] - onset_ts) / 60)
                if minute < 0:
                    continue
                if minute > MAX_MINUTE:
                    break
                ls = _classify(e["left_dc"],  e["left_ac"])
                rs = _classify(e["right_dc"], e["right_ac"])
                left_acc[ls]  += 1
                right_acc[rs] += 1
                if ls == "good":
                    left_good_total += 1
                    if e["left_ac"] is not None and e["left_ac"] > GOOD_HIGH_AC_THRESHOLD:
                        left_good_high_ac += 1
                if rs == "good":
                    right_good_total += 1
                    if e["right_ac"] is not None and e["right_ac"] > GOOD_HIGH_AC_THRESHOLD:
                        right_good_high_ac += 1

            daily.append({"date": date_str, "left": left_tl, "right": right_tl})

        total_events = sum(left_acc[s] for s in QUALITY_STATUSES + ["null"])
        dates = [d["date"] for d in daily]

        all_replacements: set = set()
        for uid in uids:
            all_replacements.update(replacements_by_uid.get(uid, set()))

        results[name] = {
            "events":    total_events,
            "from_date": dates[0] if dates else "",
            "to_date":   dates[-1] if dates else "",
            "quality": {
                "left":  _to_pct(left_acc),
                "right": _to_pct(right_acc),
            },
            "high_ac_left_pct":  round(left_good_high_ac  / left_good_total  * 100, 1) if left_good_total  else 0.0,
            "high_ac_right_pct": round(right_good_high_ac / right_good_total * 100, 1) if right_good_total else 0.0,
            "daily": daily,
            "replacements": sorted(all_replacements),
        }

    return results


# ── Print table ───────────────────────────────────────────────────────────────

def print_results(users, results):
    hdr = (f"{'Name':<16} {'Events':>6}  "
           f"{'L-Str':>6} {'L-Gd':>6} {'L-Flt':>6} {'L-Bd':>5}  "
           f"{'R-Str':>6} {'R-Gd':>6} {'R-Flt':>6} {'R-Bd':>5}  At-Risk")
    print(hdr)
    print("-" * len(hdr))
    for u in users:
        name = u["name"]
        if name not in results:
            print(f"{name:<16} {'no data':>6}")
            continue
        d = results[name]
        l, r = d["quality"]["left"], d["quality"]["right"]
        l_risk = l["floating"] + l["bad"]
        r_risk = r["floating"] + r["bad"]
        flag = "YES" if l_risk > 30 or r_risk > 30 else ""
        print(f"{name:<16} {d['events']:>6}  "
              f"{l['strong']:>5.1f}% {l['good']:>5.1f}% {l['floating']:>5.1f}% {l['bad']:>4.1f}%  "
              f"{r['strong']:>5.1f}% {r['good']:>5.1f}% {r['floating']:>5.1f}% {r['bad']:>4.1f}%  {flag}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    all_users = INTERNAL_USERS + EXTERNAL_USERS
    all_results = query_all(all_users)

    # Split results by group
    internal_names = {u["name"] for u in INTERNAL_USERS}
    external_names = {u["name"] for u in EXTERNAL_USERS}
    internal_results = {n: v for n, v in all_results.items() if n in internal_names}
    external_results = {n: v for n, v in all_results.items() if n in external_names}

    print("\n-- Internal Users --")
    print_results(INTERNAL_USERS, internal_results)
    print("\n-- External VIP Users --")
    print_results(EXTERNAL_USERS, external_results)

    out_path = REPO_ROOT / "wear_time_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"internal": internal_results, "external": external_results},
                  f, separators=(",", ":"))
    print(f"\nData saved to: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
