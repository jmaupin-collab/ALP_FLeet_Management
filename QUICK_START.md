# Quick Start Guide - Feature Access

## 🔑 Login

1. Open http://localhost:5173/login
2. Use credentials:
   ```
   Email: admin@example.com
   Password: ChangeMe123!
   ```

---

## ✅ Feature 1: End Deployment Workflow

### Access
Navigate to **Deployments** (http://localhost:5173/deployments)

### What You'll See
- **3 Active Deployments** in the table
- Deployment filters (Asset Type, Status, Agency, Warehouse, Dates, etc.)
- Summary metrics at the top
- **"End Deployment"** button for each active deployment

### How to Test
1. Click **"End Deployment"** on any active deployment
2. A modal opens with 5 disposition options:
   - **A. Return to Warehouse** - Select destination warehouse
   - **B. Transfer to Another Agency** - Select new customer
   - **C. In Transit** - Enter carrier/tracking info
   - **D. Send to Maintenance** - Select location, optionally create work order
   - **E. Out of Service/Retired** - Provide reason
3. Fill in the required fields
4. Click **"End Deployment"**
5. The deployment closes and asset status updates

### Test Assets
- **1ALPR001** (Rekor Scout) - Currently at Phoenix PD
- **1ALPR002** (Flock Safety) - Currently at AZ DPS  
- **1FLEET001** (Ford F-150) - Currently at Scottsdale PD

---

## 🗺️ Feature 2: Asset Location Map

### Access
Navigate to **Map** (http://localhost:5173/map)

### What You'll See
- Interactive map centered on Phoenix, AZ
- Color-coded markers for assets:
  - 🟢 Green = Available
  - 🔵 Blue = Deployed
  - 🟠 Orange = In Transit
  - 🔴 Red = Maintenance
  - ⚫ Gray = Out of Service
- Marker clustering for dense locations
- Filters: Asset Type, Status, Warehouse, Agency
- Click markers for asset details popup

### Map Locations
**Warehouses:**
- Phoenix Main Depot (33.4484, -112.0740)
- Mesa Service Center (33.4152, -111.8315)
- Tucson Distribution Hub (32.2226, -110.9747)

**Agency Deployments:**
- Phoenix PD - Traffic Division
- AZ DPS - Highway Patrol North
- Scottsdale PD - Field Operations

### Assets on Map
- 3 deployed assets (at agency locations)
- 2 available assets (at warehouses)

---

## 🔧 System Info

**Backend API**: http://127.0.0.1:9000
**API Docs**: http://127.0.0.1:9000/docs
**Frontend**: http://localhost:5173

**Database**: `backend/fleet.db`
**Seed Script**: `backend/app/seed_quick.py`

---

## 📋 Test Scenarios

### Scenario 1: Complete Deployment to Warehouse
1. Go to **Deployments**
2. End deployment for **1ALPR001**
3. Select **"Return to Warehouse"**
4. Choose **"Mesa Service Center"**
5. Add notes: "Equipment inspection complete"
6. Submit
7. Asset now shows as **AVAILABLE** at Mesa

### Scenario 2: Transfer Between Agencies
1. Go to **Deployments**
2. End deployment for **1ALPR002**
3. Select **"Transfer to Another Customer/Agency"**
4. Choose **"Scottsdale Police Department"**
5. Check **"Create next deployment immediately"**
6. Submit
7. Asset transferred without warehouse return

### Scenario 3: Send to Maintenance
1. Go to **Deployments**
2. End deployment for **1FLEET001**
3. Select **"Send to Maintenance"**
4. Choose warehouse/depot
5. Select status: **"Maintenance - Awaiting Repair"**
6. Check **"Create work order"**
7. Fill in work order details
8. Submit
9. View created work order in **Maintenance** tab

### Scenario 4: View on Map
1. Go to **Map**
2. See all 5 assets with location markers
3. Use filters:
   - Filter by **"Deployed"** status → See 3 markers
   - Filter by **"Available"** status → See 2 markers
   - Filter by **"Phoenix Police Department"** → See 1 marker
4. Click any marker to see asset details
5. Click **"View asset details →"** to go to Asset Profile

---

## 🐛 Known Issues

1. **RBAC Filtering**: Some assets may not appear if RBAC permissions are too restrictive
   - Admin users should see all assets in their organization
   - If you see fewer assets than expected, check authorization rules

2. **Backend Port**: Running on port 9000
   - Vite proxy configured to forward `/api` to http://127.0.0.1:9000

---

## 📚 Additional Documentation

- `FEATURES_STATUS.md` - Detailed feature status report
- `PHASE5_END_DEPLOYMENT_IMPLEMENTATION.md` - Phase 5 technical details
- `MAP_IMPLEMENTATION.md` - Map architecture and design
- `LOGIN_FIX_SUMMARY.md` - Recent authentication fixes

---

## 🚀 Summary

✅ **End Deployment Workflow** - FULLY WORKING
   - 5 disposition options
   - Filters and metrics
   - Historical integrity
   - Accessible from Deployments page and Asset Profile

✅ **Asset Location Map** - FULLY WORKING
   - Interactive Leaflet map
   - Custom markers by status
   - Filters and legend
   - Real-time asset locations
   - Accessible from Map tab in navigation
