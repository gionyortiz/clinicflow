from __future__ import annotations

import base64
import hashlib
import hmac as _hmac_mod
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("clinicflow")

from .db import connect, db_ok, init_db
from .integrations.google_calendar import create_event as gcal_create_event
from .integrations.ics_builder import build_ics
from .integrations.sms import send_sms


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("ClinicFlow starting — initialising database…")
    init_db()
    log.info("Database ready.")
    yield
    log.info("ClinicFlow shutdown.")


app = FastAPI(
    title="ClinicFlow",
    version="1.0.0",
    description="AI Receptionist for Dental Clinics",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static frontend ───────────────────────────────────────────────────────────
_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
if _FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_dashboard():
        return FileResponse(str(_FRONTEND / "index.html"))

    @app.get("/landing", include_in_schema=False)
    async def serve_landing():
        return FileResponse(str(_FRONTEND / "landing.html"))


# ── Global validation error handler (returns structured JSON, not FastAPI default) ──

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(loc) for loc in e["loc"]), "message": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


# ── Type aliases ─────────────────────────────────────────────────────────────

LeadStatus = Literal["new", "contacted", "booked", "no_show"]
LeadSource = Literal["web", "missed_call", "sms", "other"]

_PHONE_RE = re.compile(r"^[+\d][\d\s().\-]{5,28}$")


# ── Request models ────────────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr | None = None
    service: str = Field(min_length=2, max_length=80)
    preferred_time: str = Field(min_length=2, max_length=80)
    source: LeadSource = "web"

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str) -> str:
        if not _PHONE_RE.match(v):
            raise ValueError("Invalid phone number format")
        return v

    @field_validator("full_name")
    @classmethod
    def name_printable(cls, v: str) -> str:
        if not v.isprintable():
            raise ValueError("Name contains non-printable characters")
        return v.strip()


class LeadStatusUpdate(BaseModel):
    status: LeadStatus


class BookingCreate(BaseModel):
    lead_id: int = Field(gt=0)
    start_time: datetime
    duration_minutes: int = Field(default=30, ge=15, le=240)
    notes: str | None = Field(default=None, max_length=500)
    push_to_google: bool = False

    @field_validator("start_time")
    @classmethod
    def start_not_in_past(cls, v: datetime) -> datetime:
        if v < datetime.utcnow() - timedelta(minutes=5):
            raise ValueError("Booking start_time cannot be in the past")
        return v


class SmsSend(BaseModel):
    to: str = Field(min_length=7, max_length=30)
    body: str = Field(min_length=1, max_length=480)
    lead_id: int | None = Field(default=None, gt=0)


# ── Twilio HMAC signature verification ────────────────────────────────────────

def _twilio_sig_valid(request: Request, form: dict[str, str]) -> bool:
    """Verify Twilio request signature. Skipped in stub/dev mode (no auth token)."""
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not token:
        return True  # dev mode — no credentials configured, allow all
    sig = request.headers.get("X-Twilio-Signature", "")
    if not sig:
        return False
    url = str(request.url)
    s = url + "".join(f"{k}{v}" for k, v in sorted(form.items()))
    mac = _hmac_mod.new(token.encode(), s.encode(), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    return _hmac_mod.compare_digest(expected, sig)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    ok = db_ok()
    if not ok:
        log.error("Health check failed: DB not reachable")
        raise HTTPException(503, "Database unavailable")
    return {"status": "ok", "db": "ok", "version": "1.0.0"}


@app.get("/api/leads")
def list_leads() -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/leads", status_code=201)
def create_lead(payload: LeadCreate) -> dict:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO leads (full_name, phone, email, service, preferred_time, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                payload.full_name,
                payload.phone,
                payload.email,
                payload.service,
                payload.preferred_time,
                payload.source,
            ),
        )
        lead_id = cur.lastrowid
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row)


@app.patch("/api/leads/{lead_id}")
def update_lead_status(lead_id: int, payload: LeadStatusUpdate) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE leads SET status = ? WHERE id = ?",
            (payload.status, lead_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Lead not found")
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row)


@app.get("/api/kpi")
def kpi() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM leads GROUP BY status"
        ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    new = counts.get("new", 0)
    return {
        "total_leads": total,
        "booked": counts.get("booked", 0),
        "new": new,
        "no_show": counts.get("no_show", 0),
        "estimated_missed_revenue": new * 180,
    }


