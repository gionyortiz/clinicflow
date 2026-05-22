// In production FastAPI serves the frontend, so API is same origin.
// For local dev without FastAPI serving frontend, set to "http://127.0.0.1:8000".
const API_BASE = window.location.port === "5500" ? "http://127.0.0.1:8000" : "";

// ── XSS-safe HTML escaping ─────────────────────────────────────────────────
function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Formatters ─────────────────────────────────────────────────────────────
function fmtMoney(v) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

function statusBadge(status) {
  const map = { new: "badge-blue", contacted: "badge-yellow", booked: "badge-green", no_show: "badge-red", scheduled: "badge-blue", cancelled: "badge-red" };
  return `<span class="badge ${map[status] ?? "badge-gray"}">${esc(status)}</span>`;
}

function dirBadge(dir) {
  return `<span class="badge ${dir === "in" ? "badge-blue" : "badge-yellow"}">${esc(dir)}</span>`;
}

// ── API helpers ────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(JSON.stringify(err.detail ?? err));
  }
  return res.json();
}

// ── KPI ────────────────────────────────────────────────────────────────────
async function refreshKpi() {
  const d = await api("/api/kpi");
  document.getElementById("kpi-total").textContent = d.total_leads;
  document.getElementById("kpi-new").textContent = d.new;
  document.getElementById("kpi-booked").textContent = d.booked;
  document.getElementById("kpi-noshow").textContent = d.no_show;
  document.getElementById("kpi-revenue").textContent = fmtMoney(d.estimated_missed_revenue);
}

// ── Leads ──────────────────────────────────────────────────────────────────
function leadRow(lead) {
  return `<tr>
    <td>${esc(lead.id)}</td>
    <td>${esc(lead.full_name)}</td>
    <td>${esc(lead.phone)}</td>
    <td>${esc(lead.service)}</td>
    <td>${esc(lead.source)}</td>
    <td>${statusBadge(lead.status)}</td>
    <td>${esc(lead.preferred_time)}</td>
    <td>${fmtDate(lead.created_at)}</td>
    <td>
      <select class="status-select" data-lead-id="${esc(lead.id)}" title="Update status">
        <option value="">—</option>
        <option value="new">new</option>
        <option value="contacted">contacted</option>
        <option value="booked">booked</option>
        <option value="no_show">no_show</option>
      </select>
    </td>
  </tr>`;
}

async function refreshLeads() {
  const d = await api("/api/leads");
  const tbody = document.getElementById("lead-rows");
  tbody.innerHTML = d.items.map(leadRow).join("");
  tbody.querySelectorAll(".status-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const status = sel.value;
      if (!status) return;
      const id = sel.dataset.leadId;
      try {
        await api(`/api/leads/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        await refreshAll();
      } catch (e) {
        alert(`Could not update status: ${e.message}`);
      }
    });
  });
}

// ── Bookings ───────────────────────────────────────────────────────────────
function bookingRow(b) {
  return `<tr>
    <td>${esc(b.id)}</td>
    <td>${esc(b.full_name)}</td>
    <td>${esc(b.service)}</td>
    <td>${fmtDate(b.start_time)}</td>
    <td>${fmtDate(b.end_time)}</td>
    <td>${esc(b.notes ?? "")}</td>
    <td>${statusBadge(b.status)}</td>
    <td><a class="link" href="${API_BASE}/api/bookings/${esc(b.id)}.ics">📅 ICS</a></td>
  </tr>`;
}

async function refreshBookings() {
  const d = await api("/api/bookings");
  document.getElementById("booking-rows").innerHTML = d.items.map(bookingRow).join("");
}

// ── Messages ───────────────────────────────────────────────────────────────
function messageRow(m) {
  return `<tr>
    <td>${esc(m.id)}</td>
    <td>${dirBadge(m.direction)}</td>
    <td>${esc(m.from_number ?? "—")}</td>
    <td>${esc(m.to_number ?? "—")}</td>
    <td class="msg-body">${esc(m.body)}</td>
    <td><span class="badge badge-gray">${esc(m.status)}</span></td>
    <td>${fmtDate(m.created_at)}</td>
  </tr>`;
}

async function refreshMessages() {
  const d = await api("/api/messages");
  document.getElementById("message-rows").innerHTML = d.items.map(messageRow).join("");
}

// ── Sync status indicator ──────────────────────────────────────────────────
function setSyncStatus(ok) {
  const el = document.getElementById("sync-status");
  el.textContent = ok ? "● Live" : "● Offline";
  el.className = "sync-status " + (ok ? "sync-ok" : "sync-err");
}

// ── Full refresh ───────────────────────────────────────────────────────────
async function refreshAll() {
  try {
    await Promise.all([refreshKpi(), refreshLeads(), refreshBookings(), refreshMessages()]);
    setSyncStatus(true);
  } catch (e) {
    setSyncStatus(false);
    console.error("Refresh error:", e);
  }
}

// ── Auto-refresh every 30 s ────────────────────────────────────────────────
setInterval(refreshAll, 30_000);

// ── Lead form ──────────────────────────────────────────────────────────────
document.getElementById("lead-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("lead-msg");
  msg.textContent = "Saving…";
  const payload = Object.fromEntries(new FormData(e.target).entries());
  if (!payload.email) delete payload.email;
  try {
    await api("/api/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    e.target.reset();
    msg.textContent = "Lead created.";
    msg.className = "form-msg ok";
    await refreshAll();
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
    msg.className = "form-msg err";
  }
});

// ── Booking form ───────────────────────────────────────────────────────────
document.getElementById("booking-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("booking-msg");
  msg.textContent = "Saving…";
  const raw = Object.fromEntries(new FormData(e.target).entries());
  const payload = {
    lead_id: Number(raw.lead_id),
    start_time: new Date(raw.start_time).toISOString(),
    duration_minutes: Number(raw.duration_minutes),
    notes: raw.notes || undefined,
    push_to_google: raw.push_to_google === "true",
  };
  try {
    const res = await api("/api/bookings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    e.target.reset();
    msg.textContent = `Booked! SMS: ${res.sms.status} | Google: ${res.google.status}`;
    msg.className = "form-msg ok";
    await refreshAll();
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
    msg.className = "form-msg err";
  }
});

// ── Boot ───────────────────────────────────────────────────────────────────
refreshAll().catch(() => setSyncStatus(false));
