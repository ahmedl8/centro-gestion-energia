// dashboard.js — lógica de la página Dashboard (resumen general)
// Utilidades compartidas ($, fmt, api, renderCabecera, resolverCamaraActual...)
// vienen de common.js, que se carga antes que este archivo.
console.log("%c[GEV] dashboard.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
let socChart = null;
const REFRESH_MS = 60000; // refresco automático cada 60s, igual que el ciclo mínimo del backend

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

function renderGauge(pct) {
  const circunferencia = Math.PI * 80; // radio 80 del path semicircular
  const p = Math.max(0, Math.min(100, pct || 0));
  const dash = (p / 100) * circunferencia;
  const arc = $("gaugeArc");
  arc.style.strokeDasharray = `${dash} ${circunferencia}`;
  let color = "var(--green)";
  if (p < 15) color = "var(--red)";
  else if (p < 30) color = "var(--yellow)";
  arc.style.stroke = color;
  $("gaugePct").style.color = color;
  $("gaugePct").textContent = `${fmt(p)}%`;
}

function renderCompass(azimut, inclinacion) {
  const az = azimut === null || azimut === undefined ? 180 : azimut;
  $("panelGroup").setAttribute("transform", `translate(100,100) rotate(${az})`);
  $("cAzimut").textContent = azimut !== null && azimut !== undefined ? `${azimut}°` : "—";
  $("cInclinacion").textContent = inclinacion !== null && inclinacion !== undefined ? `${inclinacion}°` : "—";
  $("cOrientacion").textContent = orientacionTexto(az);
}

function renderSocChart(historico) {
  const labels = historico.map(r => {
    const d = new Date(r.ts);
    return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit" });
  });
  const data = historico.map(r => r.soc_pct);

  if (socChart) socChart.destroy();
  const ctx = $("socChart").getContext("2d");
  socChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data,
        borderColor: "#22c55e",
        backgroundColor: "rgba(34,197,94,0.08)",
        pointRadius: 0,
        borderWidth: 1.5,
        fill: true,
        tension: 0.25,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#8b97b3", maxTicksLimit: 8, font: { size: 10 } },
          grid: { color: "rgba(255,255,255,0.03)" },
        },
        y: {
          min: 0, max: 100,
          ticks: { color: "#8b97b3", callback: v => v + "%", font: { size: 10 } },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
      },
    },
  });
}

async function renderTours(camName) {
  const el = $("toursTimeline");
  try {
    const data = await api(`/api/camaras/${camName}/tours`);
    if (data.suspendido) {
      $("toursHorario").textContent = "(Suspendido por mal tiempo)";
      el.innerHTML = "<div class='empty-note'>Rutina de tours suspendida temporalmente por condiciones meteorológicas adversas.</div>";
      $("toursDuracion").textContent = "—";
      return;
    }
    $("toursHorario").textContent = `(Horario: ${data.horario})`;
    $("toursDuracion").textContent = `${data.duracion_tour_min} min`;
    if (!data.tours.length) {
      el.innerHTML = "<div class='empty-note'>No hay más tours programados dentro de la ventana de vigilancia actual.</div>";
      return;
    }
    el.innerHTML = `<div class="tour-line"></div>` + data.tours.map(t => `
      <div class="tour-stop">
        <div class="dot"></div>
        <div class="time">${t}</div>
      </div>`).join("");
  } catch (e) {
    el.innerHTML = "<div class='empty-note'>No se pudo calcular la programación de tours.</div>";
  }
}

