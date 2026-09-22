import { useEffect, useState, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { api } from "../lib/api";
import { Badge, statusLabel } from "../lib/format";

// Component to auto-fit map bounds to markers
function FitBounds({ markers }) {
  const map = useMap();
  
  useEffect(() => {
    if (markers.length > 0) {
      const bounds = L.latLngBounds(markers.map(m => [m.lat, m.lng]));
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });
    }
  }, [markers, map]);
  
  return null;
}

// Fix for default marker icons in Leaflet
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

// Custom marker icons by asset type
const createCustomIcon = (type, status) => {
  const colors = {
    available: "#10b981",
    deployed: "#3b82f6",
    in_transit: "#f59e0b",
    maintenance: "#ef4444",
  };
  
  const color = colors[String(status || "").toLowerCase()] || "#6b7280";
  
  return L.divIcon({
    className: "custom-marker",
    html: `<div style="background-color: ${color}; width: 24px; height: 24px; border-radius: 50%; border: 2px solid white; box-shadow: 0 2px 4px rgba(0,0,0,0.3);"></div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
};

export default function Map() {
  const [assets, setAssets] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({
    assetType: "",
    status: "",
    warehouseId: "",
    agencyId: "",
  });

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      // Use the dedicated map API endpoint that returns assets with coordinates
      const mapAssets = await api("/map/assets");
      setAssets(mapAssets);
      
      // Still load warehouses and agencies for filters
      const [warehousesData, agenciesData] = await Promise.all([
        api("/warehouses"),
        api("/agencies"),
      ]);
      setWarehouses(warehousesData);
      setAgencies(agenciesData);
    } catch (err) {
      console.error("Failed to load map data:", err);
    } finally {
      setLoading(false);
    }
  }

  const filteredAssets = assets.filter((asset) => {
    if (filters.assetType && asset.asset_type !== filters.assetType) return false;
    if (filters.status && String(asset.operational_status || "").toLowerCase() !== filters.status) return false;
    if (filters.warehouseId && asset.warehouse_id !== filters.warehouseId) return false;
    if (filters.agencyId && asset.agency_id !== filters.agencyId) return false;
    return true;
  });

  // Map API already returns assets with coordinates, just filter and format
  const markers = filteredAssets
    .filter((asset) => asset.latitude && asset.longitude)
    .map((asset) => ({
      id: asset.id,
      lat: asset.latitude,
      lng: asset.longitude,
      asset,
      location: asset.location,
      source: asset.location_source,
    }));

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <p className="text-slate-500">Loading map...</p>
      </div>
    );
  }

  const center = [33.4484, -112.074]; // Phoenix, AZ (default center)

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Asset Location Map</h1>
          <p className="mt-1 text-sm text-slate-600">
            Showing {markers.length} assets with known locations across {warehouses.length} warehouses and {agencies.length} agencies
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex gap-3">
        <select
          value={filters.assetType}
          onChange={(e) => setFilters({ ...filters, assetType: e.target.value })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">All Asset Types</option>
          <option value="alpr_trailer">ALPR Trailer</option>
          <option value="semi_truck">Semi Truck</option>
          <option value="fleet_vehicle">Fleet Vehicle</option>
        </select>

        <select
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">All Statuses</option>
          <option value="available">Available</option>
          <option value="deployed">Deployed</option>
          <option value="in_transit">In Transit</option>
          <option value="maintenance">Maintenance</option>
        </select>

        <select
          value={filters.warehouseId}
          onChange={(e) => setFilters({ ...filters, warehouseId: e.target.value })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">All Warehouses</option>
          {warehouses.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>

        <select
          value={filters.agencyId}
          onChange={(e) => setFilters({ ...filters, agencyId: e.target.value })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">All Agencies</option>
          {agencies.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>

        <button
          onClick={() => setFilters({ assetType: "", status: "", warehouseId: "", agencyId: "" })}
          className="text-sm text-teal-600 hover:underline"
        >
          Clear filters
        </button>
      </div>

      {/* Map */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <MapContainer
          center={center}
          zoom={10}
          style={{ height: "600px", width: "100%" }}
          scrollWheelZoom={true}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitBounds markers={markers} />
          <MarkerClusterGroup
            chunkedLoading
            spiderfyOnMaxZoom={true}
            showCoverageOnHover={true}
            zoomToBoundsOnClick={true}
            maxClusterRadius={50}
            iconCreateFunction={(cluster) => {
              const count = cluster.getChildCount();
              return L.divIcon({
                html: `<div style="background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%); color: white; border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px; border: 3px solid white; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">${count}</div>`,
                className: 'custom-cluster-icon',
                iconSize: L.point(40, 40),
              });
            }}
          >
            {markers.map((marker) => (
              <Marker
                key={marker.id}
                position={[marker.lat, marker.lng]}
                icon={createCustomIcon(marker.asset.asset_type, marker.asset.operational_status)}
              >
                <Popup>
                  <div className="p-2">
                    <p className="font-semibold text-slate-900">{marker.asset.make_model}</p>
                    <p className="text-xs text-slate-600">Asset ID: {marker.asset.vin}</p>
                    <div className="mt-2 space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-500">Status:</span>
                        <Badge value={marker.asset.operational_status} />
                      </div>
                      <p className="text-xs text-slate-600">
                        <strong>Location:</strong> {marker.location}
                      </p>
                      <p className="text-xs text-slate-500">Source: {marker.source}</p>
                    </div>
                    <a
                      href={`/assets/${marker.asset.id}`}
                      className="mt-2 inline-block text-xs text-teal-600 hover:underline"
                    >
                      View asset details →
                    </a>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MarkerClusterGroup>
        </MapContainer>
      </div>

      {/* Legend */}
      <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
        <p className="text-sm font-semibold text-slate-900">Legend</p>
        <div className="mt-2 flex flex-wrap gap-4">
          <div className="flex items-center gap-2">
            <div className="h-3 w-3 rounded-full bg-emerald-500"></div>
            <span className="text-xs text-slate-600">Available</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="h-3 w-3 rounded-full bg-blue-500"></div>
            <span className="text-xs text-slate-600">Deployed</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="h-3 w-3 rounded-full bg-amber-500"></div>
            <span className="text-xs text-slate-600">In Transit</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="h-3 w-3 rounded-full bg-red-500"></div>
            <span className="text-xs text-slate-600">Maintenance</span>
          </div>
        </div>
      </div>
    </div>
  );
}
