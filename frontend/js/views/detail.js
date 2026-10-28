import { api } from "../api.js";
import { sendClaims } from "../claims.js";
import { dt, esc, isAdmin, meta, money, options, pill, prioBadge, statusBadge, toast } from "../ui.js";

export async function render(el, params) {
  const { incident: inc, events, claim_text, claim_emails, claimable } = await api(`/incidents/${params.id}`);
  const m = meta.data;
  const pname = m.platforms[inc.platform]?.name || inc.platform;
  const recoveredDefault = (inc.status === "resuelta" ? inc.recovered_amount : inc.claimed_amount).toFixed(2);

  el.innerHTML = `
    <div class="page-head">
      <div>
        <a href="#/incidencias" class="muted">← Incidencias</a>
        <h1>Incidencia #${inc.id} ${pill(inc.platform)} ${statusBadge(inc.status)}</h1>
      </div>
      <div class="head-actions">
        <a class="btn" href="#/incidencias/${inc.id}/editar">Editar</a>
        ${isAdmin() ? `<button type="button" class="btn btn-danger" id="delete">Eliminar</button>` : ""}
      </div>
    </div>

    <div class="grid grid-detail">
      <div class="stack">
        <article class="card">
          <h2>Datos del pedido</h2>
          <dl class="details">
            <div><dt>Nº pedido</dt><dd><strong>${esc(inc.order_ref)}</strong></dd></div>
            <div><dt>Fecha del pedido</dt><dd>${dt(inc.order_date)} <small class="muted">(hace ${inc.days} días)</small></dd></div>
            <div><dt>Restaurante</dt><dd>${esc(inc.restaurant_name)} (${esc(inc.restaurant_code)})</dd></div>
            <div><dt>Tipo</dt><dd>${esc(m.incident_types[inc.incident_type] || inc.incident_type)}</dd></div>
            <div><dt>Prioridad</dt><dd>${prioBadge(inc.priority)}</dd></div>
            <div><dt>Ticket plataforma</dt><dd>${esc(inc.platform_claim_ref || "—")}</dd></div>
            <div class="span-2"><dt>Productos afectados</dt><dd>${esc(inc.items_affected || "—")}</dd></div>
            <div class="span-2"><dt>Descripción</dt><dd class="prewrap">${esc(inc.description || "—")}</dd></div>
            <div><dt>Registrada por</dt><dd>${esc(inc.created_by_name || "—")} · ${dt(inc.created_at)}</dd></div>
            <div><dt>Última actualización</dt><dd>${dt(inc.updated_at)}</dd></div>
            ${inc.import_batch_id ? `<div><dt>Origen</dt><dd><a href="#/incidencias?batch=${inc.import_batch_id}">Importación #${inc.import_batch_id}</a></dd></div>` : ""}
          </dl>
        </article>

        <article class="card">
          <h2>Importes</h2>
          <div class="amounts">
            <div><span class="kpi-label">Pedido</span><span class="kpi-value">${money(inc.order_amount)}</span></div>
            <div><span class="kpi-label">Reclamado</span><span class="kpi-value">${money(inc.claimed_amount)}</span></div>
            <div class="ok"><span class="kpi-label">Recuperado</span><span class="kpi-value">${money(inc.recovered_amount)}</span></div>
          </div>
        </article>

        ${inc.source_data?.length ? `
        <article class="card">
          <details ${inc.import_batch_id ? "open" : ""}>
            <summary><h2 class="inline-h2">Datos originales del archivo de ${esc(pname)}</h2></summary>
            <dl class="source-list">
              ${inc.source_data.map(([k, val]) => `<dt>${esc(k)}</dt><dd class="prewrap">${esc(val)}</dd>`).join("")}
            </dl>
          </details>
        </article>` : ""}

        <article class="card">
          <h2>Texto para reclamar a ${esc(pname)}</h2>
          <label for="claim_text" class="muted">Cópialo y pégalo en el portal de soporte de la plataforma.</label>
          <textarea id="claim_text" rows="9" readonly>${esc(claim_text)}</textarea>
          <button type="button" class="btn" id="copy-claim">Copiar texto</button>
          <span id="copy-status" class="muted" role="status"></span>
        </article>
      </div>

      <div class="stack">
        ${isAdmin() && claimable ? `
        <article class="card">
          <h2>Reclamación por correo</h2>
          <button type="button" class="btn btn-primary" id="send-mail">Enviar reclamación por correo</button>
          <a class="btn btn-ghost" href="#/correo">Configurar</a>
          ${claim_emails.length ? `<ul class="mail-history">${claim_emails.map((c) => `
            <li><span class="status ${c.status === "enviado" ? "status-resuelta" : "status-abierta"}">${c.status === "enviado" ? "Enviado" : "Error"}</span>
              ${c.mode === "prueba" ? `<span class="tag-generic">PRUEBA</span>` : ""}
              ${dt(c.sent_at)} · ${esc(c.recipient)}${c.error ? `<br><small class="warn-text">${esc(c.error)}</small>` : ""}</li>`).join("")}</ul>`
            : `<p class="muted">Todavía no se ha enviado ningún correo de este pedido.</p>`}
        </article>` : ""}

        <article class="card">
          <h2>Cambiar estado</h2>
          <form class="form" id="status-form">
            <div class="field"><label for="st-status">Nuevo estado</label>
              <select id="st-status" name="status">${options(m.statuses, inc.status)}</select></div>
            <div class="field" data-show-for="reclamada"><label for="st-ref">Ticket de la plataforma</label>
              <input id="st-ref" name="platform_claim_ref" maxlength="64" value="${esc(inc.platform_claim_ref || "")}"></div>
            <div class="field" data-show-for="resuelta"><label for="st-rec">Importe recuperado (€)</label>
              <input id="st-rec" name="recovered_amount" inputmode="decimal" value="${recoveredDefault}"></div>
            <div class="field"><label for="st-comment">Comentario (opcional)</label>
              <textarea id="st-comment" name="comment" rows="2" maxlength="1000"></textarea></div>
            <button type="submit" class="btn btn-primary">Actualizar estado</button>
          </form>
        </article>

        <article class="card" id="historial">
          <h2>Historial</h2>
          <form class="form comment-form" id="comment-form">
            <label for="comment">Añadir comentario</label>
            <textarea id="comment" name="comment" rows="2" maxlength="1000" required></textarea>
            <button type="submit" class="btn">Comentar</button>
          </form>
          <ol class="timeline">${events.map((e) => `
            <li class="ev ev-${esc(e.event_type)}">
              <div class="ev-meta">${dt(e.created_at)} · ${esc(e.full_name || "Sistema")}</div>
              <div class="prewrap">${esc(e.message)}</div>
            </li>`).join("")}</ol>
        </article>
      </div>
    </div>`;

  const reload = () => render(el, params);

  // Mostrar solo los campos relevantes según el estado
  const sel = el.querySelector("#st-status");
  const toggle = () => el.querySelectorAll("[data-show-for]").forEach((f) => { f.hidden = f.dataset.showFor !== sel.value; });
  sel.addEventListener("change", toggle);
  toggle();

  el.querySelector("#status-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api(`/incidents/${inc.id}/status`, { method: "POST", json: Object.fromEntries(new FormData(e.target)) });
      toast(r.message);
      await reload();
    } catch (err) {
      toast(err.message, err.status === 409 ? "warning" : "error");
    }
  });

  el.querySelector("#comment-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/incidents/${inc.id}/comments`, { method: "POST", json: { comment: e.target.comment.value } });
      toast("Comentario añadido.");
      await reload();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  el.querySelector("#copy-claim").addEventListener("click", async () => {
    const status = el.querySelector("#copy-status");
    try {
      await navigator.clipboard.writeText(claim_text);
      status.textContent = "Copiado.";
    } catch {
      el.querySelector("#claim_text").select();
      status.textContent = "Selecciona y copia con Ctrl+C.";
    }
  });

  el.querySelector("#send-mail")?.addEventListener("click", () =>
    sendClaims({ incident_ids: [inc.id] }, { onDone: reload }));

  el.querySelector("#delete")?.addEventListener("click", async () => {
    if (!confirm(`¿Eliminar la incidencia #${inc.id}? No se puede deshacer.`)) return;
    try {
      await api(`/incidents/${inc.id}`, { method: "DELETE" });
      toast(`Incidencia #${inc.id} eliminada.`);
      location.hash = "#/incidencias";
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
