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

# Comunas y sectores de búsqueda. Ciudad Empresarial es un sector de Huechuraba,
# no implica monitorear el resto de esa comuna.
COMUNAS = [
    "Las Condes",
    "Vitacura",
    "Providencia",
    "Lo Barnechea",
    "Ciudad Empresarial",
]

# Sectores a EXCLUIR dentro de Providencia (palabras clave en dirección/título)
# Si aparece cualquiera de estas palabras en la dirección, se descarta el aviso.
SECTORES_EXCLUIDOS_PROVIDENCIA = [
    "bellavista",
    "plaza italia",
    "barrio italia",
    "italia",       # cubre "barrio italia"
    "salvador",
    "baquedano",
    "vicuña mackenna",
    "vicuna mackenna",
    "vicuña mackena",
    "vicuna mackena",
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
UF_REQUEST_TIMEOUT = 8     # la consulta de UF no debe bloquear todo el ciclo

# Contenido que debe ser descartado antes del enriquecimiento por ser publicidad,
# proyectos nuevos, arriendos temporales o avisos no residenciales.
PALABRAS_CLAVE_NO_VALIDAS = [
    "proyecto",
    "multifamily",
    "diario",
    "temporal",
    "residencia",
    "estudiantil",
    "pieza con baño",
    "pieza en arriendo",
    "patrocinado",
    "nuevoproyecto",
    "arriendo diario",
    "alquiler temporal",
    "administracion de propiedades",
    "administración de propiedades",
    "corretaje",
    "comision",
    "comisión",
    "asesoria",
    "asesoría",
    "gestion de arriendo",
    "gestión de arriendo",
]

# Tipos de publicación que solo se descartan cuando aparecen como propiedad
# ofrecida en el título; no cuando se mencionan como accesorios.
TIPOS_PROPIEDAD_NO_VALIDOS = [
    "oficina",
    "terreno",
    "estacionamiento",
    "bodega",
    "local comercial",
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
    "Lo Barnechea": "lo-barnechea-metropolitana",
    "Ciudad Empresarial": "ciudad-empresarial-huechuraba-metropolitana",
}

# Búsqueda por comuna o sector, limitada por precio de arriendo y ordenada por fecha.
BASE_URL_TEMPLATE = (
    "https://www.portalinmobiliario.com/arriendo/departamento/{comuna}/"
    "_PriceRange_0-{max_precio}_NoIndex_True_O_DateCreatedDESC"
)

# Cantidad de avisos por página en Portal Inmobiliario (normalmente 48)
ITEMS_PER_PAGE = 48

# ──────────────────────────────────────────────────────────
# VALOR DE REFERENCIA UF (se actualiza automáticamente)
# ──────────────────────────────────────────────────────────
# Si la API de UF falla, se usa este valor de respaldo
UF_FALLBACK = 41_189  # Referencia del 2026-10-20; se usa solo sin cache reciente

# ──────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────
LOG_FILE = "agente_deptos.log"
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR
