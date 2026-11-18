"""
main.py — Punto de entrada único para GEV (recolector + panel)
================================================================
Arranca en paralelo los dos procesos del proyecto sin necesidad de
abrir dos terminales:

    - collector/datos_tiempo.py  (recolector: escribe en la BD)
    - app.py                     (panel web: lee de la BD)

Se lanzan como subprocesos independientes (no como imports) para que
cada uno conserve su propio logging, su propio argparse y su propio
bucle, exactamente igual que si se arrancaran a mano en dos terminales.
Si uno de los dos procesos muere, main.py lo detecta y apaga el otro
para no dejar un proceso zombie sirviendo datos obsoletos.

Configuración: las mismas variables de entorno de siempre (GEV_DB_HOST,
GEV_DB_USER, GEV_DB_PASSWORD, GEV_DB_NAME, GEV_DB_PORT). main.py no
añade ninguna variable nueva ni valida GEV_DB_PASSWORD: cada subproceso
(app.py y datos_tiempo.py) ya trae su propio valor/validación, igual
que al arrancarlos por separado.

Uso:
    python3 main.py                          # collector cada 900s (producción)
    python3 main.py --intervalo 60           # collector cada 60s (pruebas)
    python3 main.py --solo-panel             # solo arranca app.py
    python3 main.py --solo-collector         # solo arranca el recolector

Ctrl+C apaga ambos procesos de forma limpia.
"""

import os
import sys
import signal
import argparse
import subprocess
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

procesos = []          # subprocess.Popen activos
_apagando = False
_lock = threading.Lock()


def _prefijo_stream(proc: subprocess.Popen, nombre: str):
    """Lee stdout del subproceso línea a línea y lo reemite con un
    prefijo, para distinguir en la misma consola qué proceso habla."""
    assert proc.stdout is not None
    for linea in iter(proc.stdout.readline, b""):
        try:
            texto = linea.decode(errors="replace").rstrip("\n")
        except Exception:
            texto = str(linea)
        print(f"[{nombre}] {texto}", flush=True)


def _lanzar(nombre: str, cmd: list[str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    procesos.append(proc)
    hilo = threading.Thread(target=_prefijo_stream, args=(proc, nombre), daemon=True)
    hilo.start()
    print(f"[main] {nombre} arrancado (PID {proc.pid})", flush=True)
    return proc


def _apagar_todo(motivo: str = ""):
    global _apagando
    with _lock:
        if _apagando:
            return
        _apagando = True
    if motivo:
        print(f"[main] Deteniendo todo — {motivo}", flush=True)
    for proc in procesos:
        if proc.poll() is None:
            proc.terminate()
    for proc in procesos:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    ap = argparse.ArgumentParser(description="GEV — arranque conjunto de recolector + panel")
    ap.add_argument("--intervalo", type=int, default=900,
                     help="Segundos entre ciclos del recolector (default: 900, producción)")
    ap.add_argument("--solo-panel", action="store_true", help="Arranca solo el panel web (app.py)")
    ap.add_argument("--solo-collector", action="store_true", help="Arranca solo el recolector")
    args = ap.parse_args()

    if args.solo_panel and args.solo_collector:
        print("[main] --solo-panel y --solo-collector son excluyentes.", file=sys.stderr)
        sys.exit(1)

    # No se valida GEV_DB_PASSWORD aquí: cada subproceso (app.py y
    # datos_tiempo.py) ya tiene su propio valor por defecto / validación,
    # exactamente igual que cuando se arrancan por separado a mano.
    python = sys.executable

    if not args.solo_panel:
        _lanzar(
            "collector",
            [python, "-u", os.path.join("collector", "datos_tiempo.py"),
             "--intervalo", str(args.intervalo)],
        )

    if not args.solo_collector:
        _lanzar("panel", [python, "-u", "app.py"])

    def _manejar_senal(signum, frame):
        _apagar_todo(motivo=f"señal {signum} recibida")
        sys.exit(0)

    signal.signal(signal.SIGINT, _manejar_senal)
    signal.signal(signal.SIGTERM, _manejar_senal)

    # Supervisión: si un proceso muere solo (crash), apaga el resto
    import time
    try:
        while True:
            for proc in list(procesos):
                codigo = proc.poll()
                if codigo is not None:
                    _apagar_todo(motivo=f"un proceso terminó con código {codigo}")
                    sys.exit(1 if codigo != 0 else 0)
            time.sleep(1)
    except KeyboardInterrupt:
        _apagar_todo(motivo="KeyboardInterrupt")


if __name__ == "__main__":
    main()