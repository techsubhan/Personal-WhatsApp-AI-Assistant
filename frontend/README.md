# WhatsApp AI Assistant — Admin Dashboard

A real, functional admin dashboard for the WhatsApp AI Assistant backend. Every
control in this UI calls a live endpoint on your FastAPI backend — nothing here
is mocked.

## What this is

- `dashboard/` (this folder) — the frontend: React + TypeScript + Vite + Tailwind.
- The backend lives in your original project folder, with **one new file** added:
  `admin_api.py`, plus two small edits to `app.py` (mounting the router + CORS).
  No existing backend logic, route, or model was changed.

See `ANALYSIS.md` in the backend project root for the full architecture
analysis, feature-to-dashboard mapping, and API inventory this was built from.

## 1. Enable the backend API

The dashboard talks to new `/admin/*` endpoints protected by a shared secret.

1. In your backend's `.env`, set a real value for `INTERNAL_API_KEY` (this is
   also your dashboard login key):
   ```
   INTERNAL_API_KEY=some-long-random-string
   DASHBOARD_ORIGINS=http://localhost:5173
   ```
   If `INTERNAL_API_KEY` is left empty, every `/admin/*` route returns
   `503 Dashboard API is disabled` — it fails closed by design.
2. Install/run the backend as usual:
   ```
   pip install -r requirements.txt
   python app.py
   ```
   (No new Python dependencies were added — `admin_api.py` only uses
   FastAPI/SQLAlchemy/Pydantic, already in `requirements.txt`.)

## 2. Run the dashboard

```
cd dashboard
npm install
cp .env.example .env      # adjust VITE_API_BASE_URL if your backend isn't on :8000
npm run dev
```

Open http://localhost:5173, sign in with the `INTERNAL_API_KEY` value you set
above.

## 3. Build for production

```
npm run build
```

Outputs static files to `dist/` — serve them with any static host (Nginx,
Vercel, etc.), and set `DASHBOARD_ORIGINS` in the backend's `.env` to that
production URL.

## Pages

| Page | Backed by |
|---|---|
| Dashboard (overview) | `/admin/overview/stats` |
| Conversations | `/admin/contacts`, `/admin/contacts/{id}/messages` |
| Contacts | `/admin/contacts`, block/unblock, per-contact AI toggle |
| Approvals | `/admin/approvals`, approve/reject |
| AI control | `/admin/ai/status`, global AI toggle |
| Human override | `/admin/overrides`, activate/clear |
| Smart memory | `/admin/contacts/{id}/memories`, delete |
| Working hours | `/admin/settings` (working_hours.*) |
| System health | `/health`, `/status` (pre-existing, unauthenticated) |
| Activity log | `/admin/logs/admin-actions`, `/admin/logs/requests` |
| Settings | `/admin/settings`, `/admin/profile`, `/admin/reload` |

## Notes

- Rate limiting and spam detection remain in-memory in the backend (by
  original design) and have no historical view — this is expected, not a bug.
- Every dashboard action is written to the same `AdminActionLog` table your
  WhatsApp `/commands` use, tagged with actor `dashboard`, so the Activity log
  shows a unified audit trail regardless of where an action came from.
