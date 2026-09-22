import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useMe } from "../lib/useMe.js";
import { hasRole, homePathFor } from "../lib/roles.js";

/**
 * Route guard. The API enforces these rules too; this only avoids rendering a
 * page the user would just get a 403 from.
 */
export default function RequireRole({ allowed }) {
  const { me, loading } = useMe();
  const location = useLocation();

  if (loading) {
    return <p className="p-8 text-sm text-slate-600">Checking permissions…</p>;
  }
  if (!hasRole(me, allowed)) {
    const target = homePathFor(me);
    // Redirecting to a page this role also cannot open would loop forever.
    if (target === location.pathname) {
      return (
        <p className="p-8 text-sm text-slate-600">
          Your account does not have access to this page.
        </p>
      );
    }
    return <Navigate to={target} replace />;
  }
  return <Outlet />;
}
