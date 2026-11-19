"""
app.py — Backend del panel de visualización GEV
=================================================
Sirve el dashboard (templates/index.html) y una API JSON de solo lectura
sobre las tablas que escribe datos_tiempo.py:

    gev.instalaciones
    gev.camaras
    gev.bateria_estado
    gev.bat_energia_log

No escribe nada en la base de datos: es un visor.

Configuración (variables de entorno, con valores por defecto no sensibles):

    GEV_DB_HOST       (por defecto: el host de producción)
    GEV_DB_USER       (por defecto: root)
    GEV_DB_PASSWORD   (OBLIGATORIA, sin valor por defecto)
    GEV_DB_NAME       (por defecto: gev)
    GEV_DB_PORT       (por defecto: 3306)

Uso:
    pip install -r requirements.txt
    export GEV_DB_PASSWORD="tu_password"
    python3 app.py
    -> http://127.0.0.1:5000

Opcionales: GEV_HOST (por defecto 127.0.0.1; usa 0.0.0.0 solo detrás de un
proxy con autenticación), GEV_PORT (5000), GEV_DEBUG=1 (solo desarrollo).
"""

import os
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, abort, request
import mysql.connector

from gev_config import horario_instalacion

app = Flask(__name__)


@app.errorhandler(Exception)
def manejar_error(e):
    app.logger.exception("Error no controlado")
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": "Error interno del servidor"}), 500

# ─────────────────────────────────────────────────────────────
# Configuración BD — mismos parámetros que datos_tiempo.py
# ─────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":            os.environ.get("GEV_DB_HOST", "localhost"),
    "user":            os.environ.get("GEV_DB_USER", "gev"),
    "password":        os.environ.get("GEV_DB_PASSWORD", ""),
    "database":        os.environ.get("GEV_DB_NAME", "gev"),
    "port":            int(os.environ.get("GEV_DB_PORT", "3306")),
    "connect_timeout": 10,
}


def get_conn():
    if not DB_CONFIG["password"]:
        raise RuntimeError(
            "Falta GEV_DB_PASSWORD. Define la variable de entorno antes de arrancar la app."
        )
    return mysql.connector.connect(**DB_CONFIG)


def query(sql, params=None, one=False, _retry=True):
    """Ejecuta una consulta con una conexión nueva (sin pool, para evitar
    errores de 'pool exhausted' bajo varias peticiones a la vez en un panel
    de bajo tráfico). Si la conexión fue cerrada por el servidor (frecuente
    en RDS tras inactividad) se reintenta una vez con una conexión nueva."""
    try:
        conn = get_conn()
    except mysql.connector.Error:
        if _retry:
            time.sleep(0.3)
            return query(sql, params, one, _retry=False)
        raise

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return (rows[0] if rows else None) if one else rows
    except mysql.connector.Error:
        if _retry:
            time.sleep(0.3)
            return query(sql, params, one, _retry=False)
        raise
    finally:
        if conn.is_connected():
            conn.close()


# ─────────────────────────────────────────────────────────────
# Horarios de vigilancia por instalación
# El horario de tours vive en gev_config.py (compartido con el recolector).


# ─────────────────────────────────────────────────────────────
# Rutas de página
# ─────────────────────────────────────────────────────────────
# Cada ruta renderiza su propia plantilla (todas heredan de base.html) y
# pasa `active_page` para resaltar el ítem correspondiente del menú lateral.
# Antes estas secciones eran enlaces "Próximamente" sin ninguna lógica
# detrás; ahora cada una es una página real con su propio JS/API.
@app.route("/")
def index():
    return render_template("dashboard.html", active_page="dashboard", page_title="Dashboard")


@app.route("/baterias")
def baterias():
    return render_template("baterias.html", active_page="baterias", page_title="Baterías")


@app.route("/tours")
def tours():
    return render_template("tours.html", active_page="tours", page_title="Tours")


@app.route("/prevision")
def prevision():
    return render_template("prevision.html", active_page="prevision", page_title="Previsión del tiempo")


@app.route("/paneles-solares")
def paneles_solares():
    return render_template("paneles_solares.html", active_page="paneles_solares", page_title="Paneles solares")


