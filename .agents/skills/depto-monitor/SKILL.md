---
name: depto-monitor
description: >
  Skill para monitorear Portal Inmobiliario Chile y enviar alertas Telegram
  de nuevos departamentos en arriendo en Las Condes, Vitacura y Providencia
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
Editar `config.py`:
```python
TELEGRAM_BOT_TOKEN = "123456:ABC-DEF..."   # de @BotFather
TELEGRAM_CHAT_ID   = "987654321"           # de @userinfobot
```

## Cron (cada 6 horas)
```cron
0 */6 * * * cd /Users/mruanov/Documents/Deptos && /usr/bin/python3 main.py >> agente_deptos.log 2>&1
```

## Parámetros calibrados (actuales)
- Precio máximo total (arriendo + GC): $470.000 CLP
- Comunas: Las Condes, Vitacura, Providencia
- Excluidos en Providencia: Bellavista, Barrio Italia, Salvador, Baquedano, Vicuña Mackenna
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
COMUNAS = ["Las Condes", "Vitacura", "Providencia"]
```

Para agregar sectores excluidos en Providencia:
```python
SECTORES_EXCLUIDOS_PROVIDENCIA = [
    "bellavista",
    "barrio italia",
    # agregar aquí más sectores en minúsculas
]
```
