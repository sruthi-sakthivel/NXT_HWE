"""
Mixpanel API client for product usage and sleep analytics.

Business rules from mixpanel.agent.md:
  - Rule 1: Exclude internal accounts (email domains + Internal cohort)
  - Rule 2: Per-user segmentation uses properties["$user_id"] (NOT property)
  - Rule 3: Property variant discovery — scan first 50 events
  - Rule 4: Unit conversion — minutes vs seconds variants
  - Rule 5: DAU = yesterday (today incomplete)
  - Rule 6: Timezone = PST for all date ranges
  - Rule 7: Audio stimulation detection — property + event cross-ref

Auth: Basic Auth with base64(API_SECRET + ":")
Rate limit: 60 queries/hour — budget carefully.

NOTE: Requires MIXPANEL_PROJECT_ID and MIXPANEL_API_SECRET in .env.
      Credentials also live in dashboard/config.py (PROJECT_ID=3865773).
"""

import base64
import json
import logging
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from .base_client import BaseClient, load_env

log = logging.getLogger(__name__)

PST_OFFSET = timezone(timedelta(hours=-8))

# ──────────────────────────────────────────────
# Internal account exclusion (global Rule 8 + mixpanel Rule 1)
# ──────────────────────────────────────────────

INTERNAL_DOMAINS = ["@nextsense.com", "@test.com", "@example.com"]

# WHERE clause for segmentation / retention queries
# NOTE: Mixpanel's `has` keyword does NOT work in the API WHERE clause.
# Use `"value" in user["prop"]` with `not()` for string containment checks.
WHERE_EXCLUDE_INTERNAL = (
    'not ("@nextsense.com" in user["$email"]) '
    'and not ("@test.com" in user["$email"]) '
    'and not ("@example.com" in user["$email"]) '
    'and user["user_cohort"] != "Internal"'
)

# WHERE clause to select ONLY internal accounts (inverse of above)
WHERE_INCLUDE_INTERNAL = (
    '("@nextsense.com" in user["$email"]) '
    'or ("@test.com" in user["$email"]) '
    'or ("@example.com" in user["$email"]) '
    'or user["user_cohort"] == "Internal"'
)

# ──────────────────────────────────────────────
# Metric definitions (self-contained, sourced from dashboard/config.py)
# ──────────────────────────────────────────────

METRIC_DEFINITIONS = {
    "total_sleep_hours": {
        "variants": [
            "totalSleepMinutes", "total_sleep_minutes",
            "totalSleepSeconds", "total_sleep_seconds",
        ],
        "display_name": "Total Sleep Time",
        "display_unit": "hours",
        "minutes_variants": {"totalSleepMinutes", "total_sleep_minutes"},
    },
    "deep_sleep_hours": {
        "variants": [
            "totalDeepSleepMinutes", "deep_sleep_minutes",
            "deep_sleep_seconds",
        ],
        "display_name": "Deep Sleep",
        "display_unit": "hours",
        "minutes_variants": {"totalDeepSleepMinutes", "deep_sleep_minutes"},
    },
    "rem_sleep_hours": {
        "variants": [
            "totalREMMinutes", "rem_sleep_minutes",
            "rem_sleep_seconds",
        ],
        "display_name": "REM Sleep",
        "display_unit": "hours",
        "minutes_variants": {"totalREMMinutes", "rem_sleep_minutes"},
    },
    "light_sleep_hours": {
        "variants": [
            "totalLightSleepMinutes", "light_sleep_minutes",
            "light_sleep_seconds",
        ],
        "display_name": "Light Sleep",
        "display_unit": "hours",
        "minutes_variants": {"totalLightSleepMinutes", "light_sleep_minutes"},
    },
    "waso_minutes": {
        "variants": [
            "wasoMinutes", "waso_minutes",
            "wasoSeconds", "waso_seconds",
        ],
        "display_name": "WASO",
        "display_unit": "minutes",
        "minutes_variants": {"wasoMinutes", "waso_minutes"},
    },
    "latency_minutes": {
        "variants": [
            "driftOffMinutes", "drift_off_minutes",
            "sleepLatencySeconds", "sleep_latency_seconds",
        ],
        "display_name": "Sleep Latency",
        "display_unit": "minutes",
        "minutes_variants": {"driftOffMinutes", "drift_off_minutes"},
    },
    "slow_wave_count": {
        "variants": [
            "slowWaveCount", "slow_wave_count",
            "stimulation_count", "sws_count",
        ],
        "display_name": "Slow Wave Count",
        "display_unit": "count",
        "minutes_variants": set(),
    },
    "sleep_score": {
        "variants": ["sleep_score", "sleepScore"],
        "display_name": "Sleep Score",
        "display_unit": "score",
        "minutes_variants": set(),
    },
    "sleep_efficiency": {
        "variants": ["sleep_efficiency", "sleepEfficiency"],
        "display_name": "Sleep Efficiency",
        "display_unit": "%",
        "minutes_variants": set(),
    },
}

# ──────────────────────────────────────────────
# Event lists (from mixpanel.agent.md)
# ──────────────────────────────────────────────

ENGAGEMENT_EVENTS = [
    "enter_home_screen", "enter_home_screen_sleep", "enter_audio_library",
    "enter_trends", "enter_visualization", "enter_settings",
    "enter_device_settings", "enter_sleep_options", "enter_sleep_schedule",
    "enter_my_smartbuds", "play_audio_manually", "stop_audio_manually",
    "stream_audio", "start_sound_loop",
]


