"""
datos_tiempo.py
===============
Sistema de gestión de batería y previsión meteorológica para cámaras de piscifactoría.
Usa la base de datos GEV (gev.*), base de datos propia e independiente.

Lee la configuración estática de gev.instalaciones y gev.camaras,
el estado operacional de gev.bateria_estado,
construye la petición al router RUT955/956 según rut_tipo,
estima el SoC con filtro exponencial + lógica amanecer/atardecer,
descarga la previsión de 3 días, calcula el presupuesto energético
y actualiza gev.bateria_estado y gev.bat_energia_log.

Configuración (variables de entorno — las mismas que usa app.py del
panel, para no duplicar credenciales entre el recolector y el visor):

    GEV_DB_HOST       (por defecto: el host de producción)
    GEV_DB_USER       (por defecto: root)
    GEV_DB_PASSWORD   (OBLIGATORIA, sin valor por defecto)
    GEV_DB_NAME       (por defecto: gev)
    GEV_DB_PORT       (por defecto: 3306)

Uso:
  export GEV_DB_PASSWORD="tu_password"
  python3 collector/datos_tiempo.py                    # bucle continuo cada 1 min (pruebas)
  python3 collector/datos_tiempo.py --intervalo 900    # bucle cada 15 min (producción)
  python3 collector/datos_tiempo.py --una-vez          # un solo ciclo y termina
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import mysql.connector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─────────────────────────────────────────────────────────────
# BD — base de datos gev
# Mismas variables de entorno que app.py (ver docstring superior);
# así solo hay un sitio (el entorno del proceso) donde vive la
# contraseña real, en vez de estar embebida por duplicado aquí.
# ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    'host':            os.environ.get('GEV_DB_HOST', 'localhost'),
    'user':             os.environ.get('GEV_DB_USER', 'gev'),
    'password':         os.environ.get('GEV_DB_PASSWORD', ''),
    'database':         os.environ.get('GEV_DB_NAME', 'gev'),
    'port':             int(os.environ.get('GEV_DB_PORT', '3306')),
    'connect_timeout': 10,
}


TZ = ZoneInfo("Europe/Madrid")

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN OPERATIVA POR INSTALACIÓN
# Horario de vigilancia y frecuencia de tours — común a todas
# las cámaras de un mismo emplazamiento.
#
# lat/lon se usan SOLO como fallback si gev.instalaciones.lat/lon
# está a NULL (p. ej. office y cámaras sin coordenadas rellenas).
# La fuente de verdad son las coordenadas de la BD.
# ─────────────────────────────────────────────────────────────
from gev_config import INSTALACIONES_CONFIG  # noqa: E402  (fuente única, compartida con app.py)

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN FÍSICA — FALLBACK POR INSTALACIÓN
#
# La fuente de verdad operacional es gev.camaras. Estos valores
# solo se usan si la columna correspondiente de gev.camaras está
# a NULL.
#
# consumo_router_w: Teltonika RUT955 ≈ 5.5W (4G activo, promedio).
# No está en gev.camaras porque es constante para todo el parque.
# ─────────────────────────────────────────────────────────────
DEFAULTS_FISICOS_INSTALACION = {
    # consumo_cam_w = consumo total desde batería 12V con la cámara ON:
    #   Dahua TPC-SD2241-T vía PoE injector MIT-70G-1256BDNNH: ≈14W
    #   Hikvision DS-2TD4228T-10/S2 vía mismo inyector:         ≈33W
    "site_a":     {"panel_w": 100, "bat_ah":  50, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "site_b":       {"panel_w": 100, "bat_ah":  50, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "site_c":    {"panel_w":   0, "bat_ah": 100, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "site_d":    {"panel_w": 120, "bat_ah":  50, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "office":     {"panel_w":  80, "bat_ah":  40, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "site_e":     {"panel_w": 100, "bat_ah":  50, "consumo_cam_w": 33.0, "consumo_router_w": 5.5},
    "site_f":     {"panel_w": 100, "bat_ah":  50, "consumo_cam_w": 33.0, "consumo_router_w": 5.5},
    "site_g":    {"panel_w": 100, "bat_ah":  50, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
    "site_h": {"panel_w":   0, "bat_ah": 100, "consumo_cam_w": 14.0, "consumo_router_w": 5.5},
}

# Excepciones por cámara concreta — solo los campos que difieren del
# default de su instalación.
CAMARAS_CONFIG: dict[str, dict] = {
    "cam4_site_e": {"panel_w": 120},  # panel más grande en la jaula más alejada
}


def config_fisica_camara(instalacion: str, cam_name: str,
                          fila_bd: dict | None = None) -> dict:
    """
    Devuelve la configuración física completa de una maleta con esta
    prioridad (de mayor a menor):

      1. Valores no-NULL de gev.camaras — fuente de verdad operacional
      2. Excepción manual en CAMARAS_CONFIG para este cam_name
      3. Default de la instalación en DEFAULTS_FISICOS_INSTALACION

    Si tipo_cargador aparece en CARGADORES_CONFIG, se enriquece con
    eficiencia, v_umbral_carga y profundidad_descarga_max del cargador.
    Si bat_type está explícito en BD, prevalece sobre el que implica
    el cargador.
    """
    base   = DEFAULTS_FISICOS_INSTALACION.get(instalacion, {})
    excep  = CAMARAS_CONFIG.get(cam_name, {})
    merged = {**base, **excep}

    # Sobreescribir solo los campos que la BD tenga rellenos (no NULL)
    if fila_bd:
        for campo in ("panel_w", "bat_ah", "consumo_cam_w",
                      "bat_type", "tipo_cargador", "cargador_a",
                      "panel_azimut", "panel_inclinacion", "factor_calibracion"):
            if fila_bd.get(campo) is not None:
                merged[campo] = fila_bd[campo]
        # consumo_router_w no está en gev.camaras → siempre del fallback

    cargador_key = merged.get("tipo_cargador")
    carg = CARGADORES_CONFIG.get(cargador_key) if cargador_key else None

    if carg:
        merged["eficiencia_cargador"]      = carg["eficiencia"]
        merged["v_umbral_carga"]           = carg["v_umbral_carga"]
        merged["profundidad_descarga_max"] = carg["profundidad_descarga_max"]
        merged["descripcion_cargador"]     = carg["descripcion"]
        merged["sin_panel"]                = carg.get("sin_panel", False)
        if not (fila_bd and fila_bd.get("bat_type")):
            merged["bat_type"] = carg["bat_type"]
    else:
        bat_type = merged.get("bat_type", "lead")
        merged.setdefault("eficiencia_cargador", EFICIENCIA_PANEL_FALLBACK)
        merged.setdefault("v_umbral_carga",
                          UMBRAL_CARGA_LEAD if bat_type == "lead" else UMBRAL_CARGA_LIFEPO4)
        merged.setdefault("profundidad_descarga_max",
                          PROFUNDIDAD_DESCARGA_LEAD if bat_type == "lead"
                          else PROFUNDIDAD_DESCARGA_LIFEPO4)
        merged["descripcion_cargador"] = "no_identificado"

    merged.setdefault("bat_type", "lead")
    return merged


# ─────────────────────────────────────────────────────────────
# PARÁMETROS BATERÍA
# ─────────────────────────────────────────────────────────────
TABLA_SOC_LEAD = [
    (12.73, 100), (12.62, 95), (12.50, 90), (12.37, 85),
    (12.24, 80),  (12.10, 75), (11.96, 70), (11.81, 65),
    (11.66, 60),  (11.51, 55), (11.36, 50), (11.21, 45),
    (11.06, 40),  (10.90, 35), (10.74, 30), (10.58, 25),
    (10.40, 20),  (10.20, 15), (10.00, 10), ( 9.80,  5),
]
TABLA_SOC_LIFEPO4 = [
    (13.60, 100), (13.40, 95), (13.30, 90), (13.20, 80),
    (13.10, 70),  (13.00, 60), (12.90, 50), (12.80, 40),
    (12.70, 30),  (12.60, 20), (12.50, 10), (12.00,  5),
]

# ─────────────────────────────────────────────────────────────
# CATÁLOGO DE CARGADORES
#
# Cada clave coincide con gev.camaras.tipo_cargador.
# El programa lee la clave de la BD y busca aquí sus propiedades
# físicas, sin necesidad de tocar el código Python cuando se añade
# una nueva maleta.
# ─────────────────────────────────────────────────────────────
CARGADORES_CONFIG: dict[str, dict] = {
    "genasun_gv10_lifepo4_mppt": {
        "bat_type":                 "lifepo4",
        "eficiencia":               0.93,    # MPPT: 93-99 %, valor conservador
        "v_umbral_carga":           13.8,    # absorción Genasun empieza ~13.8V → 14.2V
        "profundidad_descarga_max": 0.80,    # LiFePO4 soporta 80 % de DoD
        "sin_panel":                False,
        "descripcion":              "Genasun GV-10 LiFePO4 14.2V MPPT",
    },
    "victron_bluesolar_pwm_20a": {
        "bat_type":                 "lead",
        "eficiencia":               0.80,    # PWM: 75-85 %, valor conservador
        "v_umbral_carga":           13.1,    # bulk Victron PWM empieza ~12.8-13.1V
        "profundidad_descarga_max": 0.50,    # plomo: máx 50 % de DoD para no degradar
        "sin_panel":                False,
        "descripcion":              "Victron BlueSolar PWM 12/24V 20A",
    },
    # ── Instalaciones SIN panel solar ─────────────────────────────
    # La batería se carga con cargador de red 220V mientras los
    # trabajadores están en la plataforma. 'sin_panel' activa un modo
    # alternativo: se asume recarga diaria completa en vez de predecir
    # generación solar. 'v_umbral_carga' detecta si el cargador 220V
    # está activo para ignorar esas lecturas de voltaje en el SoC.
    "cargador_red_220v_lifepo4": {
        "bat_type":                 "lifepo4",
        "eficiencia":               1.0,     # no aplica (sin panel)
        "v_umbral_carga":           13.8,
        "profundidad_descarga_max": 0.80,
        "sin_panel":                True,
        "descripcion":              "Cargador red 220V — batería LiFePO4",
    },
    "cargador_red_220v_lead": {
        "bat_type":                 "lead",
        "eficiencia":               1.0,
        "v_umbral_carga":           13.1,
        "profundidad_descarga_max": 0.50,
        "sin_panel":                True,
        "descripcion":              "Cargador red 220V — batería plomo/GEL",
    },
}

# Fallbacks globales para maletas sin tipo_cargador en la BD
EFICIENCIA_PANEL_FALLBACK    = 0.88
UMBRAL_CARGA_LEAD            = 13.1
UMBRAL_CARGA_LIFEPO4         = 14.0
PROFUNDIDAD_DESCARGA_LEAD    = 0.50
PROFUNDIDAD_DESCARGA_LIFEPO4 = 0.80

HORA_SOL_INICIO_FALLBACK = 7
HORA_SOL_FIN_FALLBACK    = 20
HORAS_SOL_PICO_BASE      = 4.5  # h/día — España costera, valor conservador

# Peso (0-1) de una lectura nueva frente al histórico filtrado
PESO_LECTURA_NUEVA = 0.25

# Peso (0-1) de un nuevo día de calibración frente al factor acumulado.
# Más bajo que PESO_LECTURA_NUEVA: promediamos 1 dato/día (menos frecuente)
# y queremos estabilidad ante días excepcionales.
PESO_CALIBRACION_NUEVA = 0.20

# Tiempo (s) durante el que se reutiliza una previsión meteo sin volver
# a llamar a la API. La previsión a 3 días no cambia minuto a minuto.
CACHE_PREVISION_SEGUNDOS = 3600  # 1 hora

# ─────────────────────────────────────────────────────────────
# ESTADO EN MEMORIA (persistido entre ciclos dentro del proceso)
# ─────────────────────────────────────────────────────────────

# Último voltaje filtrado (EMA) de cada cámara.
# Se resiembra desde gev.bateria_estado.bat_actual al arrancar.
_ultimo_voltaje_filtrado: dict[str, float] = {}

# Previsión meteo cacheada por instalación: {instalacion: (ts, prevision)}
_cache_prevision: dict[str, tuple] = {}

# Estado de calibración diaria por cámara.
# {cam_name: {"fecha": date, "soc_inicio": float|None,
#              "gen_esp_wh": float, "cons_esp_wh": float, "calibrado": bool}}
_estado_calibracion: dict[str, dict] = {}

# Acumulador de carga 220V por cámara.
# {cam_name: {"fecha": date, "horas": float,
#              "ts_prev": datetime, "carg_prev": bool}}
# Se resiembra desde gev.bateria_estado.horas_cargando_hoy al arrancar.
_estado_220v: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("datos_tiempo.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# Log de detalle por cámara — buffer circular en disco
LOG_DIR        = "logs_instalaciones"
MAX_LINEAS_LOG = 1000


# ═════════════════════════════════════════════════════════════
# 1. LECTURA DE VOLTAJE DESDE EL ROUTER (RUT955/956)
# ═════════════════════════════════════════════════════════════

def leer_voltaje_rut(cam: dict) -> float | None:
    """
    Lee el voltaje de la batería construyendo la petición correcta
    según rut_tipo (columna de gev.camaras):

      'cgi_input'    GET /cgi-bin/input?username=admin&password=X&pin=Y
                     Firmware antiguo, IO = 'an' habitualmente

      'cgi_lo_value' GET /cgi-bin/lo_value?username=admin&password=X&pin=Y
                     Firmware intermedio (ej. cam3_site_e), IO = 'adc0'

      'api'          POST /api/login → GET /api/io/{io}/status
                     Firmware reciente / RUT956, IO = 'adc0'

    Si Teltonika cambia la ruta en un futuro firmware, solo se
    modifica el helper correspondiente aquí — la BD no cambia.
    """
    cam_name = cam["cam_name"]
    host     = cam.get("rut_host")
    port     = cam.get("rut_port", 90)
    passwd   = cam.get("rut_pass", "")
    io       = cam.get("rut_io", "an")
    tipo     = cam.get("rut_tipo", "cgi_input")

    if not host:
        log.warning(f"[{cam_name}] rut_host vacío — sin lectura")
        return None
    if not passwd or passwd == "CAMBIAR":
        log.warning(f"[{cam_name}] rut_pass no configurada — sin lectura")
        return None

    if tipo in ("cgi_input", "cgi_lo_value"):
        cgi_path = "/cgi-bin/input" if tipo == "cgi_input" else "/cgi-bin/lo_value"
        return _leer_voltaje_cgi(host, port, cgi_path, passwd, io, cam_name)
    elif tipo == "api":
        return _leer_voltaje_api(cam_name, host, port, io, passwd)
    else:
        log.warning(f"[{cam_name}] rut_tipo desconocido: '{tipo}'")
        return None


def _leer_voltaje_cgi(host: str, port, path: str,
                      passwd: str, io: str, cam_name: str) -> float | None:
    """CGI directo — firmware antiguo e intermedio. 2 reintentos.

    El CGI de Teltonika exige las credenciales en la query string. Por eso
    NUNCA se registra la URL ni el texto de la excepción (las de `requests`
    incluyen la URL completa con la contraseña): solo el tipo de error.
    """
    url = f"http://{host}:{port}{path}"
    params = {"username": "admin", "password": passwd, "pin": io}
    for intento in range(2):
        try:
            r = requests.get(url, params=params, timeout=8)
            if r.status_code != 200:
                log.warning(f"[{cam_name}] HTTP {r.status_code} CGI (intento {intento+1})")
                time.sleep(3)
                continue
            v = float(r.text.strip())
            if v >= 10000:
                v = v / 1000.0
            return round(v, 1)
        except Exception as e:
            if intento < 1:
                log.debug(f"[{cam_name}] Reintento CGI: {type(e).__name__}")
                time.sleep(3)
            else:
                log.warning(f"[{cam_name}] Sin lectura CGI tras 2 intentos ({host}): {type(e).__name__}")
    return None


def _leer_voltaje_api(cam_name: str, host: str, port,
                      io: str, passwd: str) -> float | None:
    """REST API Teltonika — firmware reciente / RUT956. 2 reintentos en login."""
    base  = f"http://{host}:{port}/api"
    token = None
    for intento in range(2):
        try:
            r = requests.post(
                f"{base}/login",
                json={"username": "admin", "password": passwd},
                timeout=8,
            )
            r.raise_for_status()
            token = r.json().get("data", {}).get("token")
            if token:
                break
            log.warning(f"[{cam_name}] Login API sin token ({host}), intento {intento+1}")
            time.sleep(3)
        except Exception as e:
            if intento < 1:
                log.debug(f"[{cam_name}] Reintento login API: {type(e).__name__}")
                time.sleep(3)
            else:
                log.warning(f"[{cam_name}] Error login API ({host}) tras 2 intentos: {type(e).__name__}")
                return None
    if not token:
        return None

    try:
        r = requests.get(
            f"{base}/io/{io}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if r.status_code != 200:
            log.warning(f"[{cam_name}] HTTP {r.status_code} IO {io} ({host})")
            return None
        _data = r.json().get("data", {})
        # Teltonika devuelve "value" en firmware antiguo y "custom_value"
        # en firmware reciente (RUT956 / RUTOS 7.x).  Probamos los dos.
        valor = _data.get("value")
        if valor is None:
            valor = _data.get("custom_value")
            if valor is not None:
                log.debug(f"[{cam_name}] Usando 'custom_value' ({host})")
        if valor is None:
            log.warning(
                f"[{cam_name}] API no devolvió ni 'value' ni 'custom_value' "
                f"para IO {io} en {host}. Claves disponibles: {list(_data.keys())}"
            )
            return None
        v = float(valor)
        # La API Teltonika devuelve mV como entero en algunos firmwares
        if v >= 10000:
            v = v / 1000.0
        if not (8.0 <= v <= 17.0):
            log.warning(f"[{cam_name}] Voltaje API fuera de rango: {v:.3f}V ({host}) — descartado")
            return None
        return round(v, 1)
    except Exception as e:
        log.warning(f"[{cam_name}] Error IO {io} de {host}: {e}")
        return None


# ═════════════════════════════════════════════════════════════
# 2. ESTIMACIÓN SOC
# ═════════════════════════════════════════════════════════════

def voltaje_a_soc(v: float, bat_type: str = "lead") -> float:
    tabla = TABLA_SOC_LEAD if bat_type == "lead" else TABLA_SOC_LIFEPO4
    if v >= tabla[0][0]:
        return 100.0
    if v <= tabla[-1][0]:
        return float(tabla[-1][1])
    for i in range(len(tabla) - 1):
        v_hi, s_hi = tabla[i]
        v_lo, s_lo = tabla[i + 1]
        if v_lo <= v <= v_hi:
            return s_lo + (v - v_lo) / (v_hi - v_lo) * (s_hi - s_lo)
    return 0.0


def esta_cargando(v: float, hora_decimal: float, v_umbral_carga: float,
                  amanecer: float | None, atardecer: float | None) -> bool:
    """
    True si, a esta hora y con este voltaje, lo más probable es que el
    regulador esté metiendo carga (lectura del RUT955 no fiable para SoC).
    Válido tanto para carga solar (MPPT/PWM) como para cargador 220V.
    v_umbral_carga viene de CARGADORES_CONFIG para el cargador específico
    de esa maleta.
    """
    if amanecer is None or atardecer is None:
        amanecer, atardecer = HORA_SOL_INICIO_FALLBACK, HORA_SOL_FIN_FALLBACK
    return (amanecer <= hora_decimal <= atardecer) and (v > v_umbral_carga)


def suavizar_voltaje(cam_name: str, v: float) -> float:
    """
    Filtro de media móvil exponencial (EMA): combina la lectura nueva
    con el histórico ya filtrado, para no dar bandazos por una lectura
    puntual ruidosa. PESO_LECTURA_NUEVA controla cuánto pesa el dato nuevo.
    """
    if cam_name not in _ultimo_voltaje_filtrado:
        _ultimo_voltaje_filtrado[cam_name] = v
        return v
    _ultimo_voltaje_filtrado[cam_name] = (
        PESO_LECTURA_NUEVA * v
        + (1 - PESO_LECTURA_NUEVA) * _ultimo_voltaje_filtrado[cam_name]
    )
    return _ultimo_voltaje_filtrado[cam_name]


def estimar_soc(
    cam_name: str, v_bruto: float, bat_type: str = "lead",
    v_umbral_carga: float = UMBRAL_CARGA_LEAD,
    amanecer: float | None = None,
    atardecer: float | None = None,
) -> tuple[float, float, str, bool]:
    """
    Retorna (voltaje_filtrado, soc_pct, nota, cargando).

    Si detecta que el regulador está activo (v > umbral en horas de sol),
    usa el último valor ya filtrado en lugar del pico de voltaje actual.
    v_umbral_carga debe ser el del cargador real de esa maleta.
    """
    ahora        = datetime.now(tz=TZ)
    hora_decimal = ahora.hour + ahora.minute / 60.0
    cargando     = esta_cargando(v_bruto, hora_decimal, v_umbral_carga, amanecer, atardecer)

    if cargando and cam_name in _ultimo_voltaje_filtrado:
        # Mantener el valor filtrado anterior: el pico de carga no refleja el SoC real
        v_filtrado = _ultimo_voltaje_filtrado[cam_name]
        nota = f"carga_ignorada(bruto={v_bruto:.2f}V)→filtro_anterior"
    else:
        v_filtrado = suavizar_voltaje(cam_name, v_bruto)
        nota = "reposo_fiable" if not cargando else "sin_filtro_previo"

    soc = voltaje_a_soc(v_filtrado, bat_type)
    return round(v_filtrado, 3), round(soc, 1), nota, cargando


# ═════════════════════════════════════════════════════════════
# 3. PREVISIÓN METEOROLÓGICA
# ═════════════════════════════════════════════════════════════

def obtener_prevision(lat: float, lon: float, instalacion: str) -> dict | None:
    params_marina = {
        "latitude": lat, "longitude": lon,
        "hourly": "wave_height",
        "timezone": "Europe/Madrid",
        "forecast_days": 4,
    }
    params_tiempo = {
        "latitude": lat, "longitude": lon,
        "hourly": "wind_speed_10m,cloud_cover",
        "daily": "shortwave_radiation_sum,sunrise,sunset",
        "timezone": "Europe/Madrid",
        "forecast_days": 4,
    }
    try:
        r_mar = requests.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params=params_marina, timeout=10,
        ).json()
        r_met = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params_tiempo, timeout=10,
        ).json()
    except Exception as e:
        log.error(f"[{instalacion}] Error API tiempo: {e}")
        return None

    horas  = r_mar["hourly"]["time"]
    olas   = r_mar["hourly"]["wave_height"]
    viento = r_met["hourly"]["wind_speed_10m"]
    nubes  = r_met["hourly"]["cloud_cover"]
    rad_d  = r_met.get("daily", {}).get("shortwave_radiation_sum", [])
    dias_d = r_met.get("daily", {}).get("time", [])

    por_dia: dict[str, dict] = {}
    for i, h in enumerate(horas):
        f = h[:10]
        if f not in por_dia:
            por_dia[f] = {"olas": [], "viento": [], "nubes": []}
        if olas[i]   is not None: por_dia[f]["olas"].append(olas[i])
        if viento[i] is not None: por_dia[f]["viento"].append(viento[i])
        if nubes[i]  is not None: por_dia[f]["nubes"].append(nubes[i])

    rad_por_dia = {d: v for d, v in zip(dias_d, rad_d) if v is not None}

    def media(lst): return round(sum(lst) / len(lst), 2) if lst else None
    def maximo(lst): return round(max(lst), 2) if lst else None

    resultado = {}
    for idx, key in enumerate(["hoy", "manana", "pasado_manana"]):
        if idx < len(sorted(por_dia)):
            f = sorted(por_dia)[idx]
            d = por_dia[f]
            resultado[key] = {
                "fecha":        f,
                "olas_media":   media(d["olas"]),
                "viento_media": media(d["viento"]),
                "nubes_media":  media(d["nubes"]),
                "rad_mj":       rad_por_dia.get(f),
            }

    # Valor instantáneo más cercano a ahora
    hora_str = datetime.now(tz=TZ).strftime("%Y-%m-%dT%H:00")
    idx_now  = next((i for i, h in enumerate(horas) if h >= hora_str), len(horas) - 1)
    resultado["ahora"] = {
        "olas":   olas[idx_now],
        "viento": viento[idx_now],
        "nubes":  nubes[idx_now],
    }

    def _hora_decimal(iso_str: str | None) -> float | None:
        if not iso_str or len(iso_str) < 16:
            return None
        h, m = iso_str[11:16].split(":")
        return int(h) + int(m) / 60.0

    sunrises = r_met.get("daily", {}).get("sunrise", [])
    sunsets  = r_met.get("daily", {}).get("sunset",  [])
    resultado["sol"] = {
        "amanecer":  _hora_decimal(sunrises[0]) if sunrises else None,
        "atardecer": _hora_decimal(sunsets[0])  if sunsets  else None,
    }
    return resultado


def obtener_prevision_cacheada(lat: float, lon: float, instalacion: str) -> dict | None:
    """
    Igual que obtener_prevision(), pero reutiliza la última respuesta
    válida durante CACHE_PREVISION_SEGUNDOS en lugar de llamar a la API
    en cada ciclo de lectura de voltaje (que puede ser cada 1-5 min).
    Si la API falla y hay una previsión previa en caché, se reutiliza.
    """
    ahora    = time.time()
    en_cache = _cache_prevision.get(instalacion)
    if en_cache and (ahora - en_cache[0]) < CACHE_PREVISION_SEGUNDOS:
        return en_cache[1]

    prevision = obtener_prevision(lat, lon, instalacion)
    if prevision:
        _cache_prevision[instalacion] = (ahora, prevision)
        return prevision

    if en_cache:
        log.warning(f"[{instalacion}] Previsión meteo falló, reutilizando la última válida")
        return en_cache[1]
    return None


# ═════════════════════════════════════════════════════════════
# 4. ENERGÍA
# ═════════════════════════════════════════════════════════════

def correccion_orientacion(azimut_deg: float | None, inclinacion_deg: float | None,
                            lat_deg: float) -> float:
    """
    Factor de corrección (0–1) del rendimiento real del panel respecto al
    rendimiento óptimo (sur geográfico, inclinación = latitud - 5°).

    Si alguno de los dos está a NULL en la BD, retorna 1.0.
    Válido para latitudes 35–43°N (España peninsular y Baleares).
    """
    if azimut_deg is None or inclinacion_deg is None:
        return 1.0

    inclinacion_optima = max(lat_deg - 5.0, 25.0)
    azimut_optimo      = 180.0

    delta_incl = abs(float(inclinacion_deg) - inclinacion_optima)
    delta_azim = min(abs(float(azimut_deg) - azimut_optimo),
                     360.0 - abs(float(azimut_deg) - azimut_optimo))

    penalizacion = delta_azim * 0.001 + delta_incl * 0.0008
    return round(max(1.0 - penalizacion, 0.40), 3)


def clasificar_alerta_bat(soc: float) -> str:
    """
    Semáforo de tres niveles:
      'critico': SoC < 20% — batería en zona de daño o apagado inminente
      'bajo':    SoC < 35% — el sistema ya debería estar reduciendo tours
      'ok':      SoC ≥ 35%
    """
    if soc < 20:
        return "critico"
    if soc < 35:
        return "bajo"
    return "ok"


def intentar_registrar_inicio_dia(conn, cam_name: str, soc: float,
                                   gen_esp_wh: float, cons_esp_wh: float,
                                   hora_decimal: float, amanecer: float) -> bool:
    """
    Guarda el SoC ANTES de que el sol empiece a cargar la batería.
    Es el punto de referencia para calcular al final del día cuánto
    generó realmente el panel.

    Persiste soc_amanecer en gev.bateria_estado para que sobreviva
    reinicios del proceso y sea visible en Workbench.

    Solo actúa si:
      - Aún no hemos registrado el inicio para hoy en esta cámara
      - La hora actual es antes de amanecer + 1h
    """
    hoy    = datetime.now(tz=TZ).date()
    estado = _estado_calibracion.get(cam_name, {})

    if estado.get("fecha") == hoy and estado.get("soc_inicio") is not None:
        return False
    if hora_decimal > amanecer + 1.0:
        return False

    _estado_calibracion[cam_name] = {
        "fecha":       hoy,
        "soc_inicio":  soc,
        "gen_esp_wh":  gen_esp_wh,
        "cons_esp_wh": cons_esp_wh,
        "calibrado":   False,
    }

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE gev.bateria_estado SET soc_amanecer = %s WHERE cam_name = %s",
            (round(soc, 1), cam_name),
        )
        conn.commit()
        cur.close()
    except Exception as e:
        log.warning(f"  [{cam_name}] No se pudo guardar soc_amanecer en BD: {e}")

    log.info(f"  [{cam_name}] 🌅 SoC inicio día solar: {soc:.0f}%  "
             f"(gen_esp={gen_esp_wh:.0f}Wh)")
    return True


def intentar_calibrar_fin_dia(conn, cam_name: str, soc_fin: float,
                               cfg: dict, hora_decimal: float,
                               atardecer: float) -> float | None:
    """
    Al final del día solar (atardecer + 1.5h), calcula la generación real:

        gen_real = ΔSoC_Wh + consumo_dia_Wh

    La compara con gen_esp (lo que el modelo predijo por la mañana) y
    actualiza factor_calibracion en gev.camaras como media móvil exponencial.

    La intuición: si un día soleado con 0% nubes el panel solo genera el
    70% de lo esperado, probablemente la orientación o el sombreado son
    peores de lo que indica la geometría → el factor baja hacia ~0.70 y
    las predicciones futuras se corrigen automáticamente.

    Retorna el nuevo factor_calibracion, o None si no hay datos suficientes.
    """
    if hora_decimal < atardecer + 1.5:
        return None

    hoy    = datetime.now(tz=TZ).date()
    estado = _estado_calibracion.get(cam_name, {})
    if estado.get("fecha") != hoy:
        return None
    if estado.get("calibrado"):
        return None
    if estado.get("soc_inicio") is None:
        return None

    soc_inicio  = estado["soc_inicio"]
    gen_esp_wh  = estado["gen_esp_wh"]
    cons_esp_wh = estado["cons_esp_wh"]

    if gen_esp_wh <= 0:
        _estado_calibracion[cam_name]["calibrado"] = True
        return None

    prof_descarga = cfg.get("profundidad_descarga_max", PROFUNDIDAD_DESCARGA_LEAD)
    bat_wh_usable = cfg["bat_ah"] * 12.0 * prof_descarga

    delta_soc_wh = (soc_fin - soc_inicio) / 100.0 * bat_wh_usable
    gen_real_wh  = delta_soc_wh + cons_esp_wh
    factor_hoy   = max(0.15, min(gen_real_wh / gen_esp_wh, 2.5))

    # Leer el factor acumulado de gev.camaras (no de bateria_estado)
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT factor_calibracion FROM gev.camaras WHERE cam_name = %s",
        (cam_name,),
    )
    row = cur.fetchone()
    cur.close()

    factor_bd = row["factor_calibracion"] if row and row["factor_calibracion"] is not None else None

    if factor_bd is None:
        nuevo_factor = round(factor_hoy, 3)
    else:
        nuevo_factor = round(
            PESO_CALIBRACION_NUEVA * factor_hoy
            + (1 - PESO_CALIBRACION_NUEVA) * factor_bd,
            3,
        )

    # Actualizar en gev.camaras (el operario no debe tocar este campo después)
    cur = conn.cursor()
    cur.execute(
        "UPDATE gev.camaras SET factor_calibracion = %s WHERE cam_name = %s",
        (nuevo_factor, cam_name),
    )
    conn.commit()
    cur.close()

    _estado_calibracion[cam_name]["calibrado"] = True

    log.info(
        f"  [{cam_name}] 📊 Calibración diaria: "
        f"SoC {soc_inicio:.0f}%→{soc_fin:.0f}% | "
        f"gen_real={gen_real_wh:.0f}Wh vs esperada={gen_esp_wh:.0f}Wh | "
        f"factor_hoy={factor_hoy:.2f} → factor_acumulado={nuevo_factor:.3f}"
    )
    return nuevo_factor


def gen_panel_dia(panel_w: float, nubes_pct: float, rad_mj: float | None,
                  eficiencia: float,
                  factor_orientacion: float = 1.0,
                  factor_calibracion: float = 1.0) -> float:
    """
    Energía generada estimada en un día (Wh), con tres capas:

    1. Base física: radiación solar × panel × eficiencia del cargador
    2. Corrección de orientación: pérdida por no apuntar al sur óptimo
    3. Factor de calibración empírico: gen_real/gen_esperada histórico,
       actualizado automáticamente cada noche.
    """
    if rad_mj:
        base = (rad_mj * 1000.0 / 3.6) * (panel_w / 1000.0) * eficiencia
    else:
        factor_nubes = 1.0 - (min(nubes_pct, 100.0) / 100.0 * 0.75)
        base = panel_w * HORAS_SOL_PICO_BASE * factor_nubes * eficiencia

    return round(base * factor_orientacion * factor_calibracion, 1)


def consumo_dia(cfg: dict) -> dict:
    hora_ini, hora_fin = cfg["hora_inicio_tours"], cfg["hora_fin_tours"]
    horas_vigil = (24 - hora_ini + hora_fin) if hora_fin < hora_ini else (hora_fin - hora_ini)
    freq_min    = cfg.get("freq_actual_min", cfg["freq_base_min"])
    tours       = int((horas_vigil * 60) / freq_min)
    Wh_router   = cfg["consumo_router_w"] * 24
    Wh_camaras  = cfg["consumo_cam_w"] * (tours * cfg["duracion_tour_min"] / 60.0)
    return {"Wh_total": round(Wh_router + Wh_camaras, 1), "tours": tours}


def presupuesto_3d(cfg: dict, prevision: dict, soc: float) -> dict:
    """
    Balance energético a 3 días.

    Modo SOLAR (sin_panel=False, panel_w>0):
      Aplica los tres factores de corrección: eficiencia cargador,
      orientación del panel y calibración empírica diaria.

    Modo RED 220V (sin_panel=True o panel_w==0):
      No hay generación solar. Se asume que los trabajadores recargan
      la batería completamente cada mañana.
      El presupuesto solo informa del consumo nocturno.
    """
    prof_descarga = cfg.get("profundidad_descarga_max", PROFUNDIDAD_DESCARGA_LEAD)
    bat_wh_usable = cfg["bat_ah"] * 12.0 * prof_descarga
    sin_panel     = cfg.get("sin_panel", False) or (cfg.get("panel_w", 0) == 0)

    if sin_panel:
        cons = consumo_dia(cfg)["Wh_total"]
        dias = {k: {"gen_Wh": 0, "cons_Wh": cons, "delta_Wh": round(-cons, 1)}
                for k in ("hoy", "manana", "pasado_manana")}
        return {
            "dias":           dias,
            "delta_3dias_Wh": round(-cons * 3, 1),
            "bat_wh_actual":  round(bat_wh_usable * soc / 100, 1),
            "recarga_red":    True,
        }

    eficiencia         = cfg.get("eficiencia_cargador",  EFICIENCIA_PANEL_FALLBACK)
    factor_orientacion = cfg.get("factor_orientacion",   1.0)
    factor_calibracion = cfg.get("factor_calibracion",   1.0)

    dias = {}
    for key in ("hoy", "manana", "pasado_manana"):
        d     = prevision.get(key, {})
        nubes = d.get("nubes_media") or 50.0
        gen   = gen_panel_dia(cfg["panel_w"], nubes, d.get("rad_mj"),
                              eficiencia, factor_orientacion, factor_calibracion)
        cons  = consumo_dia(cfg)["Wh_total"]
        dias[key] = {"gen_Wh": gen, "cons_Wh": cons, "delta_Wh": round(gen - cons, 1)}

    delta_3d = sum(d["delta_Wh"] for d in dias.values())
    return {
        "dias":           dias,
        "delta_3dias_Wh": round(delta_3d, 1),
        "bat_wh_actual":  round(bat_wh_usable * soc / 100, 1),
        "recarga_red":    False,
    }


# ═════════════════════════════════════════════════════════════
# 5. FRECUENCIA DE TOURS
# ═════════════════════════════════════════════════════════════

def calcular_frecuencia(cfg: dict, soc: float, presup: dict) -> int:
    freq_base = cfg["freq_base_min"]

    # ── Instalaciones con recarga 220V (sin panel solar) ──────────
    # Solo miramos el SoC actual: cuánto queda de la carga de esta
    # mañana para llegar hasta mañana.
    if presup.get("recarga_red"):
        if   soc >= 65: factor = 1.0
        elif soc >= 45: factor = 1.5
        elif soc >= 30: factor = 2.0
        else:           factor = 3.5
        return max(freq_base, min(int(freq_base * factor), 240))

    # ── Instalaciones solares ──────────────────────────────────────
    delta_3d     = presup["delta_3dias_Wh"]
    delta_manana = presup["dias"].get("manana", {}).get("delta_Wh", 0) or 0
    bonus_solar  = delta_manana > 100  # mañana se genera claramente más de lo que se gasta

    if   soc >= 75: factor = 1.0
    elif soc >= 60: factor = 1.0  if bonus_solar else 1.25
    elif soc >= 45: factor = 1.25 if bonus_solar else 1.5
    elif soc >= 30: factor = 1.5  if bonus_solar else 2.0
    else:           factor = 3.0

    if delta_3d < -300 and soc < 60:
        factor = min(factor * 1.25, 4.0)

    return max(freq_base, min(int(freq_base * factor), 240))


# ═════════════════════════════════════════════════════════════
# 6. CARGA 220V — ACUMULADOR DIARIO
# ═════════════════════════════════════════════════════════════

def actualizar_carga_220v(
    cam_name: str, cargando_ahora: bool,
    cargador_a: int | None,
) -> tuple[float, float]:
    """
    Acumula las horas que el cargador 220V ha estado activo hoy.
    Retorna (horas_cargando_hoy, carga_hoy_wh).
    Se resetea automáticamente a medianoche.

    El acumulador se siembra desde gev.bateria_estado al arrancar el
    proceso (ver ciclo()), para no perder el histórico tras un reinicio.
    """
    hoy   = datetime.now(tz=TZ).date()
    ahora = datetime.now(tz=TZ)

    estado = _estado_220v.get(cam_name)
    if estado is None or estado["fecha"] != hoy:
        # Nuevo día o primera vez: empezar desde cero
        _estado_220v[cam_name] = {
            "fecha":     hoy,
            "horas":     0.0,
            "ts_prev":   ahora,
            "carg_prev": cargando_ahora,
        }
        estado = _estado_220v[cam_name]
        return 0.0, 0.0

    ts_prev   = estado["ts_prev"]
    carg_prev = estado["carg_prev"]
    delta_h   = (ahora - ts_prev).total_seconds() / 3600.0

    # Solo acumular si estaba cargando en el ciclo anterior Y en este
    if carg_prev and cargando_ahora:
        estado["horas"] += delta_h

    estado["ts_prev"]   = ahora
    estado["carg_prev"] = cargando_ahora

    horas = estado["horas"]
    a     = cargador_a or 10   # amperaje nominal si falta el dato
    wh    = round(horas * a * 12.0, 1)
    return round(horas, 3), wh


# ═════════════════════════════════════════════════════════════
# 7. BASE DE DATOS — GEV
# ═════════════════════════════════════════════════════════════

def get_conn():
    if not DB_CONFIG['password']:
        raise RuntimeError(
            "Falta GEV_DB_PASSWORD. Define la variable de entorno antes de arrancar el recolector."
        )
    return mysql.connector.connect(**DB_CONFIG)


def leer_camaras(conn) -> list[dict]:
    """
    Lee todas las cámaras activas con rut_host configurado,
    uniendo gev.camaras + gev.instalaciones + gev.bateria_estado.

    inst_lat / inst_lon: coordenadas del centroide de la instalación
    (fuente de verdad para la API meteo; fallback en INSTALACIONES_CONFIG).

    """
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            c.cam_name,
            c.instalacion,
            c.rut_host, c.rut_port, c.rut_pass, c.rut_io, c.rut_tipo,
            c.bat_type, c.tipo_cargador, c.modelo_camara,
            c.panel_w, c.bat_ah, c.consumo_cam_w,
            c.panel_azimut, c.panel_inclinacion, c.factor_calibracion,
            c.cargador_a,
            i.lat  AS inst_lat,
            i.lon  AS inst_lon,
            e.bat_actual,
            e.bat_antes_rutina_tours,
            e.bat_despues_rutina_tours,
            e.horas_cargando_hoy,
            e.soc_amanecer
        FROM gev.camaras c
        JOIN gev.instalaciones i USING (instalacion)
        LEFT JOIN gev.bateria_estado e USING (cam_name)
        WHERE c.activada = 1
          AND c.rut_host IS NOT NULL
          AND c.rut_host != ''
        ORDER BY c.instalacion, c.cam_name
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def upsert(conn, datos: dict):
    """
    Inserta o actualiza el estado operacional en gev.bateria_estado.
    Solo escribe campos de estado — los campos de configuración
    (panel_w, bat_ah, bat_type, tipo_cargador…) son exclusivos del
    operario y residen en gev.camaras.
    """
    sql = """
        INSERT INTO gev.bateria_estado (
            cam_name,
            bat_actual,
            soc_pct,
            alerta_bat,
            cargando_ahora,
            horas_cargando_hoy,
            carga_hoy_wh,
            nubes_actual,     nubes_manana,     nubes_pasado_manana,
            olas_actual,      olas_manana,      olas_pasado_manana,
            viento_actual,    viento_manana,    viento_pasado_manana,
            frecuencia_tours,
            gen_hoy_wh,
            cons_hoy_wh,
            factor_orientacion,
            descripcion_cargador
        ) VALUES (
            %(cam_name)s,
            %(bat_actual)s,
            %(soc_pct)s,
            %(alerta_bat)s,
            %(cargando_ahora)s,
            %(horas_cargando_hoy)s,
            %(carga_hoy_wh)s,
            %(nubes_actual)s,     %(nubes_manana)s,     %(nubes_pasado_manana)s,
            %(olas_actual)s,      %(olas_manana)s,      %(olas_pasado_manana)s,
            %(viento_actual)s,    %(viento_manana)s,    %(viento_pasado_manana)s,
            %(frecuencia_tours)s,
            %(gen_hoy_wh)s,
            %(cons_hoy_wh)s,
            %(factor_orientacion)s,
            %(descripcion_cargador)s
        )
        ON DUPLICATE KEY UPDATE
            bat_actual           = VALUES(bat_actual),
            soc_pct              = VALUES(soc_pct),
            alerta_bat           = VALUES(alerta_bat),
            cargando_ahora       = VALUES(cargando_ahora),
            horas_cargando_hoy   = VALUES(horas_cargando_hoy),
            carga_hoy_wh         = VALUES(carga_hoy_wh),
            nubes_actual         = VALUES(nubes_actual),
            nubes_manana         = VALUES(nubes_manana),
            nubes_pasado_manana  = VALUES(nubes_pasado_manana),
            olas_actual          = VALUES(olas_actual),
            olas_manana          = VALUES(olas_manana),
            olas_pasado_manana   = VALUES(olas_pasado_manana),
            viento_actual        = VALUES(viento_actual),
            viento_manana        = VALUES(viento_manana),
            viento_pasado_manana = VALUES(viento_pasado_manana),
            frecuencia_tours     = VALUES(frecuencia_tours),
            gen_hoy_wh           = VALUES(gen_hoy_wh),
            cons_hoy_wh          = VALUES(cons_hoy_wh),
            factor_orientacion   = VALUES(factor_orientacion),
            descripcion_cargador = VALUES(descripcion_cargador)
            -- NO actualizados aquí (escribe solo el operario en gev.camaras):
            --   panel_w, bat_ah, bat_type, tipo_cargador, cargador_a,
            --   panel_azimut, panel_inclinacion
            -- NO actualizados aquí (tienen sus propias funciones):
            --   factor_calibracion → intentar_calibrar_fin_dia() en gev.camaras
            --   soc_amanecer       → intentar_registrar_inicio_dia()
            --   bat_antes_rutina_tours / bat_despues_rutina_tours → sistema de tours
    """
    cur = conn.cursor()
    cur.execute(sql, datos)
    conn.commit()
    cur.close()


def inicializar_bd(conn):
    """
    Limpia registros antiguos de gev.bat_energia_log.
    Se llama UNA SOLA VEZ al arranque, no en cada ciclo.

    Las tablas las crea el operario ejecutando gev_crear_tablas.sql
    y gev_insertar_datos.sql — el programa no crea ni altera esquema.
    """
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM gev.bat_energia_log
        WHERE ts < NOW() - INTERVAL 6 MONTH
    """)
    borradas = cur.rowcount
    if borradas:
        log.info(f"[BD] Limpieza gev.bat_energia_log: {borradas} filas > 6 meses eliminadas")
    conn.commit()
    cur.close()


