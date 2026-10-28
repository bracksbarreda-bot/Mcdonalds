import { api } from "../api.js";
import { esc, isAdmin, meta, money, pill, statusBadge } from "../ui.js";

export async function render(el, _params, query) {
  const days = ["7", "30", "90"].includes(query.get("dias")) ? query.get("dias") : "30";
  const d = await api("/dashboard", { query: { dias: days } });
  const m = meta.data;
  const t = d.totals;

  const platformCards = d.by_platform.map((p) => `
    <article class="card platform-card" style="--c:${esc(p.color)}">
      <h2><span class="dot" aria-hidden="true"></span>${esc(p.name)}</h2>
      <dl class="stats">
        <div><dt>Incidencias</dt><dd>${p.total}</dd></div>
        <div><dt>Pendientes</dt><dd>${p.pending}</dd></div>
        <div><dt>Reclamado</dt><dd>${money(p.claimed)}</dd></div>
        <div><dt>Recuperado</dt><dd>${money(p.recovered)}</dd></div>
      </dl>
      <a href="#/incidencias?platform=${esc(p.key)}">Ver incidencias de ${esc(p.name)} →</a>
    </article>`).join("");

  const typeBars = d.by_type.length ? `<ul class="bars">${d.by_type.map((x) => `
      <li>
        <a class="bar-label" href="#/incidencias?type=${esc(x.incident_type)}">${esc(m.incident_types[x.incident_type] || x.incident_type)}</a>
        <span class="bar-track" aria-hidden="true"><span class="bar-fill" style="width:${(x.n / d.max_type * 100).toFixed(1)}%"></span></span>
        <span class="bar-value">${x.n}</span>
      </li>`).join("")}</ul>` : `<p class="muted">Sin incidencias en el periodo.</p>`;

  const chart = d.daily.map((x) => `
      <div class="chart-col" title="${esc(x.label)}: ${x.n} incidencias">
        <span class="chart-fill" style="height:${(x.n / d.max_daily * 100).toFixed(1)}%"></span>
      </div>`).join("");

  const alerts = d.alerts.length ? `
    <table class="table compact">
      <thead><tr><th scope="col">Pedido</th><th scope="col">Plataforma</th>${isAdmin() ? `<th scope="col">Local</th>` : ""}<th scope="col">Estado</th><th scope="col">Días</th></tr></thead>
      <tbody>${d.alerts.map((i) => `
        <tr>
          <td><a href="#/incidencias/${i.id}">${esc(i.order_ref)}</a></td>
          <td>${pill(i.platform)}</td>
          ${isAdmin() ? `<td>${esc(i.restaurant_name)}</td>` : ""}
          <td>${statusBadge(i.status)}</td>
          <td><strong>${i.days}</strong></td>
        </tr>`).join("")}</tbody>
    </table>` : `<p class="muted">Todo al día. No hay incidencias pendientes antiguas.</p>`;

  const byRestaurant = d.by_restaurant.length ? `
    <article class="card">
      <h2>Por restaurante</h2>
      <table class="table compact">
        <thead><tr><th scope="col">Local</th><th scope="col" class="num">Incidencias</th><th scope="col" class="num">Reclamado</th><th scope="col" class="num">Recuperado</th></tr></thead>
        <tbody>${d.by_restaurant.map((r) => `
          <tr>
            <td><a href="#/incidencias?restaurant=${r.id}">${esc(r.name)}</a></td>
            <td class="num">${r.total}</td><td class="num">${money(r.claimed)}</td><td class="num">${money(r.recovered)}</td>
          </tr>`).join("")}</tbody>
      </table>
    </article>` : "";

  el.innerHTML = `
    <div class="page-head">
      <h1>Panel de incidencias</h1>
      <div class="inline-form">
        <label for="dias">Periodo</label>
        <select id="dias">
          ${[7, 30, 90].map((n) => `<option value="${n}" ${String(n) === days ? "selected" : ""}>Últimos ${n} días</option>`).join("")}
        </select>
      </div>
    </div>

    <section class="kpis" aria-label="Indicadores principales">
      <div class="kpi"><span class="kpi-label">Incidencias</span><span class="kpi-value">${t.total}</span></div>
      <a class="kpi kpi-warn" href="#/incidencias?status=pendientes">
        <span class="kpi-label">Pendientes</span><span class="kpi-value">${t.pending}</span>
        <span class="kpi-sub">${money(t.pending_amount)} en juego</span>
      </a>
      <div class="kpi"><span class="kpi-label">Importe reclamado</span><span class="kpi-value">${money(t.claimed)}</span></div>
      <div class="kpi kpi-ok"><span class="kpi-label">Importe recuperado</span><span class="kpi-value">${money(t.recovered)}</span></div>
      <div class="kpi"><span class="kpi-label">Tasa de recuperación</span><span class="kpi-value">${d.rate} %</span>
        <span class="kpi-sub">sobre incidencias cerradas</span></div>
    </section>

    <section class="grid grid-3" aria-label="Resumen por plataforma">${platformCards}</section>

    <section class="grid grid-2">
      <article class="card"><h2>Por tipo de incidencia</h2>${typeBars}</article>
      <article class="card">
        <h2>Incidencias por día <small class="muted">(últimos ${d.daily.length} días)</small></h2>
        <div class="chart" role="img" aria-label="Gráfico de barras con las incidencias diarias">${chart}</div>
        <div class="chart-axis"><span>${esc(d.daily[0].label)}</span><span>Máx. ${d.max_daily}/día</span><span>${esc(d.daily.at(-1).label)}</span></div>
      </article>
    </section>

    <section class="grid ${byRestaurant ? "grid-2" : ""}">
      <article class="card">
        <h2>Requieren atención <small class="muted">pendientes desde hace más de ${m.alert_days} días</small></h2>
        ${alerts}
      </article>
      ${byRestaurant}
    </section>`;

  el.querySelector("#dias").addEventListener("change", (e) => { location.hash = `#/?dias=${e.target.value}`; });
}
