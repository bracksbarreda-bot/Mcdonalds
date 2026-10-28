import { api } from "../api.js";
import { sendClaims } from "../claims.js";
import { downloadResponse, dt, esc, isAdmin, meta, money, options, pill, prioBadge, restaurantOptions, statusBadge, toast } from "../ui.js";

const CLAIM_PLATFORMS = ["ubereats", "justeat"]; // Glovo fuera de esta fase
const FILTER_KEYS = ["q", "platform", "status", "type", "restaurant", "desde", "hasta", "batch"];

export async function render(el, _params, query) {
  const m = meta.data;
  const q = {};
  for (const k of FILTER_KEYS) if (query.get(k)) q[k] = query.get(k);
  const page = parseInt(query.get("page") || "1", 10) || 1;
  const data = await api("/incidents", { query: { ...q, page } });

  const statusOpts = `<option value="">Todos</option>
    <option value="pendientes" ${q.status === "pendientes" ? "selected" : ""}>Pendientes (abiertas + reclamadas)</option>
    ${options(m.statuses, q.status)}`;

  const canMail = isAdmin();
  const mailable = (i) => CLAIM_PLATFORMS.includes(i.platform);
  const rows = data.rows.map((i) => `
    <tr ${i.is_late ? 'class="row-late"' : ""}>
      ${canMail ? `<td>${mailable(i) ? `<input type="checkbox" class="sel" value="${i.id}" aria-label="Seleccionar pedido ${esc(i.order_ref)}">` : ""}</td>` : ""}
      <td><a href="#/incidencias/${i.id}">${i.id}</a></td>
      <td>${dt(i.order_date)}</td>
      <td>${pill(i.platform)}</td>
      <td><a href="#/incidencias/${i.id}">${esc(i.order_ref)}</a></td>
      ${isAdmin() ? `<td>${esc(i.restaurant_name)}</td>` : ""}
      <td>${esc(m.incident_types[i.incident_type] || i.incident_type)}</td>
      <td>${prioBadge(i.priority)}</td>
      <td class="num">${money(i.claimed_amount)}</td>
      <td class="num">${money(i.recovered_amount)}</td>
      <td>${statusBadge(i.status)}
        ${i.is_late ? `<span class="late-flag" title="Pendiente desde hace ${i.days} días">⚠ ${i.days} d</span>` : ""}</td>
    </tr>`).join("");

  const qsFor = (p) => new URLSearchParams({ ...q, page: p }).toString();

  el.innerHTML = `
    <div class="page-head">
      <h1>Incidencias <small class="muted">(${data.total})</small></h1>
      <div class="head-actions">
        ${isAdmin() ? `<a class="btn" href="#/importar">Importar Excel</a>` : ""}
        <button type="button" class="btn" id="export">Exportar CSV</button>
      </div>
    </div>

    ${q.batch ? `<p class="flash flash-warning">Mostrando solo las incidencias del lote de importación #${esc(q.batch)}.
      <a href="#/incidencias">Ver todas</a></p>` : ""}

    <form id="filters" class="card filters" aria-label="Filtros">
      <div class="field"><label for="f-q">Buscar</label>
        <input id="f-q" name="q" value="${esc(q.q)}" placeholder="Nº pedido, producto, ticket…"></div>
      <div class="field"><label for="f-platform">Plataforma</label>
        <select id="f-platform" name="platform">${options(m.platforms, q.platform, { empty: "Todas" })}</select></div>
      <div class="field"><label for="f-status">Estado</label><select id="f-status" name="status">${statusOpts}</select></div>
      <div class="field"><label for="f-type">Tipo</label>
        <select id="f-type" name="type">${options(m.incident_types, q.type, { empty: "Todos" })}</select></div>
      ${isAdmin() ? `<div class="field"><label for="f-rest">Local</label>
        <select id="f-rest" name="restaurant">${restaurantOptions(q.restaurant, "Todos")}</select></div>` : ""}
      <div class="field"><label for="f-desde">Desde</label><input id="f-desde" name="desde" type="date" value="${esc(q.desde)}"></div>
      <div class="field"><label for="f-hasta">Hasta</label><input id="f-hasta" name="hasta" type="date" value="${esc(q.hasta)}"></div>
      <div class="field actions">
        <button type="submit" class="btn btn-primary">Filtrar</button>
        <a class="btn btn-ghost" href="#/incidencias">Limpiar</a>
      </div>
    </form>

    <p class="summary">Reclamado: <strong>${money(data.sums.claimed)}</strong> · Recuperado: <strong>${money(data.sums.recovered)}</strong></p>

    ${canMail && data.rows.some(mailable) ? `
      <div class="card mail-bar">
        <strong>Reclamación por correo</strong> <small class="muted">(Uber Eats y Just Eat · un correo por pedido)</small>
        <div class="head-actions">
          <button type="button" class="btn btn-primary" id="mail-selected" disabled>Enviar las seleccionadas (0)</button>
          <button type="button" class="btn" id="mail-filter">Enviar todas las del filtro</button>
          <a class="btn btn-ghost" href="#/correo">Configurar correo</a>
        </div>
      </div>` : ""}

    ${data.rows.length ? `
      <div class="card table-wrap">
        <table class="table">
          <thead><tr>
            ${canMail ? `<th scope="col"><input type="checkbox" id="sel-all" aria-label="Seleccionar todas las de la página"></th>` : ""}
            <th scope="col">#</th><th scope="col">Fecha pedido</th><th scope="col">Plataforma</th><th scope="col">Nº pedido</th>
            ${isAdmin() ? `<th scope="col">Local</th>` : ""}
            <th scope="col">Tipo</th><th scope="col">Prioridad</th><th scope="col" class="num">Reclamado</th>
            <th scope="col" class="num">Recuperado</th><th scope="col">Estado</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${data.pages > 1 ? `
        <nav class="pagination" aria-label="Paginación">
          ${data.page > 1 ? `<a class="btn btn-ghost" href="#/incidencias?${qsFor(data.page - 1)}">← Anterior</a>` : ""}
          <span>Página ${data.page} de ${data.pages}</span>
          ${data.page < data.pages ? `<a class="btn btn-ghost" href="#/incidencias?${qsFor(data.page + 1)}">Siguiente →</a>` : ""}
        </nav>` : ""}
    ` : `
      <div class="card empty">
        <p>No hay incidencias con estos filtros.</p>
        <a class="btn btn-primary" href="#/incidencias/nueva">Registrar una incidencia</a>
        ${isAdmin() ? `<a class="btn" href="#/importar">Importar un Excel</a>` : ""}
      </div>`}`;

  el.querySelector("#filters").addEventListener("submit", (e) => {
    e.preventDefault();
    const params = new URLSearchParams();
    for (const [k, v] of new FormData(e.target)) if (String(v).trim()) params.set(k, String(v).trim());
    if (q.batch) params.set("batch", q.batch);
    location.hash = `#/incidencias?${params}`;
  });

  // Selección y envío de reclamaciones por correo
  const selected = () => [...el.querySelectorAll(".sel:checked")].map((c) => Number(c.value));
  const btnSel = el.querySelector("#mail-selected");
  const refresh = () => {
    if (!btnSel) return;
    const n = selected().length;
    btnSel.disabled = !n;
    btnSel.textContent = `Enviar las seleccionadas (${n})`;
  };
  el.querySelector("#sel-all")?.addEventListener("change", (e) => {
    el.querySelectorAll(".sel").forEach((c) => { c.checked = e.target.checked; });
    refresh();
  });
  el.querySelectorAll(".sel").forEach((c) => c.addEventListener("change", refresh));
  const reload = () => render(el, _params, query);
  btnSel?.addEventListener("click", () => sendClaims({ incident_ids: selected() }, { onDone: reload }));
  el.querySelector("#mail-filter")?.addEventListener("click", () => sendClaims({ filters: q }, { onDone: reload }));

  el.querySelector("#export").addEventListener("click", async () => {
    try {
      const res = await api("/incidents/export.csv", { query: q, raw: true });
      await downloadResponse(res, "incidencias.csv");
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
