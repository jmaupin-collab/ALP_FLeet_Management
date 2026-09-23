import { useEffect, useState } from "react";
import { DataTable } from "../components/DataTable";
import { Field, Modal, Notice, inputClass } from "../components/Modal.jsx";
import { api } from "../lib/api.js";
import { Badge } from "../lib/format.jsx";

const USER_ROLES = [
  { value: "org_admin", label: "Admin" },
  { value: "fleet_manager", label: "Fleet Manager" },
  { value: "technician", label: "Technician" },
  { value: "read_only", label: "Read Only" },
  { value: "customer", label: "Customer" },
];

const ROLE_DESCRIPTIONS = {
  org_admin: "Full admin access - can manage users and all system features",
  fleet_manager: "Manage fleet operations, deployments, and assets",
  technician: "Maintenance and inspections access",
  read_only: "View-only access to all features",
  customer: "Limited external customer access",
};

export default function Admin() {
  const [users, setUsers] = useState([]);
  const [agencies, setAgencies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({});
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await api("/users");
      setUsers(data);
    } catch (err) {
      setError(err.message);
      setUsers([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // Customer accounts are scoped to one agency, so the form needs the list.
    api("/agencies").then(setAgencies).catch(() => setAgencies([]));
  }, []);

  async function saveUser(event) {
    event.preventDefault();
    if (!form.email || !form.full_name || !form.role) {
      setError("Email, full name, and role are required.");
      return;
    }
    if (modal === "create" && !form.password) {
      setError("Password is required for new users.");
      return;
    }
    if (form.password && form.password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (form.role === "customer" && !form.agency_id) {
      setError("Customer accounts must be assigned to an agency.");
      return;
    }

    setBusy(true);
    setError("");
    try {
      if (modal === "create") {
        await api("/users", {
          method: "POST",
          body: JSON.stringify({
            email: form.email,
            password: form.password,
            full_name: form.full_name,
            role: form.role,
            // Only customers carry an agency; the server clears it otherwise.
            agency_id: form.role === "customer" ? form.agency_id : null,
          }),
        });
        setSuccess("User created successfully.");
      } else {
        const updates = {
          email: form.email,
          full_name: form.full_name,
          role: form.role,
          is_active: form.is_active,
          agency_id: form.role === "customer" ? form.agency_id : null,
        };
        if (form.password) {
          updates.password = form.password;
        }
        await api(`/users/${form.id}`, {
          method: "PATCH",
          body: JSON.stringify(updates),
        });
        setSuccess("User updated successfully.");
      }
      setModal(null);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteUser(userId) {
    if (!confirm("Are you sure you want to delete this user? This action cannot be undone.")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/users/${userId}`, { method: "DELETE" });
      setSuccess("User deleted successfully.");
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const getRoleBadgeColor = (role) => {
    switch (role) {
      case "org_admin":
      case "admin":
        return "bg-purple-100 text-purple-800";
      case "fleet_manager":
      case "dispatcher":
        return "bg-blue-100 text-blue-800";
      case "technician":
        return "bg-teal-100 text-teal-800";
      case "read_only":
      case "viewer":
        return "bg-slate-100 text-slate-800";
      case "customer":
        return "bg-amber-100 text-amber-800";
      default:
        return "bg-slate-100 text-slate-800";
    }
  };

  const getRoleLabel = (role) => {
    const roleMap = {
      org_admin: "Admin",
      admin: "Admin",
      fleet_manager: "Fleet Manager",
      dispatcher: "Fleet Manager",
      technician: "Technician",
      read_only: "Read Only",
      viewer: "Read Only",
      customer: "Customer",
    };
    return roleMap[role] || role;
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-slate-900">Admin</h2>
          <p className="mt-1 text-sm text-slate-600">
            Manage users. Assign customer assets from the Customer Access tab.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setForm({
              email: "",
              password: "",
              full_name: "",
              role: "read_only",
              is_active: true,
            });
            setModal("create");
          }}
          className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800"
        >
          Add User
        </button>
      </div>

      <Notice error={error} success={success} />

          {loading ? <p className="text-sm text-slate-600">Loading users…</p> : null}

          <DataTable
        columns={[
          { key: "full_name", header: "Name", render: (r) => <span className="font-medium">{r.full_name}</span> },
          { key: "email", header: "Email" },
          {
            key: "role",
            header: "Role",
            render: (r) => (
              <span className={`inline-block rounded-full px-2 py-1 text-xs font-semibold ${getRoleBadgeColor(r.role)}`}>
                {getRoleLabel(r.role)}
              </span>
            ),
          },
          {
            key: "is_active",
            header: "Status",
            render: (r) => (
              <span className={`inline-block rounded-full px-2 py-1 text-xs font-semibold ${
                r.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
              }`}>
                {r.is_active ? "Active" : "Inactive"}
              </span>
            ),
          },
          {
            key: "created_at",
            header: "Created",
            render: (r) => new Date(r.created_at).toLocaleDateString(),
          },
          {
            key: "actions",
            header: "Actions",
            render: (r) => (
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => {
                    setForm({ ...r, password: "" });
                    setModal("edit");
                  }}
                  className="font-semibold text-teal-800 hover:underline"
                >
                  Edit
                </button>
                <button
                  type="button"
                  onClick={() => deleteUser(r.id)}
                  disabled={busy}
                  className="font-semibold text-red-600 hover:underline disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
            ),
          },
        ]}
        rows={users}
      />

      {modal === "create" || modal === "edit" ? (
        <Modal
          title={modal === "create" ? "Add User" : "Edit User"}
          open={true}
          onClose={() => setModal(null)}
        >
          <form onSubmit={saveUser} className="space-y-4">
            <Field label="Full Name">
              <input
                className={inputClass}
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                required
              />
            </Field>
            <Field label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                required
              />
            </Field>
            <Field label={modal === "create" ? "Password" : "Password (leave blank to keep current)"}>
              <input
                type="password"
                className={inputClass}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required={modal === "create"}
                placeholder={modal === "edit" ? "Leave blank to keep current password" : ""}
              />
              <p className="mt-1 text-xs text-slate-500">Minimum 8 characters</p>
            </Field>
            <Field label="Role">
              <select
                className={inputClass}
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                required
              >
                {USER_ROLES.map((role) => (
                  <option key={role.value} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">{ROLE_DESCRIPTIONS[form.role]}</p>
            </Field>
            {form.role === "customer" ? (
              <Field label="Agency">
                <select
                  className={inputClass}
                  value={form.agency_id || ""}
                  onChange={(e) => setForm({ ...form, agency_id: e.target.value })}
                  required
                >
                  <option value="">-- Select an agency --</option>
                  {agencies.map((agency) => (
                    <option key={agency.id} value={agency.id}>
                      {agency.name}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate-500">
                  {agencies.length === 0
                    ? "No agencies yet. Add one under Agencies first."
                    : "This customer will only see assets currently assigned to this agency."}
                </p>
              </Field>
            ) : null}
            {modal === "edit" ? (
              <Field label="Status">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.is_active}
                    onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                  />
                  <span className="text-sm">Active</span>
                </label>
              </Field>
            ) : null}
            <div className="flex gap-3">
              <button
                disabled={busy}
                type="submit"
                className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:opacity-50"
              >
                {busy ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={() => setModal(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      ) : null}
    </div>
  );
}
