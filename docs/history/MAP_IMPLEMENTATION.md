# Asset Location Map Implementation

## ✅ Completed - Backend Infrastructure

### 1. **Enhanced Location Models**

#### Warehouse Model (Enhanced)
```python
class Warehouse:
    name: str
    address: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    country: str = "USA"
    latitude: Decimal | None  # NEW
    longitude: Decimal | None  # NEW
```

#### Agency Model (Enhanced)
```python
class Agency:
    name: str
    site_name: str | None  # NEW
    agency_type: str
    contact_name: str | None
    address: str | None  # NEW
    city: str | None  # NEW
    state: str | None  # NEW
    zip_code: str | None  # NEW
    country: str = "USA"  # NEW
    latitude: Decimal | None  # NEW
    longitude: Decimal | None  # NEW
```

#### GPS Location Model (NEW)
```python
class GPSLocation:
    """Provider-neutral GPS tracking history."""
    asset_id: UUID
    telematics_provider: str | None  # geotab, samsara, etc
    telematics_device_id: str | None
    latitude: Decimal
    longitude: Decimal
    heading: Decimal | None  # 0-360 degrees
    speed: Decimal | None
    accuracy: Decimal | None
    location_timestamp: datetime
```

#### LocationSource Enum (NEW)
```python
class LocationSource(str, Enum):
    GPS = "gps"              # Live GPS from telematics
    DEPLOYMENT = "deployment"  # Active deployment
    CUSTOMER = "customer"      # Customer/agency assignment
    WAREHOUSE = "warehouse"    # Warehouse/depot
    IN_TRANSIT = "in_transit"  # Currently in transit
    MANUAL = "manual"          # Manually entered
    UNKNOWN = "unknown"        # No location available
```

### 2. **Location Priority Service** (`app/location.py`)

**Complete implementation of location priority logic:**

```python
def get_asset_location(db: Session, asset: Asset) -> AssetLocation:
    """
    Priority order:
    1. GPS (if recent, < 24 hrs old)
    2. Active deployment/customer location
    3. Warehouse/depot location
    4. In-transit information
    5. Manual location
    6. Unknown
    """
```

**Key Features:**
- ✅ **GPS Staleness Detection** - Marks GPS older than 24 hours as stale
- ✅ **Transparent Source** - Always returns `LocationSource` with coordinates
- ✅ **Provider-Neutral** - Supports any telematics provider
- ✅ **Address Context** - Returns full address when available
- ✅ **In-Transit Support** - Handles carrier/tracking information
- ✅ **Historical Integrity** - Never overwrites historical records

**AssetLocation Return Type:**
```python
class AssetLocation(NamedTuple):
    latitude: Decimal | None
    longitude: Decimal | None
    location_name: str
    location_source: LocationSource
    location_timestamp: datetime
    is_stale: bool  # For stale GPS
    # Context
    address: str | None
    city: str | None
    state: str | None
    # In-transit
    origin: str | None
    destination: str | None
    carrier: str | None
    tracking_code: str | None
```

## 🚧 Remaining Implementation

### Critical Next Steps

#### 1. **Map API Endpoint** (`app/main.py`)

```python
from app.location import get_asset_location
from app.rbac import filter_assets_by_access

@app.get("/map/assets")
def get_map_assets(
    asset_type: AssetType | None = None,
    status: DeploymentStatus | None = None,
    warehouse_id: UUID | None = None,
    agency_id: UUID | None = None,
    location_source: LocationSource | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssetMapMarker]:
    """
    Get assets with resolved locations for map display.
    Respects organization/user authorization.
    """
    # Build query with authorization filter
    query = select(Asset).options(
        selectinload(Asset.gps_locations),
    )
    query = filter_assets_by_access(query, current_user, db)
    
    # Apply filters
    if asset_type:
        query = query.where(Asset.asset_type == asset_type)
    if status:
        query = query.where(Asset.current_status == status)
    if warehouse_id:
        query = query.where(Asset.warehouse_id == warehouse_id)
    if agency_id:
        query = query.where(Asset.agency_id == agency_id)
    
    assets = db.scalars(query).all()
    
    # Resolve location for each asset
    markers = []
    for asset in assets:
        location = get_asset_location(db, asset)
        
        # Filter by location source if requested
        if location_source and location.location_source != location_source:
            continue
        
        # Skip assets without coordinates
        if location.latitude is None or location.longitude is None:
            continue
        
        # Get maintenance summary
        from app.location import get_asset_maintenance_summary
        maintenance = get_asset_maintenance_summary(db, asset.id)
        
        markers.append({
            "id": str(asset.id),
            "vin": asset.vin,
            "asset_type": asset.asset_type.value,
            "make_model": asset.make_model,
            "current_status": asset.current_status.value if asset.current_status else None,
            "current_custody_type": asset.current_custody_type.value if asset.current_custody_type else None,
            # Location
            "latitude": float(location.latitude),
            "longitude": float(location.longitude),
            "location_name": location.location_name,
            "location_source": location.location_source.value,
            "location_timestamp": location.location_timestamp.isoformat(),
            "is_stale": location.is_stale,
            "address": location.address,
            "city": location.city,
            "state": location.state,
            # In-transit
            "carrier": location.carrier,
            "tracking_code": location.tracking_code,
            # Maintenance
            "open_work_orders": maintenance["open_work_orders"],
            # Context
            "warehouse_name": get_warehouse_name(db, asset.warehouse_id),
            "agency_name": get_agency_name(db, asset.agency_id),
        })
    
    return markers
```

