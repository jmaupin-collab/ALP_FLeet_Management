# Customer agency scoping

Notes on the authorization fix for customer asset visibility.

## The flaw

Customer visibility rested entirely on `AssetAuthorization` rows — explicit
per-asset grants. Nothing tied a customer account to an agency, so:

- A grant kept working after the asset moved to a different agency. A Scottsdale
  customer holding a grant on an asset transferred to Mesa kept seeing it.
- There was no way to express "this account belongs to Scottsdale" at all, so
  the restriction could not be enforced even in principle.
- `filter_assets_by_access()` skipped the organization predicate for customers,
  so a grant pointing at another tenant's asset would have resolved.

## The rule now

A customer sees an asset only when all three hold:

1. the asset is in their organization (unchanged, outer scope),
2. the asset's **current** `agency_id` equals the user's `agency_id`,
3. an `AssetAuthorization` row grants them `can_view`.

Agency is an additional scope inside the organization, not a replacement for the
grant. The wording of the requirement is restrictive ("may **only** see assets
whose agency matches"), so it narrows the existing grant model rather than
replacing it. That also means no existing customer gained visibility.

A customer with no `agency_id` resolves to the empty set. They never fall back
to "all customer assets".

## Why it lands in one place

`rbac.py` already funnelled every customer-facing read through two functions, so
the fix went there rather than into individual endpoints:

- `get_user_asset_ids()` — joins `Asset` and applies org + agency + grant. Used
  by `filter_by_authorized_assets()`, which already backed deployments, work
  orders, PM schedules, inspections, and the dashboard rollup.
- `require_asset_access()` — the same three scopes, all failing as 404 so a
  customer cannot distinguish an out-of-agency asset from one that does not
  exist. Asset detail, timeline, checklist, meters, and every
  `_require_via_asset()` caller inherit this.

`filter_assets_by_access()` also applies the org and agency predicates directly
in SQL, redundantly with the id set, so the scope survives in the query itself.

Because the agency is read from `Asset.agency_id` at query time rather than
copied onto the grant, transfers take effect immediately with no reconciliation
step. `transfer_to_agency` sets the new `agency_id` and `return_to_warehouse`
clears it, both of which already existed.

## Assignment rules

- Customer accounts require an agency at create time and on any role or agency
  change (`_resolve_customer_agency` in `main.py`).
- The agency must belong to the actor's organization and not be archived.
- Promoting a customer to a staff role clears the agency.
- Granting an asset outside the customer's agency is refused, so an admin cannot
  leave behind a grant that shows nothing.
- `users.agency_id` is `ON DELETE SET NULL`: deleting an agency blinds its
  customers rather than orphaning the scope.

## Existing data

`backfill_customer_agency_ids()` runs at startup and assigns an agency only when
every asset already granted to that customer sits in exactly one agency, making
it strictly a narrowing. Ambiguous accounts are left unassigned on purpose.
`count_unscoped_customers()` logs a warning naming how many accounts still need
an agency.

On the local database both existing customer accounts came out unassigned: their
grants did not resolve to a single agency, so the backfill declined to guess and
they now see nothing until an admin sets the agency in the Admin console.

## Not changed

Technicians, read-only, fleet manager, org admin, and system admin keep their
existing organization scope. None of them route through the customer branch.
