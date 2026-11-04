import { api } from "../api.js";
import { duration } from "../claims.js";
import { dt, esc, isAdmin, pill, toast } from "../ui.js";

// Configuración del correo de reclamaciones (solo administrador)
export async function render(el) {
  if (!isAdmin()) {
    el.innerHTML = `<div class="card empty"><h1>Solo el administrador</h1><a class="btn" href="#/">Volver</a></div>`;
    return;
  }
  const s = await api("/mail/settings");
  const v = (k) => esc(s[k] ?? "");

  el.innerHTML = `
    <div class="page-head"><h1>Correo de reclamaciones</h1>
      <a class="btn btn-ghost" href="#/incidencias?platform=ubereats">Ir a las incidencias</a></div>

    <div class="grid grid-detail">
      <form id="mail-form" class="card form" novalidate autocomplete="off">
        <h2>Cuenta desde la que se envían</h2>
        <p id="mail-error" class="flash flash-error" role="alert" hidden></p>
        <div class="field"><label for="m-preset">Proveedor</label>
          <select id="m-preset">
            <option value="">Otro (rellenar a mano)</option>
            <option value="gmail">Gmail</option>
            <option value="outlook">Outlook / Microsoft 365</option>
          </select></div>
        <div class="form-grid">
          <div class="field"><label for="m-host">Servidor SMTP</label><input id="m-host" name="host" value="${v("host")}" placeholder="smtp.gmail.com"></div>
          <div class="field"><label for="m-port">Puerto</label><input id="m-port" name="port" inputmode="numeric" value="${v("port")}"></div>
          <div class="field"><label for="m-sec">Seguridad</label>
            <select id="m-sec" name="security">
              ${[["starttls", "STARTTLS (puerto 587)"], ["ssl", "SSL (puerto 465)"], ["none", "Ninguna"]].map(([k, l]) =>
                `<option value="${k}" ${s.security === k ? "selected" : ""}>${l}</option>`).join("")}
            </select></div>
          <div class="field"><label for="m-user">Usuario</label><input id="m-user" name="username" value="${v("username")}" autocomplete="off"></div>
          <div class="field"><label for="m-pass">Contraseña ${s.password_set ? "(guardada; déjala vacía para no cambiarla)" : ""}</label>
            <input id="m-pass" name="password" type="password" autocomplete="new-password">
            <small class="muted">En Gmail usa una <strong>contraseña de aplicación</strong> (Cuenta de Google → Seguridad →
              Verificación en dos pasos → Contraseñas de aplicaciones).</small></div>
          <div class="field"><label for="m-from">Remitente (correo)</label><input id="m-from" name="sender" value="${v("sender")}" placeholder="reclamaciones@…"></div>
          <div class="field"><label for="m-name">Nombre del remitente</label><input id="m-name" name="sender_name" value="${v("sender_name")}"></div>
          <div class="field span-2"><label for="m-sig">Firma</label><textarea id="m-sig" name="signature" rows="3">${v("signature")}</textarea></div>
        </div>

        <h2 class="block">Destinatarios</h2>
        <label class="cat"><input type="checkbox" name="test_mode" ${s.test_mode ? "checked" : ""}>
          <span><strong>Modo prueba</strong><br><small class="muted">Todos los correos van al correo de prueba con [PRUEBA]
            en el asunto. No llega nada a las plataformas y las incidencias no cambian de estado.</small></span></label>
        <div class="form-grid">
          <div class="field"><label for="m-test">Correo de prueba</label><input id="m-test" name="test_recipient" value="${v("test_recipient")}" placeholder="tu@correo.com"></div>
          ${s.platforms.map((p) => `<div class="field"><label for="m-r-${p}">Correo de reclamaciones de ${pill(p)}</label>
            <input id="m-r-${p}" data-recipient="${p}" value="${esc(s.recipients[p] || "")}" placeholder="Solo para el modo real"></div>`).join("")}
        </div>

        <h2 class="block">Envío gradual (para no caer en spam)</h2>
        <p class="muted">Los correos salen de uno en uno con una pausa variable. Gmail personal admite unos 500 al día;
          con Google Workspace, unos 2.000.</p>
        <div class="form-grid">
          <div class="field"><label for="m-int">Segundos entre correos</label>
            <input id="m-int" name="interval_seconds" inputmode="numeric" value="${v("interval_seconds")}"></div>
          <div class="field"><label for="m-hour">Máximo por hora</label>
            <input id="m-hour" name="hourly_limit" inputmode="numeric" value="${v("hourly_limit")}"></div>
          <div class="field"><label for="m-day">Máximo por día</label>
            <input id="m-day" name="daily_limit" inputmode="numeric" value="${v("daily_limit")}"></div>
        </div>
        <div class="form-actions"><button type="submit" class="btn btn-primary">Guardar</button></div>
      </form>

      <div class="stack">
        <article class="card" id="queue-card"><h2>Cola de envío</h2><p class="muted">Cargando…</p></article>
        <article class="card">
          <h2>Probar la configuración</h2>
          <p class="muted">Envía un correo corto al correo de prueba para comprobar que la cuenta funciona.</p>
          <p>${s.missing.length ? `<span class="warn-text">Falta: ${s.missing.map(esc).join(", ")}</span>`
            : `<span class="tag-ok">Configuración completa</span>`}</p>
          <button type="button" class="btn" id="send-test" ${s.missing.length ? "disabled" : ""}>Enviar correo de prueba</button>
        </article>
        <article class="card help">
          <h2>Cómo enviar reclamaciones</h2>
          <ol class="help-list">
            <li>Guarda la configuración y envía el correo de prueba.</li>
            <li>Ve a <a href="#/incidencias">Incidencias</a>, filtra por Uber Eats o Just Eat y marca los pedidos
              (o «Todas las del filtro»).</li>
            <li>Pulsa <strong>Enviar reclamación por correo</strong>: sale un correo por pedido.</li>
          </ol>
          <p class="muted">Glovo no está incluido en esta fase. La contraseña se guarda en el servidor
            (<code>instance/correo.json</code>) y nunca se envía al navegador.</p>
          <p class="muted"><strong>Para el envío real a las plataformas</strong> lo más seguro es una cuenta con dominio
            propio (Google Workspace, Microsoft 365, Brevo, Amazon SES…) con SPF, DKIM y DMARC configurados:
            una cuenta @gmail.com personal enviando muchas reclamaciones parecidas tiene más riesgo de spam.</p>
        </article>
      </div>
    </div>`;

  const form = el.querySelector("#mail-form");
  el.querySelector("#m-preset").addEventListener("change", (e) => {
    const p = s.presets[e.target.value];
    if (!p) return;
    form.host.value = p.host;
    form.port.value = p.port;
    form.security.value = p.security;
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const box = el.querySelector("#mail-error");
    box.hidden = true;
    const data = Object.fromEntries(new FormData(form));
    data.test_mode = form.test_mode.checked;
    data.recipients = Object.fromEntries([...el.querySelectorAll("[data-recipient]")].map((i) => [i.dataset.recipient, i.value]));
    try {
      await api("/mail/settings", { method: "PUT", json: data });
      toast("Configuración guardada.");
      render(el);
    } catch (err) {
      box.textContent = err.message;
      box.hidden = false;
    }
  });

  el.querySelector("#send-test").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Enviando…";
    try {
      toast((await api("/mail/test", { method: "POST", json: {} })).message, "success", 8000);
    } catch (err) {
      toast(err.message, "error", 10000);
    } finally {
      e.target.disabled = false;
      e.target.textContent = "Enviar correo de prueba";
    }
  });

  // Cola de envío: se refresca cada 5 s mientras esta pantalla esté abierta
  const card = el.querySelector("#queue-card");
  const ST = { pendiente: ["En cola", "status-reclamada"], enviado: ["Enviado", "status-resuelta"],
    error: ["Error", "status-abierta"], cancelado: ["Cancelado", "status-rechazada"] };
  const paint = (q) => {
    card.innerHTML = `
      <h2>Cola de envío</h2>
      <div class="amounts">
        <div><span class="kpi-label">En cola</span><span class="kpi-value">${q.pending}</span></div>
        <div><span class="kpi-label">Enviados hoy</span><span class="kpi-value">${q.sent_today}<small class="muted"> / ${q.daily_limit}</small></span></div>
        <div><span class="kpi-label">Última hora</span><span class="kpi-value">${q.sent_last_hour}<small class="muted"> / ${q.hourly_limit}</small></span></div>
      </div>
      ${q.pending ? `<p class="muted">Quedan ${duration(q.eta_minutes)} aprox.${q.next_at ? ` Próximo envío: ${dt(q.next_at)}.` : ""}</p>` : ""}
      ${q.paused_until ? `<p class="flash flash-warning">En pausa hasta ${dt(q.paused_until)}.</p>` : ""}
      ${q.last_error ? `<p class="flash flash-error">${esc(q.last_error)}</p>` : ""}
      ${q.pending ? `<div class="form-actions">
        ${q.paused_until || q.last_error ? `<button type="button" class="btn" id="q-resume">Reintentar ahora</button>` : ""}
        <button type="button" class="btn btn-danger" id="q-cancel">Cancelar los ${q.pending} pendientes</button></div>` : ""}
      ${q.recent.length ? `<ul class="mail-history">${q.recent.map((r) => `
        <li><span class="status ${ST[r.status][1]}">${ST[r.status][0]}</span> ${pill(r.platform)}
          <a href="#/incidencias/${r.incident_id}">${esc(r.order_ref)}</a> · ${dt(r.sent_at || r.queued_at)}
          ${r.mode === "prueba" ? `<span class="tag-generic">PRUEBA</span>` : ""}
          ${r.error && r.status !== "cancelado" ? `<br><small class="warn-text">${esc(r.error)}</small>` : ""}</li>`).join("")}</ul>`
        : `<p class="muted">No hay correos en la lista.</p>`}
      ${q.recent.length || q.pending ? `<div class="form-actions">
        <button type="button" class="btn btn-ghost" id="q-clear">🗑 Vaciar la cola de envío</button></div>` : ""}`;
    card.querySelector("#q-clear")?.addEventListener("click", async () => {
      const extra = q.pending ? `\n\nLos ${q.pending} correos que aún no han salido se cancelarán y NO se enviarán.` : "";
      if (!confirm(`¿Eliminar toda la información de la cola de envío?${extra}\n\n` +
        "Los contadores «Enviados hoy» y «Última hora» se mantienen para respetar los límites de Gmail.")) return;
      const r = await api("/claims/queue/clear", { method: "POST", json: {} });
      paint(r.queue);
      toast(`Cola vaciada${r.cancelled ? ` (${r.cancelled} envíos pendientes cancelados)` : ""}.`);
    });
    card.querySelector("#q-cancel")?.addEventListener("click", async () => {
      if (!confirm(`¿Cancelar los ${q.pending} correos que aún no se han enviado?`)) return;
      paint((await api("/claims/queue/cancel", { method: "POST", json: {} })).queue);
      toast("Envíos pendientes cancelados.");
    });
    card.querySelector("#q-resume")?.addEventListener("click", async () => {
      paint(await api("/claims/queue/resume", { method: "POST", json: {} }));
    });
  };
  const tick = async () => {
    if (!document.body.contains(card)) return; // el usuario cambió de pantalla
    try { paint(await api("/claims/queue")); } catch { /* reintenta en el siguiente ciclo */ }
    setTimeout(tick, 5000);
  };
  tick();
}
