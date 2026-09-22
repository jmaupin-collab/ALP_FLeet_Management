# Fleet Lifecycle Management

Mixed-fleet tracker (ALPR trailers, semi trucks, fleet vehicles). FastAPI + SQLAlchemy backend, React + Tailwind + Recharts frontend, multi-tenant with organization-scoped RBAC.

## Configuration

Copy `backend/.env.example` to `backend/.env` and set secrets there. Source files never contain production keys.

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | JWT signing key |
| `DATABASE_URL` | `sqlite:///./fleet.db` locally, or `postgresql+psycopg://USER:PASSWORD@HOST:5432/fleet_mgmt` in the cloud |
| `CORS_ORIGINS` | Comma-separated frontend origins; `*` is rejected in production |
| `ENVIRONMENT` | `development` or `production` (production rejects placeholder secrets, SQLite, wildcard CORS, and demo seeding) |
| `SEED_DEMO_DATA` | Creates demo accounts with known passwords. Off by default, refused in production |
| `PROTECTED_ADMIN_EMAILS` | Comma-separated accounts that cannot be deleted, deactivated, or demoted |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime; defaults to 480 |

## Run locally

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 9000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

### Getting a first account

There are no default accounts. Either seed a throwaway local database:

```bash
SEED_DEMO_DATA=true uvicorn app.main:app --reload --port 9000
```

which creates `admin@example.com` / `ChangeMe123!` and `tech@example.com`, or create the first real administrator directly:

```bash
cd backend
python -m scripts.create_admin   # prompts for email and password
```

## Tests

```bash
cd backend
pytest -q
```

## Authorization model

Roles resolve through `app/rbac.py`:

| Role | Scope |
| --- | --- |
| `system_admin` | Cross-tenant; the only role that can create other system admins |
| `org_admin` | Full control inside one organization |
| `fleet_manager` | Assets, deployments, maintenance, exports |
| `technician` | Maintenance and inspections |
| `read_only` | View and analytics |
| `customer` | Only assets explicitly granted via `AssetAuthorization` |

A user can only assign roles at or below their own rank, so an org admin cannot create a system admin. Legacy role values (`admin`, `dispatcher`, `program_manager`, `viewer`) are normalized to the list above at startup and resolved through `canonical_role()` everywhere else.

Changing a password sets `users.password_changed_at`, which invalidates every token issued before that moment.

## Deployment

`backend/Dockerfile` builds the API image; `Procfile` covers buildpack platforms such as Railway. CI runs backend tests, the frontend build, and a Docker build on every pull request (`.github/workflows/ci.yml`).

Historical implementation notes and status reports live in `docs/history/`.
