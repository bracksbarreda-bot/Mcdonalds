// Envío de reclamaciones por correo: un correo por incidencia, en cola y de forma gradual (anti-spam).
// selection: { incident_ids: [..] } o { filters: {...} } (todas las del filtro actual).
import { api } from "./api.js";
import { esc, toast } from "./ui.js";

export function duration(minutes) {
  if (minutes < 1) return "menos de 1 minuto";
  if (minutes < 60) return `unos ${minutes} min`;
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return `unas ${h} h${m ? ` ${m} min` : ""}`;
}

export async function sendClaims(selection, { onDone } = {}) {
  let preview;
  try {
    preview = await api("/claims/preview", { method: "POST", json: selection });
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const ok = preview.emails.filter((e) => !e.error && !e.in_queue && !(e.already_sent && !preview.test_mode));
  const inQueue = preview.emails.filter((e) => e.in_queue).length;
  if (!ok.length) {
    toast(inQueue ? "Esos pedidos ya están en la cola de envío." :
      preview.emails[0]?.error || "No hay pedidos de Uber Eats o Just Eat para reclamar.", "warning");
    return;
  }
  const q = preview.queue;
  const perHour = Math.min(q.hourly_limit, Math.floor(3600 / (q.interval_seconds * 1.125)));
  const eta = Math.round((ok.length + q.pending) / perHour * 60);
  const dests = [...new Set(ok.map((e) => e.to || "(sin configurar)"))].join(", ");
  const dlg = document.createElement("dialog");
  dlg.className = "card mail-dialog";
  dlg.innerHTML = `
    <h2>${preview.test_mode ? "Enviar correos de PRUEBA" : "Enviar reclamaciones REALES"}</h2>
    <p>Se pondrán en cola <strong>${ok.length}</strong> correos (uno por pedido) para <strong>${esc(dests)}</strong>.</p>
    <p class="muted">Para que Gmail no los marque como spam se envían <strong>de uno en uno</strong>, cada ~${q.interval_seconds} s,
      con un máximo de ${q.hourly_limit} por hora y ${q.daily_limit} al día. Tiempo estimado: <strong>${duration(eta)}</strong>
      ${q.pending ? `(hay ${q.pending} correos antes en la cola)` : ""}. Puedes cerrar esta página: el servidor sigue enviando.</p>
    ${preview.test_mode ? `<p class="flash flash-warning">Modo prueba: nada llega a Uber Eats ni a Just Eat y las
      incidencias no cambian de estado.</p>` : `<p class="flash flash-error">Modo real: los correos van a las plataformas
      y las incidencias abiertas pasan a «Reclamada».</p>`}
    ${inQueue ? `<p class="muted">${inQueue} ya estaban en la cola y no se duplican.</p>` : ""}
    <details><summary>Ver el primer correo</summary>
      <p><strong>Para:</strong> ${esc(ok[0].to)}<br><strong>Asunto:</strong> ${esc(ok[0].subject)}</p>
      <pre class="mail-body">${esc(ok[0].body)}</pre></details>
    <div class="form-actions">
      <button type="button" class="btn btn-ghost" value="cancel">Cancelar</button>
      <button type="button" class="btn btn-primary" value="send">Poner ${ok.length} correo${ok.length === 1 ? "" : "s"} en cola</button>
    </div>`;
  document.body.appendChild(dlg);
  dlg.showModal();
  const choice = await new Promise((resolve) => {
    dlg.addEventListener("click", (e) => { if (e.target.value) resolve(e.target.value); });
    dlg.addEventListener("cancel", () => resolve("cancel"));
  });
  if (choice !== "send") { dlg.remove(); return; }

  const btn = dlg.querySelector('[value="send"]');
  btn.disabled = true;
  btn.textContent = "Comprobando la cuenta…";
  try {
    const r = await api("/claims/send", { method: "POST", json: { incident_ids: ok.map((e) => e.incident_id) } });
    toast(`${r.queued} correo${r.queued === 1 ? "" : "s"} en cola. Se envían poco a poco (${duration(r.queue.eta_minutes)}). ` +
      `Puedes seguir el progreso en «Correo».`, "success", 9000);
    if (r.skipped) toast(`${r.skipped} omitidos: ${r.skipped_details[0]?.error || ""}`, "warning", 8000);
    onDone?.(r);
  } catch (err) {
    toast(err.message, "error", 10000);
  } finally {
    dlg.remove();
  }
}