def guardar_log(conn, instalacion: str, cam_name: str, soc: float,
                v: float, gen: float, cons: float, freq: int,
                cargando: bool, nota: str):
    """Inserta una lectura en gev.bat_energia_log."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO gev.bat_energia_log
            (cam_name, instalacion, soc_pct, v_filtrado, gen_wh, cons_wh,
             freq, cargando, nota)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (cam_name, instalacion, soc, v, gen, cons,
          str(freq), int(cargando), nota))
    conn.commit()
    cur.close()


def registrar_lectura_camara(instalacion: str, cam_name: str, v_bruto: float,
                              v_filtrado: float, soc: float, gen: float,
                              cons: float, freq: int, cargando: bool,
                              nota: str) -> None:
    """
    Guarda una línea de detalle por cada lectura en
    logs_instalaciones/<cam_name>.txt. Buffer circular: al superar
    MAX_LINEAS_LOG líneas, las más antiguas se descartan.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ruta = os.path.join(LOG_DIR, f"{cam_name}.txt")

        linea = (
            f"{datetime.now(tz=TZ).strftime('%Y-%m-%d %H:%M:%S')} | "
            f"{instalacion} | v_bruto={v_bruto:.2f}V | "
            f"v_filt={v_filtrado:.2f}V | SoC={soc:.0f}% | "
            f"{'⚡cargando | ' if cargando else ''}"
            f"Gen={gen:.0f}Wh | Cons={cons:.0f}Wh | Freq={freq}min | {nota}\n"
        )

        lineas = []
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                lineas = f.readlines()

        lineas.append(linea)
        if len(lineas) > MAX_LINEAS_LOG:
            lineas = lineas[-MAX_LINEAS_LOG:]

        with open(ruta, "w", encoding="utf-8") as f:
            f.writelines(lineas)
    except Exception as e:
        log.warning(f"[{cam_name}] No se pudo escribir el log de detalle: {e}")