#### 2. **Pydantic Schemas** (`app/schemas.py`)

```python
class AssetMapMarker(BaseModel):
    """Asset marker for map display."""
    id: UUID
    vin: str
    asset_type: str
    make_model: str
    current_status: str | None
    current_custody_type: str | None
    # Location
    latitude: float
    longitude: float
    location_name: str
    location_source: str  # GPS, DEPLOYMENT, WAREHOUSE, etc
    location_timestamp: datetime
    is_stale: bool
    address: str | None = None
    city: str | None = None
    state: str | None = None
    # In-transit
    carrier: str | None = None
    tracking_code: str | None = None
    # Maintenance
    open_work_orders: int
    # Context
    warehouse_name: str | None = None
    agency_name: str | None = None
```

#### 3. **Frontend Map Page** (`frontend/src/pages/Map.jsx`)

**React + Leaflet Implementation:**

```jsx
import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../lib/api";

export default function Map() {
  const [markers, setMarkers] = useState([]);
  const [filters, setFilters] = useState({
    asset_type: "",
    status: "",
    location_source: "",
  });
  
  async function loadMarkers() {
    const query = new URLSearchParams(filters).toString();
    const data = await api(`/map/assets?${query}`);
    setMarkers(data);
  }
  
  useEffect(() => {
    loadMarkers();
  }, [filters]);
  
  // Custom marker icons by asset type
  const getMarkerIcon = (marker) => {
    const color = getMarkerColor(marker.asset_type, marker.current_status);
    return L.divIcon({
      className: "custom-marker",
      html: `<div style="background: ${color}; ...">${getIconSVG(marker.asset_type)}</div>`,
    });
  };
  
  return (
    <div className="flex h-screen">
      {/* Filters Sidebar */}
      <div className="w-80 bg-white p-4 overflow-y-auto">
        <h2 className="text-xl font-bold mb-4">Asset Map</h2>
        
        {/* Filters */}
        <select onChange={(e) => setFilters({...filters, asset_type: e.target.value})}>
          <option value="">All Asset Types</option>
          <option value="ALPR Trailer">ALPR Trailer</option>
          <option value="Semi Truck">Semi Truck</option>
          <option value="Fleet Vehicle">Fleet Vehicle</option>
        </select>
        
        <select onChange={(e) => setFilters({...filters, location_source: e.target.value})}>
          <option value="">All Location Sources</option>
          <option value="gps">GPS</option>
          <option value="deployment">Deployment</option>
          <option value="warehouse">Warehouse</option>
          <option value="customer">Customer</option>
        </select>
        
        {/* Asset List */}
        <div className="mt-4">
          <h3 className="font-semibold mb-2">Assets ({markers.length})</h3>
          {markers.map((marker) => (
            <div key={marker.id} className="p-2 border-b cursor-pointer hover:bg-gray-50">
              <div className="font-medium">{marker.make_model}</div>
              <div className="text-xs text-gray-600">{marker.location_name}</div>
              <div className="text-xs text-gray-500">
                Source: {marker.location_source.toUpperCase()}
              </div>
            </div>
          ))}
        </div>
      </div>
      
      {/* Map */}
      <div className="flex-1">
        <MapContainer
          center={[39.8283, -98.5795]}  // Center of USA
          zoom={4}
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          />
          
          <MarkerClusterGroup>
            {markers.map((marker) => (
              <Marker
                key={marker.id}
                position={[marker.latitude, marker.longitude]}
                icon={getMarkerIcon(marker)}
              >
                <Popup>
                  <div className="p-2">
                    <h3 className="font-bold">{marker.make_model}</h3>
                    <div className="text-sm mt-2 space-y-1">
                      <div><strong>VIN:</strong> {marker.vin}</div>
                      <div><strong>Type:</strong> {marker.asset_type}</div>
                      <div><strong>Status:</strong> {marker.current_status || 'N/A'}</div>
                      <div><strong>Location:</strong> {marker.location_name}</div>
                      <div className="text-blue-600">
                        <strong>Source:</strong> {marker.location_source.toUpperCase()}
                        {marker.is_stale && <span className="text-red-600"> (STALE)</span>}
                      </div>
                      {marker.address && <div><strong>Address:</strong> {marker.address}</div>}
                      {marker.carrier && <div><strong>Carrier:</strong> {marker.carrier}</div>}
                      {marker.open_work_orders > 0 && (
                        <div className="text-red-600">
                          <strong>Open Work Orders:</strong> {marker.open_work_orders}
                        </div>
                      )}
                      <a href={`/assets/${marker.id}`} className="text-blue-600 hover:underline">
                        View Asset Profile →
                      </a>
                    </div>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MarkerClusterGroup>
        </MapContainer>
      </div>
    </div>
  );
}
```

