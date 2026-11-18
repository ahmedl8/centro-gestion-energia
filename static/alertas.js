// alertas.js — página "Alertas" (basada en bateria_estado.alerta_bat + frescura de datos;
// no existe una tabla de alertas dedicada, así que se deriva del mismo estado que ya usa
// el resto del panel).
console.log("%c[GEV] alertas.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

const REFRESH_MS = 60000;
let ultimasFilas = [];

function estadoInfo(row) {
  if (!row.online) return { rank: 3, label: "SIN CONEXIÓN", cls: "pill-red" };
  if (row.alerta_bat === "critico") return { rank: 2, label: "CRÍTICO", cls: "pill-red" };
  if (row.alerta_bat === "bajo") return { rank: 1, label: "BAJO", cls: "pill-yellow" };
  return { rank: 0, label: "OK", cls: "pill-green" };
}

function pintarTabla() {
  const soloProblemas = $("soloProblemas").checked;
  const body = $("tablaAlertasBody");
  const filas = ultimasFilas.filter(r => !soloProblemas || estadoInfo(r).rank > 0);

  if (!filas.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-note">${soloProblemas ? "Sin alertas activas — todo en orden." : "No hay cámaras activas."}</td></tr>`;
    return;
  }

  body.innerHTML = filas.map(r => {
    const e = estadoInfo(r);
    return `<tr>
      <td><span class="pill ${e.cls}">${e.label}</span></td>
      <td>${r.instalacion_nombre}</td>
      <td>${r.cam_name}</td>
      <td>${r.soc_pct !== null ? fmt(r.soc_pct) + "%" : "—"}</td>
      <td>${r.updated_at ? new Date(r.updated_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}</td>
    </tr>`;
  }).join("");
}

async function cargarAlertas() {
  const body = $("tablaAlertasBody");
  try {
    const filas = await api("/api/alertas");
    ultimasFilas = filas.sort((a, b) => estadoInfo(b).rank - estadoInfo(a).rank);
    pintarTabla();

    // Actualizamos también la cabecera con la cámara seleccionada globalmente,
    // si existe, para mantener consistencia visual con el resto de páginas.
    const actual = getCurrentCamara();
    if (actual) {
      const fila = filas.find(r => r.cam_name === actual);
      if (fila) renderCabecera(fila);
    }
  } catch (e) {
    console.error("[GEV] Error cargando /api/alertas:", e);
    body.innerHTML = `<tr><td colspan="5" class="empty-note">${e.message || "No se pudo cargar la lista de alertas."}</td></tr>`;
  }
}

bind("btnRefresh", "click", () => cargarAlertas(), "Refrescar");
bind("soloProblemas", "change", () => pintarTabla(), "Filtro solo problemas");

cargarAlertas();
setInterval(cargarAlertas, REFRESH_MS);
