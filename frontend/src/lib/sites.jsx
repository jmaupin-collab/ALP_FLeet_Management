// Where a custody type can send an asset, and the picker that goes with it.
//
// "In Transit" spans both directories: a unit ships out to a customer agency at
// least as often as it ships back to a depot, so the destination list has to
// offer either. The other two custody types name exactly one directory.
export const SITE_FIELD = {
  "Customer / LE Agency": { label: "Customer / Agency", directories: ["agencies"] },
  "In Transit": { label: "Destination", directories: ["warehouses", "agencies"] },
  "Warehouse Depot": { label: "Warehouse", directories: ["warehouses"] },
};

const DIRECTORY_LABEL = { warehouses: "Warehouses", agencies: "Customers / Agencies" };

function optionLabel(row) {
  return `${row.name}${row.site_name ? ` - ${row.site_name}` : ""}`;
}

export function siteGroups(custodyType, { warehouses = [], agencies = [] } = {}) {
  const config = SITE_FIELD[custodyType];
  if (!config) return [];
  const source = { warehouses, agencies };
  return config.directories
    .map((directory) => ({
      directory,
      label: DIRECTORY_LABEL[directory],
      options: (source[directory] || []).map((row) => ({
        // One <select> can span two directories, so the value has to say which
        // list the row came from. The id alone would be ambiguous.
        value: `${directory}:${row.id}`,
        label: optionLabel(row),
      })),
    }))
    .filter((group) => group.options.length > 0);
}

export function siteValue(form) {
  if (form.warehouse_id) return `warehouses:${form.warehouse_id}`;
  if (form.agency_id) return `agencies:${form.agency_id}`;
  return "";
}

// Choosing a site always clears the other directory, because an asset is headed
// to one place and the backend links whichever id it receives.
export function siteSelection(value) {
  const [directory, id] = String(value || "").split(":");
  if (!id) return { warehouse_id: "", agency_id: "" };
  return {
    warehouse_id: directory === "warehouses" ? id : "",
    agency_id: directory === "agencies" ? id : "",
  };
}

export function needsSite(custodyType) {
  return Boolean(SITE_FIELD[custodyType]);
}

export function hasSite(custodyType, form) {
  return !needsSite(custodyType) || Boolean(form.warehouse_id || form.agency_id);
}

export function SiteSelect({ custodyType, form, onSelect, className, warehouses = [], agencies = [] }) {
  const config = SITE_FIELD[custodyType];
  if (!config) return null;
  const groups = siteGroups(custodyType, { warehouses, agencies });
  // A single directory reads better as a plain list than as one lone group.
  const grouped = groups.length > 1;

  return (
    <select
      required
      className={className}
      value={siteValue(form)}
      onChange={(e) => onSelect(siteSelection(e.target.value), e.target.selectedOptions[0]?.text || "")}
    >
      <option value="">Select {config.label.toLowerCase()}</option>
      {grouped
        ? groups.map((group) => (
            <optgroup key={group.directory} label={group.label}>
              {group.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </optgroup>
          ))
        : (groups[0]?.options || []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
    </select>
  );
}
