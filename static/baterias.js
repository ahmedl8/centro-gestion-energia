// baterias.js — página "Baterías": detalle de la cámara actual + tabla de flota
console.log("%c[GEV] baterias.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
const REFRESH_MS = 60000;

function renderGauge(pct) {
  const circunferencia = Math.PI * 80;
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

function renderDetalle(cam) {
  renderCabecera(cam);
  renderGauge(cam.soc_pct);
  $("dTipoBat").textContent = cam.bat_type ? cam.bat_type.toUpperCase() : "—";
  $("dCapacidad").textContent = cam.bat_ah ? `${cam.bat_ah} Ah` : "—";
  $("dEnergiaUtil").textContent = cam.energia_util_wh !== null ? `${fmt(cam.energia_util_wh)} Wh` : "—";
  $("dVoltaje").textContent = cam.bat_actual !== null ? `${fmt(cam.bat_actual, 2)} V` : "—";
  $("dCargando").textContent = cam.cargando_ahora ? "Sí ⚡" : "No";
  $("dFrescura").textContent = cam.online ? "Reciente (< 20 min)" : "Sin datos recientes";
}

function estadoBadge(row) {
  if (!row.online) return `<span class="pill pill-red">SIN CONEXIÓN</span>`;
  if (row.alerta_bat === "critico") return `<span class="pill pill-red">CRÍTICO</span>`;
  if (row.alerta_bat === "bajo") return `<span class="pill pill-yellow">BAJO</span>`;
  return `<span class="pill pill-green">OK</span>`;
}

async function renderTabla() {
  const body = $("tablaBateriasBody");
  try {
    const filas = await api("/api/baterias");
    if (!filas.length) {
      body.innerHTML = `<tr><td colspan="7" class="empty-note">No hay cámaras activas con datos de batería.</td></tr>`;
      return;
    }
    body.innerHTML = filas.map(r => `
      <tr>
        <td>${r.instalacion_nombre}</td>
        <td>${r.cam_name}</td>
        <td>${r.soc_pct !== null ? fmt(r.soc_pct) + "%" : "—"}</td>
        <td>${r.bat_actual !== null ? fmt(r.bat_actual, 2) + " V" : "—"}</td>
        <td>${r.cargando_ahora ? "Sí ⚡" : "No"}</td>
        <td>${estadoBadge(r)}</td>
        <td>${r.updated_at ? new Date(r.updated_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}</td>
      </tr>`).join("");
  } catch (e) {
    console.error("[GEV] Error cargando /api/baterias:", e);
    body.innerHTML = `<tr><td colspan="7" class="empty-note">${e.message || "No se pudo cargar la tabla de baterías."}</td></tr>`;
  }
}

async function cargarTodo() {
  if (currentCamara) {
    try {
      const cam = await api(`/api/camaras/${currentCamara}`);
      renderDetalle(cam);
    } catch (e) {
      console.error("[GEV] Error cargando detalle de cámara:", e);
    }
  }
  await renderTabla();
}

async function init() {
  try {
    currentCamara = await resolverCamaraActual();
  } catch (e) {
    console.error(e);
  }
  await cargarTodo();
}

bind("btnRefresh", "click", () => cargarTodo(), "Refrescar");

init();
setInterval(cargarTodo, REFRESH_MS);
