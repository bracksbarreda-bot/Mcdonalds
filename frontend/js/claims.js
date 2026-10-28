// Envío de reclamaciones por correo: un correo por incidencia.
// selection: { incident_ids: [..] } o { filters: {...} } (todas las del filtro actual).
import { api } from "./api.js";
import { esc, toast } from "./ui.js";

export async function sendClaims(selection, { onDone } = {}) {
  let preview;
  try {
    preview = await api("/claims/preview", { method: "POST", json: selection });
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const ok = preview.emails.filter((e) => !e.error);
  if (!ok.length) {
    toast(preview.emails[0]?.error || "No hay incidencias de Uber Eats o Just Eat para reclamar.", "warning");
    return;
  }
  const dests = [...new Set(ok.map((e) => e.to || "(sin configurar)"))].join(", ");
  const sentBefore = ok.filter((e) => e.already_sent).length;
  const dlg = document.createElement("dialog");
  dlg.className = "card mail-dialog";
  dlg.innerHTML = `
    <h2>${preview.test_mode ? "Enviar correos de PRUEBA" : "Enviar reclamaciones REALES"}</h2>
    <p>Se enviarán <strong>${ok.length}</strong> correos (uno por pedido) a <strong>${esc(dests)}</strong>.</p>
    ${preview.test_mode ? `<p class="flash flash-warning">Modo prueba: nada llega a Uber Eats ni a Just Eat y las
      incidencias no cambian de estado.</p>` : `<p class="flash flash-error">Modo real: los correos van a las plataformas
      y las incidencias abiertas pasan a «Reclamada».</p>`}
    ${sentBefore && !preview.test_mode ? `<p class="muted">${sentBefore} ya se reclamaron antes y se omitirán.</p>` : ""}
    <details><summary>Ver el primer correo</summary>
      <p><strong>Para:</strong> ${esc(ok[0].to)}<br><strong>Asunto:</strong> ${esc(ok[0].subject)}</p>
      <pre class="mail-body">${esc(ok[0].body)}</pre></details>
    <div class="form-actions">
      <button type="button" class="btn btn-ghost" value="cancel">Cancelar</button>
      <button type="button" class="btn btn-primary" value="send">Enviar ${ok.length} correo${ok.length === 1 ? "" : "s"}</button>
    </div>`;
  document.body.appendChild(dlg);
  dlg.showModal();
  const choice = await new Promise((resolve) => {
    dlg.addEventListener("click", (e) => { if (e.target.value) resolve(e.target.value); });
    dlg.addEventListener("cancel", () => resolve("cancel"));
  });
  if (choice !== "send") { dlg.remove(); return; }

  dlg.querySelector('[value="send"]').disabled = true;
  dlg.querySelector('[value="send"]').textContent = "Enviando…";
  try {
    const r = await api("/claims/send", { method: "POST", json: { incident_ids: ok.map((e) => e.incident_id) } });
    toast(`${r.sent} correo${r.sent === 1 ? "" : "s"} enviado${r.sent === 1 ? "" : "s"}` +
      (r.failed ? ` · ${r.failed} con error` : "") + (r.skipped ? ` · ${r.skipped} omitidos` : "") + ".",
      r.failed ? "warning" : "success", 8000);
    const firstErr = r.results.find((x) => x.status === "error");
    if (firstErr) toast(`Pedido ${firstErr.order_ref}: ${firstErr.error}`, "error", 10000);
    onDone?.(r);
  } catch (err) {
    toast(err.message, "error", 10000);
  } finally {
    dlg.remove();
  }
}
