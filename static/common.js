// common.js — utilidades compartidas por TODAS las páginas del panel GEV
// (sidebar, topbar, selector de instalación/cámara, helpers de fetch/formato).
// Cada página (dashboard.js, baterias.js, tours.js, ...) carga este archivo
// primero y lo usa; no debe duplicar esta lógica.
console.log("%c[GEV] common.js — build 2026-07-16e cargado correctamente", "color:#22c55e;font-weight:bold;");

const $ = (id) => document.getElementById(id);
const GEV_CAM_KEY = "gev_currentCamara";

function fmt(n, decimals = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("es-ES", { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
}

function orientacionTexto(az) {
  if (az === null || az === undefined) return "—";
  const dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
  const idx = Math.round(az / 45) % 8;
  return dirs[idx];
}

async function api(path, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, { signal: controller.signal });
    if (!res.ok) {
      let detalle = `${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error) detalle = body.error;
      } catch (_) { /* el cuerpo no era JSON, usamos el status tal cual */ }
      throw new Error(`${path} → ${detalle}`);
    }
    return await res.json();
  } catch (e) {
    if (e.name === "AbortError") {
      throw new Error(`${path} → tiempo de espera agotado (${timeoutMs}ms). ¿El servidor Flask sigue arriba?`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ───────────────────── Cámara seleccionada (persistida entre páginas) ─────────────────────
// Como ahora cada sección del menú es una página/ruta distinta, guardamos la
// cámara elegida en localStorage para que "Tours", "Baterías", etc. sigan
// mostrando la misma instalación al navegar entre ellas.

function getCurrentCamara() {
  return localStorage.getItem(GEV_CAM_KEY);
}

function setCurrentCamara(camName) {
  localStorage.setItem(GEV_CAM_KEY, camName);
}

// Resuelve qué cámara mostrar: la guardada si sigue siendo válida, o si no,
// la primera cámara activa de la primera instalación con cámaras.
async function resolverCamaraActual() {
  const instalaciones = await api("/api/instalaciones_con_camaras");
  const guardada = getCurrentCamara();
  if (guardada) {
    for (const inst of instalaciones) {
      if (inst.camaras.some(c => c.cam_name === guardada)) return guardada;
    }
    // la cámara guardada ya no existe/activa: seguimos al fallback de abajo
  }
  for (const inst of instalaciones) {
    if (inst.camaras.length) {
      setCurrentCamara(inst.camaras[0].cam_name);
      return inst.camaras[0].cam_name;
    }
  }
  return null;
}

// ───────────────────── Selector de instalación / cámara ─────────────────────

async function abrirSelector() {
  $("selectorOverlay").hidden = false;
  $("selectorBody").innerHTML = "Cargando…";

  const avisoLento = setTimeout(() => {
    if ($("selectorBody").textContent.trim() === "Cargando…") {
      $("selectorBody").innerHTML = "Cargando… (está tardando más de lo normal, comprobando conexión a la base de datos)";
    }
  }, 3000);

  try {
    const instalaciones = await api("/api/instalaciones_con_camaras");
    let html = "";
    for (const inst of instalaciones) {
      if (!inst.camaras.length) continue;
      html += `<div class="inst-group">
        <div class="inst-group-title">${inst.nombre}</div>
        ${inst.camaras.map(c => `
          <div class="cam-option" data-cam="${c.cam_name}">
            <span>${c.cam_name}</span>
            <span class="tag">${c.modelo_camara || ""}</span>
          </div>`).join("")}
      </div>`;
    }
    $("selectorBody").innerHTML = html || "<div class='empty-note'>No hay cámaras activas.</div>";
    $("selectorBody").querySelectorAll(".cam-option").forEach(el => {
      el.addEventListener("click", () => {
        setCurrentCamara(el.dataset.cam);
        cerrarSelector();
        // Recargamos la página actual para que su propio script vuelva a
        // pedir los datos ya con la nueva cámara seleccionada.
        window.location.reload();
      });
    });
  } catch (e) {
    console.error("[GEV] Error cargando selector de instalaciones:", e);
    $("selectorBody").innerHTML = `<div class='empty-note'>${e.message || "Error cargando instalaciones. ¿Está la API arriba?"}</div>`;
  } finally {
    clearTimeout(avisoLento);
  }
}

function cerrarSelector() {
  $("selectorOverlay").hidden = true;
}

// ───────────────────── Cabecera común (sidebar + topbar) ─────────────────────
// Cada página, tras cargar los datos de "su" cámara actual, debe llamar a
// renderCabecera(cam) para mantener sincronizados el nombre de instalación /
// cámara del sidebar y el breadcrumb + badge online/offline + hora de
// actualización del topbar. `cam` debe traer al menos: instalacion_nombre,
// cam_name, online, updated_at (el mismo shape que devuelve
// /api/camaras/<cam_name>).
function renderCabecera(cam) {
  $("sbInstNombre").textContent = cam.instalacion_nombre ?? "—";
  $("sbCamNombre").textContent = cam.cam_name ?? "—";
  $("bcInstalacion").textContent = cam.instalacion_nombre ?? "—";
  $("bcCamara").textContent = cam.cam_name ?? "—";

  const badge = $("badgeOnline");
  badge.textContent = cam.online ? "ONLINE" : "OFFLINE";
  badge.classList.toggle("offline", !cam.online);

  if (cam.updated_at) {
    const d = new Date(cam.updated_at);
    $("lastUpdateTime").textContent = d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
    $("lastUpdateDate").textContent = d.toLocaleDateString("es-ES");
  } else {
    $("lastUpdateTime").textContent = "—";
    $("lastUpdateDate").textContent = "—";
  }
}

function bind(id, evento, handler, nombre) {
  const el = $(id);
  if (!el) {
    console.error(`[GEV] No se encontró el elemento #${id} — el control "${nombre}" no funcionará.`);
    return;
  }
  el.addEventListener(evento, handler);
}

bind("btnCambiarInstalacion", "click", abrirSelector, "Cambiar instalación");
bind("btnCloseSelector", "click", cerrarSelector, "Cerrar selector");
bind("selectorOverlay", "click", (e) => {
  if (e.target.id === "selectorOverlay") cerrarSelector();
}, "Overlay selector");