@app.get("/api/bookings")
def list_bookings() -> dict:
    with connect() as conn:
        rows = conn.execute(
            """SELECT b.*, l.full_name, l.phone, l.email, l.service
               FROM bookings b JOIN leads l ON l.id = b.lead_id
               ORDER BY b.start_time ASC LIMIT 200"""
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/bookings", status_code=201)
async def create_booking(payload: BookingCreate) -> dict:
    end_time = payload.start_time + timedelta(minutes=payload.duration_minutes)

    with connect() as conn:
        lead = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (payload.lead_id,)
        ).fetchone()
        if not lead:
            raise HTTPException(404, "Lead not found")

        cur = conn.execute(
            """INSERT INTO bookings (lead_id, start_time, end_time, notes)
               VALUES (?, ?, ?, ?)""",
            (
                payload.lead_id,
                payload.start_time.isoformat(),
                end_time.isoformat(),
                payload.notes,
            ),
        )
        booking_id = cur.lastrowid
        conn.execute(
            "UPDATE leads SET status = 'booked' WHERE id = ?", (payload.lead_id,)
        )

    google = {"status": "skipped"}
    if payload.push_to_google:
        google = await gcal_create_event(
            summary=f"{lead['service']} - {lead['full_name']}",
            start_iso=payload.start_time.isoformat(),
            end_iso=end_time.isoformat(),
            attendee_email=lead["email"],
            description=payload.notes or "",
        )
        if google.get("event_id"):
            with connect() as conn:
                conn.execute(
                    "UPDATE bookings SET google_event_id = ? WHERE id = ?",
                    (google["event_id"], booking_id),
                )

    confirmation = (
        f"Hi {lead['full_name']}, your {lead['service']} visit is confirmed for "
        f"{payload.start_time.strftime('%a %b %d, %I:%M %p')}. Reply STOP to opt out."
    )
    sms_result = await send_sms(lead["phone"], confirmation)
    with connect() as conn:
        conn.execute(
            """INSERT INTO messages (lead_id, direction, channel, to_number, body, status)
               VALUES (?, 'out', 'sms', ?, ?, ?)""",
            (payload.lead_id, lead["phone"], confirmation, sms_result.get("status", "sent")),
        )
        row = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()

    return {"booking": dict(row), "google": google, "sms": sms_result}


