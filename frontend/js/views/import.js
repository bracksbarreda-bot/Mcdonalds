import { api } from "../api.js";
import { dt, esc, isAdmin, loadMeta, money, pill, toast } from "../ui.js";

// Importación en 3 pasos: 1) subir el Excel  2) revisar  3) listo.
// La plataforma y el restaurante de cada pedido se leen del propio Excel: no hay que elegir nada.

const STATUS = {
  nueva: { label: "Se importará", cls: "status-resuelta" },
  error: { label: "Con error", cls: "status-abierta" },
  repetida: { label: "Repetida en el Excel", cls: "status-reclamada" },
  duplicada: { label: "Ya importada antes", cls: "status-rechazada" },
};
const FILTERS = [["todas", "Todas"], ["nueva", "Se importarán"], ["error", "Con error"],
  ["repetida", "Repetidas"], ["duplicada", "Ya importadas"]];

export async function render(el) {
  if (!isAdmin()) {
    el.innerHTML = `<div class="card empty"><h1>Solo el administrador</h1>
      <p class="muted">Los Excel traen los pedidos de todos los restaurantes, así que solo el administrador los importa.</p>
      <a class="btn" href="#/">Volver al panel</a></div>`;
    return;
  }
  const state = { file_id: null, filename: "", ref_date: "", filter: "todas" };

  el.innerHTML = `
    <div class="page-head">
      <h1>Importar Excel de incidencias</h1>
      <a class="btn btn-ghost" href="#/incidencias">Ver incidencias</a>
    </div>
    <ol class="stepper" aria-label="Pasos de la importación">
      <li data-step="1" aria-current="step">1 · Subir Excel</li><li data-step="2">2 · Revisar</li><li data-step="3">3 · Listo</li>
    </ol>
    <div class="grid grid-detail">
      <div class="stack">
        <form id="step1" class="card form" novalidate>
          <h2><span class="step-n" aria-hidden="true">1</span> Sube el Excel de la plataforma</h2>
          <p class="muted">La plataforma y el restaurante de cada pedido se leen del propio Excel. No hay que elegir nada.</p>
          <label for="imp-file">Archivo Excel (.xlsx, máx. 10 MB)</label>
          <input id="imp-file" name="file" type="file" accept=".xlsx,.xlsm" required>
          <p id="step1-error" class="flash flash-error" role="alert" hidden></p>
          <button type="submit" class="btn btn-primary">Analizar Excel</button>
        </form>
        <section id="step2" class="card" hidden aria-live="polite"></section>
        <section id="step3" class="card" hidden aria-live="polite"></section>
      </div>
      <div class="stack">
        <article class="card help">
          <h2>Formato de cada Excel</h2>
          <ul class="help-list">
            <li>${pill("ubereats")} <em>Order ID · Date and time · Chargebacks · local</em></li>
            <li>${pill("justeat")} <em>PEDIDOS</em> («20:37#192370685REFUND 26,13 €») · <em>RTES</em> (local)</li>
            <li>${pill("glovo")} <em>Order received at · Order ID · Payout amount · Operational charges</em>
              (local en la columna o en el nombre del archivo)</li>
          </ul>
          <p class="muted">Un Excel por plataforma con todos los restaurantes. Los restaurantes nuevos se crean solos.
            Puedes subir el mismo Excel varias veces: los pedidos ya importados no se duplican.</p>
        </article>
        <article class="card"><h2>Últimas importaciones</h2><div id="batches"><p class="muted">Cargando…</p></div></article>
      </div>
    </div>`;

  const step1 = el.querySelector("#step1");
  const step2 = el.querySelector("#step2");
  const step3 = el.querySelector("#step3");
  const setStep = (n) => el.querySelectorAll(".stepper li").forEach((li) => {
    const s = Number(li.dataset.step);
    if (s === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    li.classList.toggle("done", s < n);
  });
  loadBatches(el.querySelector("#batches"));

  // ---------- Paso 1: subir ----------
  step1.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = el.querySelector("#step1-error");
    err.hidden = true;
    step3.hidden = true;
    const file = step1.file.files[0];
    if (!file) { err.textContent = "Selecciona el archivo Excel."; err.hidden = false; return; }
    const btn = step1.querySelector("button");
    btn.disabled = true;
    btn.textContent = "Analizando…";
    try {
      const fd = new FormData();
      fd.set("file", file);
      const r = await api("/import/preview", { method: "POST", form: fd });
      Object.assign(state, { file_id: r.file_id, filename: r.filename, ref_date: r.ref_date || "", filter: "todas" });
      renderStep2(r);
      step2.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Analizar Excel";
    }
  });

  // ---------- Paso 2: revisar ----------
  async function recalc() {
    const fd = new FormData();
    fd.set("file_id", state.file_id);
    fd.set("filename", state.filename);
    fd.set("ref_date", state.ref_date);
    step2.classList.add("is-loading");
    try { renderStep2(await api("/import/preview", { method: "POST", form: fd })); }
    catch (ex) { toast(ex.message, "error"); }
    finally { step2.classList.remove("is-loading"); }
  }

  function renderStep2(r) {
    setStep(2);
    step2.hidden = false;
    const s = r.summary;
    const counts = { todas: r.items.length };
    for (const it of r.items) counts[it.status] = (counts[it.status] || 0) + 1;
    const extra = [["Ya importados antes", s.duplicates], ["Repetidos en el Excel", s.repeated], ["Con error", s.errors]]
      .filter(([, n]) => n);

    step2.innerHTML = `
      <h2><span class="step-n" aria-hidden="true">2</span> Revisa qué se va a importar</h2>
      <p class="file-line">📄 <strong>${esc(r.filename)}</strong> · ${pill(r.platform)} · ${s.rows} pedidos</p>
      <div class="format-banner ok">
        <p><strong>✔ ${esc(r.profile.label)}</strong></p>
        <ul>${r.profile.explain.map((x) => `<li>${x}</li>`).join("")}</ul>
      </div>
      ${r.notices.map((n) => `<p class="flash flash-warning">${esc(n)}</p>`).join("")}
      ${r.profile.needs_date ? `<div class="field block"><label for="ref-date">Día de los pedidos</label>
        <input id="ref-date" type="date" value="${esc(r.ref_date || "")}" style="max-width:220px"></div>` : ""}

      <div class="funnel" role="list">
        <div class="funnel-step" role="listitem"><span class="kpi-value">${s.rows}</span><span class="kpi-label">Pedidos en el Excel</span></div>
        <span class="funnel-arrow" aria-hidden="true">→</span>
        <div class="funnel-step ok" role="listitem"><span class="kpi-value">${s.new}</span><span class="kpi-label">Nuevos a importar</span></div>
        <div class="funnel-step money" role="listitem"><span class="kpi-value">${money(s.claimed_total)}</span><span class="kpi-label">A reclamar</span></div>
      </div>
      ${extra.length ? `<p class="muted funnel-extra">${extra.map(([l, n]) => `${l}: <strong>${n}</strong>`).join(" · ")}</p>` : ""}

      <div class="block">
        <h3>Restaurantes del Excel</h3>
        <div class="table-wrap"><table class="table compact">
          <thead><tr><th scope="col">En el Excel</th><th scope="col" class="num">Pedidos</th><th scope="col">Restaurante</th></tr></thead>
          <tbody>${r.stores.map((st) => `<tr><td>${esc(st.name)}</td><td class="num">${st.rows}</td>
            <td>${esc(st.restaurant_name)}${st.is_new ? ` <span class="tag-ok">nuevo, se crea al importar</span>` : ""}</td></tr>`).join("")}</tbody>
        </table></div>
      </div>

      <div class="block">
        <h3>Pedidos</h3>
        <div class="chips" role="group" aria-label="Filtrar">${FILTERS.filter(([k]) => counts[k]).map(([k, l]) => `
          <button type="button" class="chip" data-filter="${k}" aria-pressed="${state.filter === k}">${l} <span class="count">${counts[k]}</span></button>`).join("")}</div>
        <div class="table-wrap"><table class="table compact preview">
          <thead><tr><th scope="col">Fila</th><th scope="col">Estado</th><th scope="col">Pedido</th><th scope="col">Fecha</th>
            <th scope="col">Motivo</th><th scope="col">Restaurante</th><th scope="col" class="num">A reclamar</th><th scope="col">Avisos</th></tr></thead>
          <tbody>${r.items.map((it) => `
            <tr data-status="${it.status}" ${state.filter !== "todas" && state.filter !== it.status ? "hidden" : ""}>
              <td>${it.row}</td>
              <td><span class="status ${STATUS[it.status].cls}">${STATUS[it.status].label}</span></td>
              <td>${esc(it.order_ref || "—")}</td>
              <td class="nowrap">${it.order_date ? `${it.approx_date ? `<span class="approx" title="Día estimado">≈</span> ` : ""}${dt(it.order_date)}` : "—"}</td>
              <td>${esc(it.headline)}</td>
              <td>${esc(it.restaurant_name || "—")}</td>
              <td class="num"><strong>${it.claimed_amount !== undefined ? money(it.claimed_amount) : "—"}</strong></td>
              <td class="notes">${[it.error, ...it.warnings].filter(Boolean).map((n) => `<small>${esc(n)}</small>`).join("<br>")}</td>
            </tr>`).join("")}</tbody>
        </table></div>
        ${r.items_truncated ? `<p class="muted">Se muestran las primeras ${r.items.length} filas.</p>` : ""}
      </div>

      <div class="import-actions">
        <p class="muted">${s.new ? "" : "No hay pedidos nuevos que importar."}</p>
        <div class="form-actions">
          <button type="button" class="btn btn-ghost" id="cancel-import">Cancelar</button>
          <button type="button" class="btn btn-primary" id="do-import" ${s.new ? "" : "disabled"}>
            Importar ${s.new} incidencia${s.new === 1 ? "" : "s"}${s.claimed_total ? ` · ${money(s.claimed_total)} a reclamar` : ""}</button>
        </div>
      </div>`;

    step2.querySelector("#ref-date")?.addEventListener("change", (e) => { state.ref_date = e.target.value; recalc(); });
    step2.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
      state.filter = chip.dataset.filter;
      step2.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      step2.querySelectorAll("tr[data-status]").forEach((tr) => {
        tr.hidden = state.filter !== "todas" && tr.dataset.status !== state.filter;
      });
    }));
    step2.querySelector("#cancel-import").addEventListener("click", () => { step2.hidden = true; step1.reset(); setStep(1); });
    step2.querySelector("#do-import").addEventListener("click", confirmImport);
  }

  // ---------- Paso 3: importar ----------
  async function confirmImport() {
    const btn = step2.querySelector("#do-import");
    btn.disabled = true;
    btn.textContent = "Importando…";
    try {
      const r = await api("/import/confirm", {
        method: "POST", json: { file_id: state.file_id, filename: state.filename, ref_date: state.ref_date },
      });
      if (r.created_restaurants.length) await loadMeta();
      step2.hidden = true;
      step1.reset();
      setStep(3);
      step3.hidden = false;
      step3.innerHTML = `
        <h2><span class="step-n" aria-hidden="true">3</span> Importación completada</h2>
        <div class="funnel">
          <div class="funnel-step ok"><span class="kpi-value">${r.created}</span><span class="kpi-label">Incidencias creadas</span></div>
          <div class="funnel-step money"><span class="kpi-value">${money(r.claimed_total)}</span><span class="kpi-label">A reclamar</span></div>
        </div>
        ${r.created_restaurants.length ? `<p class="flash flash-success">Restaurantes creados:
          ${r.created_restaurants.map((x) => `<strong>${esc(x.name)}</strong>`).join(", ")}</p>` : ""}
        <div class="form-actions">
          <a class="btn btn-primary" href="#/incidencias?batch=${r.batch_id}">Ver las incidencias importadas</a>
          <a class="btn" href="#/">Ir al panel</a>
        </div>`;
      toast(`${r.created} incidencias importadas.`);
      loadBatches(el.querySelector("#batches"));
    } catch (ex) {
      toast(ex.message, "error");
      btn.disabled = false;
      btn.textContent = "Importar";
    }
  }
}

async function loadBatches(box) {
  try {
    const batches = await api("/import/batches");
    box.innerHTML = batches.length ? `
      <table class="table compact">
        <thead><tr><th scope="col">Fecha</th><th scope="col">Archivo</th><th scope="col" class="num">Creadas</th></tr></thead>
        <tbody>${batches.map((b) => `<tr>
          <td class="nowrap">${dt(b.created_at)}<br>${pill(b.platform)}</td>
          <td><a href="#/incidencias?batch=${b.id}">${esc(b.filename)}</a><br><small class="muted">${b.total_rows} filas</small></td>
          <td class="num">${b.created_count}</td></tr>`).join("")}</tbody>
      </table>` : `<p class="muted">Todavía no se ha importado ningún archivo.</p>`;
  } catch (ex) {
    box.innerHTML = `<p class="muted">${esc(ex.message)}</p>`;
  }
}
