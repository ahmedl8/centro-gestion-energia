// tours.js — página "Tours (Cámaras)"
console.log("%c[GEV] tours.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
const REFRESH_MS = 60000;

async function renderTours(camName) {
  const el = $("toursTimeline");
  try {
    const data = await api(`/api/camaras/${camName}/tours`);
    if (data.suspendido) {
      $("toursHorario").textContent = "(Suspendido por mal tiempo)";
      $("kpiToursDia").textContent = "0";
      $("kpiCadaMin").textContent = "Suspendido (mal tiempo)";
      $("kpiHorario").textContent = "—";
      el.innerHTML = "<div class='empty-note'>Rutina de tours suspendida temporalmente por condiciones meteorológicas adversas.</div>";
      $("toursDuracion").textContent = "—";
      return;
    }
    $("toursHorario").textContent = `(Horario: ${data.horario})`;
    $("toursDuracion").textContent = `${data.duracion_tour_min} min`;
    $("kpiCadaMin").textContent = data.frecuencia_min ? `Cada ${data.frecuencia_min} min` : "—";
    $("kpiHorario").textContent = data.horario;
    $("kpiToursDia").textContent = data.tours.length;
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

async function cargarTodo() {
  if (!currentCamara) return;
  try {
    const cam = await api(`/api/camaras/${currentCamara}`);
    renderCabecera(cam);
  } catch (e) {
    console.error("[GEV] Error cargando cabecera:", e);
  }
  await renderTours(currentCamara);
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
