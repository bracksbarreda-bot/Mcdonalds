import { api, auth } from "./api.js";

/** Escapa texto para insertarlo en HTML (evita XSS con datos de los Excel o de usuarios). */
export const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const moneyFmt = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
export const money = (v) => moneyFmt.format(v || 0);

/** "2026-01-10 13:30:00" -> "10/01/2026 13:30" */
export function dt(s) {
  if (!s) return "—";
  const [d, t] = String(s).split(/[ T]/);
  const [y, m, dd] = d.split("-");
  return `${dd}/${m}/${y}${t ? " " + t.slice(0, 5) : ""}`;
}

/** Valor para <input type="datetime-local"> con la hora actual. */
export function nowLocal() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

// Catálogos (plataformas, estados, tipos, locales...) que da el backend en /api/meta
export const meta = { data: null };
export async function loadMeta() {
  meta.data = await api("/meta");
  return meta.data;
}

export const isAdmin = () => auth.user()?.role === "admin";

export function pill(platform) {
  const p = meta.data.platforms[platform] || { name: platform, color: "#999" };
  return `<span class="pill" style="--c:${esc(p.color)}">${esc(p.name)}</span>`;
}

export function statusBadge(status) {
  return `<span class="status status-${esc(status)}">${esc(meta.data.statuses[status] || status)}</span>`;
}

export function prioBadge(prio) {
  return `<span class="prio prio-${esc(prio)}">${esc(meta.data.priorities[prio] || prio)}</span>`;
}

/** <option>s a partir de un objeto {clave: etiqueta | {name}} */
export function options(obj, selected = "", { empty } = {}) {
  let html = empty !== undefined ? `<option value="">${esc(empty)}</option>` : "";
  for (const [k, v] of Object.entries(obj)) {
    const label = typeof v === "object" ? v.name : v;
    html += `<option value="${esc(k)}" ${String(k) === String(selected ?? "") ? "selected" : ""}>${esc(label)}</option>`;
  }
  return html;
}

export function restaurantOptions(selected = "", empty) {
  const obj = {};
  for (const r of meta.data.restaurants) obj[r.id] = `${r.name} (${r.code})`;
  return options(obj, selected, { empty });
}

/** Mensaje temporal arriba de la página. type: success | error | warning */
export function toast(message, type = "success", ms = 5000) {
  const box = document.getElementById("toasts");
  const p = document.createElement("p");
  p.className = `flash flash-${type}`;
  p.setAttribute("role", type === "error" ? "alert" : "status");
  p.textContent = message;
  box.appendChild(p);
  setTimeout(() => p.remove(), ms);
}

/** Descarga un Response (p. ej. CSV) como archivo. */
export async function downloadResponse(res, fallbackName) {
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename=([^;]+)/) || [])[1] || fallbackName;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name.replace(/"/g, "");
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
