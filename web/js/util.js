// Small DOM and formatting helpers shared by every view.

/**
 * Create an element. `spec` is "tag.class1.class2"; `attrs` may contain DOM
 * properties, `on*` event handlers and `dataset`; children may be nodes,
 * strings, arrays or null/false (skipped).
 */
export function h(spec, attrs = {}, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "class") el.className += ` ${value}`;
    else if (key in el) el[key] = value;
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Replace all children of `el`. */
export function fill(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

/** Transient message at the bottom of the screen. */
export function toast(message, kind = "info") {
  const el = h(`div.toast.${kind}`, {}, message);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 4000);
}

/** Clock text for a number of milliseconds: 1:05:00, 4:59 or 0:09.3. */
export function formatClock(ms) {
  if (ms === null || ms === undefined) return "∞";
  const total = Math.max(0, ms);
  const seconds = Math.floor(total / 1000);
  const hrs = Math.floor(seconds / 3600);
  const min = Math.floor((seconds % 3600) / 60);
  const sec = String(seconds % 60).padStart(2, "0");
  if (hrs) return `${hrs}:${String(min).padStart(2, "0")}:${sec}`;
  if (total < 20000) return `${min}:${sec}.${Math.floor((total % 1000) / 100)}`;
  return `${min}:${sec}`;
}

/** "5+3 Blitz", "1½+0 Bullet" or "No clock". */
export function timeControlLabel(tc) {
  if (!tc.base) return "No clock";
  const minutes = tc.base / 60;
  const text = Number.isInteger(minutes) ? minutes : minutes.toFixed(2).replace(/0+$/, "");
  const category = tc.category[0].toUpperCase() + tc.category.slice(1);
  return `${text}+${tc.increment} ${category}`;
}

export function formatDate(isoString) {
  if (!isoString) return "";
  return new Date(isoString).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Copy text to the clipboard. `navigator.clipboard` only exists on HTTPS or
 * localhost, and a LAN server is plain HTTP, so fall back to the classic
 * hidden-textarea technique.
 */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = h("textarea", { value: text, style: "position:fixed;opacity:0" });
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast("Copied to clipboard", "success");
  } catch {
    toast("Could not copy - select the text manually", "error");
  }
}

export function navigate(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = hash;
}

/** Group a flat list of moves into numbered rows: [[1, white, black], ...]. */
export function moveRows(moves, startPly = 0) {
  const rows = [];
  moves.forEach((move, i) => {
    const ply = startPly + i;
    if (ply % 2 === 0 || !rows.length) rows.push({ number: Math.floor(ply / 2) + 1, cells: [] });
    const row = rows[rows.length - 1];
    if (ply % 2 === 1 && !row.cells.length) row.cells.push(null); // line starts with Black
    row.cells.push({ move, index: i });
  });
  return rows;
}
