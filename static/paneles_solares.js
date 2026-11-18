// paneles_solares.js — página "Paneles solares"
console.log("%c[GEV] paneles_solares.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
const REFRESH_MS = 60000;

function renderCompass(azimut, inclinacion) {
  const az = azimut === null || azimut === undefined ? 180 : azimut;
  $("panelGroup").setAttribute("transform", `translate(100,100) rotate(${az})`);
  $("cAzimut").textContent = azimut !== null && azimut !== undefined ? `${azimut}°` : "—";
  $("cInclinacion").textContent = inclinacion !== null && inclinacion !== undefined ? `${inclinacion}°` : "—";
  $("cOrientacion").textContent = orientacionTexto(az);
}

function render(cam) {
  renderCabecera(cam);
  $("sPotencia").textContent = cam.panel_w ? `${cam.panel_w} W` : "—";
  $("sAzimut").textContent = cam.panel_azimut !== null ? `${cam.panel_azimut}° (${orientacionTexto(cam.panel_azimut)})` : "—";
  $("sInclinacion").textContent = cam.panel_inclinacion !== null ? `${cam.panel_inclinacion}°` : "—";
  $("sFactorOrient").textContent = cam.factor_orientacion !== null ? fmt(cam.factor_orientacion, 2) : "—";
  $("sFactorCal").textContent = cam.factor_calibracion !== null ? fmt(cam.factor_calibracion, 2) : "—";
  $("sRendimiento").textContent = cam.rendimiento_estimado_pct !== null ? `${cam.rendimiento_estimado_pct}%` : "—";
  renderCompass(cam.panel_azimut, cam.panel_inclinacion);
}

async function cargarTodo() {
  if (!currentCamara) return;
  try {
    const cam = await api(`/api/camaras/${currentCamara}`);
    render(cam);
  } catch (e) {
    console.error("[GEV] Error cargando configuración solar:", e);
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
