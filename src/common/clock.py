# src/common/clock.py
import datetime

class Clock:
    """
    A mockable clock to control time in tests.
    """
    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)

# Global clock instance
# In production, this is the real clock.
# In tests, this can be patched with a mock clock.
SYSTEM_CLOCK = Clock()
