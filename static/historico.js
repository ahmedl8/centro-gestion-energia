// historico.js — página "Histórico" (gráfico SoC con rango de días configurable)
console.log("%c[GEV] historico.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

let currentCamara = null;
let socChart = null;
const REFRESH_MS = 60000;

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
          ticks: { color: "#8b97b3", maxTicksLimit: 12, font: { size: 10 } },
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

async function cargarHistorico() {
  if (!currentCamara) return;
  const dias = $("selDias").value;
  try {
    const hist = await api(`/api/camaras/${currentCamara}/historico?dias=${dias}`);
    if (typeof Chart === "undefined") {
      throw new Error("Chart.js no se cargó (¿bloqueado por VPN/firewall al CDN?)");
    }
    renderSocChart(hist);
  } catch (e) {
    console.error("[GEV] Error renderizando histórico SoC:", e);
    const canvas = $("socChart");
    if (canvas) {
      canvas.replaceWith(Object.assign(document.createElement("div"), {
        className: "empty-note",
        textContent: e.message || "No se pudo cargar el gráfico.",
      }));
    }
  }
}

async function cargarCabecera() {
  if (!currentCamara) return;
  try {
    const cam = await api(`/api/camaras/${currentCamara}`);
    renderCabecera(cam);
  } catch (e) {
    console.error("[GEV] Error cargando cabecera:", e);
  }
}

async function cargarTodo() {
  await cargarCabecera();
  await cargarHistorico();
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
bind("selDias", "change", () => cargarHistorico(), "Selector de días");

init();
setInterval(cargarTodo, REFRESH_MS);
