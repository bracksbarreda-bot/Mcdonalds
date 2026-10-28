// Router muy simple basado en el hash de la URL (#/ruta?param=valor)
import { auth } from "./api.js";
import { esc, loadMeta, meta, toast } from "./ui.js";
import * as login from "./views/login.js";
import * as dashboard from "./views/dashboard.js";
import * as incidents from "./views/incidents.js";
import * as detail from "./views/detail.js";
import * as form from "./views/form.js";
import * as importView from "./views/import.js";
import * as account from "./views/account.js";
import * as mail from "./views/mail.js";

const routes = [
  { re: /^\/cuenta$/, view: account, noMeta: true },
  { re: /^\/login$/, view: login, public: true },
  { re: /^\/$/, view: dashboard, nav: "dashboard" },
  { re: /^\/incidencias$/, view: incidents, nav: "incidencias" },
  { re: /^\/incidencias\/nueva$/, view: form, nav: "incidencias" },
  { re: /^\/incidencias\/(?<id>\d+)\/editar$/, view: form, nav: "incidencias" },
  { re: /^\/incidencias\/(?<id>\d+)$/, view: detail, nav: "incidencias" },
  { re: /^\/importar$/, view: importView, nav: "importar" },
  { re: /^\/correo$/, view: mail, nav: "correo" },
];

const viewEl = document.getElementById("view");

function updateChrome(route) {
  const user = auth.user();
  const top = document.getElementById("topbar");
  top.hidden = !user || route.public;
  if (user) {
    document.getElementById("user-label").innerHTML =
      `<a href="#/cuenta" class="user-link" title="Cambiar contraseña">${esc(user.full_name)} · ` +
      `<small>${esc(user.role === "admin" ? "Todos los locales" : user.restaurant_name)}</small></a>`;
  }
  for (const k of ["importar", "correo"]) document.querySelector(`[data-nav="${k}"]`).hidden = user?.role !== "admin";
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === route.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

async function router() {
  const [path = "/", qs = ""] = (location.hash.slice(1) || "/").split("?");
  const query = new URLSearchParams(qs);
  const route = routes.find((r) => r.re.test(path));
  if (!route) {
    location.hash = "#/";
    return;
  }
  if (!route.public && !auth.token) {
    location.hash = "#/login";
    return;
  }
  if (route.public && auth.token) {
    location.hash = "#/";
    return;
  }
  if (!route.public && auth.user()?.must_change_password && route.view !== account) {
    location.hash = "#/cuenta"; // hasta cambiar la contraseña de fábrica no se puede usar nada más
    return;
  }

  updateChrome(route);
  viewEl.innerHTML = `<p class="muted">Cargando…</p>`;
  try {
    if (!route.public && !route.noMeta && !meta.data) await loadMeta();
    const params = path.match(route.re).groups || {};
    await route.view.render(viewEl, params, query);
    document.title = `${viewEl.querySelector("h1")?.textContent.trim() || "Delivery Hub"} · Delivery Hub`;
  } catch (err) {
    if (err.status === 401) return; // api.js ya redirige al login
    viewEl.innerHTML = `<div class="card empty"><h1>Algo ha fallado</h1><p class="muted">${esc(err.message)}</p>
      <a class="btn" href="#/">Volver al panel</a></div>`;
  }
}

document.getElementById("logout").addEventListener("click", () => {
  auth.clear();
  meta.data = null;
  toast("Sesión cerrada.");
  location.hash = "#/login";
});

window.addEventListener("hashchange", router);
router();
