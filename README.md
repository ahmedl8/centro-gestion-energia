# GEV — Gestor de Energía

Sistema que gestiona la autonomía energética de cámaras térmicas desplegadas en instalaciones remotas. Cada maleta funciona con batería (LiFePO4 o plomo) alimentada por panel solar con regulador MPPT, o por cargador de red 220 V donde no hay sol suficiente. GEV **estima el estado de carga (SoC)** de cada batería, calcula el **presupuesto energético** con la previsión meteorológica de 3 días y **ajusta la frecuencia de tours** de cada cámara para no agotar la batería en días de mal tiempo.

> **EN:** Energy manager for remote solar/battery-powered camera units. A collector reads each unit's router, estimates battery state of charge, computes a 3-day energy budget from the weather forecast and adapts the camera tour frequency; a read-only Flask dashboard visualises it. Python, Flask, MySQL, Chart.js.

**Problema que resuelve:** el estado de la batería solo se conocía visitando la instalación o revisando el router a mano, sin previsión ni ajuste automático de la vigilancia. GEV automatiza esa vigilancia energética y avisa de quién necesita atención antes de quedarse sin batería.

## Arquitectura

Dos procesos independientes que comparten la base de datos `gev` y las mismas variables de entorno, sin duplicar credenciales:

| Proceso | Archivo | Función |
|---|---|---|
| **Recolector** | `collector/datos_tiempo.py` | Único proceso que **escribe** en la BD. Por cada instalación activa: descarga la previsión, lee el voltaje de cada cámara vía router Teltonika RUT955/956, estima el SoC, calcula el presupuesto y la frecuencia de tours, y escribe `bateria_estado` y `bat_energia_log` |
| **Panel web** | `app.py` | Flask. Sirve el dashboard y una API JSON de **solo lectura**; corre en paralelo al recolector sin riesgo de condiciones de carrera |
| **Lanzador** | `main.py` | Arranca ambos como subprocesos supervisados con salida prefijada (`[collector]` / `[panel]`). Si uno muere, apaga el otro para no servir datos obsoletos |

```
gev-dashboard/
├── main.py               # Lanzador de ambos procesos
├── app.py                # Backend Flask + API JSON (solo lectura)
├── collector/
│   └── datos_tiempo.py   # Recolector: lee routers, escribe en BD
├── templates/            # Vistas del panel
├── static/               # CSS/JS (gauge, brújula, Chart.js…)
└── requirements.txt
```

## Modelo de datos (esquema `gev`)

Separa configuración estática (la escribe el operario) de estado operacional (lo escribe solo el recolector).

| Tabla | Función |
|---|---|
| `instalaciones` | Un registro por emplazamiento: clave, nombre, coordenadas, tipo de energía (`solar` o `red_220v`, que cambia el cálculo del presupuesto) |
| `camaras` | Configuración de cada cámara/maleta: modelo, consumo, acceso al router, química y capacidad de batería, panel solar, cargador. `activada=0` la excluye del ciclo sin borrar su histórico |
| `bateria_estado` | Estado actual por cámara: voltaje filtrado, SoC, alerta, previsión de 3 días, generación/consumo de hoy, frecuencia de tours. El panel solo la lee |
| `bat_energia_log` | Histórico de lecturas (una fila por cámara y ciclo, retención de 6 meses). Alimenta el gráfico de SoC de 7 días |

`camaras.instalacion → instalaciones.instalacion` (RESTRICT/CASCADE) y `bateria_estado.cam_name → camaras.cam_name` (CASCADE). `bat_energia_log` no tiene FK a propósito, para no bloquear la escritura si una cámara se desactiva o borra con lecturas en curso.

## Ciclo del recolector

