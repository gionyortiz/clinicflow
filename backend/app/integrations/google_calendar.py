"""Google Calendar event creation using an OAuth access token from env.

When GOOGLE_ACCESS_TOKEN is not set, the function returns a stub response.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("clinicflow.gcal")


async def create_event(
    summary: str,
    start_iso: str,
    end_iso: str,
    attendee_email: str | None = None,
    description: str | None = None,
    timezone: str = "America/Puerto_Rico",
) -> dict:
    token = os.getenv("GOOGLE_ACCESS_TOKEN")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    if not token:
        log.info("[STUB GCAL] summary=%s start=%s end=%s", summary, start_iso, end_iso)
        return {"status": "stubbed", "event_id": None}

    body: dict = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
    }
    if description:
        body["description"] = description
    if attendee_email:
        body["attendees"] = [{"email": attendee_email}]

    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return {"status": "created", "event_id": data.get("id"), "html_link": data.get("htmlLink")}
