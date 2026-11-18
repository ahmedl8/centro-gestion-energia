// prevision.js — página "Previsión del tiempo"
console.log("%c[GEV] prevision.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
const REFRESH_MS = 60000;

function diaLabel(offset) {
  if (offset === 0) return "Hoy";
  if (offset === 1) return "Mañana";
  return "Pasado mañana";
}

function nubesIcon(pct) {
  if (pct === null || pct === undefined) return "☁";
  if (pct < 20) return "☀";
  if (pct < 60) return "⛅";
  return "☁";
}

function renderWeather(cam) {
  const dias = [
    { label: diaLabel(0), nubes: cam.nubes_actual, olas: cam.olas_actual, viento: cam.viento_actual },
    { label: diaLabel(1), nubes: cam.nubes_manana, olas: cam.olas_manana, viento: cam.viento_manana },
    { label: diaLabel(2), nubes: cam.nubes_pasado_manana, olas: cam.olas_pasado_manana, viento: cam.viento_pasado_manana },
  ];
  $("weatherRow").innerHTML = dias.map(d => `
    <div class="weather-day">
      <div class="wd-label">${d.label}</div>
      <div class="wd-icon">${nubesIcon(d.nubes)}</div>
      <div class="wd-line">${d.nubes !== null && d.nubes !== undefined ? d.nubes + "% nubes" : "—"}</div>
      <div class="wd-line">Olas: ${fmt(d.olas, 1)} m</div>
      <div class="wd-line">Viento: ${fmt(d.viento, 0)} km/h</div>
    </div>
  `).join("");

  const gen1 = cam.gen_hoy_wh || 0, cons1 = cam.cons_hoy_wh || 0;
  const rows = [
    { label: "Hoy", gen: gen1, cons: cons1, tours: cam.tours_dia },
    { label: "Mañana", gen: null, cons: cons1, tours: cam.tours_dia },
    { label: "Pasado mañana", gen: null, cons: cons1, tours: cam.tours_dia },
  ];
  $("energyTableBody").innerHTML = rows.map(r => {
    const gen = r.gen !== null ? r.gen : "—";
    const balance = r.gen !== null ? (r.gen - r.cons) : null;
    const balClass = balance === null ? "" : (balance >= 0 ? "balance-pos" : "balance-neg");
    const balTxt = balance === null ? "—" : `${balance >= 0 ? "+" : ""}${fmt(balance)}`;
    return `<tr>
      <td>${r.label}</td>
      <td>${gen === "—" ? "—" : fmt(gen)}</td>
      <td>${fmt(r.cons)}</td>
      <td class="${balClass}">${balTxt}</td>
      <td>${r.tours ?? "—"}</td>
    </tr>`;
  }).join("");

  const balanceHoy = cam.balance_hoy_wh;
  $("balanceNote").hidden = !(balanceHoy !== null && balanceHoy !== undefined && balanceHoy < 0);
}

async function cargarTodo() {
  if (!currentCamara) return;
  try {
    const cam = await api(`/api/camaras/${currentCamara}`);
    renderCabecera(cam);
    renderWeather(cam);
  } catch (e) {
    console.error("[GEV] Error cargando previsión:", e);
  }
}

async function init() {
  try {
    currentCamara = await resolverCamaraActual();
    if (!currentCamara) {
      $("bcInstalacion").textContent = "Sin instalaciones con cámaras activas";
      return;
    }
  } catch (e) {
    console.error(e);
    return;
  }
  await cargarTodo();
}

bind("btnRefresh", "click", () => cargarTodo(), "Refrescar");

init();
setInterval(cargarTodo, REFRESH_MS);
