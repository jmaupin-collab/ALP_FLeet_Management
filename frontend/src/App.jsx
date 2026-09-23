import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import RequireAuth from "./components/RequireAuth.jsx";
import RequireRole from "./components/RequireRole.jsx";
import Admin from "./pages/Admin.jsx";
import CustomerAccess from "./pages/CustomerAccess.jsx";
import Analytics from "./pages/Analytics.jsx";
import AssetTimeline from "./pages/AssetTimeline.jsx";
import Assets from "./pages/Assets.jsx";
import Attention from "./pages/Attention.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Deployments from "./pages/Deployments.jsx";
import DirectoryPage from "./pages/DirectoryPage.jsx";
import Documents from "./pages/Documents.jsx";
import Inspections from "./pages/Inspections.jsx";
import Login from "./pages/Login.jsx";
import Maintenance from "./pages/Maintenance.jsx";
import Map from "./pages/Map.jsx";
import Parts from "./pages/Parts.jsx";
import PreventiveMaintenance from "./pages/PreventiveMaintenance.jsx";
import WorkOrderDetail from "./pages/WorkOrderDetail.jsx";
import { ADMINS, ALL_INTERNAL, MANAGERS, OPERATORS, VIEWERS } from "./lib/roles.js";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          {/* Customers reach only the map and their own inspections. */}
          <Route path="/map" element={<Map />} />
          <Route path="/inspections" element={<Inspections />} />

          {/* Maintenance work: managers, technicians, and read-only. */}
          <Route element={<RequireRole allowed={ALL_INTERNAL} />}>
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="/maintenance/:woId" element={<WorkOrderDetail />} />
            <Route path="/preventive-maintenance" element={<PreventiveMaintenance />} />
          </Route>

          {/* Fleet oversight: managers and read-only, but not technicians. */}
          <Route element={<RequireRole allowed={VIEWERS} />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/assets/:assetId" element={<AssetTimeline />} />
            <Route path="/deployments" element={<Deployments />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route
              path="/warehouses"
              element={
                <DirectoryPage
                  title="Warehouses / Depots"
                  path="/warehouses"
                  extraFields={[{ key: "address", label: "Address" }]}
                />
              }
            />
            <Route
              path="/agencies"
              element={
                <DirectoryPage
                  title="Customers / LE Agencies"
                  path="/agencies"
                  extraFields={[
                    { key: "agency_type", label: "Type" },
                    { key: "contact_name", label: "Contact" },
                    { key: "address", label: "Address" },
                  ]}
                />
              }
            />
            <Route
              path="/vendors"
              element={
                <DirectoryPage
                  title="Vendors"
                  path="/vendors"
                  extraFields={[{ key: "specialty", label: "Specialty" }]}
                />
              }
            />
          </Route>

          {/* Hands-on work: managers and technicians. */}
          <Route element={<RequireRole allowed={OPERATORS} />}>
            <Route path="/attention" element={<Attention />} />
            <Route path="/parts" element={<Parts />} />
          </Route>

          <Route element={<RequireRole allowed={MANAGERS} />}>
            <Route path="/documents" element={<Documents />} />
            <Route path="/customer-access" element={<CustomerAccess />} />
          </Route>

          <Route element={<RequireRole allowed={ADMINS} />}>
            <Route path="/admin" element={<Admin />} />
          </Route>
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
