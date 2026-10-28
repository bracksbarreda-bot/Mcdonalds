import { api } from "../api.js";
import { esc, isAdmin, meta, nowLocal, options, restaurantOptions, toast } from "../ui.js";

export async function render(el, params) {
  const m = meta.data;
  const editing = Boolean(params.id);
  let inc = { order_date: nowLocal(), priority: "media" };
  if (editing) {
    inc = { ...(await api(`/incidents/${params.id}`)).incident };
    inc.order_date = inc.order_date.replace(" ", "T").slice(0, 16);
    inc.order_amount = inc.order_amount.toFixed(2);
    inc.claimed_amount = inc.claimed_amount.toFixed(2);
  }
  const v = (k) => esc(inc[k] ?? "");
  const title = editing ? `Editar incidencia #${inc.id}` : "Nueva incidencia";
  const cancel = editing ? `#/incidencias/${inc.id}` : "#/incidencias";

  el.innerHTML = `
    <div class="page-head"><h1>${title}</h1><a class="btn btn-ghost" href="${cancel}">Cancelar</a></div>
    <form class="card form" id="inc-form" novalidate>
      <div id="form-errors" class="flash flash-error" role="alert" hidden></div>
      <fieldset>
        <legend>Plataforma *</legend>
        <div class="platform-picker">
          ${Object.entries(m.platforms).map(([k, p]) => `
            <label class="platform-option" style="--c:${esc(p.color)}">
              <input type="radio" name="platform" value="${esc(k)}" ${inc.platform === k ? "checked" : ""} required>
              <span>${esc(p.name)}</span>
            </label>`).join("")}
        </div>
      </fieldset>
      <div class="form-grid">
        ${isAdmin() ? `<div class="field"><label for="i-rest">Restaurante *</label>
          <select id="i-rest" name="restaurant_id" required>${restaurantOptions(inc.restaurant_id, "Selecciona…")}</select></div>` : ""}
        <div class="field"><label for="i-ref">Nº de pedido *</label>
          <input id="i-ref" name="order_ref" maxlength="64" required value="${v("order_ref")}" placeholder="Ej. GLV-12345678"></div>
        <div class="field"><label for="i-date">Fecha y hora del pedido *</label>
          <input id="i-date" name="order_date" type="datetime-local" required value="${v("order_date")}"></div>
        <div class="field"><label for="i-type">Tipo de incidencia *</label>
          <select id="i-type" name="incident_type" required>${options(m.incident_types, inc.incident_type, { empty: "Selecciona…" })}</select></div>
        <div class="field"><label for="i-prio">Prioridad</label>
          <select id="i-prio" name="priority">${options(m.priorities, inc.priority)}</select></div>
        <div class="field"><label for="i-amount">Importe del pedido (€)</label>
          <input id="i-amount" name="order_amount" inputmode="decimal" value="${v("order_amount")}" placeholder="0,00"></div>
        <div class="field"><label for="i-claim">Importe a reclamar (€)</label>
          <input id="i-claim" name="claimed_amount" inputmode="decimal" value="${v("claimed_amount")}" placeholder="0,00" aria-describedby="claim-help">
          <small id="claim-help" class="muted">Lo que la plataforma descontó o debería abonar.</small></div>
        <div class="field"><label for="i-ticket">Ticket de reclamación en la plataforma</label>
          <input id="i-ticket" name="platform_claim_ref" maxlength="64" value="${v("platform_claim_ref")}" placeholder="Opcional"></div>
        <div class="field span-2"><label for="i-items">Productos afectados</label>
          <input id="i-items" name="items_affected" maxlength="500" value="${v("items_affected")}" placeholder="Ej. Big Mac, Patatas medianas"></div>
        <div class="field span-2"><label for="i-desc">Descripción</label>
          <textarea id="i-desc" name="description" rows="4" maxlength="2000" placeholder="Qué pasó, qué dice el cliente, qué dice el rider…">${v("description")}</textarea></div>
      </div>
      <div class="form-actions">
        <button type="submit" class="btn btn-primary">${editing ? "Guardar cambios" : "Registrar incidencia"}</button>
      </div>
    </form>`;

  const form = el.querySelector("#inc-form");
  const errBox = el.querySelector("#form-errors");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    const data = Object.fromEntries(new FormData(form));
    try {
      if (editing) {
        await api(`/incidents/${inc.id}`, { method: "PUT", json: data });
        toast("Cambios guardados.");
        location.hash = `#/incidencias/${inc.id}`;
      } else {
        const r = await api("/incidents", { method: "POST", json: data });
        toast(`Incidencia #${r.id} registrada.`);
        if (r.duplicate_of) toast(`Ojo: ya existía otra incidencia para este pedido (#${r.duplicate_of}).`, "warning", 9000);
        location.hash = `#/incidencias/${r.id}`;
      }
    } catch (err) {
      const list = err.data?.errors || [err.message];
      errBox.innerHTML = list.length > 1 ? `<ul>${list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : esc(list[0]);
      errBox.hidden = false;
      errBox.scrollIntoView({ behavior: "smooth", block: "center" });
      btn.disabled = false;
    }
  });
}
