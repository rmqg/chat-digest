"""Time zones must resolve on hosts without a database, such as OpenWrt."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

import timezones


class NoDatabase(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(timezones, 'ZoneInfo', side_effect=ZoneInfoNotFoundError('no database'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_shanghai_falls_back_to_a_fixed_offset(self):
        zone = timezones.project_zone('Asia/Shanghai')
        self.assertEqual(zone.utcoffset(None), timedelta(hours=8))
        self.assertEqual(datetime(2026, 11, 8, 21, 0, tzinfo=timezone.utc).astimezone(zone).hour, 5)
        self.assertTrue(timezones.uses_fixed_offset('Asia/Shanghai'))

    def test_half_hour_offset(self):
        self.assertEqual(timezones.project_zone('Asia/Kolkata').utcoffset(None), timedelta(hours=5, minutes=30))

    def test_zone_with_daylight_saving_is_refused_with_advice(self):
        with self.assertRaisesRegex(ValueError, 'tzdata'):
            timezones.project_zone('Europe/Berlin')
        self.assertFalse(timezones.uses_fixed_offset('Europe/Berlin'))


class WithDatabase(unittest.TestCase):
    def test_database_is_preferred(self):
        with patch.object(timezones, 'ZoneInfo', return_value='from-database') as zone_info:
            self.assertEqual(timezones.project_zone('Asia/Shanghai'), 'from-database')
            self.assertFalse(timezones.uses_fixed_offset('Asia/Shanghai'))
        zone_info.assert_called_with('Asia/Shanghai')


if __name__ == '__main__':
    unittest.main()
