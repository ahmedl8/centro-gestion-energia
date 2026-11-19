import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_horario_compartido():
    from gev_config import horario_instalacion, HORARIO_DEFAULT, INSTALACIONES_CONFIG
    nombre = next(iter(INSTALACIONES_CONFIG))
    h = horario_instalacion(nombre)
    assert set(h) == {"inicio", "fin", "duracion_tour_min"}
    assert horario_instalacion("no_existe") == HORARIO_DEFAULT
    assert horario_instalacion(None) == HORARIO_DEFAULT
    # normaliza mayúsculas/espacios
    assert horario_instalacion(f" {nombre.upper()} ") == h


def test_app_usa_la_misma_fuente():
    import app
    import gev_config
    assert app.horario_instalacion is gev_config.horario_instalacion
    assert not hasattr(app, "HORARIOS_INSTALACION")


def test_error_handler_no_filtra_detalles():
    import app
    app.app.testing = False

    @app.app.route("/_boom")
    def _boom():
        raise ValueError("secreto-interno")

    r = app.app.test_client().get("/_boom")
    assert r.status_code == 500
    assert "secreto-interno" not in r.get_data(as_text=True)


def test_cgi_no_registra_la_contrasena(caplog, monkeypatch):
    import datos_tiempo as dt
    import requests

    def falla(*a, **k):
        # Las excepciones reales de requests incluyen la URL completa.
        raise requests.ConnectionError("HTTPConnectionPool: /cgi-bin/input?username=admin&password=SUPERSECRETO")

    monkeypatch.setattr(dt.requests, "get", falla)
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)
    with caplog.at_level(logging.DEBUG):
        v = dt._leer_voltaje_cgi("10.0.0.1", 90, "/cgi-bin/input", "SUPERSECRETO", "an", "cam_x")
    assert v is None
    assert "SUPERSECRETO" not in caplog.text
    assert "ConnectionError" in caplog.text


def test_cgi_envia_credenciales_como_params(monkeypatch):
    import datos_tiempo as dt
    vistos = {}

    class R:
        status_code = 200
        text = "12.6\n"

    def get(url, params=None, timeout=None):
        vistos["url"], vistos["params"] = url, params
        return R()

    monkeypatch.setattr(dt.requests, "get", get)
    assert dt._leer_voltaje_cgi("10.0.0.1", 90, "/cgi-bin/input", "pw", "an", "c") == 12.6
    assert "pw" not in vistos["url"]
    assert vistos["params"]["password"] == "pw"


def test_cgi_milivoltios(monkeypatch):
    import datos_tiempo as dt

    class R:
        status_code = 200
        text = "12600"

    monkeypatch.setattr(dt.requests, "get", lambda *a, **k: R())
    assert dt._leer_voltaje_cgi("h", 90, "/cgi-bin/input", "pw", "an", "c") == 12.6


def test_get_conn_exige_password(monkeypatch):
    import app
    monkeypatch.setitem(app.DB_CONFIG, "password", "")
    with pytest.raises(RuntimeError):
        app.get_conn()
    import datos_tiempo as dt
    monkeypatch.setitem(dt.DB_CONFIG, "password", "")
    with pytest.raises(RuntimeError):
        dt.get_conn()


def test_query_cierra_la_conexion(monkeypatch):
    import app

    class Cur:
        def execute(self, *a): pass
        def fetchall(self): return [{"x": 1}]
        def close(self): pass

    class Conn:
        closed = 0
        def cursor(self, dictionary=False): return Cur()
        def is_connected(self): return self.closed == 0
        def close(self): Conn.closed += 1

    monkeypatch.setattr(app, "get_conn", lambda: Conn())
    assert app.query("SELECT 1", one=True) == {"x": 1}
    assert Conn.closed == 1
