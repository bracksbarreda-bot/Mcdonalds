import { api, auth } from "../api.js";
import { esc, loadMeta, toast } from "../ui.js";

export async function render(el) {
  const user = auth.user();
  el.innerHTML = `
    <div class="login-wrap">
      <form class="card login-card" id="pw-form" novalidate autocomplete="off">
        <h1>Cambiar contraseña</h1>
        ${user?.must_change_password ? `<p class="flash flash-warning">Estás usando la contraseña de fábrica.
          Cámbiala para poder usar la aplicación.</p>` : ""}
        <p class="muted">Mínimo 10 caracteres, con letras y números. Al cambiarla se cierran las sesiones
          abiertas en otros dispositivos.</p>
        <p id="pw-error" class="flash flash-error" role="alert" hidden></p>
        <label for="current_password">Contraseña actual</label>
        <input id="current_password" name="current_password" type="password" autocomplete="current-password" required>
        <label for="new_password">Contraseña nueva</label>
        <input id="new_password" name="new_password" type="password" autocomplete="new-password" minlength="10" required>
        <label for="confirm_password">Repite la contraseña nueva</label>
        <input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="10" required>
        <button type="submit" class="btn btn-primary btn-block">Guardar contraseña</button>
      </form>
    </div>`;

  const form = el.querySelector("#pw-form");
  const errBox = el.querySelector("#pw-error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const btn = form.querySelector("button");
    btn.disabled = true;
    try {
      const r = await api("/auth/password", { method: "POST", json: Object.fromEntries(new FormData(form)) });
      auth.set(r.token, r.user);
      await loadMeta();
      toast("Contraseña cambiada. Las sesiones de otros dispositivos se han cerrado.");
      location.hash = "#/";
    } catch (err) {
      errBox.innerHTML = esc(err.message);
      errBox.hidden = false;
      btn.disabled = false;
    }
  });
}