@app.get("/api/bookings/{booking_id}.ics")
def booking_ics(booking_id: int) -> Response:
    with connect() as conn:
        row = conn.execute(
            """SELECT b.*, l.full_name, l.service
               FROM bookings b JOIN leads l ON l.id = b.lead_id
               WHERE b.id = ?""",
            (booking_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Booking not found")

    ics = build_ics(
        uid=f"booking-{booking_id}",
        summary=f"{row['service']} - {row['full_name']}",
        start=datetime.fromisoformat(row["start_time"]),
        end=datetime.fromisoformat(row["end_time"]),
        description=row["notes"] or "",
    )
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=booking-{booking_id}.ics"},
    )


@app.post("/api/sms/send")
async def sms_send(payload: SmsSend) -> dict:
    result = await send_sms(payload.to, payload.body)
    with connect() as conn:
        conn.execute(
            """INSERT INTO messages (lead_id, direction, channel, to_number, body, status)
               VALUES (?, 'out', 'sms', ?, ?, ?)""",
            (payload.lead_id, payload.to, payload.body, result.get("status", "sent")),
        )
    return result


@app.post("/api/sms/reminders/run")
async def run_reminders() -> dict:
    """Send 24h reminders for upcoming bookings and follow-up to no-shows."""
    now = datetime.utcnow()
    in_24h = now + timedelta(hours=24)
    in_25h = now + timedelta(hours=25)

    sent = 0
    follow_ups = 0
    with connect() as conn:
        upcoming = conn.execute(
            """SELECT b.id, b.start_time, l.phone, l.full_name, l.service
               FROM bookings b JOIN leads l ON l.id = b.lead_id
               WHERE b.status = 'scheduled'
                 AND b.start_time BETWEEN ? AND ?""",
            (in_24h.isoformat(), in_25h.isoformat()),
        ).fetchall()

        no_shows = conn.execute(
            "SELECT l.id, l.phone, l.full_name FROM leads l WHERE l.status = 'no_show'"
        ).fetchall()

    for b in upcoming:
        body = (
            f"Reminder: {b['full_name']}, your {b['service']} visit is tomorrow. "
            "Reply C to confirm or R to reschedule."
        )
        result = await send_sms(b["phone"], body)
        with connect() as conn:
            conn.execute(
                """INSERT INTO messages (direction, channel, to_number, body, status)
                   VALUES ('out', 'sms', ?, ?, ?)""",
                (b["phone"], body, result.get("status", "sent")),
            )
        sent += 1

    for lead_row in no_shows:
        body = (
            f"Hi {lead_row['full_name']}, we missed you today. "
            "Reply BOOK to schedule a new visit."
        )
        result = await send_sms(lead_row["phone"], body)
        with connect() as conn:
            conn.execute(
                """INSERT INTO messages (lead_id, direction, channel, to_number, body, status)
                   VALUES (?, 'out', 'sms', ?, ?, ?)""",
                (lead_row["id"], lead_row["phone"], body, result.get("status", "sent")),
            )
        follow_ups += 1

    return {"reminders_sent": sent, "follow_ups_sent": follow_ups}


# ── Twilio webhooks ────────────────────────────────────────────────────────────

@app.post("/twilio/voice")
async def twilio_voice(
    request: Request,
    From: str = Form(default=""),
    To: str = Form(default=""),
    CallStatus: str = Form(default=""),
) -> Response:
    form = {k: v for k, v in (await request.form()).items()}
    if not _twilio_sig_valid(request, form):
        log.warning("Twilio voice: invalid signature from %s", request.client)
        raise HTTPException(403, "Invalid Twilio signature")

    log.info("Incoming call from=%s status=%s", From, CallStatus)

    if From:
        with connect() as conn:
            conn.execute(
                """INSERT INTO leads (full_name, phone, service, preferred_time, source)
                   VALUES (?, ?, 'Unknown', 'Callback requested', 'missed_call')""",
                (f"Caller {From[-4:]}", From),
            )

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        '<Say voice="Polly.Joanna">'
        "Thanks for calling our dental office. We are with another patient. "
        "We just texted you a link to book online, or we will call you back shortly."
        "</Say>"
        "<Hangup/>"
        "</Response>"
    )

    if From:
        sms_body = (
            "Hi! Thanks for calling our dental office. Tap here to book: "
            "https://example.com/book or reply with a good callback time."
        )
        sms_result = await send_sms(From, sms_body)
        with connect() as conn:
            conn.execute(
                """INSERT INTO messages (direction, channel, to_number, from_number, body, status)
                   VALUES ('out', 'sms', ?, ?, ?, ?)""",
                (From, To, sms_body, sms_result.get("status", "sent")),
            )

    return Response(content=twiml, media_type="application/xml")


@app.post("/twilio/sms")
async def twilio_sms(
    request: Request,
    From: str = Form(default=""),
    Body: str = Form(default=""),
) -> Response:
    form = {k: v for k, v in (await request.form()).items()}
    if not _twilio_sig_valid(request, form):
        log.warning("Twilio SMS: invalid signature from %s", request.client)
        raise HTTPException(403, "Invalid Twilio signature")

    log.info("Inbound SMS from=%s body=%r", From, Body[:60])

    with connect() as conn:
        conn.execute(
            """INSERT INTO messages (direction, channel, from_number, body, status)
               VALUES ('in', 'sms', ?, ?, 'received')""",
            (From, Body),
        )

    upper = Body.strip().upper()
    if upper.startswith("STOP"):
        reply = "You have been unsubscribed. Reply START to re-subscribe."
    elif upper.startswith("START"):
        reply = "You are subscribed again. Reply STOP to opt out."
    elif upper.startswith("BOOK"):
        reply = "Awesome! Tap here to book your visit: https://example.com/book"
    elif upper.startswith("C"):
        reply = "Confirmed! See you soon. Reply R to reschedule or STOP to opt out."
    elif upper.startswith("R"):
        reply = "No problem. Reply with your preferred day and time and we will reschedule."
    else:
        reply = "Thanks! A team member will respond shortly. Reply BOOK to schedule online."

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{reply}</Message></Response>"
    )
    return Response(content=twiml, media_type="application/xml")


# ── Messages ──────────────────────────────────────────────────────────────────

@app.get("/api/messages")
def list_messages() -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}
