import unittest
from backend.app.algorithms.regime.classifier import _session_axis
from backend.app.algorithms.regime.exchange_calendar import exchange_session

class SessionAxisTest(unittest.TestCase):
    def test_dst_regular_session_uses_new_york_time_not_fixed_utc(self):
        self.assertEqual(_session_axis("2026-07-23T13:00:00Z"), "outside_regular")
        self.assertEqual(_session_axis("2026-07-23T13:45:00Z"), "opening")
        self.assertEqual(_session_axis("2026-07-23T16:00:00Z"), "midday")
        self.assertEqual(_session_axis("2026-07-23T18:00:00Z"), "afternoon")
        self.assertEqual(_session_axis("2026-07-23T19:45:00Z"), "closing")
        self.assertEqual(_session_axis("2026-07-23T20:15:00Z"), "outside_regular")

    def test_standard_time_regular_session(self):
        self.assertEqual(_session_axis("2026-01-06T14:15:00Z"), "outside_regular")
        self.assertEqual(_session_axis("2026-01-06T14:45:00Z"), "opening")
        self.assertEqual(_session_axis("2026-01-06T20:45:00Z"), "closing")
        self.assertEqual(_session_axis("2026-01-06T21:15:00Z"), "outside_regular")

    def test_holiday_weekend_and_early_close_calendar(self):
        self.assertEqual(_session_axis("2026-07-03T15:00:00Z"), "outside_regular")
        self.assertEqual(_session_axis("2026-07-18T15:00:00Z"), "outside_regular")
        self.assertEqual(_session_axis("2026-11-27T17:45:00Z"), "closing")
        self.assertEqual(_session_axis("2026-11-27T18:15:00Z"), "outside_regular")

    def test_timezone_offset_timestamps_are_converted(self):
        session = exchange_session("2026-07-23T09:45:00-04:00")

        self.assertEqual(session.status, "opening")
        self.assertEqual(session.session_date, "2026-07-23")
        self.assertEqual(session.reason, "regular_exchange_session")
