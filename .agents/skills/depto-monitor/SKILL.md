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

## Estructura del proyecto
```
Deptos/
├── main.py          ← Orquestador principal (punto de entrada)
├── config.py        ← Parámetros de búsqueda y credenciales
├── scraper.py       ← Extracción web (requests + BeautifulSoup + Playwright)
├── filtro.py        ← Filtros y scoring
├── database.py      ← SQLite para deduplicación e historial
├── notificador.py   ← Bot Telegram
├── uf_helper.py     ← Valor UF actualizado desde mindicador.cl
└── requirements.txt ← Dependencias Python
```

## Comandos principales

### Instalación inicial
```bash
cd /Users/mruanov/Documents/Deptos
pip install -r requirements.txt
playwright install chromium
```

### Verificar conexión Telegram
```bash
python main.py --verify
```

### Ejecución de prueba (sin notificaciones ni escritura en DB)
```bash
python main.py --test
```

### Ejecución real
```bash
python main.py
```

## Configuración de Telegram
Para GitHub Actions, configura estos secretos en `Settings > Secrets and variables > Actions`:
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

Para ejecutar localmente, exporta las variables de entorno en la terminal:
```bash
export TELEGRAM_BOT_TOKEN="..."   # token de @BotFather
export TELEGRAM_CHAT_ID="..."     # ID del chat
```
No guardes credenciales en `config.py` ni en el repositorio.

Si el portal redirige a una verificación de cuenta o bloquea el acceso, el workflow
fallará y enviará una alerta a Telegram con el enlace a sus logs. El monitor no
intenta saltarse esa verificación; para reanudar la búsqueda se necesita un método
de acceso permitido por el portal.

## Cron (cada 4 horas)
```cron
0 */4 * * * cd /Users/mruanov/Documents/Deptos && /usr/bin/python3 main.py >> agente_deptos.log 2>&1
```

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
