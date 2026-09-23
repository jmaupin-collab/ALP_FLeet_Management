// Sidebar tab order is a personal preference, so it lives in localStorage per
// account rather than on the server. Two people sharing a browser keep their
// own arrangement, and clearing it just falls back to the shipped order.

const STORAGE_PREFIX = "fleet.navOrder";

export function storageKey(user) {
  return `${STORAGE_PREFIX}:${user?.email || "anonymous"}`;
}

export function loadOrder(user) {
  try {
    const raw = window.localStorage.getItem(storageKey(user));
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : null;
  } catch {
    // A corrupt or unavailable store should never break the nav.
    return null;
  }
}

export function saveOrder(user, paths) {
  try {
    window.localStorage.setItem(storageKey(user), JSON.stringify(paths));
  } catch {
    /* private mode or a full quota: the order just won't persist */
  }
}

export function clearOrder(user) {
  try {
    window.localStorage.removeItem(storageKey(user));
  } catch {
    /* nothing to do */
  }
}

export function orderTabs(tabs, savedOrder) {
  if (!Array.isArray(savedOrder) || savedOrder.length === 0) return tabs;
  const position = new Map(savedOrder.map((path, index) => [path, index]));
  // A tab added to the app after this order was saved has no stored position.
  // Park it at the end instead of dropping it off the nav entirely.
  return tabs
    .map((tab, index) => ({ tab, index, rank: position.has(tab.to) ? position.get(tab.to) : Infinity }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.tab);
}

export function moveItem(list, from, to) {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const next = [...list];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
