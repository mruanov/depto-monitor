"""
=============================================================
  CONFIGURACIÓN CENTRAL DEL AGENTE DE BÚSQUEDA DE DEPTOS
=============================================================
Edita este archivo para personalizar filtros y credenciales.
Las credenciales Telegram se leen desde variables de entorno
(mismo patrón que flight_monitor.py en Claude_Projects).
"""

# ──────────────────────────────────────────────────────────
# PARÁMETROS DE BÚSQUEDA
# ──────────────────────────────────────────────────────────

# Precio máximo TOTAL (arriendo + GC) en pesos chilenos
MAX_PRECIO_TOTAL_CLP = 470_000

# Comunas a monitorear
COMUNAS = ["Las Condes", "Vitacura", "Providencia"]

# Sectores a EXCLUIR dentro de Providencia (palabras clave en dirección/título)
# Si aparece cualquiera de estas palabras en la dirección, se descarta el aviso.
SECTORES_EXCLUIDOS_PROVIDENCIA = [
    "bellavista",
    "barrio italia",
    "italia",       # cubre "barrio italia"
    "salvador",
    "baquedano",
    "vicuña mackenna",
    "vicuna mackenna",
    "av. vicuña",
    "av vicuna",
]

# ──────────────────────────────────────────────────────────
# TELEGRAM
# ──────────────────────────────────────────────────────────
# Las credenciales se proporcionan mediante el entorno; nunca guardarlas en el código.
import os as _os
TELEGRAM_BOT_TOKEN = _os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = _os.getenv("TELEGRAM_CHAT_ID", "")

# ──────────────────────────────────────────────────────────
# BASE DE DATOS / HISTORIAL
# ──────────────────────────────────────────────────────────
# Archivo SQLite para deduplicación y auditoría
DB_PATH = "deptos.db"

# ──────────────────────────────────────────────────────────
# SCRAPING
# ──────────────────────────────────────────────────────────
# Tiempo de espera entre requests (segundos) para no gatillar anti-bot
DELAY_ENTRE_PAGES  = 2.5   # segundos entre páginas
MAX_PAGINAS        = 1     # SOLO LA PRIMERA PÁGINA PARA VER LO MÁS RECIENTE
REQUEST_TIMEOUT    = 30    # segundos de timeout por request

# Contenido que debe ser descartado antes del enriquecimiento por ser publicidad,
# proyectos nuevos, arriendos temporales o avisos no residenciales.
PALABRAS_CLAVE_NO_VALIDAS = [
    "proyecto",
    "multifamily",
    "diario",
    "temporal",
    "residencia",
    "estudiantil",
    "oficina",
    "pieza con baño",
    "pieza en arriendo",
    "patrocinado",
    "nuevoproyecto",
    "arriendo diario",
    "alquiler temporal",
]

# User-Agent rotativo (se elige uno al azar en cada request)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]

# ──────────────────────────────────────────────────────────
# PORTAL INMOBILIARIO – URLs base de búsqueda
# ──────────────────────────────────────────────────────────
# La URL se construye dinámicamente en scraper.py;
# estos slugs deben coincidir con los parámetros del portal.
COMUNA_SLUGS = {
    "Las Condes":  "las-condes-metropolitana",
    "Vitacura":    "vitacura-metropolitana",
    "Providencia": "providencia-metropolitana",
}

# Búsqueda por comuna, limitada por precio de arriendo y ordenada por fecha.
BASE_URL_TEMPLATE = (
    "https://www.portalinmobiliario.com/arriendo/departamento/{comuna}/"
    "_PriceRange_0-{max_precio}_O_DateCreatedDESC"
)

# Cantidad de avisos por página en Portal Inmobiliario (normalmente 48)
ITEMS_PER_PAGE = 48

# ──────────────────────────────────────────────────────────
# VALOR DE REFERENCIA UF (se actualiza automáticamente)
# ──────────────────────────────────────────────────────────
# Si la API de UF falla, se usa este valor de respaldo
UF_FALLBACK = 38_000  # CLP por UF (actualiza si es necesario)

# ──────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────
LOG_FILE = "agente_deptos.log"
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
