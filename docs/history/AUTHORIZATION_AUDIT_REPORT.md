# Authorization Audit Report

**Date:** September 12, 2026  
**Scope:** Cross-organization write protections plus remaining customer asset-authorization READ gaps.

This report covers the routes and tests exercised in the latest pytest run. It does not claim production readiness, SaaS deployment safety, or that no security risks remain.

---

## Result of this pass

Cross-organization and customer asset isolation tests passed for the routes covered.

`pytest` (backend): **62 passed**.

---

## Customer authorization gaps found

Organization-only filtering was not enough for `UserRole.CUSTOMER` on several READ endpoints. A customer in the same org as Asset A (authorized) and Asset B (not authorized) could previously receive Asset B data from:

- `GET /dashboard/kpis` — org-wide fleet size, cost, downtime
- `GET /deployments` — org-wide active deployments
- `GET /work-orders` — org-wide work orders
- `GET /work-orders/{wo_id}` — org membership only
- `GET /pm/schedules` — org-wide PM schedules
- `GET /pm/schedules/{schedule_id}` — org membership only
- `GET /analytics` — org-wide analytics (customers have no analytics permission)
- `GET /work-orders/users` — all active users, including other organizations

Already correct (not changed in this pass except where noted):

- `GET /assets`, `GET /assets/{asset_id}`, `GET /assets/{asset_id}/timeline`
- `GET /map/assets`
- `GET /inspections`, `GET /inspections/{inspection_id}`

---

## Endpoints fixed

| Endpoint | Change |
|---|---|
| `GET /dashboard/kpis` | KPIs use `filter_by_authorized_assets()` so customers only count authorized assets |
| `GET /deployments` | Customer lists limited to authorized `asset_id`s |
| `GET /work-orders` | Same authorized-asset filter |
| `GET /work-orders/{wo_id}` | Uses `require_work_order_access()` |
| `GET /pm/schedules` | Same authorized-asset filter |
| `GET /pm/schedules/{schedule_id}` | Uses `require_pm_schedule_access()` |
| `GET /analytics` | Requires `view_analytics`; customers receive 403 |
| `GET /analytics/export` | Already `require_reporter` (customers 403); unchanged |
| `GET /work-orders/users` | Requires operator; scoped to current org except System Admin; excludes customer / read-only / viewer |

Reusable helper added: `filter_by_authorized_assets()` in `backend/app/rbac.py`.

---

## Directory System Admin behavior

Warehouses, agencies, and vendors list by `current_user.organization_id` for every role, including System Admin.

That is intentional option B: System Admin operates in their assigned organization. There is no organization-switcher. Org isolation is not weakened.

---

## Port documentation

Working configuration (unchanged in source):

- Frontend: `http://localhost:5173`
- Backend: `http://127.0.0.1:9000`

Updated stale docs/examples to 9000: `README.md`, `QUICK_START.md`, `FEATURES_STATUS.md`, `ADMIN_DELETE_TEST_PLAN.md`, `PHASE5_END_DEPLOYMENT_IMPLEMENTATION.md`.

Already correct: `frontend/vite.config.js`, `restart_backend.sh`.

---

## Tests

Added `backend/tests/test_customer_asset_authorization.py`:

- Authorized Asset A visible; unauthorized same-org Asset B returns 404 on direct GET
- Deployments, work orders, PM, inspections lists omit Asset B
- Direct GET of Asset B work order, PM, and inspection returns 404
- Map omits Asset B
- Dashboard KPIs exclude Asset B (`fleet_size == 1`, cost is Asset A only)
- Analytics and CSV export return 403 for customers

Fixed `backend/tests/test_authorization_complete.py` fixtures to the current Asset / Deployment / Inspection / WorkOrder schema and wired `TestClient` to the same in-memory `db_session`.

Isolated phase4 / phase5 / PM / multitenant tests onto their own SQLite files so a full `pytest` run does not share engines or write into `fleet.db`.

---

## Remaining known issues

- System Admin still has no cross-org directory or KPI view (by design, no switcher).
- Customer analytics are denied rather than computed on authorized assets only.
- Customers do not have Dashboard / Analytics / Maintenance UI tabs; the API still enforces the rules above if called directly.
- This audit does not include penetration testing, rate limiting, or production secret/config review.

---

## Files changed (this pass)

- `backend/app/rbac.py`
- `backend/app/main.py`
- `backend/app/services.py`
- `backend/app/ops.py`
- `backend/app/pm.py`
- `backend/app/workorders.py`
- `backend/tests/test_authorization_complete.py`
- `backend/tests/test_customer_asset_authorization.py` (new)
- `backend/tests/test_phase4_crud.py`
- `backend/tests/test_phase5_maintenance.py`
- `backend/tests/test_pm.py`
- `backend/tests/test_multitenant.py`
- `README.md`, `QUICK_START.md`, `FEATURES_STATUS.md`, `ADMIN_DELETE_TEST_PLAN.md`, `PHASE5_END_DEPLOYMENT_IMPLEMENTATION.md`
- `AUTHORIZATION_AUDIT_REPORT.md`
