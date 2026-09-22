export const ASSET_TYPES = ["ALPR Trailer", "Semi Truck", "Fleet Vehicle"];
export const CUSTODY_TYPES = ["Warehouse Depot", "In Transit", "Customer / LE Agency"];
export const DEPLOYMENT_STATUSES = ["scheduled", "active", "completed", "cancelled", "staged", "in_transit", "deployed", "idle", "stored", "returned"];
export const OPERATIONAL_STATUSES = ["available", "deployed", "in_transit", "maintenance", "retired"];
export const WORK_ORDER_STATUSES = [
  "open",
  "investigating",
  "in_progress",
  "waiting_parts",
  "waiting_vendor",
  "completed",
  "cancelled",
];
export const WORK_ORDER_COLUMNS = [
  { id: "open", label: "Open" },
  { id: "investigating", label: "Investigating" },
  { id: "in_progress", label: "In Progress" },
  { id: "waiting_parts", label: "Waiting on Parts" },
  { id: "waiting_vendor", label: "Waiting on Vendor" },
  { id: "completed", label: "Complete" },
  { id: "cancelled", label: "Cancelled" },
];
export const REPAIR_CHANNELS = ["Internal", "Third-Party Vendor"];
export const ISSUE_SOURCES = ["Inspection", "Manual Report", "Preventive Maintenance", "Other"];
export const PRIORITIES = ["low", "medium", "high", "critical"];
export const ROOT_CAUSE_CATEGORIES = [
  "Mechanical Failure",
  "Electrical Failure",
  "Wear and Tear",
  "Installation Issue",
  "Manufacturing Defect",
  "Operator Damage",
  "Environmental Damage",
  "Software / Firmware",
  "Unknown",
  "Other",
];
export const OPEN_WO_STATUSES = ["open", "investigating", "in_progress", "waiting_parts", "waiting_vendor"];

export const PM_STATUSES = ["ok", "due_soon", "due", "overdue"];
export const PM_STATUS_LABELS = {
  ok: "OK",
  due_soon: "Due Soon",
  due: "Due",
  overdue: "Overdue",
};
export const PM_STATUS_COLORS = {
  ok: "bg-green-50 text-green-800 ring-green-200",
  due_soon: "bg-amber-50 text-amber-800 ring-amber-200",
  due: "bg-orange-50 text-orange-800 ring-orange-200",
  overdue: "bg-red-50 text-red-800 ring-red-200",
};
