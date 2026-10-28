import { api, auth } from "../api.js";
import { esc, loadMeta } from "../ui.js";

export async function render(el) {
  el.innerHTML = `
    <div class="login-wrap">
      <form class="card login-card" id="login-form" novalidate>
        <div class="brand brand-lg"><span class="logo" aria-hidden="true">M</span><span>Delivery Hub</span></div>
        <h1>Iniciar sesión</h1>
        <p class="muted">Incidencias de Glovo, Uber Eats y Just Eat en un solo sitio.</p>
        <p id="login-error" class="flash flash-error" role="alert" hidden></p>
        <label for="username">Usuario</label>
        <input id="username" name="username" autocomplete="username" required autofocus>
        <label for="password">Contraseña</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button type="submit" class="btn btn-primary btn-block">Entrar</button>
      </form>
    </div>`;

  const form = el.querySelector("#login-form");
  const errBox = el.querySelector("#login-error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button");
    btn.disabled = true;
    errBox.hidden = true;
    try {
      const { token, user } = await api("/auth/login", {
        method: "POST",
        json: { username: form.username.value.trim(), password: form.password.value },
      });
      auth.set(token, user);
      if (user.must_change_password) {
        location.hash = "#/cuenta"; // primero hay que cambiar la contraseña de fábrica
        return;
      }
      await loadMeta();
      location.hash = "#/";
    } catch (err) {
      errBox.innerHTML = esc(err.message);
      errBox.hidden = false;
      btn.disabled = false;
    }
  });
}
