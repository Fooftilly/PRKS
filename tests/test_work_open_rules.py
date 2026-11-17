"""The open-event rules both the sync family and the direct open use (kept by PG-C2 #641).

``backend/work_open_sync.py`` decides the instant a reported open is
recorded at: a fast client clock is clamped to the arrival, and a legacy
second-resolution value compares as ``.000``. Pure functions, no database.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend import work_open_sync as opened


class OpenRuleTests(unittest.TestCase):
    def test_future_skew_is_clamped_to_arrival_not_discarded(self) -> None:
        received = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
        inside = received + timedelta(seconds=opened.MAX_CLIENT_FUTURE_SKEW_SECONDS - 1)
        horizon = received + timedelta(seconds=opened.MAX_CLIENT_FUTURE_SKEW_SECONDS)
        outside = received + timedelta(hours=6)
        self.assertEqual(opened.effective_opened_at(inside.isoformat(), received), "2026-09-11 12:04:59.000")
        self.assertEqual(opened.effective_opened_at(horizon.isoformat(), received), "2026-09-11 12:05:00.000")
        self.assertEqual(opened.effective_opened_at(outside.isoformat(), received), "2026-09-11 12:00:00.000")


if __name__ == "__main__":
    unittest.main()
