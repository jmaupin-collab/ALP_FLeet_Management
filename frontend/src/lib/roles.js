// Mirrors backend/app/rbac.py. Legacy role values are resolved to their modern
// equivalent so a permission list only ever needs to name the canonical role.

export const ROLES = {
  SYSTEM_ADMIN: "system_admin",
  ORG_ADMIN: "org_admin",
  FLEET_MANAGER: "fleet_manager",
  TECHNICIAN: "technician",
  READ_ONLY: "read_only",
  CUSTOMER: "customer",
};

const CANONICAL = {
  admin: ROLES.ORG_ADMIN,
  dispatcher: ROLES.FLEET_MANAGER,
  program_manager: ROLES.FLEET_MANAGER,
  viewer: ROLES.READ_ONLY,
};

export function canonicalRole(user) {
  const role = typeof user === "string" ? user : user?.role;
  if (!role) return null;
  return CANONICAL[role] || role;
}

export function hasRole(user, allowed) {
  const role = canonicalRole(user);
  return Boolean(role) && allowed.includes(role);
}

// Named groups so route and nav definitions stay readable and stay in sync.
export const ADMINS = [ROLES.SYSTEM_ADMIN, ROLES.ORG_ADMIN];
export const MANAGERS = [ROLES.SYSTEM_ADMIN, ROLES.ORG_ADMIN, ROLES.FLEET_MANAGER];
// Managers plus read-only: fleet oversight pages. Technicians are excluded.
export const VIEWERS = [...MANAGERS, ROLES.READ_ONLY];
// Managers plus technicians: hands-on maintenance work.
export const OPERATORS = [...MANAGERS, ROLES.TECHNICIAN];
export const ALL_INTERNAL = [...OPERATORS, ROLES.READ_ONLY];
export const EVERYONE = [...ALL_INTERNAL, ROLES.CUSTOMER];

/** First page a role can actually open. Used as a redirect target so a guard
 *  never bounces a user to a page they also cannot see. */
export function homePathFor(user) {
  const role = canonicalRole(user);
  if (role === ROLES.CUSTOMER) return "/inspections";
  if (role === ROLES.TECHNICIAN) return "/attention";
  return "/";
}