@app.route("/historico")
def historico():
    return render_template("historico.html", active_page="historico", page_title="Histórico")


@app.route("/alertas")
def alertas():
    return render_template("alertas.html", active_page="alertas", page_title="Alertas")


@app.route("/dispositivos")
def dispositivos():
    return render_template("dispositivos.html", active_page="dispositivos", page_title="Dispositivos")


@app.route("/configuracion")
def configuracion():
    return render_template("configuracion.html", active_page="configuracion", page_title="Configuración")


# ─────────────────────────────────────────────────────────────
# API — instalaciones y cámaras
# ─────────────────────────────────────────────────────────────
@app.route("/api/instalaciones")
def api_instalaciones():
    rows = query(
        "SELECT instalacion, nombre, lat, lon, tipo_energia FROM instalaciones ORDER BY nombre"
    )
    return jsonify(rows)


@app.route("/api/instalaciones_con_camaras")
def api_instalaciones_con_camaras():
    """Instalaciones + sus cámaras activas en una sola consulta — usado por
    el selector 'Cambiar instalación' para evitar N peticiones en cadena."""
    rows = query(
        """
        SELECT i.instalacion, i.nombre AS instalacion_nombre,
               c.cam_name, c.modelo_camara
        FROM instalaciones i
        JOIN camaras c ON c.instalacion = i.instalacion AND c.activada = 1
        ORDER BY i.nombre, c.cam_name
        """
    )
    agrupado = {}
    for r in rows:
        clave = r["instalacion"]
        if clave not in agrupado:
            agrupado[clave] = {
                "instalacion": clave,
                "nombre": r["instalacion_nombre"],
                "camaras": [],
            }
        agrupado[clave]["camaras"].append({
            "cam_name": r["cam_name"],
            "modelo_camara": r["modelo_camara"],
        })
    return jsonify(list(agrupado.values()))


@app.route("/api/instalaciones/<instalacion>/camaras")
def api_camaras_de_instalacion(instalacion):
    rows = query(
        """
        SELECT c.cam_name, c.activada, c.modelo_camara, c.panel_w, c.bat_type,
               b.updated_at
        FROM camaras c
        LEFT JOIN bateria_estado b ON b.cam_name = c.cam_name
        WHERE c.instalacion = %s
        ORDER BY c.cam_name
        """,
        (instalacion,),
    )
    return jsonify(rows)


