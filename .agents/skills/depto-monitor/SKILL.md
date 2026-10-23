---
name: depto-monitor
description: >
  Skill para monitorear Portal Inmobiliario Chile y enviar alertas Telegram
  de nuevos departamentos en arriendo en Las Condes, Vitacura, Providencia
  y Lo Barnechea
  que cumplan los criterios de precio total ≤ $470.000 CLP.
  Activa este skill cuando el usuario mencione: departamentos, Portal Inmobiliario,
  arriendo Chile, agente depto, búsqueda inmobiliaria, Telegram alertas depto.
---

# Skill: Depto Monitor — Portal Inmobiliario Chile

## Propósito
Ejecutar el agente de búsqueda de departamentos ubicado en `/Users/mruanov/Documents/Deptos/`.
El monitoreo programado corre en un Android TV Box con Termux (red residencial).
GitHub Actions quedó solo como workflow informativo porque Portal Inmobiliario
bloquea las IPs de datacenter y redirige a `/gz/account-verification`.

## Estructura del proyecto
```
Deptos/
├── main.py                 ← Orquestador principal (punto de entrada)
├── config.py               ← Parámetros de búsqueda y credenciales
├── scraper.py              ← Extracción web (requests + curl_cffi/Playwright opcionales)
├── filtro.py               ← Filtros y scoring
├── database.py             ← SQLite para deduplicación e historial
├── notificador.py          ← Bot Telegram
├── uf_helper.py            ← Valor UF actualizado desde mindicador.cl
├── run_monitor.sh          ← Ejecuta un ciclo con .env y TZ de Chile
├── setup_termux.sh         ← Instalación y cron en Termux
├── README_ANDROID.md       ← Guía paso a paso para Android TV Box
├── requirements.txt        ← Dependencias para macOS/Linux/Actions
└── requirements-termux.txt ← Dependencias mínimas para Termux
```

## Comandos principales

### Instalación en Termux (Android TV Box)
```bash
pkg update && pkg upgrade -y
pkg install -y git
git clone https://github.com/mruanov/depto-monitor.git
cd depto-monitor
bash setup_termux.sh
```
Ver `README_ANDROID.md` para el detalle.

### Instalación local (macOS/Linux)
```bash
cd /Users/mruanov/Documents/Deptos
pip install -r requirements.txt
playwright install chromium   # opcional, solo fallback
```

### Verificar conexión Telegram
```bash
./run_monitor.sh --verify
```

### Ejecución de prueba (sin notificaciones ni escritura en DB)
```bash
./run_monitor.sh --test
```

### Ejecución real
```bash
./run_monitor.sh
```

## Configuración de Telegram
En Termux, `setup_termux.sh` crea `.env` con:
```bash
TELEGRAM_BOT_TOKEN="..."   # token de @BotFather
TELEGRAM_CHAT_ID="..."     # ID del chat
```
`run_monitor.sh` carga ese archivo y las variables de entorno. No guardes
credenciales en `config.py` ni en el repositorio.

## Cron en Termux (cada 4 horas)
```cron
0 */4 * * * /data/data/com.termux/files/home/depto-monitor/run_monitor.sh >> /data/data/com.termux/files/home/depto-monitor/monitor-cron.log 2>&1
```
Mantén `termux-wake-lock`, `sv up crond` y la batería de Termux sin restricciones.
Usa `sv status crond` para comprobar el servicio.

## Parámetros calibrados (actuales)
- Precio máximo total (arriendo + GC): $470.000 CLP
- Comunas: Las Condes, Vitacura, Providencia, Lo Barnechea
- Excluidos en Providencia: Bellavista, Plaza Italia, Barrio Italia, Salvador, Baquedano, Vicuña Mackenna
- Dormitorios: sin filtro
- Superficie: sin filtro
- Canal: Telegram Bot

## Ajustar parámetros
Para cambiar el límite de precio, abrir `config.py` y editar:
```python
MAX_PRECIO_TOTAL_CLP = 470_000  # ← cambiar este valor
```

Para agregar o quitar comunas:
```python
COMUNAS = ["Las Condes", "Vitacura", "Providencia", "Lo Barnechea"]
```

Para agregar sectores excluidos en Providencia:
```python
SECTORES_EXCLUIDOS_PROVIDENCIA = [
    "bellavista",
    "plaza italia",
    "barrio italia",
    "salvador",
    "vicuña mackenna",
    # agregar aquí más sectores en minúsculas
]
```
