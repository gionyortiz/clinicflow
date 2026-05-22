# ClinicFlow MVP

A quick MVP for an AI receptionist concept focused on dental clinics.

## What is included

- FastAPI backend with endpoints to create and list leads
- KPI endpoint for a simple dashboard
- Frontend dashboard (HTML, CSS, JS) to submit and view leads

## Project structure

- `backend/app/main.py` FastAPI app
- `backend/requirements.txt` Python dependencies
- `frontend/index.html` dashboard markup
- `frontend/styles.css` dashboard styles
- `frontend/app.js` dashboard logic

## Run locally

1. Create and activate a Python environment
2. Install dependencies:

```powershell
cd backend
pip install -r requirements.txt
```

3. Run API server:

```powershell
uvicorn app.main:app --reload
```

4. In another terminal, run the static frontend:

```powershell
cd frontend
python -m http.server 5500
```

5. Open:

- Frontend: http://127.0.0.1:5500
- API docs: http://127.0.0.1:8000/docs

## Next upgrades

- Twilio voice webhook for missed calls
- Calendar integration for real booking
- SMS reminders and confirmation workflow
- Persistent database (PostgreSQL)