# ═════════════════════════════════════════════════════════════
# 8. CICLO PRINCIPAL
# ═════════════════════════════════════════════════════════════

def ciclo():
    log.info("=" * 60)
    log.info(f" Ciclo  {datetime.now(tz=TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    try:
        conn = get_conn()
    except Exception as e:
        log.error(f"Error BD: {e}")
        return

    try:
        camaras = leer_camaras(conn)
        if not camaras:
            log.warning("No hay cámaras activas con rut_host configurado en gev.camaras.")
            return

        # Agrupar por instalación para pedir el tiempo una vez por zona
        por_inst: dict[str, list] = {}
        for cam in camaras:
            inst = (cam["instalacion"] or "").lower().strip()
            por_inst.setdefault(inst, []).append(cam)

        for instalacion, cams in sorted(por_inst.items()):
            inst_cfg = INSTALACIONES_CONFIG.get(instalacion)
            if not inst_cfg:
                log.warning(f"[{instalacion}] Sin config en INSTALACIONES_CONFIG — omitiendo")
                continue

            # lat/lon: BD primero, fallback a INSTALACIONES_CONFIG.
            # float() necesario: MySQL DECIMAL(9,6) llega como decimal.Decimal
            # y Python no mezcla Decimal con float literales en aritmética.
            _raw_lat = cams[0].get("inst_lat") or inst_cfg.get("lat")
            _raw_lon = cams[0].get("inst_lon") or inst_cfg.get("lon")
            inst_lat = float(_raw_lat) if _raw_lat is not None else None
            inst_lon = float(_raw_lon) if _raw_lon is not None else None
            if not inst_lat or not inst_lon:
                log.warning(f"[{instalacion}] Sin coordenadas (BD y config vacíos) — omitiendo")
                continue

            log.info(f"\n{'─'*55}")
            log.info(f" {instalacion.upper()}  ({len(cams)} cámaras)")
            log.info(f"{'─'*55}")

            prevision = obtener_prevision_cacheada(inst_lat, inst_lon, instalacion)
            if not prevision:
                log.warning(f"  [{instalacion}] Sin previsión meteorológica — saltando instalación")
                continue

            p_now    = prevision.get("ahora",        {})
            p_man    = prevision.get("manana",        {})
            p_pasado = prevision.get("pasado_manana", {})
            p_sol    = prevision.get("sol",           {})

            log.info(f"  Tiempo: olas={p_now.get('olas')}m | "
                     f"viento={p_now.get('viento')}km/h | nubes={p_now.get('nubes')}% | "
                     f"sol {p_sol.get('amanecer')}h-{p_sol.get('atardecer')}h")

            for cam in cams:
                cam_name = cam["cam_name"]

                fisica = config_fisica_camara(instalacion, cam_name, fila_bd=cam)
                if not fisica:
                    log.warning(f"  [{cam_name}] Sin configuración física — omitiendo")
                    continue

                cfg            = {**inst_cfg, **fisica}
                bat_type       = cfg["bat_type"]
                v_umbral_carga = cfg["v_umbral_carga"]

                # ── Corrección de orientación (dato estático del operario en BD)
                factor_orientacion = correccion_orientacion(
                    cfg.get("panel_azimut"),
                    cfg.get("panel_inclinacion"),
                    inst_lat,
                )
                cfg["factor_orientacion"] = factor_orientacion

                # ── Factor de calibración empírico (actualizado cada noche)
                cfg["factor_calibracion"] = float(cfg.get("factor_calibracion") or 1.0)

                # ── Resembrar el filtro de voltaje desde BD tras reinicio del proceso
                if cam_name not in _ultimo_voltaje_filtrado and cam.get("bat_actual") is not None:
                    _ultimo_voltaje_filtrado[cam_name] = float(cam["bat_actual"])

                # ── Resembrar el acumulador 220V desde BD tras reinicio del proceso
                if cam_name not in _estado_220v and cam.get("horas_cargando_hoy") is not None:
                    hoy = datetime.now(tz=TZ).date()
                    _estado_220v[cam_name] = {
                        "fecha":     hoy,
                        "horas":     float(cam["horas_cargando_hoy"]),
                        "ts_prev":   datetime.now(tz=TZ),
                        "carg_prev": False,
                    }

                # ── Leer voltaje desde el RUT955/956
                v_bruto = leer_voltaje_rut(cam)
                if v_bruto is None:
                    log.warning(f"  [{cam_name}] Sin lectura de voltaje — saltando")
                    continue

                # ── Estimar SoC (retorna 4 valores)
                v_filtrado, soc, nota, cargando = estimar_soc(
                    cam_name, v_bruto, bat_type, v_umbral_carga,
                    p_sol.get("amanecer"), p_sol.get("atardecer"),
                )

                # ── Acumular carga 220V si procede
                horas_cargando, carga_hoy_wh = actualizar_carga_220v(
                    cam_name, cargando, cfg.get("cargador_a"),
                )

                # ── Presupuesto energético
                presup = presupuesto_3d(cfg, prevision, soc)
                freq   = calcular_frecuencia(cfg, soc, presup)

                gen_hoy  = presup["dias"].get("hoy", {}).get("gen_Wh",  0)
                cons_hoy = presup["dias"].get("hoy", {}).get("cons_Wh", 0)

                # ── Calibración diaria
                amanecer_h  = float(p_sol.get("amanecer") or HORA_SOL_INICIO_FALLBACK)
                atardecer_h = float(p_sol.get("atardecer") or HORA_SOL_FIN_FALLBACK)
                ahora       = datetime.now(tz=TZ)
                hora_dec    = ahora.hour + ahora.minute / 60.0

                if nota == "reposo_fiable":
                    intentar_registrar_inicio_dia(
                        conn, cam_name, soc, gen_hoy, cons_hoy, hora_dec, amanecer_h
                    )
                    nuevo_factor = intentar_calibrar_fin_dia(
                        conn, cam_name, soc, cfg, hora_dec, atardecer_h
                    )
                    if nuevo_factor is not None:
                        cfg["factor_calibracion"] = nuevo_factor

                alerta = clasificar_alerta_bat(soc)

                fila = {
                    "cam_name":           cam_name,
                    "bat_actual":         v_filtrado,
                    "soc_pct":            soc,
                    "alerta_bat":         alerta,
                    "cargando_ahora":     int(cargando),
                    "horas_cargando_hoy": horas_cargando,
                    "carga_hoy_wh":       carga_hoy_wh,
                    "nubes_actual":       int(p_now.get("nubes")           or 0),
                    "nubes_manana":       int(p_man.get("nubes_media")     or 0),
                    "nubes_pasado_manana":int(p_pasado.get("nubes_media")  or 0),
                    "olas_actual":        p_now.get("olas"),
                    "olas_manana":        p_man.get("olas_media"),
                    "olas_pasado_manana": p_pasado.get("olas_media"),
                    "viento_actual":      p_now.get("viento"),
                    "viento_manana":      p_man.get("viento_media"),
                    "viento_pasado_manana": p_pasado.get("viento_media"),
                    "frecuencia_tours":   str(freq),
                    "gen_hoy_wh":         gen_hoy,
                    "cons_hoy_wh":        cons_hoy,
                    "factor_orientacion": factor_orientacion,
                    "descripcion_cargador": cfg.get("descripcion_cargador"),
                }

                alerta_emoji = {"ok": "✅", "bajo": "🟡", "critico": "🔴"}.get(alerta, "")
                carg_emoji   = "⚡" if cargando else ""

                upsert(conn, fila)
                guardar_log(conn, instalacion, cam_name, soc, v_filtrado,
                            gen_hoy, cons_hoy, freq, cargando, nota)
                registrar_lectura_camara(instalacion, cam_name, v_bruto, v_filtrado,
                                          soc, gen_hoy, cons_hoy, freq, cargando, nota)

                log.info(
                    f"  [{cam_name}] {v_bruto:.2f}V→{v_filtrado:.2f}V | "
                    f"SoC={soc:.0f}% {alerta_emoji}{carg_emoji} | "
                    f"{cfg.get('descripcion_cargador','?')} | "
                    f"Panel={cfg['panel_w']}W({bat_type}) Bat={cfg['bat_ah']}Ah | "
                    f"f_orient={factor_orientacion:.2f} f_cal={cfg['factor_calibracion']:.2f} | "
                    f"Gen={gen_hoy:.0f}Wh Cons={cons_hoy:.0f}Wh Freq={freq}min | {nota}"
                )

    finally:
        conn.close()

    log.info("\n✅ Ciclo completado.\n")


# ═════════════════════════════════════════════════════════════
# 9. ENTRY POINT
# ═════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="GEV — Gestión Energética de Vigilancia")
    ap.add_argument("--una-vez",   action="store_true",
                    help="Ejecuta un solo ciclo y termina")
    ap.add_argument("--intervalo", type=int, default=60,
                    help="Segundos entre ciclos (default: 60)")
    args = ap.parse_args()

    # Inicialización única al arranque: limpia log histórico antiguo
    conn_init = get_conn()
    try:
        inicializar_bd(conn_init)
    finally:
        conn_init.close()

    if args.una_vez:
        ciclo()
    else:
        log.info(f"Bucle continuo — intervalo {args.intervalo}s")
        while True:
            try:
                ciclo()
            except Exception:
                # Un ciclo fallido (BD caída, red…) no debe parar el recolector.
                log.exception("Ciclo fallido; se reintenta en el siguiente intervalo")
            time.sleep(args.intervalo)


if __name__ == "__main__":
    main()