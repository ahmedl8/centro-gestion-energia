"""
gev_config.py — Configuración operativa compartida
===================================================
Fuente única del horario de tours y la frecuencia base por instalación.
La usan tanto el recolector (collector/datos_tiempo.py) como el panel
(app.py), de modo que no puedan divergir.

lat/lon se usan SOLO como fallback si gev.instalaciones.lat/lon está a
NULL. La fuente de verdad son las coordenadas de la BD.
Los identificadores de instalación son genéricos (ejemplo).
"""

INSTALACIONES_CONFIG = {
    "site_a":     {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 14, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_b":       {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 15, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_c":    {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 15, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_d":    {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 15, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "office":     {"lat": None,    "lon": None,    "hora_inicio_tours":  8, "hora_fin_tours": 20, "freq_base_min": 30, "duracion_tour_min": 5},
    "site_e":     {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 14, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_f":     {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 14, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_g":    {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 14, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
    "site_h": {"lat": 40.0, "lon": -3.0, "hora_inicio_tours": 15, "hora_fin_tours": 8,  "freq_base_min": 60, "duracion_tour_min": 5},
}

HORARIO_DEFAULT = {"inicio": 19, "fin": 7, "duracion_tour_min": 5}


def horario_instalacion(instalacion):
    """Devuelve {'inicio', 'fin', 'duracion_tour_min'} de una instalación,
    o el horario por defecto si no está configurada."""
    c = INSTALACIONES_CONFIG.get((instalacion or "").lower().strip())
    if not c:
        return dict(HORARIO_DEFAULT)
    return {
        "inicio": c["hora_inicio_tours"],
        "fin": c["hora_fin_tours"],
        "duracion_tour_min": c["duracion_tour_min"],
    }