function renderWeather(cam) {
  const dias = [
    { label: diaLabel(0), nubes: cam.nubes_actual, olas: cam.olas_actual, viento: cam.viento_actual,
      gen: cam.gen_hoy_wh, cons: cam.cons_hoy_wh, tours: cam.tours_dia },
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

function render(cam) {
  renderCabecera(cam);

  // KPIs
  $("kpiSoc").textContent = fmt(cam.soc_pct);
  $("kpiVoltaje").textContent = cam.bat_actual !== null ? `${fmt(cam.bat_actual, 1)} V` : "— V";
  $("kpiCargando").hidden = !cam.cargando_ahora;

  $("kpiGen").textContent = fmt(cam.gen_hoy_wh);
  const balance = cam.balance_hoy_wh;
  const balEl = $("kpiBalance");
  balEl.textContent = balance !== null && balance !== undefined ? `${balance >= 0 ? "+" : ""}${fmt(balance)} Wh Balance` : "— Wh Balance";
  balEl.className = "pill " + (balance >= 0 ? "pill-green" : "pill-red");

  $("kpiCons").textContent = fmt(cam.cons_hoy_wh);

  if (cam.suspendido_mal_tiempo) {
    $("kpiToursDia").textContent = "0";
    $("kpiCadaMin").textContent = "Suspendido (mal tiempo)";
  } else {
    $("kpiToursDia").textContent = cam.tours_dia ?? "—";
    $("kpiCadaMin").textContent = cam.frecuencia_min ? `Cada ${cam.frecuencia_min} min` : "—";
  }
  $("kpiHorario").textContent = `${String(cam.horario_inicio).padStart(2, "0")}:00 - ${String(cam.horario_fin).padStart(2, "0")}:00`;

  const sysEl = $("kpiSysEstado");
  sysEl.className = "kpi-main";
  if (!cam.online) {
    sysEl.textContent = "SIN CONEXIÓN"; sysEl.classList.add("sys-bad");
    $("kpiSysDetalle").textContent = "Sin datos recientes de la maleta";
  } else if (cam.alerta_bat === "critico") {
    sysEl.textContent = "CRÍTICO"; sysEl.classList.add("sys-bad");
    $("kpiSysDetalle").textContent = "Batería en nivel crítico";
  } else if (cam.alerta_bat === "bajo") {
    sysEl.textContent = "ATENCIÓN"; sysEl.classList.add("sys-warn");
    $("kpiSysDetalle").textContent = "Batería en nivel bajo";
  } else {
    sysEl.textContent = "OK"; sysEl.classList.add("sys-ok");
    $("kpiSysDetalle").textContent = "Todo funcionando correctamente";
  }

  // Battery detail
  renderGauge(cam.soc_pct);
  $("dTipoBat").textContent = cam.bat_type ? cam.bat_type.toUpperCase() : "—";
  $("dCapacidad").textContent = cam.bat_ah ? `${cam.bat_ah} Ah` : "—";
  $("dEnergiaUtil").textContent = cam.energia_util_wh !== null ? `${fmt(cam.energia_util_wh)} Wh` : "—";
  $("dVoltaje").textContent = cam.bat_actual !== null ? `${fmt(cam.bat_actual, 2)} V` : "—";
  $("dCargando").textContent = cam.cargando_ahora ? "Sí ⚡" : "No";

  renderWeather(cam);

  // Solar config
  $("sPotencia").textContent = cam.panel_w ? `${cam.panel_w} W` : "—";
  $("sAzimut").textContent = cam.panel_azimut !== null ? `${cam.panel_azimut}° (${orientacionTexto(cam.panel_azimut)})` : "—";
  $("sInclinacion").textContent = cam.panel_inclinacion !== null ? `${cam.panel_inclinacion}°` : "—";
  $("sFactorOrient").textContent = cam.factor_orientacion !== null ? fmt(cam.factor_orientacion, 2) : "—";
  $("sFactorCal").textContent = cam.factor_calibracion !== null ? fmt(cam.factor_calibracion, 2) : "—";
  $("sRendimiento").textContent = cam.rendimiento_estimado_pct !== null ? `${cam.rendimiento_estimado_pct}%` : "—";

  renderCompass(cam.panel_azimut, cam.panel_inclinacion);
}

async function cargarCamara(camName) {
  currentCamara = camName;

  try {
    const cam = await api(`/api/camaras/${camName}`);
    render(cam);
  } catch (e) {
    console.error("Error cargando detalle de cámara:", e);
    return; // sin datos base no tiene sentido seguir
  }

  // El histórico y los tours son independientes entre sí: si uno falla
  // (p. ej. Chart.js no llegó a cargar por un bloqueo de red/VPN al CDN)
  // no debe dejar al otro colgado en "Cargando…".
  try {
    const hist = await api(`/api/camaras/${camName}/historico`);
    if (typeof Chart === "undefined") {
      throw new Error("Chart.js no se cargó (¿bloqueado por VPN/firewall al CDN?)");
    }
    renderSocChart(hist);
  } catch (e) {
    console.error("Error renderizando histórico SoC:", e);
    document.querySelector("#socChart").replaceWith(
      Object.assign(document.createElement("div"), {
        className: "empty-note",
        textContent: "No se pudo cargar el gráfico (revisa la consola: puede que Chart.js esté bloqueado).",
      })
    );
  }

  try {
    await renderTours(camName);
  } catch (e) {
    console.error("Error renderizando tours:", e);
  }
}

async function init() {
  try {
    const cam = await resolverCamaraActual();
    if (!cam) {
      $("bcInstalacion").textContent = "Sin instalaciones con cámaras activas";
      return;
    }
    await cargarCamara(cam);
  } catch (e) {
    $("bcInstalacion").textContent = "Error de conexión con la API";
    console.error(e);
  }
}

bind("btnRefresh", "click", () => {
  if (currentCamara) cargarCamara(currentCamara);
}, "Refrescar");

init();
setInterval(() => {
  if (currentCamara) cargarCamara(currentCamara);
}, REFRESH_MS);