@app.route("/api/camaras/<cam_name>")
def api_camara_detalle(cam_name):
    row = query(
        """
        SELECT c.cam_name, c.instalacion, c.activada, c.modelo_camara,
               c.consumo_cam_w, c.bat_type, c.bat_ah, c.tipo_cargador,
               c.cargador_a, c.panel_w, c.panel_azimut, c.panel_inclinacion,
               c.factor_calibracion,
               i.nombre AS instalacion_nombre, i.tipo_energia,
               b.bat_actual, b.soc_pct, b.alerta_bat, b.cargando_ahora,
               b.horas_cargando_hoy, b.carga_hoy_wh,
               b.nubes_actual, b.nubes_manana, b.nubes_pasado_manana,
               b.olas_actual, b.olas_manana, b.olas_pasado_manana,
               b.viento_actual, b.viento_manana, b.viento_pasado_manana,
               b.frecuencia_tours, b.gen_hoy_wh, b.cons_hoy_wh,
               b.factor_orientacion, b.descripcion_cargador, b.updated_at,
               TIMESTAMPDIFF(MINUTE, b.updated_at, NOW()) AS minutos_desde_actualizacion
        FROM camaras c
        JOIN instalaciones i ON i.instalacion = c.instalacion
        LEFT JOIN bateria_estado b ON b.cam_name = c.cam_name
        WHERE c.cam_name = %s
        """,
        (cam_name,),
        one=True,
    )
    if not row:
        abort(404, description="Cámara no encontrada")

    # Frescura calculada por el propio MySQL (TIMESTAMPDIFF contra NOW()),
    # para no comparar la hora local del proceso Flask (datetime.now())
    # contra un updated_at que MySQL guarda en su propia zona horaria
    # (normalmente UTC en RDS) — esa mezcla es lo que antes hacía
    # aparecer "SIN CONEXIÓN" con datos recién llegados.
    minutos = row.pop("minutos_desde_actualizacion", None)
    online = minutos is not None and minutos < 20

    soc = row.get("soc_pct")
    bat_ah = row.get("bat_ah") or 0
    v_nominal = 13.2 if row.get("bat_type") == "lifepo4" else 12.6
    capacidad_wh = bat_ah * v_nominal
    energia_util_wh = round(capacidad_wh * (soc or 0) / 100)

    gen = row.get("gen_hoy_wh") or 0
    cons = row.get("cons_hoy_wh") or 0
    balance = round(gen - cons, 1)

    freq_raw = row.get("frecuencia_tours")
    suspendido = freq_raw == "mal_tiempo"
    freq_min = None if suspendido else (int(freq_raw) if freq_raw else None)

    horario = horario_instalacion(row["instalacion"])
    tours_dia = None
    if freq_min:
        ventana_h = (horario["fin"] - horario["inicio"]) % 24
        if ventana_h == 0:
            ventana_h = 24
        tours_dia = max(1, round((ventana_h * 60) / freq_min))

    rendimiento_estimado = None
    if row.get("factor_orientacion") is not None and row.get("factor_calibracion") is not None:
        rendimiento_estimado = round(
            row["factor_orientacion"] * row["factor_calibracion"] * 100
        )

    row.update({
        "online": online,
        "capacidad_wh": round(capacidad_wh),
        "energia_util_wh": energia_util_wh,
        "balance_hoy_wh": balance,
        "tours_dia": tours_dia,
        "frecuencia_min": freq_min,
        "suspendido_mal_tiempo": suspendido,
        "horario_inicio": horario["inicio"],
        "horario_fin": horario["fin"],
        "duracion_tour_min": horario["duracion_tour_min"],
        "rendimiento_estimado_pct": rendimiento_estimado,
    })
    if row.get("updated_at"):
        row["updated_at"] = row["updated_at"].isoformat()
    return jsonify(row)


@app.route("/api/camaras/<cam_name>/historico")
def api_camara_historico(cam_name):
    try:
        dias = int(request.args.get("dias", 7))
    except (TypeError, ValueError):
        dias = 7
    dias = max(1, min(dias, 90))  # límite razonable para no tirar de años de log
    rows = query(
        """
        SELECT ts, soc_pct, v_filtrado
        FROM bat_energia_log
        WHERE cam_name = %s AND ts >= NOW() - INTERVAL %s DAY
        ORDER BY ts ASC
        """,
        (cam_name, dias),
    )
    for r in rows:
        r["ts"] = r["ts"].isoformat()
    return jsonify(rows)


@app.route("/api/camaras/<cam_name>/tours")
def api_camara_tours(cam_name):
    cam = query(
        "SELECT instalacion FROM camaras WHERE cam_name = %s", (cam_name,), one=True
    )
    if not cam:
        abort(404)
    estado = query(
        "SELECT frecuencia_tours FROM bateria_estado WHERE cam_name = %s",
        (cam_name,),
        one=True,
    )
    freq_raw = (estado or {}).get("frecuencia_tours")
    if not freq_raw or freq_raw == "mal_tiempo":
        return jsonify({"suspendido": True, "tours": []})

    freq_min = int(freq_raw)
    horario = horario_instalacion(cam["instalacion"])

    now = datetime.now()
    inicio_hoy = now.replace(hour=horario["inicio"] % 24, minute=0, second=0, microsecond=0)
    if horario["inicio"] >= horario["fin"]:
        # ventana nocturna: si ya pasó la hora de inicio de hoy, sigue vigente hasta mañana
        fin_ventana = inicio_hoy + timedelta(days=1, hours=(horario["fin"] - horario["inicio"]) % 24)
    else:
        fin_ventana = inicio_hoy + timedelta(hours=(horario["fin"] - horario["inicio"]))

    if now < inicio_hoy:
        # aún no ha empezado hoy: la ventana activa es la de ayer si seguimos dentro, si no la de hoy
        inicio_ventana = inicio_hoy - timedelta(days=1)
        fin_ventana_prev = inicio_ventana + timedelta(hours=(horario["fin"] - horario["inicio"]) % 24 or 24)
        if now < fin_ventana_prev:
            inicio_hoy = inicio_ventana
            fin_ventana = fin_ventana_prev

    tours = []
    t = inicio_hoy
    while t <= fin_ventana and len(tours) < 12:
        if t >= now:
            tours.append(t.strftime("%H:%M"))
        t += timedelta(minutes=freq_min)

    return jsonify({
        "suspendido": False,
        "frecuencia_min": freq_min,
        "duracion_tour_min": horario["duracion_tour_min"],
        "horario": f"{horario['inicio']:02d}:00 - {horario['fin']:02d}:00",
        "tours": tours,
    })