class MixpanelClient(BaseClient):
    """Client for the Mixpanel Analytics API.

    Handles auth, rate limiting, and business rule application for
    product usage metrics (DAU, sleep sessions, sleep quality, engagement).

    Uses two base URLs:
      - Standard: https://mixpanel.com/api/2.0 (segmentation, engage, retention)
      - Export:   https://data.mixpanel.com/api/2.0 (raw event export, JSONL)
    """

    STANDARD_BASE = "https://mixpanel.com/api/2.0"
    EXPORT_BASE = "https://data.mixpanel.com/api/2.0"

    def __init__(
        self,
        project_id: Optional[str] = None,
        api_secret: Optional[str] = None,
    ):
        """
        Args:
            project_id: Mixpanel project ID. If None, loads from .env.
            api_secret: Mixpanel API secret. If None, loads from .env.
        """
        load_env()
        self.project_id = project_id or os.environ.get("MIXPANEL_PROJECT_ID", "")
        self.api_secret = api_secret or os.environ.get("MIXPANEL_API_SECRET", "")

        if not self.project_id or not self.api_secret:
            raise ValueError(
                "Mixpanel credentials not found. Set MIXPANEL_PROJECT_ID and "
                "MIXPANEL_API_SECRET in .env or pass directly.\n"
                "Credentials also live in dashboard/config.py (PROJECT_ID=3865773)."
            )

        # Auth: Basic Auth with base64(API_SECRET + ":")
        encoded = base64.b64encode(f"{self.api_secret}:".encode()).decode()
        headers = {"Authorization": f"Basic {encoded}"}

        # Rate limit: 60 queries/hour → 2s delay (conservative floor)
        super().__init__(
            base_url=self.STANDARD_BASE,
            headers=headers,
            rate_limit_delay=2.0,
        )

    # ──────────────────────────────────────────────
    # Date helpers (PST timezone, Rule 5 + 6)
    # ──────────────────────────────────────────────

    def _pst_yesterday(self) -> str:
        """Yesterday's date in PST, as YYYY-MM-DD. Rule 5: today is incomplete."""
        now_pst = datetime.now(PST_OFFSET)
        yesterday = now_pst - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d")

    def _pst_date_range(self, days: int) -> tuple[str, str]:
        """(from_date, to_date) for the last N days in PST.

        to_date = yesterday (today incomplete).
        from_date = yesterday - (days - 1).
        """
        now_pst = datetime.now(PST_OFFSET)
        to_date = now_pst - timedelta(days=1)
        from_date = to_date - timedelta(days=days - 1)
        return from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")

    # ──────────────────────────────────────────────
    # API query helpers
    # ──────────────────────────────────────────────

    def _segmentation_query(
        self,
        event: str,
        from_date: str,
        to_date: str,
        unit: str = "day",
        query_type: str = "general",
        per_user: bool = False,
    ) -> dict:
        """Run a segmentation query with internal account exclusion.

        Args:
            event: Event name to query.
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).
            unit: Aggregation unit (day, week, month).
            query_type: "general" for counts, "unique" for unique users.
            per_user: If True, break down by user_id (Rule 2).

        Returns:
            The 'values' dict from the response, or empty dict on failure.
        """
        params = {
            "project_id": self.project_id,
            "from_date": from_date,
            "to_date": to_date,
            "event": event,
            "unit": unit,
            "type": query_type,
            "where": WHERE_EXCLUDE_INTERNAL,
        }
        if per_user:
            # Rule 2: MUST be properties["$user_id"], NOT property["$user_id"]
            params["on"] = 'properties["$user_id"]'

        data = self.get("/segmentation", params=params)
        if data:
            return data.get("data", {}).get("values", {})
        return {}

    def _export_events(
        self,
        event_names: list[str],
        from_date: str,
        to_date: str,
    ) -> list[dict]:
        """Fetch raw events via Export API (streaming JSONL).

        Uses the separate data.mixpanel.com base URL.
        Gotcha: Export returns JSONL (one JSON per line), NOT a JSON array.
        """
        url = f"{self.EXPORT_BASE}/export"
        # NOTE: Do NOT pass project_id to the Export API — it returns 400
        # "Parameter project_id is only allowed when authenticating with a service account"
        # The API secret in the auth header already identifies the project.
        params = {
            "from_date": from_date,
            "to_date": to_date,
            "event": json.dumps(event_names),
        }

        # Pass full URL + stream=True via **kwargs to BaseClient
        resp = self._request_with_retry(
            "GET", url, params=params, stream=True, timeout=120
        )
        if not resp or resp.status_code != 200:
            return []

        events = []
        for line in resp.iter_lines():
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        log.info(f"Exported {len(events)} events for {event_names}")
        return events

    # ──────────────────────────────────────────────
    # Property discovery + unit conversion (Rules 3, 4)
    # ──────────────────────────────────────────────

    def _discover_properties(self, events: list[dict]) -> dict[str, str]:
        """Scan first 50 events to find which property variant names exist.

        Rule 3: Property names change between firmware versions (camelCase
        vs snake_case, minutes vs seconds). Always discover before calculating.

        Returns:
            Dict mapping metric_key → actual_property_name.
            e.g. {"total_sleep_hours": "total_sleep_seconds", ...}
        """
        all_keys: set[str] = set()
        for evt in events[:50]:
            all_keys.update(evt.get("properties", {}).keys())

        discovered: dict[str, str] = {}
        for metric_key, defn in METRIC_DEFINITIONS.items():
            for variant in defn["variants"]:
                if variant in all_keys:
                    discovered[metric_key] = variant
                    break

        log.info(f"Discovered properties: {discovered}")
        return discovered

    def _convert_value(
        self, val, metric_key: str, found_property_name: str
    ) -> Optional[float]:
        """Convert a raw property value to the metric's display unit.

        Rule 4:
          - minutes variant + hours display → / 60
          - seconds variant + hours display → / 3600
          - seconds variant + minutes display → / 60
          - count, score, % → no conversion
        """
        try:
            val = float(val)
        except (ValueError, TypeError):
            return None
        if val < 0:
            return None

        defn = METRIC_DEFINITIONS[metric_key]
        unit = defn["display_unit"]
        is_minutes = found_property_name in defn["minutes_variants"]

        if unit == "hours":
            return val / 60.0 if is_minutes else val / 3600.0
        if unit == "minutes":
            return val if is_minutes else val / 60.0
        # count, score, % — no conversion
        return val

    def _filter_internal_event(self, event: dict) -> bool:
        """Return True if the event belongs to an internal account (should be excluded).

        Post-fetch filter for Export API data (WHERE clause not available on export).
        """
        props = event.get("properties", {})
        email = (props.get("$email") or "").lower()
        user_cohort = props.get("user_cohort", "")

        if user_cohort == "Internal":
            return True
        for domain in INTERNAL_DOMAINS:
            if email.endswith(domain):
                return True
        return False

    # ──────────────────────────────────────────────
    # Public methods: Product metrics
    # ──────────────────────────────────────────────

    def _count_active_users(self, from_date: str, to_date: str,
                             min_events: int = 3) -> dict[str, int]:
        """Count events per user via Export API, returning users with >= min_events.

        Used by get_dau() and get_mau() to implement the '>= 3 events' threshold
        from mixpanel.agent.md.

        Args:
            from_date: Start date (YYYY-MM-DD).
            to_date: End date (YYYY-MM-DD).
            min_events: Minimum event count to qualify as active (default 3).

        Returns:
            Dict mapping user_id → event count (only users meeting threshold).
        """
        # Export all events for the period — uses the Export API (streaming JSONL)
        url = f"{self.EXPORT_BASE}/export"
        params = {
            "from_date": from_date,
            "to_date": to_date,
        }

        resp = self._request_with_retry(
            "GET", url, params=params, stream=True, timeout=180
        )
        if not resp or resp.status_code != 200:
            log.error(f"Export failed for active user count ({from_date} to {to_date})")
            return {}

        # Count events per user, filtering internal accounts inline
        user_counts: dict[str, int] = {}
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Filter internal accounts
            if self._filter_internal_event(event):
                continue

            # Get user ID
            props = event.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            if user_id:
                user_counts[user_id] = user_counts.get(user_id, 0) + 1

        # Filter to users with >= min_events
        active = {uid: count for uid, count in user_counts.items()
                  if count >= min_events}

        log.info(f"Active users ({from_date} to {to_date}): "
                 f"{len(active)} with >= {min_events} events "
                 f"(out of {len(user_counts)} total users)")
        return active

    def get_dau(self, date: Optional[str] = None) -> dict:
        """Daily Active Users for yesterday (or specified date).

        Rule 5: Today is incomplete — always use yesterday.
        DAU = unique users with >= 3 events yesterday (mixpanel.agent.md definition).

        Args:
            date: Optional date override (YYYY-MM-DD). Defaults to yesterday PST.

        Returns:
            {"dau": int, "date": str, "threshold": int}
        """
        target_date = date or self._pst_yesterday()
        active = self._count_active_users(target_date, target_date, min_events=3)
        return {"dau": len(active), "date": target_date, "threshold": 3}

    def get_mau(self) -> dict:
        """Monthly Active Users for the last 30 days, with MoM%.

        MAU = unique users with >= 3 events in last 30 days (mixpanel.agent.md definition).

        Returns:
            {"mau": int, "prev_mau": int, "mom_pct": float,
             "from_date": str, "to_date": str, "threshold": int}
        """
        from_date, to_date = self._pst_date_range(30)
        active = self._count_active_users(from_date, to_date, min_events=3)
        mau = len(active)

        # Previous 30-day window for MoM%
        prev_to_dt = datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=1)
        prev_from_dt = prev_to_dt - timedelta(days=29)
        prev_from = prev_from_dt.strftime("%Y-%m-%d")
        prev_to = prev_to_dt.strftime("%Y-%m-%d")
        prev_active = self._count_active_users(prev_from, prev_to, min_events=3)
        prev_mau = len(prev_active)

        mom_pct = ((mau - prev_mau) / prev_mau * 100) if prev_mau > 0 else 0.0

        return {
            "mau": mau, "prev_mau": prev_mau, "mom_pct": round(mom_pct, 1),
            "from_date": from_date, "to_date": to_date, "threshold": 3,
        }

    def get_sleep_sessions(self, days: int = 7) -> dict:
        """Count of sleep_session_started events for the period.

        Args:
            days: Number of days to look back (default 7).

        Returns:
            {"total_sessions": int, "daily": dict, "from_date", "to_date", "period_days"}
        """
        from_date, to_date = self._pst_date_range(days)
        values = self._segmentation_query(
            "sleep_session_started", from_date, to_date
        )

        total = 0
        daily: dict[str, int] = {}
        for event_data in values.values():
            for date_str, count in event_data.items():
                total += count
                daily[date_str] = daily.get(date_str, 0) + count

        return {
            "total_sessions": total,
            "daily": daily,
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    def get_sleep_quality(self, days: int = 7) -> dict:
        """Average sleep metrics from session_statistics events.

        Uses Export API + property discovery (Rule 3) + unit conversion (Rule 4).
        Excludes internal accounts post-fetch.

        Args:
            days: Number of days to look back (default 7).

        Returns:
            Dict with averages for each discovered metric (sleep_score,
            total_sleep_hours, deep_sleep_hours, etc.) plus metadata.
        """
        from_date, to_date = self._pst_date_range(days)
        log.info(f"Fetching sleep quality: {from_date} to {to_date}")

        events = self._export_events(["session_statistics"], from_date, to_date)

        # Post-fetch: filter internal accounts
        events = [e for e in events if not self._filter_internal_event(e)]

        if not events:
            return {
                "error": "No session_statistics events found",
                "from_date": from_date,
                "to_date": to_date,
                "period_days": days,
            }

        # Property discovery (Rule 3)
        discovered = self._discover_properties(events)

        # Extract and convert values (Rule 4)
        metric_values: dict[str, list[float]] = {k: [] for k in METRIC_DEFINITIONS}
        for evt in events:
            props = evt.get("properties", {})
            for metric_key, prop_name in discovered.items():
                raw_val = props.get(prop_name)
                if raw_val is not None:
                    converted = self._convert_value(raw_val, metric_key, prop_name)
                    if converted is not None:
                        metric_values[metric_key].append(converted)

        # Compute averages
        averages: dict = {}
        for metric_key, vals in metric_values.items():
            if vals:
                avg = sum(vals) / len(vals)
                averages[metric_key] = round(avg, 2)
                averages[f"{metric_key}_count"] = len(vals)

        averages["total_events"] = len(events)
        averages["discovered_properties"] = discovered
        averages["from_date"] = from_date
        averages["to_date"] = to_date
        averages["period_days"] = days
        return averages

    def get_engagement_summary(self, days: int = 7) -> dict:
        """Count of 14 engagement events, broken down by event name.

        NOTE: Makes 14 segmentation calls (one per event). That is 14 out of
        the 60/hr rate limit budget. For rate-limit-sensitive workflows,
        consider using _export_events() with all 14 event names in a single
        call and counting client-side (1 API call but more data transfer).

        Args:
            days: Number of days to look back (default 7).

        Returns:
            Dict with total count, per-event breakdown, and top 5 features.
        """
        from_date, to_date = self._pst_date_range(days)
        breakdown: dict[str, int] = {}
        total = 0

        for event_name in ENGAGEMENT_EVENTS:
            values = self._segmentation_query(event_name, from_date, to_date)
            event_total = 0
            for event_data in values.values():
                event_total += sum(event_data.values())
            breakdown[event_name] = event_total
            total += event_total

        # Sort by count descending
        sorted_events = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_engagement_events": total,
            "breakdown": dict(sorted_events),
            "top_features": sorted_events[:5],
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    def get_retention(self, weeks: int = 8) -> dict:
        """Retention curve (week over week).

        Uses the /retention endpoint with sleep_session_started as both
        the born_event and the return_event.

        Args:
            weeks: Number of weekly intervals to measure (default 8).

        Returns:
            Retention data from the API plus metadata.
        """
        to_date = self._pst_yesterday()
        from_dt = datetime.strptime(to_date, "%Y-%m-%d") - timedelta(weeks=weeks)
        from_date = from_dt.strftime("%Y-%m-%d")

        params = {
            "project_id": self.project_id,
            "from_date": from_date,
            "to_date": to_date,
            "born_event": "sleep_session_started",
            "event": "sleep_session_started",
            "interval_count": weeks,
            "unit": "week",
            "where": WHERE_EXCLUDE_INTERNAL,
        }
        data = self.get("/retention", params=params)
        if not data:
            return {"error": "Retention query failed"}

        return {
            "retention_data": data,
            "from_date": from_date,
            "to_date": to_date,
            "weeks": weeks,
        }

    def get_completion_rate(self, days: int = 7) -> dict:
        """Sleep session completion rate: ended / started.

        Args:
            days: Number of days to look back (default 7).

        Returns:
            Dict with started, completed, and completion_rate percentage.
        """
        from_date, to_date = self._pst_date_range(days)

        started_values = self._segmentation_query(
            "sleep_session_started", from_date, to_date
        )
        ended_values = self._segmentation_query(
            "sleep_session_end", from_date, to_date
        )

        started_total = sum(
            sum(v.values()) for v in started_values.values()
        )
        ended_total = sum(
            sum(v.values()) for v in ended_values.values()
        )

        rate = ended_total / started_total if started_total > 0 else 0

        return {
            "started": started_total,
            "completed": ended_total,
            "completion_rate": round(rate * 100, 1),
            "completion_rate_formatted": f"{rate:.1%}",
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    def get_active_users_trend(self, days: int = 7) -> dict:
        """DAU per day for the period (daily active user trend).

        Args:
            days: Number of days to look back (default 7).

        Returns:
            {"daily_active_users": {date: count}, "from_date", "to_date"}
        """
        from_date, to_date = self._pst_date_range(days)
        values = self._segmentation_query(
            event="enter_home_screen",
            from_date=from_date,
            to_date=to_date,
            unit="day",
            query_type="unique",
        )

        daily: dict[str, int] = {}
        for event_data in values.values():
            for date_str, count in event_data.items():
                daily[date_str] = daily.get(date_str, 0) + count

        return {
            "daily_active_users": daily,
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    # ──────────────────────────────────────────────
    # Profiles (Engage API with pagination)
    # ──────────────────────────────────────────────

    def get_profiles(self, exclude_internal: bool = True) -> list[dict]:
        """Fetch all user profiles via Engage API.

        Gotcha: session_id MUST be passed on subsequent pages.

        Args:
            exclude_internal: If True, filter out internal accounts post-fetch.

        Returns:
            List of profile dicts.
        """
        all_profiles: list[dict] = []
        session_id = None
        page = 0

        while True:
            params: dict = {
                "project_id": self.project_id,
                "page_size": 1000,
            }
            if session_id:
                params["session_id"] = session_id
                params["page"] = page

            data = self.get("/engage", params=params)
            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            all_profiles.extend(results)
            session_id = data.get("session_id")
            total = data.get("total", 0)
            log.info(f"  Profiles page {page}: {len(results)} (total={total})")

            if len(all_profiles) >= total:
                break
            page += 1

        if exclude_internal:
            all_profiles = [
                p for p in all_profiles
                if not any(
                    ((p.get("$properties") or {}).get("$email") or "")
                    .lower().endswith(d)
                    for d in INTERNAL_DOMAINS
                )
                and (p.get("$properties") or {}).get("user_cohort") != "Internal"
            ]

        log.info(f"Total profiles: {len(all_profiles)}")
        return all_profiles

    # ──────────────────────────────────────────────
    # Helpers for new metrics
    # ──────────────────────────────────────────────

    def _get_daily_user_counts(self, days: int = 14) -> dict[str, int]:
        """Export all events for N days, count active users (>=3 events) per day.

        Returns {date_str: active_user_count} for each day.
        Used by get_dau_enhanced() to compute median DAU and WoW%.
        """
        from_date, to_date = self._pst_date_range(days)

        url = f"{self.EXPORT_BASE}/export"
        params = {"from_date": from_date, "to_date": to_date}

        resp = self._request_with_retry("GET", url, params=params, stream=True, timeout=300)
        if not resp or resp.status_code != 200:
            return {}

        # {date: {user_id: event_count}}
        daily_users: dict[str, dict[str, int]] = {}
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._filter_internal_event(event):
                continue

            props = event.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            if not user_id:
                continue

            # Extract date from event time (Unix timestamp → PST date)
            ts = props.get("time", 0)
            if ts:
                evt_date = datetime.fromtimestamp(ts, tz=PST_OFFSET).strftime("%Y-%m-%d")
            else:
                continue

            if evt_date not in daily_users:
                daily_users[evt_date] = {}
            daily_users[evt_date][user_id] = daily_users[evt_date].get(user_id, 0) + 1

        # Count users with >=3 events per day
        result: dict[str, int] = {}
        for date_str, users in daily_users.items():
            result[date_str] = sum(1 for count in users.values() if count >= 3)

        log.info(f"Daily user counts for {days} days: {result}")
        return result

    def _get_user_onboarding_dates(self, from_date: str, to_date: str) -> dict[str, datetime]:
        """Export change_onboarding_completed events, return {user_id: onboarding_datetime}.

        Used by D30 retention and Time to 10 Sessions.
        """
        events = self._export_events(["change_onboarding_completed"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        onboarding_dates: dict[str, datetime] = {}
        for evt in events:
            props = evt.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if user_id and ts:
                dt = datetime.fromtimestamp(ts, tz=PST_OFFSET)
                # Keep earliest onboarding date per user
                if user_id not in onboarding_dates or dt < onboarding_dates[user_id]:
                    onboarding_dates[user_id] = dt

        log.info(f"Onboarding dates: {len(onboarding_dates)} users ({from_date} to {to_date})")
        return onboarding_dates

    def _get_qualifying_sessions(self, events: list[dict], min_hours: float = 2.0) -> list[dict]:
        """Filter session_statistics events to qualifying sleep sessions.

        Qualifying = type == 'sleep' and totalSleepMinutes >= min_hours * 60.
        Discovers properties first, then filters.

        Returns list of {user_id, timestamp, properties_dict} for qualifying sessions.
        """
        if not events:
            return []

        discovered = self._discover_properties(events)
        total_sleep_key = discovered.get("total_sleep_hours")

        qualifying = []
        for evt in events:
            props = evt.get("properties", {})

            # Filter to sleep type (not naps)
            session_type = props.get("type", "").lower()
            if session_type and session_type != "sleep":
                continue

            # Check total sleep duration
            if total_sleep_key:
                raw_val = props.get(total_sleep_key)
                converted = self._convert_value(raw_val, "total_sleep_hours", total_sleep_key)
                if converted is None or converted < min_hours:
                    continue
            else:
                continue

            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if user_id and ts:
                qualifying.append({
                    "user_id": user_id,
                    "timestamp": ts,
                    "properties": props,
                })

        return qualifying

    def _get_d30_retention(self, return_events: list[str] = None) -> dict:
        """Shared D30 retention logic with personal windows.

        Cohort: users who onboarded 23-44 days ago (so their D24-D30 window has passed).
        Retained: user had qualifying event(s) during their personal days 24-30.

        Args:
            return_events: Event names to check for retention. None = any event.

        Returns:
            {"rate": float, "retained": int, "cohort_size": int, "cohort_window": str}
        """
        now_pst = datetime.now(PST_OFFSET)
        yesterday = now_pst - timedelta(days=1)

        # Cohort window: onboarded 23-44 days ago
        cohort_end = yesterday - timedelta(days=23)
        cohort_start = yesterday - timedelta(days=44)
        cohort_from = cohort_start.strftime("%Y-%m-%d")
        cohort_to = cohort_end.strftime("%Y-%m-%d")

        # Get onboarding dates for the cohort
        onboarding_dates = self._get_user_onboarding_dates(cohort_from, cohort_to)
        if not onboarding_dates:
            return {"rate": 0.0, "retained": 0, "cohort_size": 0,
                    "cohort_window": f"{cohort_from} to {cohort_to}"}

        # Calculate the export window that covers all possible D24-D30 dates
        # Earliest onboard + 24 days → latest onboard + 30 days
        earliest_d24 = cohort_start + timedelta(days=24)
        latest_d30 = cohort_end + timedelta(days=30)
        export_from = earliest_d24.strftime("%Y-%m-%d")
        export_to = min(latest_d30, yesterday).strftime("%Y-%m-%d")

        # Export events for the retention window
        if return_events:
            events = self._export_events(return_events, export_from, export_to)
        else:
            # All events — use general export
            url = f"{self.EXPORT_BASE}/export"
            params = {"from_date": export_from, "to_date": export_to}
            resp = self._request_with_retry("GET", url, params=params, stream=True, timeout=300)
            events = []
            if resp and resp.status_code == 200:
                for line in resp.iter_lines():
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        events = [e for e in events if not self._filter_internal_event(e)]

        # Build {user_id: set of event timestamps}
        user_events: dict[str, list[float]] = {}
        for evt in events:
            props = evt.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if user_id and ts:
                if user_id not in user_events:
                    user_events[user_id] = []
                user_events[user_id].append(ts)

        # Check retention with personal windows
        retained = 0
        for user_id, onboard_dt in onboarding_dates.items():
            d24 = onboard_dt + timedelta(days=24)
            d30 = onboard_dt + timedelta(days=30)
            d24_ts = d24.timestamp()
            d30_ts = d30.timestamp() + 86400  # end of day 30

            user_ts_list = user_events.get(user_id, [])
            for ts in user_ts_list:
                if d24_ts <= ts <= d30_ts:
                    retained += 1
                    break

        cohort_size = len(onboarding_dates)
        rate = retained / cohort_size if cohort_size > 0 else 0.0

        return {
            "rate": round(rate, 4),
            "retained": retained,
            "cohort_size": cohort_size,
            "cohort_window": f"{cohort_from} to {cohort_to}",
        }

    # ──────────────────────────────────────────────
    # New public methods: Enhanced hero metrics
    # ──────────────────────────────────────────────

    def get_dau_enhanced(self) -> dict:
        """Enhanced DAU: median of 7 daily counts, WoW%, DAU/MAU ratio.

        Single 14-day export, partitioned by date client-side.

        Returns:
            {"dau": int, "prev_dau": int, "wow_pct": float,
             "dau_mau_ratio": float, "daily_counts": dict,
             "from_date": str, "to_date": str}
        """
        daily = self._get_daily_user_counts(days=14)
        if not daily:
            return {"error": "No data available for DAU calculation"}

        # Sort dates and split into current week (last 7) and previous week
        sorted_dates = sorted(daily.keys())

        # Get the last 14 dates
        from_date, to_date = self._pst_date_range(7)
        prev_from, prev_to = self._pst_date_range(14)

        current_counts = []
        prev_counts = []
        for d, count in daily.items():
            if from_date <= d <= to_date:
                current_counts.append(count)
            elif prev_from <= d < from_date:
                prev_counts.append(count)

        median_dau = int(statistics.median(current_counts)) if current_counts else 0
        prev_median = int(statistics.median(prev_counts)) if prev_counts else 0
        wow_pct = ((median_dau - prev_median) / prev_median * 100) if prev_median > 0 else 0.0

        # DAU/MAU ratio
        mau_data = self.get_mau()
        mau = mau_data.get("mau", 1)
        ratio = median_dau / mau if mau > 0 else 0.0

        return {
            "dau": median_dau,
            "prev_dau": prev_median,
            "wow_pct": round(wow_pct, 1),
            "dau_mau_ratio": round(ratio, 4),
            "daily_counts": daily,
            "mau": mau,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_wau(self) -> dict:
        """Weekly Active Users: distinct users with >=3 events in 7 days, with WoW%.

        Returns:
            {"wau": int, "prev_wau": int, "wow_pct": float, "threshold": 3}
        """
        from_date, to_date = self._pst_date_range(7)
        current_active = self._count_active_users(from_date, to_date, min_events=3)
        wau = len(current_active)

        # Previous week for WoW%
        prev_to_dt = datetime.strptime(from_date, "%Y-%m-%d") - timedelta(days=1)
        prev_from_dt = prev_to_dt - timedelta(days=6)
        prev_from = prev_from_dt.strftime("%Y-%m-%d")
        prev_to = prev_to_dt.strftime("%Y-%m-%d")
        prev_active = self._count_active_users(prev_from, prev_to, min_events=3)
        prev_wau = len(prev_active)

        wow_pct = ((wau - prev_wau) / prev_wau * 100) if prev_wau > 0 else 0.0

        return {
            "wau": wau,
            "prev_wau": prev_wau,
            "wow_pct": round(wow_pct, 1),
            "threshold": 3,
            "from_date": from_date,
            "to_date": to_date,
        }

    # ──────────────────────────────────────────────
    # New public methods: Sleep behavior metrics
    # ──────────────────────────────────────────────

    def get_weekly_active_sleepers(self) -> dict:
        """Weekly Active Sleepers: users with >=1 sleep_session_started this week / MAU.

        Returns:
            {"active_sleepers": int, "mau": int, "rate": float}
        """
        from_date, to_date = self._pst_date_range(7)

        # Get unique users who started a sleep session
        values = self._segmentation_query(
            "sleep_session_started", from_date, to_date,
            query_type="unique", per_user=True,
        )

        # Count unique user IDs from the per-user breakdown
        user_ids = set()
        for event_data in values.values():
            for user_id, count_data in event_data.items():
                if isinstance(count_data, dict):
                    if any(v > 0 for v in count_data.values()):
                        user_ids.add(user_id)
                elif count_data and count_data > 0:
                    user_ids.add(user_id)

        active_sleepers = len(user_ids)
        mau_data = self.get_mau()
        mau = mau_data.get("mau", 1)
        rate = active_sleepers / mau if mau > 0 else 0.0

        return {
            "active_sleepers": active_sleepers,
            "mau": mau,
            "rate": round(rate, 4),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_data_engagement_rate(self, days: int = 7) -> dict:
        """Data Engagement Rate: users viewing Trends/Recents out of sleep session starters.

        Denominator: users who started sleep/nap (sleepMode = Sleep or Timed Sleep).
        Numerator: of those, users who also fired enter_trends or enter_visualization.

        Note: enter_recents from spec = enter_visualization in Mixpanel.

        Returns:
            {"engaged_users": int, "sleep_users": int, "rate": float}
        """
        from_date, to_date = self._pst_date_range(days)

        # Single export for all 3 events
        events = self._export_events(
            ["sleep_session_started", "enter_trends", "enter_visualization"],
            from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        sleep_users: set[str] = set()
        data_users: set[str] = set()

        for evt in events:
            props = evt.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            if not user_id:
                continue

            event_name = evt.get("event", "")
            if event_name == "sleep_session_started":
                sleep_mode = props.get("sleepMode", "")
                if sleep_mode in ("Sleep", "Timed Sleep", ""):
                    sleep_users.add(user_id)
            elif event_name in ("enter_trends", "enter_visualization"):
                data_users.add(user_id)

        # Engaged = sleep users who also viewed data
        engaged = sleep_users & data_users
        rate = len(engaged) / len(sleep_users) if sleep_users else 0.0

        return {
            "engaged_users": len(engaged),
            "sleep_users": len(sleep_users),
            "rate": round(rate, 4),
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    def get_regular_sleepers(self, days: int = 7) -> dict:
        """Regular Sleepers: users with >=3 qualifying sleep sessions (≥90min) this week / MAU.

        Uses session_statistics with duration filter instead of raw sleep_session_started.

        Returns:
            {"regular_sleepers": int, "mau": int, "rate": float}
        """
        from_date, to_date = self._pst_date_range(days)

        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]
        qualifying = self._get_qualifying_sessions(events, min_hours=1.5)

        # Count qualifying sessions per user
        user_sessions: dict[str, int] = {}
        for s in qualifying:
            uid = s["user_id"]
            user_sessions[uid] = user_sessions.get(uid, 0) + 1

        regular = sum(1 for count in user_sessions.values() if count >= 3)
        mau_data = self.get_mau()
        mau = mau_data.get("mau", 1)
        rate = regular / mau if mau > 0 else 0.0

        return {
            "regular_sleepers": regular,
            "mau": mau,
            "rate": round(rate, 4),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_median_sleep_sessions(self, days: int = 7) -> dict:
        """Median Weekly Sleep Sessions per user.

        Population: active monthly sleepers (>=1 qualifying session in 30d).
        For each: count current-week sessions where type=sleep, duration>=90min.
        Return median of per-user counts.

        Returns:
            {"median_sessions": float, "user_count": int, "population": int}
        """
        # Get 30d population
        pop_from, pop_to = self._pst_date_range(30)
        events_30d = self._export_events(["session_statistics"], pop_from, pop_to)
        events_30d = [e for e in events_30d if not self._filter_internal_event(e)]

        qualifying_30d = self._get_qualifying_sessions(events_30d, min_hours=1.5)

        # Active monthly sleepers
        monthly_sleepers = set(s["user_id"] for s in qualifying_30d)

        if not monthly_sleepers:
            return {"median_sessions": 0.0, "user_count": 0, "population": 0}

        # Current week sessions for those users
        week_from, week_to = self._pst_date_range(days)
        week_from_ts = datetime.strptime(week_from, "%Y-%m-%d").replace(
            tzinfo=PST_OFFSET).timestamp()
        week_to_ts = (datetime.strptime(week_to, "%Y-%m-%d").replace(
            tzinfo=PST_OFFSET) + timedelta(days=1)).timestamp()

        # Count current-week qualifying sessions per monthly sleeper
        user_week_counts: dict[str, int] = {uid: 0 for uid in monthly_sleepers}
        for session in qualifying_30d:
            if session["timestamp"] >= week_from_ts and session["timestamp"] < week_to_ts:
                uid = session["user_id"]
                if uid in user_week_counts:
                    user_week_counts[uid] += 1

        counts = list(user_week_counts.values())
        median_val = statistics.median(counts) if counts else 0.0

        return {
            "median_sessions": round(median_val, 1),
            "user_count": sum(1 for c in counts if c > 0),
            "population": len(monthly_sleepers),
            "from_date": week_from,
            "to_date": week_to,
        }

    def get_sleep_adherence(self) -> dict:
        """Contiguous Sleep Adherence: users with longest streak >=3 consecutive nights / MAU.

        Uses session_statistics with ≥90min duration filter (qualifying sessions only).
        For each user: find longest streak of consecutive calendar days with a qualifying session.

        Returns:
            {"adherent_users": int, "mau": int, "rate": float}
        """
        from_date, to_date = self._pst_date_range(30)
        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]
        qualifying = self._get_qualifying_sessions(events, min_hours=1.5)

        # Collect qualifying-session calendar dates per user
        user_dates: dict[str, set[str]] = {}
        for s in qualifying:
            uid = s["user_id"]
            date_str = datetime.fromtimestamp(s["timestamp"], tz=PST_OFFSET).strftime("%Y-%m-%d")
            if uid not in user_dates:
                user_dates[uid] = set()
            user_dates[uid].add(date_str)

        # Find longest consecutive streak per user
        adherent = 0
        for user_id, dates in user_dates.items():
            sorted_dates = sorted(dates)
            if len(sorted_dates) < 3:
                continue

            max_streak = 1
            current_streak = 1
            for i in range(1, len(sorted_dates)):
                d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
                if (d2 - d1).days == 1:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 1

            if max_streak >= 3:
                adherent += 1

        mau_data = self.get_mau()
        mau = mau_data.get("mau", 1)
        rate = adherent / mau if mau > 0 else 0.0

        return {
            "adherent_users": adherent,
            "mau": mau,
            "rate": round(rate, 4),
            "from_date": from_date,
            "to_date": to_date,
        }

    # ──────────────────────────────────────────────
    # New public methods: Product health metrics
    # ──────────────────────────────────────────────

    def get_avg_time_in_app(self, days: int = 7) -> dict:
        """Average time in app from $ae_session events (Android only).

        Returns:
            {"avg_minutes": float, "session_count": int, "note": str}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["$ae_session"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        durations = []
        for evt in events:
            props = evt.get("properties", {})
            length = props.get("$ae_session_length")
            if length is not None:
                try:
                    seconds = float(length)
                    if seconds > 0:
                        durations.append(seconds / 60.0)  # convert to minutes
                except (ValueError, TypeError):
                    continue

        avg_minutes = sum(durations) / len(durations) if durations else 0.0

        return {
            "avg_minutes": round(avg_minutes, 1),
            "session_count": len(durations),
            "note": "Android only — iOS does not report $ae_session events",
            "from_date": from_date,
            "to_date": to_date,
            "period_days": days,
        }

    def get_time_to_10_sessions(self) -> dict:
        """Median days from onboarding to 10th qualifying sleep session.

        Qualifying: type=sleep, totalSleepMinutes >= 90.
        Only includes users whose 10th session was in the last 30 days.

        Returns:
            {"median_days": float, "user_count": int}
        """
        # 90-day lookback for session history
        from_date, to_date = self._pst_date_range(90)
        onboarding_dates = self._get_user_onboarding_dates(from_date, to_date)

        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]
        qualifying = self._get_qualifying_sessions(events, min_hours=1.5)

        # Group by user, sort by timestamp
        user_sessions: dict[str, list[float]] = {}
        for s in qualifying:
            uid = s["user_id"]
            if uid not in user_sessions:
                user_sessions[uid] = []
            user_sessions[uid].append(s["timestamp"])

        # Find time to 10th session
        now_pst = datetime.now(PST_OFFSET)
        thirty_days_ago_ts = (now_pst - timedelta(days=30)).timestamp()
        days_to_10: list[float] = []

        for uid, timestamps in user_sessions.items():
            if len(timestamps) < 10:
                continue
            if uid not in onboarding_dates:
                continue

            sorted_ts = sorted(timestamps)
            tenth_ts = sorted_ts[9]  # 0-indexed

            # Only include if 10th session was in the last 30 days
            if tenth_ts < thirty_days_ago_ts:
                continue

            onboard_ts = onboarding_dates[uid].timestamp()
            delta_days = (tenth_ts - onboard_ts) / 86400.0
            if delta_days > 0:
                days_to_10.append(delta_days)

        median_days = statistics.median(days_to_10) if days_to_10 else 0.0

        return {
            "median_days": round(median_days, 1),
            "user_count": len(days_to_10),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_sleep_improvement_rate(self) -> dict:
        """Sleep Improvement Rate: compare first 3 vs last 3 sessions across 5 dimensions.

        Population: users with >=3 sessions this week AND >=7 total in 90 days.
        Improved = any dimension improved >=10%.

        Dimensions: totalSleepMinutes (higher=better), driftOffMinutes (lower=better),
        wakeCount (lower=better), slowWaveCount (higher=better, exclude zeros),
        sleepEfficiency (higher=better).

        Returns:
            {"rate": float, "improved": int, "eligible": int, "dimension_breakdown": dict}
        """
        from_date, to_date = self._pst_date_range(90)
        week_from, week_to = self._pst_date_range(7)

        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]
        qualifying = self._get_qualifying_sessions(events, min_hours=2.0)

        if not qualifying:
            return {"rate": 0.0, "improved": 0, "eligible": 0, "dimension_breakdown": {}}

        # Discover properties for dimension extraction
        discovered = self._discover_properties(events)

        # Dimension config: key → (property_key_in_discovered, higher_is_better)
        dimensions = {
            "Total Sleep": ("total_sleep_hours", True),
            "Drift-off Time": ("latency_minutes", False),
            "Wake Count": ("slow_wave_count", False),  # Actually wakeCount
            "Slow Wave Count": ("slow_wave_count", True),
            "Sleep Efficiency": ("sleep_efficiency", True),
        }

        # We need raw wakeCount — discover it separately
        all_keys: set[str] = set()
        for evt in events[:50]:
            all_keys.update(evt.get("properties", {}).keys())
        wake_count_prop = None
        for variant in ["wakeCount", "wake_count"]:
            if variant in all_keys:
                wake_count_prop = variant
                break

        # Group qualifying sessions by user, sorted by time
        user_sessions: dict[str, list[dict]] = {}
        for s in qualifying:
            uid = s["user_id"]
            if uid not in user_sessions:
                user_sessions[uid] = []
            user_sessions[uid].append(s)

        for uid in user_sessions:
            user_sessions[uid].sort(key=lambda x: x["timestamp"])

        # Determine current week timestamps
        week_from_ts = datetime.strptime(week_from, "%Y-%m-%d").replace(
            tzinfo=PST_OFFSET).timestamp()

        # Eligible: >=3 sessions this week AND >=7 total
        eligible_users = []
        for uid, sessions in user_sessions.items():
            if len(sessions) < 7:
                continue
            week_count = sum(1 for s in sessions if s["timestamp"] >= week_from_ts)
            if week_count >= 3:
                eligible_users.append(uid)

        if not eligible_users:
            return {"rate": 0.0, "improved": 0, "eligible": 0, "dimension_breakdown": {}}

        # Compare first 3 vs last 3 for each eligible user
        improved_count = 0
        dim_improved: dict[str, int] = {
            "Total Sleep": 0, "Drift-off Time": 0,
            "Wake Count": 0, "Slow Wave Count": 0, "Sleep Efficiency": 0,
        }

        for uid in eligible_users:
            sessions = user_sessions[uid]
            first_3 = sessions[:3]
            last_3 = sessions[-3:]

            user_improved = False
            for dim_name, (metric_key, higher_better) in dimensions.items():
                prop_name = discovered.get(metric_key)

                # Special handling for Wake Count
                if dim_name == "Wake Count":
                    prop_name = wake_count_prop
                    if not prop_name:
                        continue

                if not prop_name:
                    continue

                first_vals = []
                last_vals = []

                for s in first_3:
                    raw = s["properties"].get(prop_name)
                    if raw is not None:
                        try:
                            v = float(raw)
                            if dim_name == "Slow Wave Count" and v == 0:
                                continue  # Exclude zeros
                            first_vals.append(v)
                        except (ValueError, TypeError):
                            pass

                for s in last_3:
                    raw = s["properties"].get(prop_name)
                    if raw is not None:
                        try:
                            v = float(raw)
                            if dim_name == "Slow Wave Count" and v == 0:
                                continue
                            last_vals.append(v)
                        except (ValueError, TypeError):
                            pass

                if not first_vals or not last_vals:
                    continue

                first_avg = sum(first_vals) / len(first_vals)
                last_avg = sum(last_vals) / len(last_vals)

                if first_avg == 0:
                    continue

                if higher_better:
                    pct_change = (last_avg - first_avg) / first_avg
                else:
                    pct_change = (first_avg - last_avg) / first_avg

                if pct_change >= 0.10:  # >=10% improvement
                    dim_improved[dim_name] += 1
                    user_improved = True

            if user_improved:
                improved_count += 1

        eligible = len(eligible_users)
        rate = improved_count / eligible if eligible > 0 else 0.0

        # Convert dimension counts to percentages
        dim_pcts = {k: round(v / eligible * 100, 1) if eligible > 0 else 0.0
                    for k, v in dim_improved.items()}

        return {
            "rate": round(rate, 4),
            "improved": improved_count,
            "eligible": eligible,
            "dimension_breakdown": dim_pcts,
        }

    # ──────────────────────────────────────────────
    # New public methods: Retention metrics
    # ──────────────────────────────────────────────

    def get_d30_app_retention(self) -> dict:
        """D30 App Retention with personal windows (days 24-30).

        Any event counts as retention.

        Returns:
            {"rate": float, "retained": int, "cohort_size": int}
        """
        result = self._get_d30_retention(return_events=None)
        result["_type"] = "app"
        return result

    def get_d30_sleep_retention(self) -> dict:
        """D30 Sleep Retention with personal windows (days 24-30).

        Only sleep_session_started counts as retention.

        Returns:
            {"rate": float, "retained": int, "cohort_size": int}
        """
        result = self._get_d30_retention(return_events=["sleep_session_started"])
        result["_type"] = "sleep"
        return result

    def get_retention_curves(self) -> dict:
        """30-Day Retention Curves: day-by-day app + sleep retention.

        Uses /retention endpoint with unit=day.

        Returns:
            {"app_curve": dict, "sleep_curve": dict}
        """
        to_date = self._pst_yesterday()
        from_dt = datetime.strptime(to_date, "%Y-%m-%d") - timedelta(days=90)
        from_date = from_dt.strftime("%Y-%m-%d")

        def _fetch_curve(return_event: str = None) -> dict:
            params = {
                "project_id": self.project_id,
                "from_date": from_date,
                "to_date": to_date,
                "born_event": "change_onboarding_completed",
                "event": return_event or "change_onboarding_completed",
                "interval_count": 30,
                "unit": "day",
                "where": WHERE_EXCLUDE_INTERNAL,
            }
            if return_event:
                params["event"] = return_event

            data = self.get("/retention", params=params)
            return data or {}

        app_data = _fetch_curve()
        sleep_data = _fetch_curve("sleep_session_started")

        return {
            "app_curve": app_data,
            "sleep_curve": sleep_data,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_cohort_retention_table(self, weeks: int = 8) -> dict:
        """Cohort Deep Dive: weekly cohort table with app + sleep retention.

        Uses /retention endpoint with unit=week.

        Returns:
            {"app_data": dict, "sleep_data": dict, "weeks": int}
        """
        to_date = self._pst_yesterday()
        from_dt = datetime.strptime(to_date, "%Y-%m-%d") - timedelta(weeks=weeks)
        from_date = from_dt.strftime("%Y-%m-%d")

        def _fetch_weekly(return_event: str = None) -> dict:
            params = {
                "project_id": self.project_id,
                "from_date": from_date,
                "to_date": to_date,
                "born_event": "change_onboarding_completed",
                "event": return_event or "change_onboarding_completed",
                "interval_count": weeks,
                "unit": "week",
                "where": WHERE_EXCLUDE_INTERNAL,
            }
            if return_event:
                params["event"] = return_event
            data = self.get("/retention", params=params)
            return data or {}

        app_data = _fetch_weekly()
        sleep_data = _fetch_weekly("sleep_session_started")

        return {
            "app_data": app_data,
            "sleep_data": sleep_data,
            "from_date": from_date,
            "to_date": to_date,
            "weeks": weeks,
        }

    def get_onboarding_count(self, days: int = 7) -> dict:
        """Count of users who completed onboarding in the period.

        Used for Activation Rate cross-source metric.

        Returns:
            {"onboarded": int, "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)
        values = self._segmentation_query(
            "change_onboarding_completed", from_date, to_date,
            query_type="unique",
        )

        total = 0
        for event_data in values.values():
            total += sum(event_data.values())

        return {
            "onboarded": total,
            "from_date": from_date,
            "to_date": to_date,
        }

    # ──────────────────────────────────────────────
    # OKR Hardware & Device Metrics
    # ──────────────────────────────────────────────

    def get_all_night_wear_rate(self, days: int = 7) -> dict:
        """All-night wear rate: sessions >= 6 hours / total sleep sessions. O1 KRg.

        Uses session_statistics events filtered to type=sleep.
        A session counts as "all night" if totalSleepMinutes >= 360 (6 hours).

        API budget: 1 Export call.

        Returns:
            {"rate": float, "sessions_6hr": int, "total_sessions": int,
             "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)

        events = self._export_events(["session_statistics"], from_date, to_date)
        # Get ALL sleep sessions (min_hours=0 to include all)
        all_sleep = self._get_qualifying_sessions(events, min_hours=0)

        # Discover property names once from all events
        discovered = self._discover_properties(all_sleep)
        total_key = discovered.get("total_sleep_hours")

        sessions_6hr = 0
        for session in all_sleep:
            props = session.get("properties", {})
            if total_key:
                raw_val = props.get(total_key, 0)
                hours = self._convert_value(raw_val, "total_sleep_hours", total_key)
                if hours is not None and hours >= 6.0:  # 6 hours
                    sessions_6hr += 1

        total = len(all_sleep)
        rate = sessions_6hr / total if total > 0 else 0.0

        log.info(f"All-night wear rate: {rate:.1%} ({sessions_6hr}/{total} sessions >= 6hrs)")

        return {
            "rate": rate,
            "sessions_6hr": sessions_6hr,
            "total_sessions": total,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_battery_runout_rate(self, days: int = 7) -> dict:
        """Battery runout rate: low_battery_during_sleep / total sessions. O3 KRa.

        API budget: 2 Segmentation calls.

        Returns:
            {"rate": float, "runout_count": int, "total_sessions": int,
             "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)

        # Count low battery events
        battery_vals = self._segmentation_query(
            "low_battery_during_sleep", from_date, to_date,
        )
        runout_count = 0
        for event_data in battery_vals.values():
            runout_count += sum(event_data.values())

        # Count total sleep sessions started
        session_vals = self._segmentation_query(
            "sleep_session_started", from_date, to_date,
        )
        total_sessions = 0
        for event_data in session_vals.values():
            total_sessions += sum(event_data.values())

        rate = runout_count / total_sessions if total_sessions > 0 else 0.0

        log.info(f"Battery runout rate: {rate:.1%} ({runout_count}/{total_sessions})")

        return {
            "rate": rate,
            "runout_count": runout_count,
            "total_sessions": total_sessions,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_feature_adoption(self, feature_event: str, days: int = 7) -> dict:
        """Generic feature adoption rate for any event. O5 placeholder.

        Calculates: unique users who triggered feature_event / MAU.

        API budget: 1-2 calls (segmentation + MAU).

        Args:
            feature_event: Mixpanel event name (e.g., "enter_winddown")
            days: Lookback window

        Returns:
            {"unique_users": int, "event_count": int, "adoption_rate": float,
             "mau": int, "feature_event": str, "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)

        # Count unique users
        unique_vals = self._segmentation_query(
            feature_event, from_date, to_date, query_type="unique",
        )
        unique_users = 0
        for event_data in unique_vals.values():
            unique_users += sum(event_data.values())

        # Count total events
        total_vals = self._segmentation_query(
            feature_event, from_date, to_date,
        )
        event_count = 0
        for event_data in total_vals.values():
            event_count += sum(event_data.values())

        # Get MAU for denominator
        mau_data = self.get_mau()
        mau = mau_data.get("mau", 0)

        adoption_rate = unique_users / mau if mau > 0 else 0.0

        log.info(f"Feature adoption ({feature_event}): {adoption_rate:.1%} ({unique_users}/{mau})")

        return {
            "unique_users": unique_users,
            "event_count": event_count,
            "adoption_rate": adoption_rate,
            "mau": mau,
            "feature_event": feature_event,
            "from_date": from_date,
            "to_date": to_date,
        }

    def discover_event_properties(self, event_name: str, days: int = 7) -> dict:
        """Export a sample of events and list all property names + sample values.

        Investigation helper — use to discover properties on unfamiliar events
        (e.g., impedance_measurements for O2 DC fit pass rate).

        API budget: 1 Export call.

        Returns:
            {"event_name": str, "sample_size": int,
             "properties": {name: {"type": str, "sample": any, "count": int}}}
        """
        from_date, to_date = self._pst_date_range(days)

        events = self._export_events([event_name], from_date, to_date)

        all_props: dict = {}
        for evt in events[:200]:  # Sample first 200
            for k, v in evt.get("properties", {}).items():
                if k.startswith("$") or k.startswith("mp_"):
                    continue  # Skip Mixpanel system properties
                if k not in all_props:
                    all_props[k] = {
                        "type": type(v).__name__,
                        "sample": v,
                        "count": 1,
                    }
                else:
                    all_props[k]["count"] += 1

        log.info(f"Discovered {len(all_props)} properties on {event_name} ({len(events)} events)")

        return {
            "event_name": event_name,
            "sample_size": min(len(events), 200),
            "total_events": len(events),
            "properties": all_props,
        }

    # ──────────────────────────────────────────────
    # Summary (executive snapshot)
    # ──────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Product analytics snapshot: DAU, MAU, sleep sessions, quality, completion.

        API budget: ~6 calls (DAU=1, MAU=1, sessions=1, export=1, completion=2).
        Well within the 60/hr limit.

        Returns:
            Dict with key product KPIs for executive summaries.
        """
        log.info("=== Mixpanel Summary ===")

        dau = self.get_dau()
        mau = self.get_mau()
        sessions = self.get_sleep_sessions(days=7)
        quality = self.get_sleep_quality(days=7)
        completion = self.get_completion_rate(days=7)

        summary: dict = {
            "dau": dau["dau"],
            "dau_date": dau["date"],
            "mau": mau["mau"],
            "sleep_sessions_7d": sessions["total_sessions"],
            "completion_rate": completion["completion_rate"],
            "completion_rate_formatted": completion["completion_rate_formatted"],
        }

        # Add sleep quality metrics if available
        for key in ["sleep_score", "sleep_efficiency", "total_sleep_hours",
                     "deep_sleep_hours", "waso_minutes", "latency_minutes"]:
            if key in quality:
                summary[f"avg_{key}"] = quality[key]

        return summary

    # ──────────────────────────────────────────────
    # Combined DAU + Sleep metrics
    # ──────────────────────────────────────────────

    def get_dau_user_ids(self, days: int = 7) -> dict:
        """Return the set of user IDs that qualify as DAU (>=3 events/day).

        Same export as _get_daily_user_counts() but preserves user IDs.

        API budget: 1 Export call.

        Returns:
            {"user_ids": set, "dau": int, "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)

        url = f"{self.EXPORT_BASE}/export"
        params = {"from_date": from_date, "to_date": to_date}

        resp = self._request_with_retry("GET", url, params=params, stream=True, timeout=300)
        if not resp or resp.status_code != 200:
            return {"user_ids": set(), "dau": 0, "from_date": from_date, "to_date": to_date}

        # {date: {user_id: event_count}}
        daily_users: dict[str, dict[str, int]] = {}
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self._filter_internal_event(event):
                continue

            props = event.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            if not user_id:
                continue

            ts = props.get("time", 0)
            if ts:
                evt_date = datetime.fromtimestamp(ts, tz=PST_OFFSET).strftime("%Y-%m-%d")
            else:
                continue

            if evt_date not in daily_users:
                daily_users[evt_date] = {}
            daily_users[evt_date][user_id] = daily_users[evt_date].get(user_id, 0) + 1

        # Collect user IDs with >=3 events on ANY day
        all_dau_ids: set = set()
        for date_str, users in daily_users.items():
            for uid, count in users.items():
                if count >= 3:
                    all_dau_ids.add(uid)

        log.info(f"DAU user IDs ({days}d): {len(all_dau_ids)} unique users")
        return {
            "user_ids": all_dau_ids,
            "dau": len(all_dau_ids),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_sleep_quality_for_users(self, user_ids: set, days: int = 7) -> dict:
        """Sleep quality metrics filtered to a specific user subset.

        Exports session_statistics, filters to user_ids, computes per-user
        total sleep hours, then returns median/mean across those users.

        API budget: 1 Export call.

        Returns:
            {"median_total_sleep_hours": float, "mean_total_sleep_hours": float,
             "sessions_count": int, "users_with_sleep": int,
             "users_without_sleep": int, "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        if not events:
            return {
                "median_total_sleep_hours": 0.0,
                "mean_total_sleep_hours": 0.0,
                "sessions_count": 0,
                "users_with_sleep": 0,
                "users_without_sleep": len(user_ids),
                "from_date": from_date,
                "to_date": to_date,
            }

        # Property discovery
        discovered = self._discover_properties(events)
        total_sleep_key = discovered.get("total_sleep_hours")

        # Per-user: sum of total sleep hours across qualifying sessions
        user_sleep: dict[str, list[float]] = {}
        sessions_count = 0

        for evt in events:
            props = evt.get("properties", {})
            user_id = props.get("$user_id") or props.get("distinct_id") or ""
            if not user_id or user_id not in user_ids:
                continue

            # Filter to sleep type (not naps)
            session_type = props.get("type", "").lower()
            if session_type and session_type != "sleep":
                continue

            # Get total sleep hours
            if total_sleep_key:
                raw_val = props.get(total_sleep_key)
                converted = self._convert_value(raw_val, "total_sleep_hours", total_sleep_key)
                if converted is not None and converted >= 2.0:  # >=2hr qualifying
                    if user_id not in user_sleep:
                        user_sleep[user_id] = []
                    user_sleep[user_id].append(converted)
                    sessions_count += 1

        # Compute per-user averages, then population median/mean
        per_user_avg = []
        for uid, hours_list in user_sleep.items():
            per_user_avg.append(sum(hours_list) / len(hours_list))

        users_with = len(user_sleep)
        users_without = len(user_ids) - users_with

        if per_user_avg:
            median_sleep = statistics.median(per_user_avg)
            mean_sleep = statistics.mean(per_user_avg)
        else:
            median_sleep = 0.0
            mean_sleep = 0.0

        log.info(f"Sleep for user subset: {users_with}/{len(user_ids)} users have sleep data, "
                 f"median={median_sleep:.1f}h, {sessions_count} sessions")
        return {
            "median_total_sleep_hours": round(median_sleep, 2),
            "mean_total_sleep_hours": round(mean_sleep, 2),
            "sessions_count": sessions_count,
            "users_with_sleep": users_with,
            "users_without_sleep": users_without,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_dau_sleep_summary(self, days: int = 7) -> dict:
        """Median sleep time for DAU users — chains get_dau_user_ids + get_sleep_quality_for_users.

        API budget: 2 Export calls.

        Returns:
            {"dau": int, "dau_with_sleep": int, "dau_without_sleep": int,
             "median_total_sleep_hours": float, "mean_total_sleep_hours": float,
             "sessions_count": int, "from_date": str, "to_date": str}
        """
        dau_data = self.get_dau_user_ids(days)
        user_ids = dau_data["user_ids"]

        if not user_ids:
            return {
                "dau": 0, "dau_with_sleep": 0, "dau_without_sleep": 0,
                "median_total_sleep_hours": 0.0, "mean_total_sleep_hours": 0.0,
                "sessions_count": 0,
                "from_date": dau_data["from_date"], "to_date": dau_data["to_date"],
            }

        sleep_data = self.get_sleep_quality_for_users(user_ids, days)

        return {
            "dau": dau_data["dau"],
            "dau_with_sleep": sleep_data["users_with_sleep"],
            "dau_without_sleep": sleep_data["users_without_sleep"],
            "median_total_sleep_hours": sleep_data["median_total_sleep_hours"],
            "mean_total_sleep_hours": sleep_data["mean_total_sleep_hours"],
            "sessions_count": sleep_data["sessions_count"],
            "from_date": dau_data["from_date"],
            "to_date": dau_data["to_date"],
        }

    # ──────────────────────────────────────────────
    # Product & Engineering Metrics (Spec v1)
    # ──────────────────────────────────────────────

    def get_onboarding_detailed(self, days: int = 7) -> dict:
        """Detailed onboarding funnel: pairing, completion, HealthKit opt-in, EEG sharing.

        API budget: 3 Export calls.

        Returns:
            {"pairing_attempts": int, "pairing_success": int, "pairing_failure_rate": float,
             "pairing_stuck_count": int, "stuck_screens": dict,
             "completion_starts": int, "completion_done": int, "completion_rate": float,
             "healthkit_sleep_optin_rate": float, "healthkit_heart_optin_rate": float,
             "eeg_sharing_optin_rate": float, ...}
        """
        from_date, to_date = self._pst_date_range(days)

        # Export 1: Pairing events
        pairing_events = self._export_events(
            ["enter_pairing_smartbuds_screen", "device_paired", "onboarding_pairing_stuck"],
            from_date, to_date,
        )
        pairing_events = [e for e in pairing_events if not self._filter_internal_event(e)]

        pairing_users = set()
        paired_users = set()
        stuck_count = 0
        stuck_screens: dict[str, int] = {}

        for evt in pairing_events:
            name = evt.get("event", "")
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if not uid:
                continue

            if name == "enter_pairing_smartbuds_screen":
                pairing_users.add(uid)
            elif name == "device_paired":
                paired_users.add(uid)
            elif name == "onboarding_pairing_stuck":
                stuck_count += 1
                screen = props.get("screen", "unknown")
                stuck_screens[screen] = stuck_screens.get(screen, 0) + 1

        pairing_attempts = len(pairing_users)
        pairing_success = len(paired_users & pairing_users)
        pairing_failure_rate = 1 - (pairing_success / pairing_attempts) if pairing_attempts > 0 else 0

        # Export 2: Onboarding completion
        onboard_events = self._export_events(
            ["enter_welcome_member_screen", "change_onboarding_completed"],
            from_date, to_date,
        )
        onboard_events = [e for e in onboard_events if not self._filter_internal_event(e)]

        welcome_users = set()
        completed_users = set()
        for evt in onboard_events:
            name = evt.get("event", "")
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if not uid:
                continue
            if name == "enter_welcome_member_screen":
                welcome_users.add(uid)
            elif name == "change_onboarding_completed":
                if props.get("isOnboardingCompleted") in (True, "true", 1, "1"):
                    completed_users.add(uid)

        completion_starts = len(welcome_users)
        completion_done = len(completed_users)
        completion_rate = completion_done / completion_starts if completion_starts > 0 else 0

        # Export 3: HealthKit + EEG sharing permissions
        perm_events = self._export_events(
            ["health_kit_sleep_permission", "health_kit_heart_rate_permission",
             "user_data_sharing_permissions", "not_enable_brain_data_onboarding"],
            from_date, to_date,
        )
        perm_events = [e for e in perm_events if not self._filter_internal_event(e)]

        hk_sleep_users: set[str] = set()
        hk_heart_users: set[str] = set()
        eeg_sharing_enabled = 0
        eeg_sharing_total = 0
        eeg_not_enabled = 0

        for evt in perm_events:
            name = evt.get("event", "")
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""

            if name == "health_kit_sleep_permission":
                if uid:
                    hk_sleep_users.add(uid)
            elif name == "health_kit_heart_rate_permission":
                if uid:
                    hk_heart_users.add(uid)
            elif name == "user_data_sharing_permissions":
                eeg_sharing_total += 1
                if props.get("is_sharing_brain_data") in (True, "true", 1, "1"):
                    eeg_sharing_enabled += 1
            elif name == "not_enable_brain_data_onboarding":
                eeg_not_enabled += 1

        # Denominator: all unique users seen across the entire onboarding funnel
        all_onboarding_users = pairing_users | paired_users | welcome_users | completed_users
        total_users = len(all_onboarding_users)
        hk_sleep_rate = len(hk_sleep_users) / total_users if total_users > 0 else 0
        hk_heart_rate = len(hk_heart_users) / total_users if total_users > 0 else 0
        eeg_rate = eeg_sharing_enabled / eeg_sharing_total if eeg_sharing_total > 0 else 0

        log.info(f"Onboarding detailed: pairing={pairing_attempts}, paired={pairing_success}, "
                 f"stuck={stuck_count}, completion={completion_done}/{completion_starts}")
        return {
            "pairing_attempts": pairing_attempts,
            "pairing_success": pairing_success,
            "pairing_failure_rate": round(pairing_failure_rate, 4),
            "pairing_stuck_count": stuck_count,
            "stuck_screens": stuck_screens,
            "completion_starts": completion_starts,
            "completion_done": completion_done,
            "completion_rate": round(completion_rate, 4),
            "healthkit_sleep_optin_rate": round(hk_sleep_rate, 4),
            "healthkit_sleep_users": len(hk_sleep_users),
            "healthkit_heart_optin_rate": round(hk_heart_rate, 4),
            "healthkit_heart_users": len(hk_heart_users),
            "total_onboarded_users": total_users,
            "eeg_sharing_optin_rate": round(eeg_rate, 4),
            "eeg_sharing_total": eeg_sharing_total,
            "eeg_not_enabled_count": eeg_not_enabled,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_audio_skips(self, days: int = 7) -> dict:
        """A2DP audio skip stats: expired/lost counts by side, with median/P90/P99.

        API budget: 1 Export call.

        Returns:
            {"total_events": int, "sessions_with_skips": int,
             "expired": {"median": float, "p90": float, "p99": float,
                         "left_total": int, "right_total": int},
             "lost": {"median": float, "p90": float, "p99": float,
                      "left_total": int, "right_total": int}, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["a2dp_audio_skipped"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        expired_counts = []
        lost_counts = []
        expired_left = 0
        expired_right = 0
        lost_left = 0
        lost_right = 0
        session_ids = set()

        for evt in events:
            props = evt.get("properties", {})
            expired = int(props.get("a2DpSkipExpiredCount", 0) or 0)
            lost = int(props.get("a2DpSkipLostCount", 0) or 0)
            side = props.get("side", "").lower()
            session_id = props.get("$insert_id") or props.get("time", "")

            if expired > 0 or lost > 0:
                session_ids.add(session_id)

            expired_counts.append(expired)
            lost_counts.append(lost)

            if side == "left":
                expired_left += expired
                lost_left += lost
            elif side == "right":
                expired_right += expired
                lost_right += lost

        def _percentiles(vals):
            if not vals:
                return {"median": 0, "p90": 0, "p99": 0}
            s = sorted(vals)
            return {
                "median": statistics.median(s),
                "p90": s[int(len(s) * 0.9)] if len(s) >= 10 else s[-1],
                "p99": s[int(len(s) * 0.99)] if len(s) >= 100 else s[-1],
            }

        log.info(f"A2DP audio skips ({days}d): {len(events)} events, "
                 f"{len(session_ids)} sessions with skips")
        return {
            "total_events": len(events),
            "sessions_with_skips": len(session_ids),
            "expired": {**_percentiles(expired_counts),
                        "left_total": expired_left, "right_total": expired_right},
            "lost": {**_percentiles(lost_counts),
                     "left_total": lost_left, "right_total": lost_right},
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_connectivity_stats(self, days: int = 7) -> dict:
        """BLE/Classic BT disconnect stats from sleep_session_end properties.

        API budget: 1 Export call.

        Returns:
            {"total_sessions": int, "sessions_with_disconnect": int, "disconnect_rate": float,
             "ble": {"median": float, "p90": float}, "classic": {"median": float, "p90": float}, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["sleep_session_end"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        ble_counts = []
        classic_counts = []
        sessions_with_disconnect = 0

        def _parse_int(v):
            if v is None or v == "<null>":
                return 0
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        for evt in events:
            props = evt.get("properties", {})
            # Actual Mixpanel property names (camelCase, per-side)
            ble_left = _parse_int(props.get("bleLeftDisconnectCount"))
            ble_right = _parse_int(props.get("bleRightDisconnectCount"))
            ble = ble_left + ble_right
            classic = _parse_int(props.get("classicDisconnectCount"))
            ble_counts.append(ble)
            classic_counts.append(classic)
            if ble + classic > 0:
                sessions_with_disconnect += 1

        total = len(events)
        disconnect_rate = sessions_with_disconnect / total if total > 0 else 0

        def _stats(vals):
            """Compute median + P90 among sessions that had any disconnects."""
            nonzero = [v for v in vals if v > 0]
            if not nonzero:
                return {"median": 0, "p90": 0, "sessions": 0}
            s = sorted(nonzero)
            return {
                "median": round(statistics.median(s), 1),
                "p90": round(s[int(len(s) * 0.9)] if len(s) >= 10 else s[-1], 1),
                "sessions": len(nonzero),
            }

        log.info(f"Connectivity ({days}d): {total} sessions, "
                 f"{sessions_with_disconnect} with disconnects ({disconnect_rate:.1%})")
        return {
            "total_sessions": total,
            "sessions_with_disconnect": sessions_with_disconnect,
            "disconnect_rate": round(disconnect_rate, 4),
            "ble": _stats(ble_counts),
            "classic": _stats(classic_counts),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_fota_stats(self, days: int = 30) -> dict:
        """Firmware update (FOTA) stats: success rate, failure breakdown, duration.

        API budget: 1 Export call (all firmware_update_* events).

        Returns:
            {"starts": int, "completions": int, "failures": int,
             "success_rate": float, "failure_reasons": [(reason, count)],
             "duration_median_sec": float, "duration_p90_sec": float, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            ["firmware_update_start", "firmware_update_complete",
             "firmware_update_failed", "firmware_update_committing"],
            from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        starts = 0
        completions = 0
        failures = 0
        durations = []
        failure_reasons: dict[str, int] = {}

        for evt in events:
            name = evt.get("event", "")
            props = evt.get("properties", {})

            if name == "firmware_update_start":
                starts += 1
            elif name == "firmware_update_complete":
                completions += 1
                dur = props.get("duration")
                if dur is not None:
                    try:
                        durations.append(float(dur))
                    except (ValueError, TypeError):
                        pass
            elif name == "firmware_update_failed":
                failures += 1
                error = props.get("error", "unknown")
                failure_reasons[error] = failure_reasons.get(error, 0) + 1

        success_rate = completions / starts if starts > 0 else 0
        sorted_reasons = sorted(failure_reasons.items(), key=lambda x: x[1], reverse=True)

        dur_median = statistics.median(durations) if durations else 0
        dur_p90 = sorted(durations)[int(len(durations) * 0.9)] if len(durations) >= 10 else (
            max(durations) if durations else 0)

        log.info(f"FOTA ({days}d): {starts} starts, {completions} completions, "
                 f"{failures} failures, rate={success_rate:.1%}")
        return {
            "starts": starts,
            "completions": completions,
            "failures": failures,
            "success_rate": round(success_rate, 4),
            "failure_reasons": sorted_reasons[:10],
            "duration_median_sec": round(dur_median, 1),
            "duration_p90_sec": round(dur_p90, 1),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_audio_stim_stats(self, days: int = 7) -> dict:
        """Audio stimulation stats: activation rate, duration, sham vs stim ratio.

        API budget: 1 Export call.

        Returns:
            {"stim_sessions": int, "sham_sessions": int, "total_eligible": int,
             "activation_rate": float, "stim_ratio": float, "sham_ratio": float, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            ["audio_stim_start", "audio_sham_start"],
            from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        stim_sessions = set()
        sham_sessions = set()

        for evt in events:
            name = evt.get("event", "")
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            session_key = f"{uid}_{ts}"

            if name == "audio_stim_start":
                stim_sessions.add(session_key)
            elif name == "audio_sham_start":
                sham_sessions.add(session_key)

        stim_count = len(stim_sessions)
        sham_count = len(sham_sessions)
        total = stim_count + sham_count

        # Get total sleep sessions for activation rate
        sleep_events = self._export_events(["sleep_session_started"], from_date, to_date)
        sleep_events = [e for e in sleep_events if not self._filter_internal_event(e)]
        total_sleep_sessions = len(sleep_events)

        activation_rate = total / total_sleep_sessions if total_sleep_sessions > 0 else 0

        log.info(f"Audio stim ({days}d): {stim_count} stim, {sham_count} sham, "
                 f"{total_sleep_sessions} total sleep sessions")
        return {
            "stim_sessions": stim_count,
            "sham_sessions": sham_count,
            "total_eligible": total_sleep_sessions,
            "activation_rate": round(activation_rate, 4),
            "stim_ratio": round(stim_count / total, 4) if total > 0 else 0,
            "sham_ratio": round(sham_count / total, 4) if total > 0 else 0,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_signal_quality(self, days: int = 7) -> dict:
        """Signal quality: DC fit pass rate per side, AC impedance buckets for DC-passers.

        API budget: 1 Export call.

        Returns:
            {"total_samples": int,
             "dc_pass_rate_left": float, "dc_pass_rate_right": float,
             "dc_pass_rate_overall": float,
             "ac_distribution": {"left": {...}, "right": {...}}, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["impedance_measurements"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        dc_left_pass = 0
        dc_left_total = 0
        dc_right_pass = 0
        dc_right_total = 0

        # AC values for DC-passers only
        ac_left_values = []
        ac_right_values = []

        for evt in events:
            props = evt.get("properties", {})

            # DC check — left
            left_dc = props.get("leftDCImpedanceCheckStatus", "")
            if left_dc:
                dc_left_total += 1
                if left_dc.lower() in ("pass", "passed", "true", "1"):
                    dc_left_pass += 1
                    # Collect AC value for DC-passers
                    ac_val = props.get("leftACImpedanceValue")
                    if ac_val is not None:
                        try:
                            ac_left_values.append(float(ac_val))
                        except (ValueError, TypeError):
                            pass

            # DC check — right
            right_dc = props.get("rightDCImpedanceCheckStatus", "")
            if right_dc:
                dc_right_total += 1
                if right_dc.lower() in ("pass", "passed", "true", "1"):
                    dc_right_pass += 1
                    ac_val = props.get("rightACImpedanceValue")
                    if ac_val is not None:
                        try:
                            ac_right_values.append(float(ac_val))
                        except (ValueError, TypeError):
                            pass

        dc_left_rate = dc_left_pass / dc_left_total if dc_left_total > 0 else 0
        dc_right_rate = dc_right_pass / dc_right_total if dc_right_total > 0 else 0
        dc_total = dc_left_total + dc_right_total
        dc_pass = dc_left_pass + dc_right_pass
        dc_overall = dc_pass / dc_total if dc_total > 0 else 0

        def _bucket_ac(values):
            """Bucket AC impedance values. Thresholds TBD — using placeholder ranges."""
            if not values:
                return {"excellent": 0, "good": 0, "marginal": 0, "poor": 0, "total": 0}
            excellent = sum(1 for v in values if v < 10)
            good = sum(1 for v in values if 10 <= v < 25)
            marginal = sum(1 for v in values if 25 <= v < 50)
            poor = sum(1 for v in values if v >= 50)
            total = len(values)
            return {
                "excellent": excellent, "good": good,
                "marginal": marginal, "poor": poor, "total": total,
                "median": round(statistics.median(values), 1) if values else 0,
            }

        total_samples = len(events)
        log.info(f"Signal quality ({days}d): {total_samples} samples, "
                 f"DC pass: L={dc_left_rate:.1%} R={dc_right_rate:.1%}")
        return {
            "total_samples": total_samples,
            "dc_pass_rate_left": round(dc_left_rate, 4),
            "dc_pass_rate_right": round(dc_right_rate, 4),
            "dc_pass_rate_overall": round(dc_overall, 4),
            "dc_left_total": dc_left_total,
            "dc_right_total": dc_right_total,
            "ac_distribution": {
                "left": _bucket_ac(ac_left_values),
                "right": _bucket_ac(ac_right_values),
            },
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_battery_health(self, days: int = 7) -> dict:
        """Battery health: dead-battery session rate with per-side breakdown.

        API budget: 1 Export call.

        Returns:
            {"battery_dead_rate": float, "total_sessions": int, "dead_sessions": int,
             "left_only": int, "right_only": int, "both_dead": int, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["sleep_session_end"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        total = 0
        dead_sessions = 0
        left_only = 0
        right_only = 0
        both_dead = 0

        for evt in events:
            props = evt.get("properties", {})
            total += 1

            battery_l = props.get("batteryL")
            battery_r = props.get("batteryR")

            l_dead = battery_l is not None and (battery_l == 0 or battery_l == "0")
            r_dead = battery_r is not None and (battery_r == 0 or battery_r == "0")

            if l_dead and r_dead:
                both_dead += 1
                dead_sessions += 1
            elif l_dead:
                left_only += 1
                dead_sessions += 1
            elif r_dead:
                right_only += 1
                dead_sessions += 1

        rate = dead_sessions / total if total > 0 else 0
        log.info(f"Battery health ({days}d): {dead_sessions}/{total} dead ({rate:.1%}), "
                 f"L={left_only} R={right_only} both={both_dead}")
        return {
            "battery_dead_rate": round(rate, 4),
            "total_sessions": total,
            "dead_sessions": dead_sessions,
            "left_only": left_only,
            "right_only": right_only,
            "both_dead": both_dead,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_comfort_metrics(self, days: int = 7) -> dict:
        """Comfort metrics: long session rate (>6hr) and sessions/user/week.

        API budget: 1 Export call.

        Returns:
            {"long_session_rate": float, "long_sessions": int, "total_sessions": int,
             "sessions_per_user_per_week_median": float, "unique_users": int, ...}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["sleep_session_end"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]

        total = 0
        long_sessions = 0
        user_sessions: dict[str, int] = {}

        for evt in events:
            props = evt.get("properties", {})
            total += 1

            # Long session check: durationSeconds > 21600 (6 hours)
            duration = props.get("durationSeconds") or props.get("duration_seconds")
            if duration is not None:
                try:
                    if float(duration) > 21600:
                        long_sessions += 1
                except (ValueError, TypeError):
                    pass

            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if uid:
                user_sessions[uid] = user_sessions.get(uid, 0) + 1

        long_rate = long_sessions / total if total > 0 else 0

        # Sessions per user per week
        weeks = max(days / 7, 1)
        per_user_per_week = []
        for uid, count in user_sessions.items():
            per_user_per_week.append(count / weeks)

        median_per_week = statistics.median(per_user_per_week) if per_user_per_week else 0

        log.info(f"Comfort ({days}d): {long_sessions}/{total} long sessions ({long_rate:.1%}), "
                 f"median {median_per_week:.1f} sessions/user/week")
        return {
            "long_session_rate": round(long_rate, 4),
            "long_sessions": long_sessions,
            "total_sessions": total,
            "sessions_per_user_per_week_median": round(median_per_week, 2),
            "unique_users": len(user_sessions),
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_slow_wave_counts(self, days: int = 7) -> dict:
        """Slow wave counts: aggregate slow wave detections from session_statistics.

        Extracts slowWaveCount (or slow_wave_count) from qualifying sleep sessions
        (>= 4 hours, type=sleep). Zero counts are included in session totals but
        excluded from the "active" average.

        Computes per-session SW rate (count / duration_hours), per-user medians,
        population median, and daily bins (Low/Moderate/High) with stim status.

        API budget: 1 Export call.

        Returns:
            {"total_sessions": int, "sessions_with_sw": int, "sessions_without_sw": int,
             "total_slow_waves": int, "avg_per_session": float, "avg_per_active_session": float,
             "avg_sw_rate_per_hour": float, "population_median_sw_per_session": float,
             "unique_users": int, "users_with_sw": int,
             "_user_sw_averages": list, "sw_daily_users": list,
             "from_date": str, "to_date": str}
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(["session_statistics"], from_date, to_date)
        events = [e for e in events if not self._filter_internal_event(e)]
        qualifying = self._get_qualifying_sessions(events, min_hours=4.0)

        if not qualifying:
            return {
                "total_sessions": 0, "sessions_with_sw": 0, "sessions_without_sw": 0,
                "total_slow_waves": 0, "avg_per_session": 0.0, "avg_per_active_session": 0.0,
                "avg_sw_rate_per_hour": 0.0, "population_median_sw_per_session": 0.0,
                "unique_users": 0, "users_with_sw": 0,
                "_user_sw_averages": [], "sw_daily_users": [], "sw_user_rates": [],
                "sw_rate_p25": 0.0, "sw_rate_p75": 0.0,
                "from_date": from_date, "to_date": to_date,
            }

        # Discover slow_wave_count property name
        discovered = self._discover_properties(events)
        sw_prop = discovered.get("slow_wave_count")

        if not sw_prop:
            # Try direct lookup in case discovery missed it
            all_keys: set[str] = set()
            for evt in events[:100]:
                all_keys.update(evt.get("properties", {}).keys())
            for variant in ["slowWaveCount", "slow_wave_count", "stimulation_count", "sws_count"]:
                if variant in all_keys:
                    sw_prop = variant
                    break

        if not sw_prop:
            log.warning(
                "Slow wave: slowWaveCount property not found in session_statistics events. "
                "Tried: slowWaveCount, slow_wave_count, stimulation_count, sws_count"
            )
            return {
                "total_sessions": len(qualifying), "sessions_with_sw": 0,
                "sessions_without_sw": len(qualifying),
                "total_slow_waves": 0, "avg_per_session": 0.0, "avg_per_active_session": 0.0,
                "avg_sw_rate_per_hour": 0.0, "population_median_sw_per_session": 0.0,
                "unique_users": len(set(s["user_id"] for s in qualifying)),
                "users_with_sw": 0,
                "_user_sw_averages": [], "sw_daily_users": [], "sw_user_rates": [],
                "sw_rate_p25": 0.0, "sw_rate_p75": 0.0,
                "_sw_property_missing": True,
                "from_date": from_date, "to_date": to_date,
                "_note": "slowWaveCount property not found in session_statistics events",
            }

        # Discover total_sleep_hours property for SW rate calculation
        total_sleep_key = discovered.get("total_sleep_hours")

        # ── Helper: compute sleep night date using 6PM PST cutoff ──
        def _night_date_from_ts(ts):
            dt = datetime.fromtimestamp(ts, tz=PST_OFFSET)
            if dt.hour < 18:
                return (dt - timedelta(days=1)).strftime('%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')

        # ── Signal quality per session (separate event) ──
        # eeg_signal_quality_session_end has left/right/final properties
        # Build lookup: sq_by_night["uid_YYYY-MM-DD"] = {left, right, final}
        sq_by_night: dict[str, dict] = {}
        try:
            sq_events = self._export_events(
                ["eeg_signal_quality_session_end"], from_date, to_date,
            )
            sq_events = [e for e in sq_events if not self._filter_internal_event(e)]
            # Log property names from first few events for discovery
            if sq_events:
                sample_props = set()
                for evt in sq_events[:10]:
                    sample_props.update(evt.get("properties", {}).keys())
                # Filter out standard Mixpanel props (start with $)
                custom_props = sorted(k for k in sample_props if not k.startswith("$") and not k.startswith("mp_"))
                log.info(f"Signal quality event custom properties: {custom_props}")
            for sq_evt in sq_events:
                sq_props = sq_evt.get("properties", {})
                sq_uid = sq_props.get("$user_id") or sq_props.get("distinct_id") or ""
                sq_ts = sq_evt.get("timestamp", 0) or sq_props.get("time", 0)
                if not sq_uid or not sq_ts:
                    continue
                sq_night = _night_date_from_ts(sq_ts)
                sq_key = f"{sq_uid}_{sq_night}"
                # Extract left/right/final signal quality levels
                # Property names: aggregated_final_level, left_final_level, right_final_level
                sq_final = None
                sq_left = None
                sq_right = None
                for fk in ("aggregated_final_level", "final_eeg_signal_quality",
                           "finalEegSignalQuality", "eeg_signal_quality_final"):
                    raw = sq_props.get(fk)
                    if raw is not None:
                        try:
                            sq_final = int(float(raw))
                        except (ValueError, TypeError):
                            sq_str = str(raw).lower()
                            if sq_str == "poor": sq_final = 0
                            elif sq_str in ("medium", "med"): sq_final = 1
                            elif sq_str == "good": sq_final = 2
                        break
                for lk in ("left_final_level", "left_eeg_signal_quality",
                           "leftEegSignalQuality"):
                    raw = sq_props.get(lk)
                    if raw is not None:
                        try:
                            sq_left = int(float(raw))
                        except (ValueError, TypeError):
                            pass
                        break
                for rk in ("right_final_level", "right_eeg_signal_quality",
                           "rightEegSignalQuality"):
                    raw = sq_props.get(rk)
                    if raw is not None:
                        try:
                            sq_right = int(float(raw))
                        except (ValueError, TypeError):
                            pass
                        break
                if sq_final is not None or sq_left is not None or sq_right is not None:
                    sq_by_night[sq_key] = {
                        "final": sq_final,
                        "left": sq_left,
                        "right": sq_right,
                    }
            log.info(f"Signal quality events: {len(sq_events)} raw, {len(sq_by_night)} nights matched")
        except Exception as e:
            log.warning(f"Signal quality export failed (non-blocking): {e}")

        # Extract per-user slow wave counts
        user_data: dict[str, dict] = {}  # user_id -> {sessions, slow_waves, active_sessions}
        user_sw_lists: dict[str, list] = {}  # user_id -> [sw_count, ...]
        all_sw_rates: list[float] = []  # sw_count / duration_hours per session
        user_sw_hours: dict[str, float] = {}   # uid -> total sleep hours (for rate histogram)
        user_sw_counts_for_rate: dict[str, int] = {}  # uid -> total SW count (includes 0-SW sessions with valid duration)
        # daily_data: {user_id: {night_date: [{sw_count, has_stim}, ...]}}
        daily_data: dict[str, dict[str, list]] = {}
        total_sw = 0
        sessions_with_sw = 0
        sessions_without_sw = 0

        for sess in qualifying:
            uid = sess["user_id"]
            props = sess["properties"]
            raw_sw = props.get(sw_prop)

            sw_count = 0
            if raw_sw is not None:
                try:
                    sw_count = int(float(raw_sw))
                except (ValueError, TypeError):
                    sw_count = 0

            if uid not in user_data:
                user_data[uid] = {"sessions": 0, "slow_waves": 0, "active_sessions": 0}
            if uid not in user_sw_lists:
                user_sw_lists[uid] = []
            if uid not in daily_data:
                daily_data[uid] = {}

            user_data[uid]["sessions"] += 1
            user_sw_lists[uid].append(sw_count)

            # SW rate per hour (7c)
            sess_duration_hours = None
            if total_sleep_key:
                raw_dur = props.get(total_sleep_key)
                duration_hours = self._convert_value(raw_dur, "total_sleep_hours", total_sleep_key)
                if duration_hours and duration_hours > 0:
                    sess_duration_hours = duration_hours
                    all_sw_rates.append(sw_count / duration_hours)
                    user_sw_hours[uid] = user_sw_hours.get(uid, 0.0) + duration_hours
                    user_sw_counts_for_rate[uid] = user_sw_counts_for_rate.get(uid, 0) + sw_count

            # Daily bin data (7f) -- group by user + night_date
            ts = sess.get("timestamp", 0)
            night_date = _night_date_from_ts(ts) if ts else None

            # Stim detection via audioStimulation property
            audio_stim_val = props.get("audioStimulation")
            has_stim = False
            if audio_stim_val is not None:
                if isinstance(audio_stim_val, bool):
                    has_stim = audio_stim_val
                elif isinstance(audio_stim_val, str):
                    has_stim = audio_stim_val.lower() in ("true", "1", "yes", "on")
                else:
                    try:
                        has_stim = bool(int(audio_stim_val))
                    except (ValueError, TypeError):
                        has_stim = False

            # Session start hour (PST) for after-midnight analysis
            session_start_hour = None
            if ts:
                dt_pst = datetime.fromtimestamp(ts, tz=PST_OFFSET)
                session_start_hour = dt_pst.hour

            # Deep sleep minutes (from session_statistics properties)
            deep_sleep_min = None
            for deep_key in ("totalDeepSleepMinutes", "deep_sleep_minutes"):
                raw_deep = props.get(deep_key)
                if raw_deep is not None:
                    try:
                        deep_sleep_min = float(raw_deep)
                    except (ValueError, TypeError):
                        pass
                    break

            # Signal quality (from session_statistics properties if firmware includes it)
            signal_quality_final = None
            for sq_key in ("eeg_signal_quality_session_end_final",
                           "eegSignalQualityFinal", "EEG_SIGNAL_QUALITY_FINAL_LEVEL",
                           "eeg_signal_quality_final_level", "signal_quality_level"):
                raw_sq = props.get(sq_key)
                if raw_sq is not None:
                    try:
                        signal_quality_final = int(float(raw_sq))  # 0=Poor, 1=Med, 2=Good
                    except (ValueError, TypeError):
                        sq_str = str(raw_sq).lower()
                        if sq_str == "poor":
                            signal_quality_final = 0
                        elif sq_str in ("medium", "med"):
                            signal_quality_final = 1
                        elif sq_str == "good":
                            signal_quality_final = 2
                    break

            if night_date:
                if night_date not in daily_data[uid]:
                    daily_data[uid][night_date] = []
                daily_data[uid][night_date].append({
                    "sw_count": sw_count,
                    "has_stim": has_stim,
                    "duration_hours": sess_duration_hours,
                    "session_start_hour": session_start_hour,
                    "deep_sleep_min": deep_sleep_min,
                    "signal_quality_final": signal_quality_final,
                    "timestamp": ts,
                })

            if sw_count > 0:
                sessions_with_sw += 1
                user_data[uid]["active_sessions"] += 1
                user_data[uid]["slow_waves"] += sw_count
                total_sw += sw_count
            else:
                sessions_without_sw += 1

        total_sessions = len(qualifying)
        avg_per_session = total_sw / total_sessions if total_sessions > 0 else 0
        avg_per_active = total_sw / sessions_with_sw if sessions_with_sw > 0 else 0

        # SW rate per hour average (7c)
        avg_sw_rate_per_hour = (
            sum(all_sw_rates) / len(all_sw_rates) if all_sw_rates else 0.0
        )

        # Population median of per-user medians (7e)
        per_user_medians: list[float] = []
        for uid, sw_list in user_sw_lists.items():
            if sw_list:
                per_user_medians.append(statistics.median(sw_list))
        population_median = (
            statistics.median(per_user_medians) if per_user_medians else 0.0
        )

        # Build _user_sw_averages (internal, for regularity binning) (7d)
        _user_sw_averages = []
        for uid, info in user_data.items():
            avg = info["slow_waves"] / info["sessions"] if info["sessions"] > 0 else 0
            _user_sw_averages.append({
                "user_id": uid,
                "sessions": info["sessions"],
                "slow_waves": info["slow_waves"],
                "active_sessions": info["active_sessions"],
                "avg_per_session": round(avg, 1),
            })
        _user_sw_averages.sort(key=lambda x: x["slow_waves"], reverse=True)

        # Per-user SW rate per hour (for rate histogram on frontend)
        sw_user_rates: list[dict] = []
        for uid in user_sw_hours:
            total_hours = user_sw_hours[uid]
            total_sw_for_rate = user_sw_counts_for_rate.get(uid, 0)
            if total_hours > 0:
                rate = total_sw_for_rate / total_hours
                sw_user_rates.append({
                    "user_id": uid,
                    "avg_rate_per_hour": round(rate, 2),
                    "total_hours": round(total_hours, 1),
                    "sessions": user_data[uid]["sessions"],
                })
        sw_user_rates.sort(key=lambda x: x["avg_rate_per_hour"])

        # Rate-based bin thresholds (P25 / P75)
        if sw_user_rates:
            rate_vals = [u["avg_rate_per_hour"] for u in sw_user_rates]  # already sorted
            n = len(rate_vals)
            sw_rate_p25 = round(rate_vals[int(n * 0.25)], 2)
            sw_rate_p75 = round(rate_vals[int(n * 0.75)], 2)
        else:
            sw_rate_p25 = 0.0
            sw_rate_p75 = 0.0

        users_with_sw = sum(1 for u in _user_sw_averages if u["slow_waves"] > 0)

        # Build daily bins for fluctuation chart (7f)
        # Bins: Low <= 200, Moderate <= 600, High > 600
        sw_daily_users: list[dict] = []
        for uid, dates_map in daily_data.items():
            user_days: list[dict] = []
            for night_date, session_list in sorted(dates_map.items()):
                sw_counts = [s["sw_count"] for s in session_list]
                avg_sw = sum(sw_counts) / len(sw_counts) if sw_counts else 0
                has_stim_any = any(s["has_stim"] for s in session_list)

                # Rate per hour for this day
                day_total_sw = sum(s["sw_count"] for s in session_list)
                day_total_hours = sum(s["duration_hours"] for s in session_list if s.get("duration_hours"))
                avg_rate = round(day_total_sw / day_total_hours, 2) if day_total_hours > 0 else None

                if avg_sw <= 200:
                    sw_bin = "Low"
                elif avg_sw <= 600:
                    sw_bin = "Moderate"
                else:
                    sw_bin = "High"

                # Session start hour (pick earliest session's start hour for the day)
                start_hours = [s["session_start_hour"] for s in session_list if s.get("session_start_hour") is not None]
                day_start_hour = min(start_hours) if start_hours else None

                # Deep sleep minutes (sum across fragments for the day)
                deep_vals = [s["deep_sleep_min"] for s in session_list if s.get("deep_sleep_min") is not None]
                day_deep_sleep_min = round(sum(deep_vals), 1) if deep_vals else None

                # Signal quality: worst (min) of sessions' final quality for the day
                # First try session_statistics properties, then fallback to separate event
                sq_vals = [s["signal_quality_final"] for s in session_list if s.get("signal_quality_final") is not None]
                day_signal_quality = min(sq_vals) if sq_vals else None
                day_signal_quality_left = None
                day_signal_quality_right = None

                if day_signal_quality is None:
                    # Fallback: use eeg_signal_quality_session_end event lookup
                    sq_key = f"{uid}_{night_date}"
                    sq_data = sq_by_night.get(sq_key)
                    if sq_data:
                        day_signal_quality = sq_data.get("final")
                        day_signal_quality_left = sq_data.get("left")
                        day_signal_quality_right = sq_data.get("right")
                else:
                    # Also check event for L/R details even if final came from session_statistics
                    sq_key = f"{uid}_{night_date}"
                    sq_data = sq_by_night.get(sq_key)
                    if sq_data:
                        day_signal_quality_left = sq_data.get("left")
                        day_signal_quality_right = sq_data.get("right")

                # Earliest session timestamp for stim timing cross-reference
                ts_vals = [s["timestamp"] for s in session_list if s.get("timestamp")]
                day_session_ts = min(ts_vals) if ts_vals else None

                user_days.append({
                    "date": night_date,
                    "avg_sw": round(avg_sw, 1),
                    "bin": sw_bin,
                    "has_stim": has_stim_any,
                    "avg_rate": avg_rate,
                    "session_start_hour": day_start_hour,
                    "deep_sleep_min": day_deep_sleep_min,
                    "signal_quality": day_signal_quality,
                    "signal_quality_left": day_signal_quality_left,
                    "signal_quality_right": day_signal_quality_right,
                    "session_start_ts": day_session_ts,
                })
            if user_days:
                sw_daily_users.append({
                    "user_id": uid,
                    "days": user_days,
                })

        log.info(f"Slow wave ({days}d): {sessions_with_sw}/{total_sessions} sessions "
                 f"with SW, total {total_sw} detections, {users_with_sw} users with SW")
        return {
            "total_sessions": total_sessions,
            "sessions_with_sw": sessions_with_sw,
            "sessions_without_sw": sessions_without_sw,
            "total_slow_waves": total_sw,
            "avg_per_session": round(avg_per_session, 1),
            "avg_per_active_session": round(avg_per_active, 1),
            "avg_sw_rate_per_hour": round(avg_sw_rate_per_hour, 2),
            "population_median_sw_per_session": round(population_median, 1),
            "unique_users": len(user_data),
            "users_with_sw": users_with_sw,
            "_user_sw_averages": _user_sw_averages,
            "sw_daily_users": sw_daily_users,
            "sw_user_rates": sw_user_rates,
            "sw_rate_p25": sw_rate_p25,
            "sw_rate_p75": sw_rate_p75,
            "from_date": from_date,
            "to_date": to_date,
        }

    def get_audio_stim_dashboard(self, days: int = 30) -> dict:
        """Full audio stimulation dashboard: Stim vs No Stim comparison.

        ALL metrics use >=4h qualifying session_statistics as the base (not
        raw sleep_session_started counts).  This ensures consistency with the
        sleep/wake accuracy pipeline.

        Metrics:
          1. Stim activation rate (stim / total qualifying sessions)
          2. Stim duration per session (paired start/end timestamps)
          3. User wake-up rate due to stim (end_during_stim / stim sessions)
          4. Audio stim efficacy -- SW boost (stim vs no-stim slowWaveCount)
          5. Stim events for Oura staging cross-reference (for eng_dashboard)

        Guardrails (out-of-ear, coverage) are applied in eng_dashboard.py
        via Firebase cross-reference (fields not available in Mixpanel).

        API budget: 1 Export call (6 event types).
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            [
                "audio_stim_start", "audio_stim_end", "audio_sham_start",
                "end_sleep_session_during_stimulation",
                "session_statistics", "sleep_session_started",
            ],
            from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        # ── Partition events by type ──
        stim_starts = []
        stim_ends = []
        sham_starts = []
        end_during_stim = []
        session_stats_events = []

        for evt in events:
            name = evt.get("event", "")
            if name == "audio_stim_start":
                stim_starts.append(evt)
            elif name == "audio_stim_end":
                stim_ends.append(evt)
            elif name == "audio_sham_start":
                sham_starts.append(evt)
            elif name == "end_sleep_session_during_stimulation":
                end_during_stim.append(evt)
            elif name == "session_statistics":
                session_stats_events.append(evt)

        # ── Helper: compute sleep night key using 6PM PST cutoff ──
        # Sessions before 6PM PST belong to the previous night's sleep.
        def _night_key_from_ts(uid, ts):
            dt = datetime.fromtimestamp(ts, tz=PST_OFFSET)
            if dt.hour < 18:
                night_date = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                night_date = dt.strftime('%Y-%m-%d')
            return f"{uid}_{night_date}"

        def _session_key(evt):
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if uid and ts:
                return _night_key_from_ts(uid, ts)
            return None

        # ── Build stim/sham session key lookup from raw events ──
        stim_session_keys = set()
        sham_session_keys = set()
        stim_users = set()

        for evt in stim_starts:
            key = _session_key(evt)
            if key:
                stim_session_keys.add(key)
                stim_users.add(key.split("_")[0])

        for evt in sham_starts:
            key = _session_key(evt)
            if key:
                sham_session_keys.add(key)

        # ── Base: ALL metrics use >=4h qualifying sessions ──
        qualifying_raw = self._get_qualifying_sessions(session_stats_events, min_hours=4.0)
        total_raw_sessions = len(session_stats_events)

        # Deduplicate by uid+night (6PM cutoff): combine fragments within
        # the same sleep night, then pick the primary (longest) fragment.
        discovered = self._discover_properties(session_stats_events)
        total_sleep_key = discovered.get("total_sleep_hours")

        # Step 1: Group qualifying sessions by night key
        night_fragments: dict[str, list[dict]] = {}
        for sess in qualifying_raw:
            uid = sess["user_id"]
            ts = sess["timestamp"]
            night_key = _night_key_from_ts(uid, ts)
            night_fragments.setdefault(night_key, []).append(sess)

        # Step 2: Combine fragments per night, pick primary (longest) fragment
        qualifying_by_key: dict[str, dict] = {}
        for night_key, fragments in night_fragments.items():
            combined_total = 0.0
            best_fragment = fragments[0]
            best_dur = 0.0
            for frag in fragments:
                frag_dur = 0.0
                if total_sleep_key:
                    try:
                        frag_dur = float(frag["properties"].get(total_sleep_key, 0))
                    except (ValueError, TypeError):
                        pass
                combined_total += frag_dur
                if frag_dur > best_dur:
                    best_dur = frag_dur
                    best_fragment = frag
            # Carry combined total + fragment count on primary for debugging
            best_fragment["_combined_total_sleep_hours"] = combined_total
            best_fragment["_fragment_count"] = len(fragments)
            qualifying_by_key[night_key] = best_fragment

        qualifying = list(qualifying_by_key.values())

        # Build qualifying session keys + classify stim/no-stim
        qualifying_keys = set()
        qualifying_stim_keys = set()
        qualifying_users = set()

        for sess in qualifying:
            uid = sess["user_id"]
            ts = sess["timestamp"]
            sess_key = _night_key_from_ts(uid, ts)
            qualifying_keys.add(sess_key)
            qualifying_users.add(uid)

            # Stim classification: audioStimulation property (primary) ->
            # audio_stim_start event (fallback). Confirmed 2026-03: no new
            # stim events; this two-tier approach is intentional.
            audio_stim_val = sess["properties"].get("audioStimulation")
            is_stim = False
            if audio_stim_val is not None:
                try:
                    is_stim = int(float(audio_stim_val)) > 0
                except (ValueError, TypeError):
                    pass
            else:
                if sess_key in stim_session_keys:
                    is_stim = True
            if is_stim:
                qualifying_stim_keys.add(sess_key)

        total_qualifying = len(qualifying_keys)
        stim_count = len(qualifying_stim_keys)
        no_stim_count = total_qualifying - stim_count
        qualifying_stim_users = set(k.split("_")[0] for k in qualifying_stim_keys)
        activation_rate = stim_count / total_qualifying if total_qualifying > 0 else 0

        # ── Stim duration per night (simple sum of all paired start/end blocks) ──
        # Each qualifying stim night may have multiple stim intervals (blocks).
        # We sum all valid block durations (0 < dur_min < 480) for the night total.
        stim_duration_events: dict[str, list[tuple[float, str]]] = {}

        for evt in stim_starts:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if uid and ts:
                key = _night_key_from_ts(uid, ts)
                if key in qualifying_stim_keys:
                    stim_duration_events.setdefault(key, []).append((ts, "start"))

        for evt in stim_ends:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if uid and ts:
                key = _night_key_from_ts(uid, ts)
                if key in qualifying_stim_keys:
                    stim_duration_events.setdefault(key, []).append((ts, "end"))

        session_durations = []
        session_intervals = []
        dropped_over_cap = 0

        for key, evt_list in stim_duration_events.items():
            evt_list.sort(key=lambda x: x[0])
            total_dur = 0.0
            intervals = 0
            i = 0
            while i < len(evt_list):
                if evt_list[i][1] == "start":
                    start_ts = evt_list[i][0]
                    end_ts = None
                    for j in range(i + 1, len(evt_list)):
                        if evt_list[j][1] == "end":
                            end_ts = evt_list[j][0]
                            i = j + 1
                            break
                    if end_ts is not None:
                        dur_min = (end_ts - start_ts) / 60.0
                        if 0 < dur_min < 480:
                            total_dur += dur_min
                            intervals += 1
                        elif dur_min >= 480:
                            dropped_over_cap += 1
                            log.warning(
                                f"Audio stim: dropped interval >=480min in {key}: "
                                f"{dur_min:.1f}min"
                            )
                    else:
                        i += 1
                else:
                    i += 1

            if intervals > 0:
                session_durations.append(total_dur)
                session_intervals.append(intervals)

        if dropped_over_cap > 0:
            log.warning(f"Audio stim: {dropped_over_cap} total intervals dropped (>=480min cap)")

        mean_duration = statistics.mean(session_durations) if session_durations else 0
        median_duration = statistics.median(session_durations) if session_durations else 0
        total_duration = sum(session_durations)
        mean_intervals = statistics.mean(session_intervals) if session_intervals else 0

        # ── Per-night stim details (for cross-reference with slow_wave section) ──
        stim_night_details: dict[str, dict] = {}
        for key, evt_list in stim_duration_events.items():
            evt_list_sorted = sorted(evt_list, key=lambda x: x[0])
            first_stim_ts = None
            night_total_dur = 0.0
            night_intervals = 0
            idx = 0
            while idx < len(evt_list_sorted):
                if evt_list_sorted[idx][1] == "start":
                    if first_stim_ts is None:
                        first_stim_ts = evt_list_sorted[idx][0]
                    start_ts = evt_list_sorted[idx][0]
                    end_ts = None
                    for jj in range(idx + 1, len(evt_list_sorted)):
                        if evt_list_sorted[jj][1] == "end":
                            end_ts = evt_list_sorted[jj][0]
                            idx = jj + 1
                            break
                    if end_ts is not None:
                        dur_min = (end_ts - start_ts) / 60.0
                        if 0 < dur_min < 480:
                            night_total_dur += dur_min
                            night_intervals += 1
                    else:
                        idx += 1
                else:
                    idx += 1
            if night_intervals > 0:
                stim_night_details[key] = {
                    "stim_duration_min": round(night_total_dur, 1),
                    "first_stim_ts": first_stim_ts,
                    "intervals": night_intervals,
                }
        log.info(f"Audio stim: {len(stim_night_details)} nights with per-night stim details")

        # ── Wake-up rate (only qualifying stim sessions) ──
        wakeup_keys = set()
        for evt in end_during_stim:
            key = _session_key(evt)
            if key and key in qualifying_stim_keys:
                wakeup_keys.add(key)
        wakeup_count = len(wakeup_keys)
        wakeup_rate = wakeup_count / stim_count if stim_count > 0 else 0

        # Per-user wake-up distribution
        user_stim_counts: dict[str, int] = {}
        user_wakeup_counts: dict[str, int] = {}
        for key in qualifying_stim_keys:
            uid = key.split("_")[0]
            user_stim_counts[uid] = user_stim_counts.get(uid, 0) + 1
        for key in wakeup_keys:
            uid = key.split("_")[0]
            user_wakeup_counts[uid] = user_wakeup_counts.get(uid, 0) + 1
        per_user_wakeup = []
        for uid, stim_n in user_stim_counts.items():
            wake_n = user_wakeup_counts.get(uid, 0)
            rate = wake_n / stim_n if stim_n > 0 else 0
            per_user_wakeup.append({
                "user_id": uid,
                "stim_sessions": stim_n,
                "wakeups": wake_n,
                "wakeup_rate": round(rate, 4),
            })
        per_user_wakeup.sort(key=lambda x: x["wakeup_rate"], reverse=True)

        # ── SW efficacy (stim vs no-stim, deduped night-level >=4h sessions) ──
        # With 6PM night grouping, fragments within the same sleep night are
        # combined before dedup, ensuring full-night SW counts.
        sw_prop = discovered.get("slow_wave_count")
        if not sw_prop:
            all_keys: set[str] = set()
            for evt in session_stats_events[:100]:
                all_keys.update(evt.get("properties", {}).keys())
            for variant in ["slowWaveCount", "slow_wave_count", "stimulation_count", "sws_count"]:
                if variant in all_keys:
                    sw_prop = variant
                    break
        sw_prop_found = sw_prop is not None

        if not sw_prop_found:
            log.warning(
                "No slow wave count property found in session_statistics events. "
                "Tried: slowWaveCount, slow_wave_count, stimulation_count, sws_count"
            )

        # Session-level flat lists (for overall means) + per-user grouping
        stim_sw_vals = []
        no_stim_sw_vals = []
        eff_stim_users = set()
        eff_no_stim_users = set()
        user_stim_sw: dict[str, list[int]] = {}
        user_no_stim_sw: dict[str, list[int]] = {}

        for sess in qualifying:
            uid = sess["user_id"]
            props = sess["properties"]
            ts = sess["timestamp"]
            sess_key = _night_key_from_ts(uid, ts)
            is_stim = sess_key in qualifying_stim_keys

            sw_count = 0
            if sw_prop:
                raw_sw = props.get(sw_prop)
                if raw_sw is not None:
                    try:
                        sw_count = int(float(raw_sw))
                    except (ValueError, TypeError):
                        sw_count = 0

            if is_stim:
                stim_sw_vals.append(sw_count)
                eff_stim_users.add(uid)
                user_stim_sw.setdefault(uid, []).append(sw_count)
            else:
                no_stim_sw_vals.append(sw_count)
                eff_no_stim_users.add(uid)
                user_no_stim_sw.setdefault(uid, []).append(sw_count)

        # Session-level aggregates
        sw_stim_mean = statistics.mean(stim_sw_vals) if stim_sw_vals else 0
        sw_no_stim_mean = statistics.mean(no_stim_sw_vals) if no_stim_sw_vals else 0
        sw_stim_median = statistics.median(stim_sw_vals) if stim_sw_vals else 0
        sw_no_stim_median = statistics.median(no_stim_sw_vals) if no_stim_sw_vals else 0
        sw_boost_ratio = sw_stim_mean / sw_no_stim_mean if sw_no_stim_mean > 0 else None
        sw_percent_diff = ((sw_stim_mean - sw_no_stim_mean) / sw_no_stim_mean * 100) if sw_no_stim_mean > 0 else None

        # Per-user then population aggregation (>=2 stim AND >=2 no-stim per user)
        qualifying_efficacy_users = []
        sw_per_user_details = []
        for uid in set(user_stim_sw.keys()) & set(user_no_stim_sw.keys()):
            u_stim_vals = user_stim_sw[uid]
            u_no_stim_vals = user_no_stim_sw[uid]
            if len(u_stim_vals) >= 2 and len(u_no_stim_vals) >= 2:
                u_stim_mean = statistics.mean(u_stim_vals)
                u_no_stim_mean = statistics.mean(u_no_stim_vals)
                u_stim_median = statistics.median(u_stim_vals)
                u_no_stim_median = statistics.median(u_no_stim_vals)
                u_pct_diff = ((u_stim_mean - u_no_stim_mean) / u_no_stim_mean * 100) if u_no_stim_mean > 0 else 0
                qualifying_efficacy_users.append(u_pct_diff)
                sw_per_user_details.append({
                    "user_id": uid,
                    "stim_sessions": len(u_stim_vals),
                    "no_stim_sessions": len(u_no_stim_vals),
                    "stim_sw_mean": round(u_stim_mean, 1),
                    "no_stim_sw_mean": round(u_no_stim_mean, 1),
                    "stim_sw_median": round(u_stim_median, 1),
                    "no_stim_sw_median": round(u_no_stim_median, 1),
                    "percent_diff": round(u_pct_diff, 1),
                })

        pop_median_pct_diff = statistics.median(qualifying_efficacy_users) if qualifying_efficacy_users else 0
        pop_std_pct_diff = statistics.stdev(qualifying_efficacy_users) if len(qualifying_efficacy_users) >= 2 else 0
        sw_insufficient_users = len(qualifying_efficacy_users) == 0

        # ── Stim events for wearable staging cross-reference ──
        # Build session time windows for validation
        session_time_windows: dict[str, tuple[float, float]] = {}
        for sess in qualifying:
            uid = sess["user_id"]
            ts = sess["timestamp"]
            night_key = _night_key_from_ts(uid, ts)
            props = sess["properties"]
            total_hrs = 0
            if total_sleep_key:
                try:
                    total_hrs = float(props.get(total_sleep_key, 0))
                except (ValueError, TypeError):
                    pass
            session_start = ts
            session_end = ts + (total_hrs * 3600) if total_hrs > 0 else ts + 28800
            if night_key not in session_time_windows:
                session_time_windows[night_key] = (session_start, session_end)
            else:
                existing = session_time_windows[night_key]
                session_time_windows[night_key] = (
                    min(existing[0], session_start),
                    max(existing[1], session_end),
                )

        stim_event_list = []
        stim_outside_window = 0
        for evt in stim_starts:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            ts = props.get("time", 0)
            if uid and ts:
                key = _night_key_from_ts(uid, ts)
                if key in qualifying_stim_keys:
                    # Validate stim timestamp falls within session window
                    window = session_time_windows.get(key)
                    in_window = True
                    if window:
                        if ts < window[0] - 1800 or ts > window[1] + 1800:
                            in_window = False
                            stim_outside_window += 1
                    dt = datetime.fromtimestamp(ts, tz=PST_OFFSET)
                    # Use night date (6PM cutoff) for the date field
                    if dt.hour < 18:
                        night_date = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
                    else:
                        night_date = dt.strftime('%Y-%m-%d')
                    stim_event_list.append({
                        "user_id": uid,
                        "timestamp": ts,
                        "date": night_date,
                        "in_session_window": in_window,
                    })

        # ── Qualifying session list for Firebase guardrail cross-reference ──
        qualifying_session_list = []
        for sess in qualifying:
            uid = sess["user_id"]
            ts = sess["timestamp"]
            night_key = _night_key_from_ts(uid, ts)
            # Extract night date from key (uid_YYYY-MM-DD)
            night_date = night_key.split("_", 1)[1] if "_" in night_key else ""
            qualifying_session_list.append({
                "user_id": uid,
                "date": night_date,
                "is_stim": night_key in qualifying_stim_keys,
            })

        log.info(
            f"Audio stim dashboard ({days}d): {stim_count} stim, "
            f"{no_stim_count} no-stim, {total_qualifying} qualifying (>=4h) / "
            f"{total_raw_sessions} raw, stim_proportion={activation_rate:.1%}, "
            f"wakeup_rate={wakeup_rate:.1%}, "
            f"SW pct_diff={'N/A' if sw_percent_diff is None else f'{sw_percent_diff:.1f}%'}, "
            f"SW pop_median={pop_median_pct_diff:.1f}% ({len(qualifying_efficacy_users)} users)"
        )

        return {
            "days": days,
            # Session counts (all >=4h qualifying, night-level via 6PM cutoff)
            "stim_sessions": stim_count,
            "no_stim_sessions": no_stim_count,
            "total_qualifying_sessions": total_qualifying,
            "total_raw_sessions": total_raw_sessions,
            "activation_rate": round(activation_rate, 4),
            "stim_night_proportion": round(activation_rate, 4),  # alias
            "unique_stim_users": len(qualifying_stim_users),
            "unique_qualifying_users": len(qualifying_users),
            # Stim duration (simple sum of all start/end blocks per night)
            "stim_duration_mean_min": round(mean_duration, 1),
            "stim_duration_median_min": round(median_duration, 1),
            "stim_duration_total_min": round(total_duration, 1),
            "stim_intervals_per_session": round(mean_intervals, 1),
            "sessions_with_duration": len(session_durations),
            "dropped_intervals_over_cap": dropped_over_cap,
            # Wake-up rate
            "wakeup_during_stim": wakeup_count,
            "wakeup_rate": round(wakeup_rate, 4),
            "per_user_wakeup": per_user_wakeup,
            # Efficacy -- SW (stim vs no-stim) -- session-level
            "sw_stim_mean": round(sw_stim_mean, 1),
            "sw_no_stim_mean": round(sw_no_stim_mean, 1),
            "sw_stim_median": round(sw_stim_median, 1),
            "sw_no_stim_median": round(sw_no_stim_median, 1),
            "sw_boost_ratio": round(sw_boost_ratio, 2) if sw_boost_ratio is not None else None,
            "sw_percent_diff": round(sw_percent_diff, 1) if sw_percent_diff is not None else None,
            "efficacy_stim_sessions": len(stim_sw_vals),
            "efficacy_no_stim_sessions": len(no_stim_sw_vals),
            "efficacy_stim_users": len(eff_stim_users),
            "efficacy_no_stim_users": len(eff_no_stim_users),
            # Efficacy -- SW per-user population (>=2 stim + >=2 no-stim each)
            "sw_per_user_population_median_pct_diff": round(pop_median_pct_diff, 1),
            "sw_per_user_population_std": round(pop_std_pct_diff, 1),
            "sw_qualifying_user_count": len(qualifying_efficacy_users),
            "sw_per_user_details": sw_per_user_details,
            "sw_insufficient_users": sw_insufficient_users,
            # SW property detection
            "sw_property_found": sw_prop_found,
            "sw_property_name": sw_prop,
            # Stim session window validation
            "stim_outside_session_window": stim_outside_window,
            # For Firebase cross-reference (eng_dashboard.py)
            "stim_events_for_staging": stim_event_list,
            "qualifying_sessions_for_guardrails": qualifying_session_list,
            # Per-night stim details (for slow_wave cross-reference in frontend)
            "stim_night_details": stim_night_details,
            # Meta
            "from_date": from_date,
            "to_date": to_date,
        }

    # ── HWE: Session Completion & Failure Modes ────────────────────────

    def get_hwe_session_completion(self, days: int = 90) -> dict:
        """HWE session completion & failure mode breakdown with WoW trends.

        API budget: 1 Export call (2 event types, ~90 days).

        Failure mode classification (priority order, first match wins):
          1. Battery died -- batteryL == 0 or batteryR == 0
          2. Disconnect-heavy -- total BLE+Classic disconnects >= 5
          3. Short session -- matched start->end duration < 2 hours
          4. Normal completion -- none of the above
        Separately: Incomplete -- started with no matching end (same uid+date).

        Returns:
            Overall totals + per-ISO-week arrays for charting.
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            ["sleep_session_started", "sleep_session_end"],
            from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        # ── Partition ──
        starts: list[dict] = []
        ends: list[dict] = []
        for evt in events:
            name = evt.get("event", "")
            if name == "sleep_session_started":
                starts.append(evt)
            elif name == "sleep_session_end":
                ends.append(evt)

        # ── Helpers ──
        def _evt_uid(evt: dict) -> str:
            props = evt.get("properties", {})
            return props.get("$user_id") or props.get("distinct_id") or ""

        def _evt_pst_dt(evt: dict) -> datetime:
            ts = evt.get("properties", {}).get("time", 0)
            return datetime.fromtimestamp(ts, tz=PST_OFFSET)

        def _session_key(evt: dict) -> str:
            """uid + PST date as session key."""
            return f"{_evt_uid(evt)}_{_evt_pst_dt(evt).strftime('%Y-%m-%d')}"

        def _iso_week(evt: dict) -> str:
            return _evt_pst_dt(evt).strftime("%G-W%V")

        def _parse_int(v) -> int:
            if v is None or v == "<null>":
                return 0
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        # ── Build start-time lookup (uid+date -> earliest start ts) ──
        start_keys: dict[str, float] = {}  # session_key -> timestamp
        for evt in starts:
            key = _session_key(evt)
            ts = evt.get("properties", {}).get("time", 0)
            if key not in start_keys or ts < start_keys[key]:
                start_keys[key] = ts

        # Unique start keys + per-week start counts
        unique_start_keys: set[str] = set()
        start_week_keys: dict[str, set] = {}
        for evt in starts:
            key = _session_key(evt)
            unique_start_keys.add(key)
            wk = _iso_week(evt)
            if wk not in start_week_keys:
                start_week_keys[wk] = set()
            start_week_keys[wk].add(key)

        start_by_week = {wk: len(keys) for wk, keys in start_week_keys.items()}

        # ── Deduplicate ends by uid+date (keep latest timestamp) ──
        end_by_key: dict[str, dict] = {}
        for evt in ends:
            key = _session_key(evt)
            ts = evt.get("properties", {}).get("time", 0)
            if key not in end_by_key or ts > end_by_key[key].get("properties", {}).get("time", 0):
                end_by_key[key] = evt

        # ── Classify each ended session ──
        battery_count = 0
        disconnect_count = 0
        short_count = 0
        moderate_count = 0
        normal_count = 0

        # Sub-reason counters for short (<2h) and moderate (2-4h) sessions
        short_reasons: dict[str, int] = {"low_battery": 0, "some_disconnects": 0, "other": 0}
        moderate_reasons: dict[str, int] = {"low_battery": 0, "some_disconnects": 0, "other": 0}

        week_buckets: dict[str, dict] = {}

        for key, evt in end_by_key.items():
            props = evt.get("properties", {})
            wk = _iso_week(evt)

            if wk not in week_buckets:
                week_buckets[wk] = {
                    "ended": 0, "battery": 0, "disconnect": 0,
                    "short": 0, "moderate": 0, "normal": 0,
                }
            week_buckets[wk]["ended"] += 1

            # Priority 1: Battery died
            battery_l = props.get("batteryL")
            battery_r = props.get("batteryR")
            l_dead = battery_l is not None and (battery_l == 0 or battery_l == "0")
            r_dead = battery_r is not None and (battery_r == 0 or battery_r == "0")

            if l_dead or r_dead:
                battery_count += 1
                week_buckets[wk]["battery"] += 1
                continue

            # Priority 2: Disconnect-heavy (>= 5 total)
            ble_left = _parse_int(props.get("bleLeftDisconnectCount"))
            ble_right = _parse_int(props.get("bleRightDisconnectCount"))
            classic = _parse_int(props.get("classicDisconnectCount"))
            total_disconnects = ble_left + ble_right + classic

            if total_disconnects >= 5:
                disconnect_count += 1
                week_buckets[wk]["disconnect"] += 1
                continue

            # ── Duration-based classification (Short / Moderate / Normal) ──
            end_ts = props.get("time", 0)
            start_ts = start_keys.get(key, 0)
            duration_hours = 0
            if start_ts > 0 and end_ts > start_ts:
                duration_hours = (end_ts - start_ts) / 3600.0

            # Determine sub-reason for short/moderate sessions
            # (these already passed battery-dead and disconnect-heavy checks)
            def _sub_reason() -> str:
                """Classify probable cause for early session end."""
                # Low battery: either earbud ended below 20%
                try:
                    bl = int(battery_l) if battery_l is not None else 100
                    br = int(battery_r) if battery_r is not None else 100
                except (ValueError, TypeError):
                    bl, br = 100, 100
                if min(bl, br) < 20:
                    return "low_battery"
                # Some disconnects (1-4, below the >=5 threshold)
                if total_disconnects >= 1:
                    return "some_disconnects"
                # No clear hardware cause
                return "other"

            if duration_hours < 2.0:
                short_count += 1
                week_buckets[wk]["short"] += 1
                short_reasons[_sub_reason()] += 1
                continue

            if duration_hours < 4.0:
                moderate_count += 1
                week_buckets[wk]["moderate"] += 1
                moderate_reasons[_sub_reason()] += 1
                continue

            # Normal completion (>= 4h)
            normal_count += 1
            week_buckets[wk]["normal"] += 1

        # ── Incomplete sessions (started, no matching end) ──
        ended_keys = set(end_by_key.keys())
        incomplete_keys = unique_start_keys - ended_keys

        incomplete_week_keys: dict[str, set] = {}
        for evt in starts:
            key = _session_key(evt)
            if key in incomplete_keys:
                wk = _iso_week(evt)
                if wk not in incomplete_week_keys:
                    incomplete_week_keys[wk] = set()
                incomplete_week_keys[wk].add(key)

        incomplete_by_week = {wk: len(keys) for wk, keys in incomplete_week_keys.items()}

        # ── Assemble WoW arrays (chronological) ──
        all_weeks = sorted(set(
            list(start_by_week.keys()) +
            list(week_buckets.keys()) +
            list(incomplete_by_week.keys())
        ))

        weeks = all_weeks
        wow_started = [start_by_week.get(w, 0) for w in weeks]
        wow_ended = [week_buckets.get(w, {}).get("ended", 0) for w in weeks]
        wow_completion = [
            (wow_ended[i] / wow_started[i]) if wow_started[i] > 0 else 0
            for i in range(len(weeks))
        ]
        wow_battery = [week_buckets.get(w, {}).get("battery", 0) for w in weeks]
        wow_disconnect = [week_buckets.get(w, {}).get("disconnect", 0) for w in weeks]
        wow_short = [week_buckets.get(w, {}).get("short", 0) for w in weeks]
        wow_moderate = [week_buckets.get(w, {}).get("moderate", 0) for w in weeks]
        wow_normal = [week_buckets.get(w, {}).get("normal", 0) for w in weeks]
        wow_incomplete = [incomplete_by_week.get(w, 0) for w in weeks]

        # ── Overall totals ──
        total_started = len(unique_start_keys)
        total_ended = len(end_by_key)
        incomplete_count = len(incomplete_keys)
        completion_rate = total_ended / total_started if total_started > 0 else 0

        battery_rate = battery_count / total_ended if total_ended > 0 else 0
        disconnect_rate = disconnect_count / total_ended if total_ended > 0 else 0
        short_rate = short_count / total_ended if total_ended > 0 else 0
        moderate_rate = moderate_count / total_ended if total_ended > 0 else 0
        incomplete_rate = incomplete_count / total_started if total_started > 0 else 0

        log.info(
            f"HWE session completion ({days}d): {total_started} started, "
            f"{total_ended} ended ({completion_rate:.1%}), "
            f"battery={battery_count}, disconnect={disconnect_count}, "
            f"short={short_count} {dict(short_reasons)}, "
            f"moderate={moderate_count} {dict(moderate_reasons)}, "
            f"normal={normal_count}, "
            f"incomplete={incomplete_count}"
        )

        return {
            "days": days,
            "from_date": from_date,
            "to_date": to_date,
            # Overall totals
            "total_started": total_started,
            "total_ended": total_ended,
            "completion_rate": round(completion_rate, 4),
            "battery_abort_count": battery_count,
            "battery_abort_rate": round(battery_rate, 4),
            "disconnect_abort_count": disconnect_count,
            "disconnect_abort_rate": round(disconnect_rate, 4),
            "short_session_count": short_count,
            "short_session_rate": round(short_rate, 4),
            "short_reasons": dict(short_reasons),
            "moderate_session_count": moderate_count,
            "moderate_session_rate": round(moderate_rate, 4),
            "moderate_reasons": dict(moderate_reasons),
            "normal_completion_count": normal_count,
            "incomplete_count": incomplete_count,
            "incomplete_rate": round(incomplete_rate, 4),
            # WoW arrays (chronological)
            "weeks": weeks,
            "wow_started": wow_started,
            "wow_ended": wow_ended,
            "wow_completion_rate": [round(v, 4) for v in wow_completion],
            "wow_battery": wow_battery,
            "wow_disconnect": wow_disconnect,
            "wow_short": wow_short,
            "wow_moderate": wow_moderate,
            "wow_normal": wow_normal,
            "wow_incomplete": wow_incomplete,
        }

    # ── Tips & Wings ───────────────────────────────────────────────────

    def get_tips_wings_distribution(self) -> dict:
        """Tips & wings size distribution from Engage API user profiles.

        Primary source for tip/wing sizes -- reads profile properties
        `tips_size` (or `left_tip_size`) and `wings_size`.

        Returns per-user dict for merging with Firebase in eng_dashboard.py.
        API budget: 1 Engage call (paginated).
        """
        profiles = self.get_profiles(exclude_internal=True)

        per_user: dict[str, dict] = {}
        for p in profiles:
            props = p.get("$properties", {})
            uid = p.get("$distinct_id", "")
            if not uid:
                continue
            tip = (props.get("tips_size") or props.get("left_tip_size") or "").strip().lower()
            wing = (props.get("wings_size") or "").strip().lower()
            if tip or wing:
                per_user[uid] = {"tip": tip, "wing": wing}

        log.info(
            f"Tips/wings from profiles: {len(per_user)} users with sizes "
            f"out of {len(profiles)} total profiles"
        )

        return {
            "per_user": per_user,
            "source": "mixpanel_profiles",
            "total_profiles": len(profiles),
            "profiles_with_sizes": len(per_user),
        }

    def get_tips_wings_from_events(self, days: int = 90) -> dict:
        """Tips & wings sizes from tips_wings_replaced events (fallback source).

        Uses each user's most recent replacement to determine current size.
        API budget: 1 Export call.

        Returns per-user dict for merging with other sources.
        """
        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            ["tips_wings_replaced"], from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        # Keep latest replacement per user
        latest_by_user: dict[str, dict] = {}
        for evt in events:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if not uid:
                continue
            ts = props.get("time", 0)
            if uid not in latest_by_user or ts > latest_by_user[uid].get("properties", {}).get("time", 0):
                latest_by_user[uid] = evt

        per_user: dict[str, dict] = {}
        for uid, evt in latest_by_user.items():
            props = evt.get("properties", {})
            tip = (props.get("new_tips_size") or "").lower().strip()
            wing = (props.get("new_wings_size") or "").lower().strip()
            if tip or wing:
                per_user[uid] = {"tip": tip, "wing": wing}

        log.info(
            f"Tips/wings from events ({days}d): {len(per_user)} users "
            f"from {len(events)} replacement events"
        )

        return {
            "per_user": per_user,
            "source": "mixpanel_events",
            "total_replacements": len(events),
        }

    def get_tips_wings_replacement_history(self, days: int = 180) -> dict:
        """Replacement history: WoW activity + 4-category change classification.

        Uses within-event old/new comparison for each replacement to classify:
          - same_size:  old == new for both tips and wings (wear-and-tear swap)
          - tip_only:   tips size changed, wings stayed the same
          - wing_only:  wings size changed, tips stayed the same
          - both:       both tips and wings sizes changed

        API budget: 1 Export call.

        Returns:
            WoW arrays per category, transition counts, summary stats.
        """
        from collections import defaultdict, Counter

        from_date, to_date = self._pst_date_range(days)
        events = self._export_events(
            ["tips_wings_replaced"], from_date, to_date,
        )
        events = [e for e in events if not self._filter_internal_event(e)]

        # ── Build per-user replacement history (chronological) ──
        user_history: dict[str, list[dict]] = defaultdict(list)
        for evt in events:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if not uid:
                continue
            ts = props.get("time", 0)
            insert_id = props.get("$insert_id") or ""
            user_history[uid].append({
                "ts": ts,
                "insert_id": insert_id,
                "old_tip": (props.get("old_tips_size") or "").lower().strip(),
                "new_tip": (props.get("new_tips_size") or "").lower().strip(),
                "old_wing": (props.get("old_wings_size") or "").lower().strip(),
                "new_wing": (props.get("new_wings_size") or "").lower().strip(),
            })

        for uid in user_history:
            user_history[uid].sort(key=lambda x: x["ts"])

        # ── Deduplicate by $insert_id (SDK re-send removal) ──
        pre_insert_id_count = sum(len(h) for h in user_history.values())
        for uid in list(user_history.keys()):
            seen_ids: set[str] = set()
            unique_events: list[dict] = []
            for h in user_history[uid]:
                iid = h.get("insert_id", "")
                if iid and iid in seen_ids:
                    continue  # skip SDK re-send
                if iid:
                    seen_ids.add(iid)
                unique_events.append(h)
            user_history[uid] = unique_events
        post_insert_id_count = sum(len(h) for h in user_history.values())
        insert_id_dedup_removed = pre_insert_id_count - post_insert_id_count

        # ── Helper: classify a replacement event ──
        def _classify_event(h: dict) -> str:
            """Classify a replacement: same_size, tip_only, wing_only, or both."""
            tc = h["old_tip"] and h["new_tip"] and h["old_tip"] != h["new_tip"]
            wc = h["old_wing"] and h["new_wing"] and h["old_wing"] != h["new_wing"]
            if tc and wc:
                return "both"
            elif tc:
                return "tip_only"
            elif wc:
                return "wing_only"
            return "same_size"

        # ── Deduplicate: one event per (user, PST_date, change_type) ──
        raw_event_count = sum(len(h) for h in user_history.values())
        for uid in list(user_history.keys()):
            seen: set[tuple[str, str]] = set()
            deduped: list[dict] = []
            for h in user_history[uid]:
                dt = datetime.fromtimestamp(h["ts"], tz=PST_OFFSET)
                day_str = dt.strftime("%Y-%m-%d")
                change_type = _classify_event(h)
                key = (day_str, change_type)
                if key not in seen:
                    seen.add(key)
                    deduped.append(h)
            user_history[uid] = deduped
        deduped_event_count = sum(len(h) for h in user_history.values())
        dedup_removed = raw_event_count - deduped_event_count
        single_replacement_users = sum(1 for h in user_history.values() if len(h) == 1)

        # ── Classify each replacement using within-event old/new fields ──
        # 4 categories: same_size, tip_only, wing_only, both
        week_same: dict[str, int] = defaultdict(int)
        week_tip_only: dict[str, int] = defaultdict(int)
        week_wing_only: dict[str, int] = defaultdict(int)
        week_both: dict[str, int] = defaultdict(int)
        week_users: dict[str, set] = defaultdict(set)

        # Transition counters
        tip_transitions: Counter = Counter()
        wing_transitions: Counter = Counter()

        # Per-user tracking
        size_changers: set[str] = set()       # users who changed any size
        tip_changers: set[str] = set()        # users who changed tips
        wing_changers: set[str] = set()       # users who changed wings
        both_changers: set[str] = set()       # users who changed both (in a single event)
        multi_replacement_users = 0

        # Category totals
        total_same = 0
        total_tip_only = 0
        total_wing_only = 0
        total_both = 0

        # ── Time-between-replacement bins ──
        TIME_BIN_EDGES = [0, 7, 14, 28, 60, 90, 180]
        TIME_BIN_LABELS = ["0-7d", "1-2w", "2-4w", "1-2mo", "2-3mo", "3-6mo"]
        tbin_same: dict[str, int] = defaultdict(int)
        tbin_tip: dict[str, int] = defaultdict(int)
        tbin_wing: dict[str, int] = defaultdict(int)
        tbin_both: dict[str, int] = defaultdict(int)
        tbin_users: dict[str, set] = defaultdict(set)

        def _time_bin(delta_days: float) -> str:
            """Map a time delta (days) to a bin label."""
            for j in range(len(TIME_BIN_EDGES) - 1):
                if delta_days < TIME_BIN_EDGES[j + 1]:
                    return TIME_BIN_LABELS[j]
            return TIME_BIN_LABELS[-1]

        for uid, history in user_history.items():
            if len(history) >= 2:
                multi_replacement_users += 1

                # Compute inter-replacement time bins
                for i in range(1, len(history)):
                    delta_days = (history[i]["ts"] - history[i - 1]["ts"]) / 86400.0
                    blab = _time_bin(delta_days)
                    ctype = _classify_event(history[i])
                    if ctype == "both":
                        tbin_both[blab] += 1
                    elif ctype == "tip_only":
                        tbin_tip[blab] += 1
                    elif ctype == "wing_only":
                        tbin_wing[blab] += 1
                    else:
                        tbin_same[blab] += 1
                    tbin_users[blab].add(uid)

            for h in history:
                dt = datetime.fromtimestamp(h["ts"], tz=PST_OFFSET)
                wk = dt.strftime("%G-W%V")
                week_users[wk].add(uid)

                ctype = _classify_event(h)
                tip_changed = ctype in ("tip_only", "both")
                wing_changed = ctype in ("wing_only", "both")

                # Track transitions
                if tip_changed:
                    tip_transitions[
                        f"{h['old_tip'].upper()}\u2192{h['new_tip'].upper()}"
                    ] += 1
                    tip_changers.add(uid)
                if wing_changed:
                    wing_transitions[
                        f"{h['old_wing'].upper()}\u2192{h['new_wing'].upper()}"
                    ] += 1
                    wing_changers.add(uid)

                # Classify into 4 categories
                if ctype == "both":
                    week_both[wk] += 1
                    total_both += 1
                    both_changers.add(uid)
                    size_changers.add(uid)
                elif ctype == "tip_only":
                    week_tip_only[wk] += 1
                    total_tip_only += 1
                    size_changers.add(uid)
                elif ctype == "wing_only":
                    week_wing_only[wk] += 1
                    total_wing_only += 1
                    size_changers.add(uid)
                else:
                    week_same[wk] += 1
                    total_same += 1

        # ── Assemble WoW arrays ──
        all_weeks = sorted(set(
            list(week_same.keys()) + list(week_tip_only.keys()) +
            list(week_wing_only.keys()) + list(week_both.keys())
        ))

        weeks = all_weeks
        wow_same = [week_same.get(w, 0) for w in weeks]
        wow_tip_only = [week_tip_only.get(w, 0) for w in weeks]
        wow_wing_only = [week_wing_only.get(w, 0) for w in weeks]
        wow_both = [week_both.get(w, 0) for w in weeks]
        wow_users = [len(week_users.get(w, set())) for w in weeks]

        total_replacements = deduped_event_count
        total_changes = total_tip_only + total_wing_only + total_both
        change_rate = total_changes / total_replacements if total_replacements else 0

        log.info(
            f"Tips/wings replacement history ({days}d): "
            f"{len(user_history)} users, {total_replacements} replacements "
            f"(dedup removed {dedup_removed}: {raw_event_count}→{deduped_event_count}), "
            f"{len(size_changers)} size changers "
            f"(tip_only={total_tip_only}, wing_only={total_wing_only}, "
            f"both={total_both}, same_size={total_same}, "
            f"{change_rate:.1%} change rate, "
            f"single_event_users={single_replacement_users})"
        )

        return {
            "days": days,
            "from_date": from_date,
            "to_date": to_date,
            # Summary
            "total_users": len(user_history),
            "total_replacements": total_replacements,
            "raw_events": raw_event_count,
            "insert_id_dedup_removed": insert_id_dedup_removed,
            "deduped_events": deduped_event_count,
            "dedup_removed": dedup_removed,
            "single_replacement_users": single_replacement_users,
            "multi_replacement_users": multi_replacement_users,
            "size_changers": len(size_changers),
            "tip_changers": len(tip_changers),
            "wing_changers": len(wing_changers),
            "both_changers": len(both_changers),
            "total_size_changes": total_changes,
            "change_rate": round(change_rate, 4),
            # Category totals
            "total_same_size": total_same,
            "total_tip_only": total_tip_only,
            "total_wing_only": total_wing_only,
            "total_both_changed": total_both,
            # Transitions (top 10 each)
            "tip_transitions": dict(tip_transitions.most_common(10)),
            "wing_transitions": dict(wing_transitions.most_common(10)),
            # WoW arrays (4 categories + user count)
            "weeks": weeks,
            "wow_same_size": wow_same,
            "wow_tip_only": wow_tip_only,
            "wow_wing_only": wow_wing_only,
            "wow_both_changed": wow_both,
            "wow_unique_users": wow_users,
            # Time-between-replacement bins (users with ≥2 replacements)
            "time_bins": TIME_BIN_LABELS,
            "time_bin_same_size": [tbin_same.get(b, 0) for b in TIME_BIN_LABELS],
            "time_bin_tip_only": [tbin_tip.get(b, 0) for b in TIME_BIN_LABELS],
            "time_bin_wing_only": [tbin_wing.get(b, 0) for b in TIME_BIN_LABELS],
            "time_bin_both": [tbin_both.get(b, 0) for b in TIME_BIN_LABELS],
            "time_bin_users": [len(tbin_users.get(b, set())) for b in TIME_BIN_LABELS],
            "time_bin_total_intervals": sum(
                tbin_same.get(b, 0) + tbin_tip.get(b, 0) +
                tbin_wing.get(b, 0) + tbin_both.get(b, 0)
                for b in TIME_BIN_LABELS
            ),
        }

    # ── Internal Replacement Schedule ─────────────────────────────────

    def get_internal_replacement_schedule(self, days: int = 180) -> dict:
        """Replacement schedule for internal users only.

        For each internal user, computes:
          - Current tip/wing sizes (from profiles)
          - Last replacement date (from tips_wings_replaced events)
          - Days since last replacement
          - Qualifying sleep sessions (≥90 min) since last replacement
          - Sessions remaining until target (21)
          - Status: overdue (≥21), due_soon (18-20), ok (<18), never_replaced

        API budget: 1 Engage + 2 Export calls.
        """
        from collections import defaultdict

        TARGET_SESSIONS = 21
        DUE_SOON_THRESHOLD = 18  # sessions_since >= this → due_soon

        # ── 1. Get internal-only profiles ──
        all_profiles = self.get_profiles(exclude_internal=False)
        internal_profiles: dict[str, dict] = {}
        for p in all_profiles:
            props = p.get("$properties", {})
            email = (props.get("$email") or "").lower()
            cohort = props.get("user_cohort", "")
            is_internal = cohort == "Internal" or any(
                email.endswith(d) for d in INTERNAL_DOMAINS
            )
            if not is_internal:
                continue
            uid = p.get("$distinct_id", "")
            if not uid:
                continue
            tip = (props.get("tips_size") or props.get("left_tip_size") or "").strip().lower()
            wing = (props.get("wings_size") or "").strip().lower()
            name = (props.get("$name") or props.get("$first_name") or "").strip()
            region = (props.get("$region") or props.get("region") or "").strip()
            city = (props.get("$city") or "").strip()
            location = f"{city}, {region}" if city and region else (region or city or "")
            internal_profiles[uid] = {
                "email": email or uid,
                "uid": uid,
                "name": name,
                "location": location,
                "tip_size": tip,
                "wing_size": wing,
            }

        log.info(f"Internal replacement schedule: {len(internal_profiles)} internal profiles")

        if not internal_profiles:
            return {
                "users": [],
                "summary": {
                    "total_internal": 0, "overdue": 0, "due_soon": 0,
                    "ok": 0, "never_replaced": 0,
                },
                "target_sessions": TARGET_SESSIONS,
            }

        internal_uids = set(internal_profiles.keys())

        # ── 2. Get replacement events for internal users ──
        # NOTE: Export API events don't carry $email or user_cohort properties,
        # so _filter_internal_event() won't work. Instead, filter by matching
        # the event's user_id against the known internal_uids from profiles.
        from_date, to_date = self._pst_date_range(days)
        all_replace_events = self._export_events(
            ["tips_wings_replaced"], from_date, to_date,
        )

        # Find latest replacement per internal user (filter by uid match)
        latest_replacement: dict[str, dict] = {}
        internal_replace_count = 0
        for evt in all_replace_events:
            props = evt.get("properties", {})
            uid = props.get("$user_id") or props.get("distinct_id") or ""
            if uid not in internal_uids:
                continue
            internal_replace_count += 1
            ts = props.get("time", 0)
            if uid not in latest_replacement or ts > latest_replacement[uid]["ts"]:
                latest_replacement[uid] = {
                    "ts": ts,
                    "new_tip": (props.get("new_tips_size") or "").lower().strip(),
                    "new_wing": (props.get("new_wings_size") or "").lower().strip(),
                }

        log.info(
            f"Internal replacements: {internal_replace_count} internal events "
            f"(of {len(all_replace_events)} total), "
            f"{len(latest_replacement)} users with replacement history"
        )

        # ── 3. Get sleep sessions for internal users ──
        # Export all session_statistics, then filter qualifying sessions,
        # then keep only those belonging to internal users (by uid match).
        all_session_events = self._export_events(
            ["session_statistics"], from_date, to_date,
        )

        # Filter to qualifying nighttime sleeps (≥90 min) first (on all events)
        qualifying = self._get_qualifying_sessions(all_session_events, min_hours=1.5)

        # Group by internal user only
        sessions_by_user: dict[str, list[float]] = defaultdict(list)
        internal_session_count = 0
        for q in qualifying:
            uid = q["user_id"]
            if uid in internal_uids:
                internal_session_count += 1
                sessions_by_user[uid].append(q["timestamp"])

        log.info(
            f"Internal sessions: {len(all_session_events)} total raw, "
            f"{len(qualifying)} qualifying (all users), "
            f"{internal_session_count} internal qualifying, "
            f"{len(sessions_by_user)} internal users with sessions"
        )

        # ── 4. Compute per-user schedule ──
        now_ts = datetime.now(PST_OFFSET).timestamp()
        users_out: list[dict] = []
        counts = {"overdue": 0, "due_soon": 0, "ok": 0, "never_replaced": 0}

        for uid, profile in internal_profiles.items():
            entry = {**profile}

            if uid in latest_replacement:
                rep = latest_replacement[uid]
                rep_ts = rep["ts"]
                rep_date = datetime.fromtimestamp(rep_ts, tz=PST_OFFSET)
                entry["last_replaced"] = rep_date.strftime("%Y-%m-%d")
                entry["days_since"] = int((now_ts - rep_ts) / 86400)

                # Update tip/wing sizes from replacement event if profile is empty
                if not entry["tip_size"] and rep["new_tip"]:
                    entry["tip_size"] = rep["new_tip"]
                if not entry["wing_size"] and rep["new_wing"]:
                    entry["wing_size"] = rep["new_wing"]

                # Count qualifying sessions AFTER last replacement
                sessions_after = sum(
                    1 for ts in sessions_by_user.get(uid, []) if ts > rep_ts
                )
                entry["sessions_since"] = sessions_after
                entry["sessions_remaining"] = TARGET_SESSIONS - sessions_after

                if sessions_after >= TARGET_SESSIONS:
                    entry["status"] = "overdue"
                    counts["overdue"] += 1
                elif sessions_after >= DUE_SOON_THRESHOLD:
                    entry["status"] = "due_soon"
                    counts["due_soon"] += 1
                else:
                    entry["status"] = "ok"
                    counts["ok"] += 1
            else:
                # No replacement event found
                entry["last_replaced"] = None
                entry["days_since"] = None
                # Count all qualifying sessions (no replacement baseline)
                all_sessions = len(sessions_by_user.get(uid, []))
                entry["sessions_since"] = all_sessions
                entry["sessions_remaining"] = TARGET_SESSIONS - all_sessions if all_sessions else None
                entry["status"] = "never_replaced"
                counts["never_replaced"] += 1

            users_out.append(entry)

        log.info(
            f"Internal schedule summary: {len(users_out)} users — "
            f"overdue={counts['overdue']}, due_soon={counts['due_soon']}, "
            f"ok={counts['ok']}, never_replaced={counts['never_replaced']}"
        )

        return {
            "users": users_out,
            "summary": {
                "total_internal": len(users_out),
                **counts,
            },
            "target_sessions": TARGET_SESSIONS,
        }
