"""SMS sending via Twilio with a stub fallback when credentials are missing."""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("clinicflow.sms")


def _twilio_creds() -> tuple[str, str, str] | None:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")
    if sid and token and from_number:
        return sid, token, from_number
    return None


async def send_sms(to: str, body: str) -> dict:
    creds = _twilio_creds()
    if not creds:
        log.info("[STUB SMS] to=%s body=%s", to, body)
        return {"status": "stubbed", "to": to, "body": body}

    sid, token, from_number = creds
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            data={"To": to, "From": from_number, "Body": body},
            auth=(sid, token),
        )
        resp.raise_for_status()
        payload = resp.json()
        return {"status": payload.get("status", "sent"), "sid": payload.get("sid")}
