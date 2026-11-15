"""
research/calendar.py — Festival and season signals
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Optional

from src.utils import get_logger, load_festivals
from src.research.trends import Signal

log = get_logger("research.calendar")


def fetch_calendar_signals(today: Optional[date] = None) -> list[Signal]:
    """Return festival/season signals active today (within lead window)."""
    if today is None:
        today = date.today()

    festivals_cfg = load_festivals()
    signals = []

    # Named festivals
    for fest in festivals_cfg.get("festivals", []):
        try:
            fest_date = date.fromisoformat(fest["date"])
            lead = int(fest.get("lead_days", 5))
            # Active if within [today, fest_date] and within lead window
            days_until = (fest_date - today).days
            if 0 <= days_until <= lead:
                urgency = 1.0 - (days_until / max(lead, 1))  # 1.0 = day-of
                signals.append(Signal(
                    source="festival_calendar",
                    title=fest["name"],
                    summary=f"{fest['name']} is in {days_until} days",
                    timestamp=datetime.now(),
                    score=0.6 + 0.4 * urgency,
                    category="festival",
                    region="IN",
                    extra={
                        "festival_name": fest["name"],
                        "fest_date": fest["date"],
                        "days_until": days_until,
                        "language_preference": fest.get("language_preference"),
                        "tags": fest.get("tags", []),
                    },
                ))
        except (ValueError, KeyError) as e:
            log.warning(f"Festival parse error ({fest.get('name', '?')}): {e}")

    # Recurring seasons
    for season in festivals_cfg.get("seasons", []):
        start_m = season.get("start_month", 1)
        end_m = season.get("end_month", 12)

        in_season = (
            (start_m <= end_m and start_m <= today.month <= end_m)
            or (start_m > end_m and (today.month >= start_m or today.month <= end_m))
        )

        if in_season:
            signals.append(Signal(
                source="season_calendar",
                title=season["name"],
                summary=f"Currently in {season['name']}",
                timestamp=datetime.now(),
                score=0.4,
                category="season",
                region="IN",
                extra={
                    "season_signal": season.get("signal"),
                    "tags": season.get("tags", []),
                },
            ))

    log.info(f"Calendar: {len(signals)} signals ({len([s for s in signals if s.category=='festival'])} festival, {len([s for s in signals if s.category=='season'])} season)")
    return signals


def get_upcoming_festivals(days: int = 30) -> list[dict]:
    """Helper: print upcoming festivals (used by check_festivals.py script)."""
    today = date.today()
    upcoming = []
    for fest in load_festivals().get("festivals", []):
        try:
            fest_date = date.fromisoformat(fest["date"])
            days_until = (fest_date - today).days
            if 0 <= days_until <= days:
                upcoming.append({
                    "name": fest["name"],
                    "date": fest["date"],
                    "days_until": days_until,
                    "language": fest.get("language_preference", "auto"),
                })
        except ValueError:
            pass
    return sorted(upcoming, key=lambda x: x["days_until"])
