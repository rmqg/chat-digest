"""Resolve IANA time zone names, also on hosts without a time zone database.

Minimal systems such as OpenWrt/iStoreOS ship neither /usr/share/zoneinfo nor the ``tzdata`` package, so
``zoneinfo.ZoneInfo`` fails there even for common names. The host-side schedulers must still run, so zones
that do not observe daylight saving time fall back to their fixed offset. A zone that does (for example
Europe/Berlin) has no correct fixed offset, and raises an error that says what to install.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FIXED_OFFSET_HOURS = {
    'UTC': 0, 'Etc/UTC': 0, 'GMT': 0, 'Etc/GMT': 0,
    'Asia/Shanghai': 8, 'Asia/Chongqing': 8, 'Asia/Harbin': 8, 'PRC': 8, 'Asia/Hong_Kong': 8, 'Asia/Macau': 8,
    'Asia/Taipei': 8, 'Asia/Singapore': 8, 'Asia/Kuala_Lumpur': 8, 'Asia/Manila': 8, 'Asia/Tokyo': 9,
    'Asia/Seoul': 9, 'Asia/Bangkok': 7, 'Asia/Ho_Chi_Minh': 7, 'Asia/Jakarta': 7, 'Asia/Kolkata': 5.5,
    'Asia/Dubai': 4,
}


def uses_fixed_offset(name: str) -> bool:
    """True when this host has no database entry for ``name`` and the fixed-offset fallback applies."""
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return name in FIXED_OFFSET_HOURS
    return False


def project_zone(name: str):
    """Return a tzinfo for ``name``. Raises ValueError for a zone that cannot be resolved correctly."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        hours = FIXED_OFFSET_HOURS.get(name)
        if hours is None:
            raise ValueError(f'time zone {name!r} is not available on this host; install the tzdata package '
                             '(OpenWrt: opkg install zoneinfo-core and the zoneinfo package for your region)') from None
        return timezone(timedelta(hours=hours), name)
