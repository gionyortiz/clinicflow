"""Generate ICS calendar invites without external dependencies."""

from __future__ import annotations

from datetime import datetime


def _fmt_utc(dt: datetime) -> str:
    """Format a UTC datetime as ICS YYYYMMDDTHHMMSSZ."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _esc(value: str) -> str:
    """Escape ICS text fields per RFC 5545 (backslash, semicolon, comma, newline)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def build_ics(
    uid: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str = "",
    location: str = "ClinicFlow Dental",
) -> str:
    now = _fmt_utc(datetime.utcnow())
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//ClinicFlow//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{_esc(uid)}@clinicflow\r\n"
        f"DTSTAMP:{now}\r\n"
        f"DTSTART:{_fmt_utc(start)}\r\n"
        f"DTEND:{_fmt_utc(end)}\r\n"
        f"SUMMARY:{_esc(summary)}\r\n"
        f"DESCRIPTION:{_esc(description)}\r\n"
        f"LOCATION:{_esc(location)}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
