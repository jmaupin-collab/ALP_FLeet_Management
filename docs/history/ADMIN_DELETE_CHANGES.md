# Asset Deletion Feature - Before & After

## Before Changes

### Backend Behavior
```python
# ops.py - delete_asset()
def delete_asset(db: Session, asset: Asset) -> None:
    if asset_has_operational_history(db, asset):
        # ❌ BLOCKS deletion for ALL users (including admins)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This asset has lifecycle history..."
        )
    db.delete(asset)
    db.commit()
```

**Issue:** Even admins couldn't delete assets with operational history

### Frontend Behavior
```jsx
// Assets.jsx - Delete button
<button type="button" onClick={() => setConfirm({ action: "delete", ... })}>
  Delete
</button>
```

**Issue:** Delete button shown to ALL users (though backend would reject non-admins)

---

## After Changes

### Backend Behavior
```python
# ops.py - delete_asset()
def delete_asset(db: Session, asset: Asset, force: bool = False) -> None:
    """Delete an asset. If force=True (admin), bypass operational history check."""
    if not force and asset_has_operational_history(db, asset):
        # ⚠️ Only blocks if force=False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This asset has lifecycle history..."
        )
    db.delete(asset)
    db.commit()
```

```python
# main.py - remove_asset()
@app.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_asset(
    asset_id: UUID,
    _user: User = Depends(require_admin),  # Already requires admin
    db: Session = Depends(get_db),
) -> None:
    """Delete asset. Admins can force-delete assets with operational history."""
    asset = get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    delete_asset(db, asset, force=True)  # ✅ Pass force=True for admins
```

**Improvement:** Admins can now delete assets with operational history

### Frontend Behavior
```jsx
// Assets.jsx - Role-based rendering
const [currentUser, setCurrentUser] = useState(null);

useEffect(() => {
  // ✅ Fetch current user on mount
  api("/auth/me").then(setCurrentUser).catch(() => setCurrentUser(null));
}, [assetType, includeArchived]);

// ✅ Check if user is admin
const isAdmin = currentUser && ["admin", "org_admin", "system_admin"].includes(currentUser.role);

// ✅ Conditionally render Delete button
{isAdmin && (
  <button type="button" onClick={() => setConfirm({ action: "delete", ... })}>
    Delete
  </button>
)}
```

**Improvement:** Delete button only visible to admin users

---

## User Flow Comparison

### Admin User Flow

#### Before:
1. ✅ Sees Delete button
2. ❌ Backend blocks deletion if asset has history (409 Conflict)
3. ❌ Must archive instead

#### After:
1. ✅ Sees Delete button
2. ✅ Backend allows deletion even with history
3. ✅ Asset is permanently deleted

### Non-Admin User Flow

#### Before:
1. ⚠️ Sees Delete button (misleading)
2. ❌ Backend blocks with 403 Forbidden

#### After:
1. ✅ Delete button is hidden (better UX)
2. ✅ No confusion about permissions
3. ✅ Still see Archive, Edit, View buttons

---

## Security Model

### Authorization Layers

1. **Frontend (UI Layer)**
   - Hide Delete button for non-admins
   - Improves UX, not a security control

2. **Backend (API Layer)**
   - `require_admin` dependency enforces role check
   - Returns 403 Forbidden for non-admins
   - Security enforcement happens here

### Admin Roles
The following roles can delete assets:
- `admin` (legacy role)
- `org_admin` (organization administrator)
- `system_admin` (system-wide administrator)

### Non-Admin Roles
The following roles CANNOT delete assets:
- `fleet_manager`
- `program_manager`
- `technician`
- `read_only` / `viewer`
- `customer`

---

## Code Changes Summary

| File | Changes | Lines Changed |
|------|---------|---------------|
| `backend/app/ops.py` | Added `force` parameter to `delete_asset()` | ~3 |
| `backend/app/main.py` | Pass `force=True` in `remove_asset()` endpoint | ~2 |
| `frontend/src/pages/Assets.jsx` | Added user role check and conditional rendering | ~6 |
| `backend/tests/test_phase4_crud.py` | Updated test expectations | ~10 |

**Total:** ~21 lines changed across 4 files

---

## Key Benefits

1. **✅ Admins can delete assets with history** - Useful for data cleanup, mistakes, duplicates
2. **✅ Better UX** - Non-admins don't see buttons they can't use
3. **✅ Maintains security** - Backend still enforces admin-only deletion
4. **✅ Backward compatible** - Archive functionality unchanged
5. **✅ Clear separation** - Force-delete vs. soft-delete (archive)