1. **Previsión meteorológica** de 3 días (nubes, olas, viento) y hora de amanecer/atardecer, con las coordenadas de `instalaciones` (fallback a `INSTALACIONES_CONFIG` si son NULL).
2. **Lectura de voltaje** por router RUT955/956 según `rut_tipo` (`cgi_input`, `cgi_lo_value` o `api`) sobre la entrada analógica configurada.
3. **Estimación de SoC:** filtro exponencial sobre el voltaje bruto, con lógica de amanecer/atardecer y umbrales según química (`lead` o `lifepo4`). Devuelve voltaje filtrado, SoC, nota de calidad de la lectura y si está cargando.
4. **Acumulador de carga 220 V:** horas de carga y Wh estimados del día; se resiembra desde la BD si el proceso se reinicia a mitad de jornada.
5. **Presupuesto energético:** generación esperada (potencia del panel × factor de orientación × factor de calibración × meteorología) frente al consumo de cámara + router, para hoy y los dos días siguientes.
6. **Frecuencia de tours:** a partir del SoC y el balance de 3 días; puede quedar *suspendida por mal tiempo* si el balance previsto es muy negativo.
7. **Calibración diaria:** al amanecer se registra el SoC de referencia; al atardecer se recalcula `factor_calibracion` comparando generación real y esperada, solo con lecturas de calidad `reposo_fiable`.
8. **Clasificación de alerta:** `ok` / `bajo` / `crítico`, visible en la API y en el color de las tarjetas.
9. **Escritura:** upsert en `bateria_estado` y nueva fila en `bat_energia_log`.

## Panel web

- **Cabecera:** instalación/cámara seleccionada, estado ONLINE/OFFLINE (según `updated_at` de los últimos 20 min) y refresco manual.
- **Tarjetas:** SoC actual, energía generada/consumida hoy, tours al día y semáforo OK / ATENCIÓN / CRÍTICO / SIN CONEXIÓN.
- **Estado de la batería:** medidor circular de SoC y ficha técnica (química, capacidad, energía útil, voltaje, carga).
- **Previsión (3 días):** nubes, olas y viento, y tabla de generación vs. consumo con aviso si el balance de hoy es negativo.
- **Configuración solar:** potencia, azimut, inclinación y factores, con brújula SVG que rota con el azimut guardado.
- **Histórico de SoC (7 días)** y **próximos tours programados** (o «suspendido por mal tiempo»).
- **Selector de instalación** con sus cámaras activas. Refresco automático cada 60 s (`REFRESH_MS` en `static/dashboard.js`).

La API JSON expone `/api/instalaciones`, `/api/camaras/<cam>`, `/api/camaras/<cam>/historico`, `/api/camaras/<cam>/tours`, `/api/baterias`, `/api/alertas` y `/api/dispositivos`. Las páginas Tours, Previsión, Paneles solares, Histórico, Alertas, Dispositivos y Configuración tienen ya su plantilla, pero la vista completa está en desarrollo.

## Instalación y ejecución

```bash
python3 -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt

export GEV_DB_HOST="localhost"
export GEV_DB_USER="gev"
export GEV_DB_PASSWORD="tu_password"      # obligatoria, sin valor por defecto
export GEV_DB_NAME="gev"

python3 main.py                            # recolector (ciclo de 15 min) + panel
python3 main.py --intervalo 60             # recolector cada 60 s (pruebas)
python3 main.py --solo-panel               # solo el panel
python3 main.py --solo-collector           # solo el recolector
python3 collector/datos_tiempo.py --una-vez   # un único ciclo y termina
```

Abre `http://localhost:5000`. En Windows, `tzdata` se instala automáticamente: el recolector depende de `Europe/Madrid` para su lógica de amanecer/atardecer.

## Limitaciones y mejoras previstas

- **SoC por voltaje.** No hay sensor de corriente (shunt) en las maletas, así que el SoC se estima solo por voltaje. Es suficiente para una alerta operativa, pero hereda las limitaciones habituales del método: curvas planas (sobre todo en LiFePO4), caídas de tensión bajo carga y dependencia de la temperatura. Por eso la calibración solo usa lecturas en reposo. Mejora prevista: sensor de corriente.
- **El router es el único canal de lectura.** Si pierde la 4G o falla la entrada analógica, la cámara deja de reportar y el dato no distingue «router caído» de «batería agotada».
- **Servidor de desarrollo.** `app.py` arranca con `debug=True` y el servidor de Flask, válido para la red local pero no para exponerlo. Antes de hacerlo hay que desactivar `debug`, usar un servidor WSGI de producción (waitress o gunicorn), añadir autenticación y servirlo tras HTTPS.
- No se incluye el esquema SQL de `gev`; las tablas están descritas arriba.
- Los nombres de instalaciones, coordenadas y parámetros de ejemplo son ficticios.
