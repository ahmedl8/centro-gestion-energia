// configuracion.js — página "Configuración" (solo lectura, el panel GEV no escribe en BD)
console.log("%c[GEV] configuracion.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
const REFRESH_MS = 60000;

function render(cam) {
  renderCabecera(cam);
  $("cfInstalacion").textContent = cam.instalacion_nombre ?? "—";
  $("cfModelo").textContent = cam.modelo_camara ?? "—";
  $("cfConsumo").textContent = cam.consumo_cam_w ? `${cam.consumo_cam_w} W` : "—";
  $("cfBatTipo").textContent = cam.bat_type ? cam.bat_type.toUpperCase() : "—";
  $("cfBatAh").textContent = cam.bat_ah ? `${cam.bat_ah} Ah` : "—";
  $("cfCargadorTipo").textContent = cam.descripcion_cargador || cam.tipo_cargador || "—";
  $("cfCargadorA").textContent = cam.cargador_a ? `${cam.cargador_a} A` : "—";
  $("cfPanelW").textContent = cam.panel_w ? `${cam.panel_w} W` : "—";
  $("cfAzimut").textContent = cam.panel_azimut !== null && cam.panel_azimut !== undefined ? `${cam.panel_azimut}° (${orientacionTexto(cam.panel_azimut)})` : "—";
  $("cfInclinacion").textContent = cam.panel_inclinacion !== null && cam.panel_inclinacion !== undefined ? `${cam.panel_inclinacion}°` : "—";
  $("cfFactorCal").textContent = cam.factor_calibracion !== null && cam.factor_calibracion !== undefined ? fmt(cam.factor_calibracion, 2) : "—";
}

async function cargarTodo() {
  if (!currentCamara) return;
  try {
    const cam = await api(`/api/camaras/${currentCamara}`);
    render(cam);
  } catch (e) {
    console.error("[GEV] Error cargando configuración de la cámara:", e);
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
