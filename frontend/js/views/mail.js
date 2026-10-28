import { api } from "../api.js";
import { esc, isAdmin, pill, toast } from "../ui.js";

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
        <div class="form-actions"><button type="submit" class="btn btn-primary">Guardar</button></div>
      </form>

      <div class="stack">
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
}