def _con_online_y_fecha(rows):
    """Añade `online` (bool) a partir de minutos_desde_actualizacion y convierte
    updated_at a isoformat — mismo criterio de frescura (< 20 min) que ya usa
    api_camara_detalle, replicado aquí para las vistas de flota."""
    for r in rows:
        minutos = r.pop("minutos_desde_actualizacion", None)
        r["online"] = minutos is not None and minutos < 20
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat()
    return rows


@app.route("/api/baterias")
def api_baterias():
    """Estado de batería de todas las cámaras activas — usado por la página
    'Baterías' para la tabla de flota completa."""
    rows = query(
        """
        SELECT c.cam_name, c.instalacion, i.nombre AS instalacion_nombre,
               b.soc_pct, b.bat_actual, b.cargando_ahora, b.alerta_bat, b.updated_at,
               TIMESTAMPDIFF(MINUTE, b.updated_at, NOW()) AS minutos_desde_actualizacion
        FROM camaras c
        JOIN instalaciones i ON i.instalacion = c.instalacion
        LEFT JOIN bateria_estado b ON b.cam_name = c.cam_name
        WHERE c.activada = 1
        ORDER BY i.nombre, c.cam_name
        """
    )
    return jsonify(_con_online_y_fecha(rows))


@app.route("/api/alertas")
def api_alertas():
    """Todas las cámaras activas con su estado derivado (crítico / bajo / sin
    conexión / ok). No existe una tabla de alertas dedicada: se deriva del
    mismo campo bateria_estado.alerta_bat y de la frescura del dato que ya
    usa el resto del panel, para no duplicar una fuente de verdad distinta."""
    rows = query(
        """
        SELECT c.cam_name, c.instalacion, i.nombre AS instalacion_nombre,
               b.soc_pct, b.alerta_bat, b.updated_at,
               TIMESTAMPDIFF(MINUTE, b.updated_at, NOW()) AS minutos_desde_actualizacion
        FROM camaras c
        JOIN instalaciones i ON i.instalacion = c.instalacion
        LEFT JOIN bateria_estado b ON b.cam_name = c.cam_name
        WHERE c.activada = 1
        ORDER BY i.nombre, c.cam_name
        """
    )
    return jsonify(_con_online_y_fecha(rows))


@app.route("/api/dispositivos")
def api_dispositivos():
    """Inventario de cámaras de todas las instalaciones (activas o no) — usado
    por la página 'Dispositivos'."""
    rows = query(
        """
        SELECT c.cam_name, c.instalacion, i.nombre AS instalacion_nombre,
               c.activada, c.modelo_camara, c.panel_w, c.bat_type, c.consumo_cam_w,
               b.updated_at, b.soc_pct,
               TIMESTAMPDIFF(MINUTE, b.updated_at, NOW()) AS minutos_desde_actualizacion
        FROM camaras c
        JOIN instalaciones i ON i.instalacion = c.instalacion
        LEFT JOIN bateria_estado b ON b.cam_name = c.cam_name
        ORDER BY i.nombre, c.cam_name
        """
    )
    return jsonify(_con_online_y_fecha(rows))


if __name__ == "__main__":
    host = os.environ.get("GEV_HOST", "127.0.0.1")
    port = int(os.environ.get("GEV_PORT", "5000"))
    if os.environ.get("GEV_DEBUG") == "1":
        # Solo desarrollo local: el depurador de Flask permite ejecutar código.
        app.run(host="127.0.0.1", port=port, debug=True)
    else:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
