"""Eastern-time report slots, independent of Railway's UTC cron clock."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
SLOTS = (("fear", "09:45"), ("fear", "11:45"), ("fear", "13:45"),
         ("fear", "15:45"), ("breadth", "16:30"), ("reddit", "16:42"))
GRACE = timedelta(minutes=15)
JOB_TIMEOUT_SECONDS = 480


@lru_cache(maxsize=3)
def exchange_holidays(year):
    import holidays
    return holidays.financial_holidays("NYSE", years=year)


@dataclass(frozen=True)
class Occurrence:
    job: str
    at: datetime

    @property
    def key(self):
        return f"{self.at.astimezone(EASTERN):%Y-%m-%d:%H:%M}:{self.job}"


def candidates(now):
    """Catch delayed starts and pre-wake for the 16:42 slot; never send early."""
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    local = now.astimezone(EASTERN)
    if local.weekday() >= 5 or local.date() in exchange_holidays(local.year):
        return []
    window_end = datetime.fromtimestamp(
        (int(now.timestamp()) // 300 + 1) * 300, tz=timezone.utc)
    result = []
    for job, clock in SLOTS:
        hour, minute = map(int, clock.split(":"))
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now - GRACE <= at < window_end:
            result.append(Occurrence(job, at))
    return result
