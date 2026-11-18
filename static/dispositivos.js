// dispositivos.js — página "Dispositivos" (inventario de cámaras de todas las instalaciones)
console.log("%c[GEV] dispositivos.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

const REFRESH_MS = 60000;

async function cargarDispositivos() {
  const body = $("tablaDispositivosBody");
  try {
    const filas = await api("/api/dispositivos");
    if (!filas.length) {
      body.innerHTML = `<tr><td colspan="7" class="empty-note">No hay cámaras registradas.</td></tr>`;
      return;
    }
    body.innerHTML = filas.map(r => `
      <tr>
        <td>${r.instalacion_nombre}</td>
        <td>${r.cam_name}</td>
        <td>${r.modelo_camara || "—"}</td>
        <td>${r.activada ? '<span class="pill pill-green">Sí</span>' : '<span class="pill pill-red">No</span>'}</td>
        <td>${r.panel_w ? r.panel_w + " W" : "—"}</td>
        <td>${r.bat_type ? r.bat_type.toUpperCase() : "—"}</td>
        <td>${r.updated_at ? new Date(r.updated_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}</td>
      </tr>`).join("");

    const actual = getCurrentCamara();
    if (actual) {
      const fila = filas.find(r => r.cam_name === actual);
      if (fila) renderCabecera(fila);
    }
  } catch (e) {
    console.error("[GEV] Error cargando /api/dispositivos:", e);
    body.innerHTML = `<tr><td colspan="7" class="empty-note">${e.message || "No se pudo cargar el inventario de dispositivos."}</td></tr>`;
  }
}

bind("btnRefresh", "click", () => cargarDispositivos(), "Refrescar");

cargarDispositivos();
setInterval(cargarDispositivos, REFRESH_MS);
