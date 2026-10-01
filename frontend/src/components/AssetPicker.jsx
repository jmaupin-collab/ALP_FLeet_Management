import { useEffect, useId, useMemo, useRef, useState } from "react";

// A fleet can run to thousands of units, so the list is filtered as you type and
// only a slice is rendered. Anything past this is reachable by typing more, not
// by scrolling further.
const MAX_VISIBLE = 50;

export function assetLabel(asset) {
  if (!asset) return "";
  return `${asset.make_model} · ${asset.license_plate || asset.vin}`;
}

// Everything you might recognise a unit by. Asset ID and plate are both here
// because people search by whichever one is printed on the paperwork in hand.
function haystack(asset) {
  return [asset.vin, asset.license_plate, asset.make_model, asset.asset_type, asset.current_location]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

export default function AssetPicker({
  assets,
  value,
  onChange,
  inputClass = "",
  placeholder = "Search by Asset ID, plate, or model",
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef(null);
  const listId = useId();

  const selected = useMemo(
    () => assets.find((asset) => String(asset.id) === String(value)) || null,
    [assets, value]
  );

  const matches = useMemo(() => {
    // Every word has to match somewhere, so "ford az" narrows instead of widening.
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return assets;
    return assets.filter((asset) => {
      const text = haystack(asset);
      return terms.every((term) => text.includes(term));
    });
  }, [assets, query]);

  const visible = matches.slice(0, MAX_VISIBLE);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (!wrapRef.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function choose(asset) {
    onChange(String(asset.id));
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setActive((index) => {
        const next = event.key === "ArrowDown" ? index + 1 : index - 1;
        if (next < 0) return visible.length - 1;
        if (next >= visible.length) return 0;
        return next;
      });
    } else if (event.key === "Enter" && open && visible[active]) {
      event.preventDefault();
      choose(visible[active]);
    } else if (event.key === "Escape" && open) {
      // Close the list without letting the surrounding modal close too.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div ref={wrapRef} className="relative">
      <input
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        className={inputClass}
        placeholder={placeholder}
        // Closed, the box reads back what is selected; typing only applies while open.
        value={open ? query : assetLabel(selected)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {open ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-slate-300 bg-white py-1 shadow-lg"
        >
          {visible.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-500">No vehicle matches “{query}”.</li>
          ) : null}
          {visible.map((asset, index) => (
            <li key={asset.id} role="option" aria-selected={index === active}>
              <button
                type="button"
                // Runs before blur, so the click is not lost to the list closing.
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(asset)}
                className={`block w-full px-3 py-2 text-left text-sm ${
                  index === active ? "bg-teal-50" : ""
                }`}
              >
                <span className="block truncate text-slate-900">{asset.make_model}</span>
                <span className="mt-0.5 block font-mono text-xs text-slate-500">
                  {asset.vin}
                  {asset.license_plate ? ` · ${asset.license_plate}` : ""}
                </span>
              </button>
            </li>
          ))}
          {matches.length > visible.length ? (
            <li className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
              Showing {visible.length} of {matches.length}. Keep typing to narrow it down.
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