#### 4. **Install Frontend Dependencies**

```bash
cd frontend
npm install leaflet react-leaflet react-leaflet-cluster
```

#### 5. **Add Map Route** (`frontend/src/App.jsx`)

```jsx
import Map from "./pages/Map.jsx";

// In routes:
<Route path="/map" element={<Map />} />
```

#### 6. **Add Navigation Link** (`frontend/src/components/Layout.jsx`)

```jsx
const tabs = [
  // ... existing tabs
  { to: "/map", label: "Map" },
];
```

#### 7. **Update Seed Data** with Realistic Locations

```python
# In app/seed.py or similar
warehouses = [
    {
        "name": "Phoenix Distribution Center",
        "address": "1234 Industrial Way",
        "city": "Phoenix",
        "state": "AZ",
        "zip_code": "85001",
        "latitude": Decimal("33.4484"),
        "longitude": Decimal("-112.0740"),
    },
    {
        "name": "Los Angeles Depot",
        "address": "5678 Warehouse Blvd",
        "city": "Los Angeles",
        "state": "CA",
        "zip_code": "90001",
        "latitude": Decimal("34.0522"),
        "longitude": Decimal("-118.2437"),
    },
]

agencies = [
    {
        "name": "LAPD",
        "site_name": "Central Division",
        "address": "251 E 6th St",
        "city": "Los Angeles",
        "state": "CA",
        "zip_code": "90014",
        "latitude": Decimal("34.0447"),
        "longitude": Decimal("-118.2473"),
    },
]
```

### Testing Checklist

#### Backend Tests (`tests/test_location.py`)

```python
def test_location_priority_gps():
    """GPS location takes priority when recent."""
    # Create asset with GPS, warehouse, and agency
    # Verify GPS location returned
    pass

def test_location_priority_stale_gps():
    """Stale GPS falls back to operational location."""
    # Create asset with 48-hour-old GPS and warehouse
    # Verify warehouse location returned
    pass

def test_location_priority_deployment():
    """Active deployment takes priority over warehouse."""
    # Create asset with both
    # Verify deployment location returned
    pass

def test_location_authorization():
    """Map API respects organization boundaries."""
    # Create assets in different orgs
    # Verify user only sees their org's assets
    pass

def test_customer_asset_authorization():
    """Customer users only see authorized assets on map."""
    # Create customer user with specific asset access
    # Verify map only shows authorized assets
    pass
```

## 📊 Location Source Transparency

**CRITICAL: The UI must clearly distinguish sources:**

| Source | Display | Never Label As |
|--------|---------|----------------|
| GPS | "GPS - Geotab" + timestamp | - |
| DEPLOYMENT | "Deployed at Phoenix PD" | "Live GPS" |
| CUSTOMER | "Mesa Police Department" | "Live GPS" |
| WAREHOUSE | "Phoenix Distribution Center" | "Live GPS" |
| IN_TRANSIT | "In Transit via FedEx" | "Live GPS" |
| MANUAL | "Manually Entered" | "Live GPS" |
| UNKNOWN | "Unknown Location" | "Live GPS" |

**Visual Indicators:**
- GPS icon: 📡 (green if fresh, yellow if stale)
- Deployment icon: 📍
- Warehouse icon: 🏭
- Customer icon: 🏛️
- In-transit icon: 🚚
- Manual icon: ✏️
- Unknown icon: ❓

## 🔮 Future GPS Integration

When GPS becomes available:

1. **No code changes to location priority logic** - Already implemented
2. **Add GPS data via API or webhook:**
   ```python
   gps_location = GPSLocation(
       asset_id=asset.id,
       telematics_provider="geotab",
       telematics_device_id="G12345",
       latitude=Decimal("33.4484"),
       longitude=Decimal("-112.0740"),
       location_timestamp=datetime.now(UTC),
   )
   db.add(gps_location)
   ```
3. **Map automatically uses GPS** - Priority #1 in logic
4. **Operational custody preserved** - Warehouse/agency assignments unchanged

## Summary

✅ **Complete:**
- Location models with lat/long
- GPS location model (provider-neutral)
- LocationSource enum
- Complete location priority service
- Stale GPS detection
- In-transit support
- Historical integrity

🚧 **Remaining:**
- Map API endpoint (~50 lines)
- Frontend React map page (~200 lines)
- Seed data with coordinates
- Location tests

**The architecture is complete and GPS-ready. Just needs API endpoint and frontend implementation!**
