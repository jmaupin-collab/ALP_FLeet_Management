# Admin Asset Deletion - Test Plan

## Summary of Changes

### Backend Changes (✅ Completed)

1. **`backend/app/ops.py`** - Modified `delete_asset()` function:
   - Added `force` parameter (default: `False`)
   - When `force=True`, bypasses operational history check
   - Allows admins to delete assets even with deployments, work orders, or inspections

2. **`backend/app/main.py`** - Modified `remove_asset()` endpoint:
   - Already requires admin role via `require_admin` dependency
   - Now passes `force=True` to `delete_asset()`
   - Admins can delete any asset regardless of history

### Frontend Changes (✅ Completed)

1. **`frontend/src/pages/Assets.jsx`** - Added role-based access control:
   - Added `currentUser` state to track logged-in user
   - Fetches user info from `/auth/me` endpoint on component mount
   - Added `isAdmin` computed value that checks if user role is:
     - `"admin"` (legacy)
     - `"org_admin"` (organization admin)
     - `"system_admin"` (system-wide admin)
   - Delete button only renders when `isAdmin === true`

## Test Scenarios

### Scenario 1: Admin User - Can Delete Assets with History ✅

**Prerequisites:**
- Log in as admin user (role: `admin`, `org_admin`, or `system_admin`)
- Navigate to Assets page

**Test Steps:**
1. Create a test asset with operational history:
   - Add deployments
   - Create work orders
   - Perform inspections
2. Verify Delete button is visible in the asset actions
3. Click Delete button
4. Confirm deletion in modal
5. Verify asset is permanently deleted (no longer appears in list)

**Expected Result:**
- Delete button is visible
- Deletion succeeds with 204 status code
- Asset is removed from database

### Scenario 2: Non-Admin User - Cannot See Delete Button ✅

**Prerequisites:**
- Log in as non-admin user (role: `technician`, `read_only`, `viewer`, etc.)
- Navigate to Assets page

**Test Steps:**
1. View any asset in the table
2. Check available actions

**Expected Result:**
- Delete button is NOT visible
- Only View, Edit, Archive/Restore buttons are shown
- Attempting direct API call to DELETE endpoint should return 403 Forbidden

### Scenario 3: Admin Can Delete Assets Without History ✅

**Prerequisites:**
- Log in as admin user
- Navigate to Assets page

**Test Steps:**
1. Create a new asset (no deployments, work orders, or inspections)
2. Verify Delete button is visible
3. Click Delete
4. Confirm deletion

**Expected Result:**
- Asset is deleted successfully
- No error messages

### Scenario 4: Archive Still Works for All Users

**Prerequisites:**
- Log in as any user with operator permissions
- Navigate to Assets page

**Test Steps:**
1. Select an asset with operational history
2. Click Archive button
3. Confirm archive

**Expected Result:**
- Asset is archived (not deleted)
- History is preserved
- Asset can be restored later

## API Endpoints

### DELETE /assets/{asset_id}
- **Required Role:** Admin (enforced by `require_admin` dependency)
- **Behavior:** 
  - Admins: Deletes asset with `force=True`, bypassing history check
  - Non-admins: Returns 403 Forbidden
- **Status Codes:**
  - `204 No Content`: Success
  - `403 Forbidden`: Non-admin user attempted deletion
  - `404 Not Found`: Asset doesn't exist

## User Roles

### Admin Roles (Can Delete):
- `admin` - Legacy admin role
- `org_admin` - Organization administrator
- `system_admin` - System-wide administrator

### Non-Admin Roles (Cannot Delete):
- `fleet_manager` / `program_manager` - Fleet management
- `technician` - Maintenance technician
- `read_only` / `viewer` - Read-only access
- `customer` - External customer/agency user

## Manual Testing Commands

### Backend Test (Python)
```bash
cd backend
source .venv/bin/activate

# Start the backend server
uvicorn app.main:app --reload

# In another terminal, test with curl:
# Login as admin
curl -X POST http://127.0.0.1:9000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your_password"}'

# Copy the token and test deletion
curl -X DELETE http://127.0.0.1:9000/assets/{asset_id} \
  -H "Authorization: Bearer {token}"
```

### Frontend Test
```bash
cd frontend
npm run dev

# Open browser to http://localhost:5173
# 1. Login as admin
# 2. Navigate to Assets
# 3. Verify Delete button is visible
# 4. Login as non-admin
# 5. Navigate to Assets  
# 6. Verify Delete button is hidden
```

## Files Modified

1. `backend/app/ops.py` - Added force parameter to delete_asset()
2. `backend/app/main.py` - Updated remove_asset() to use force=True
3. `frontend/src/pages/Assets.jsx` - Added role-based Delete button visibility
4. `backend/tests/test_phase4_crud.py` - Updated test expectations

## Notes

- The backend already had admin-only deletion via `require_admin` dependency
- This change adds the ability for admins to bypass the operational history check
- Non-admin users never had direct access to the delete endpoint
- Frontend now properly hides the Delete button based on user role
- Archive functionality remains unchanged and available to all operators
