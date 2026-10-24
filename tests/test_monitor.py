import sys
import io
import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import SERIES, calculate, merge, is_trading_day, fetch, fetch_page_update


def sample(values, series=SERIES[0]):
    rows = []
    day = date(2026, 1, 5)
    for value in values:
        while not is_trading_day(day.isoformat()):
            day += timedelta(days=1)
        rows.append(dict(series=series, date=day.isoformat(), value=str(value),
                         first_seen_utc="x", last_changed_utc="x"))
        day += timedelta(days=1)
    return rows


class SignalsTest(unittest.TestCase):
    def test_fred_api_observation_and_source_time(self):
        seen = []
        def response(request, timeout):
            seen.append(request.full_url)
            if "/series/observations?" in request.full_url:
                return io.BytesIO(b'{"observations":[{"date":"2026-10-22","value":"3.40"},{"date":"2026-10-23","value":"."}]}')
            return io.BytesIO(b'{"seriess":[{"last_updated":"2026-10-23 08:34:00-05"}]}')
        with patch.dict(os.environ, {"FRED_API_KEY": "test-secret"}), patch("monitor.urlopen", side_effect=response):
            self.assertEqual(fetch(SERIES[0], "2026-10-01"), {"2026-10-22": 3.4})
            self.assertEqual(fetch_page_update(SERIES[0])["utc"], "2026-10-23T13:34:00+00:00")
        self.assertIn("observation_start=2026-10-01", seen[0])
        self.assertIn("api_key=test-secret", seen[0])

    def test_confirmed_only_after_twenty_later_observations(self):
        vals = list(range(1, 21)) + [30] + list(range(29, 9, -1))
        rows = sample(vals)
        signals = calculate(rows)
        peak = [x for x in signals if x['peak_date'] == rows[20]['date']]
        self.assertEqual([x['signal'] for x in peak], ['候选高点', '连续3日下行', '连续5日下行', '正式历史峰值'])
        self.assertEqual(peak[-1]['date'], rows[40]['date'])

    def test_tie_invalidates_and_never_confirms(self):
        vals = list(range(1, 21)) + [30, 29, 28, 30] + [20] * 20
        rows = sample(vals)
        sig = calculate(rows)
        self.assertIn('峰值失效', [x['signal'] for x in sig if x['peak_date'] == rows[20]['date']])
        self.assertNotIn('正式历史峰值', [x['signal'] for x in sig])

    def test_gap_and_interrupted_fall(self):
        vals = list(range(1, 21)) + [30, 29, 28, 28, 27, 26, 25]
        rows = sample(vals)
        sig = calculate(rows)
        three = [x for x in sig if x['signal'] == '连续3日下行']
        self.assertEqual(len(three), 1)
        self.assertEqual(three[0]['date'], rows[26]['date'])

    def test_weekend_and_regular_market_holiday_excluded(self):
        self.assertFalse(is_trading_day('2026-01-31'))
        self.assertFalse(is_trading_day('2026-09-07'))
        self.assertTrue(is_trading_day('2026-09-08'))

    def test_merge_idempotent_and_revision_audited(self):
        rows, rev, added, changed = merge([], {SERIES[0]: {'2026-01-01': 1.2}}, 't1')
        same, rev2, added2, changed2 = merge(rows, {SERIES[0]: {'2026-01-01': 1.2}}, 't2')
        self.assertEqual((added, changed, added2, changed2, rev, rev2), (1, 0, 0, 0, [], []))
        self.assertEqual(rows, same)
        newer, rev3, _, changed3 = merge(rows, {SERIES[0]: {'2026-01-01': 1.3}}, 't3')
        self.assertEqual((changed3, rev3[0]['previous'], newer[0]['first_seen_utc']), (1, '1.2', 't1'))


if __name__ == '__main__':
    unittest.main()
