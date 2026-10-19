"""
=============================================================
  MÓDULO: SCRAPER (Optimizado con Filtros Nativos de URL)
  Extracción de publicaciones desde Portal Inmobiliario
=============================================================
Estrategia:
- Requests + BeautifulSoup para el listado (rápido, sin JS)
- Playwright solo si el listado retorna vacío (fallback JS)
- Retry con backoff exponencial ante errores de red
- Rotación de User-Agent en cada request
- Filtrado nativo en URL por precio máximo y arriendo tradicional
=============================================================
"""

import re
import time
import random
import logging
import requests
from typing import NamedTuple, Optional, Tuple, List
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from config import (
    COMUNAS, COMUNA_SLUGS, BASE_URL_TEMPLATE,
    ITEMS_PER_PAGE, MAX_PAGINAS,
    DELAY_ENTRE_PAGES, REQUEST_TIMEOUT,
    SECTORES_EXCLUIDOS_PROVIDENCIA, MAX_PRECIO_TOTAL_CLP,
    PALABRAS_CLAVE_NO_VALIDAS
)
from uf_helper import obtener_valor_uf

logger = logging.getLogger(__name__)


def _url_sin_query(url: str) -> str:
    partes = urlsplit(url)
    return urlunsplit((partes.scheme, partes.netloc, partes.path, "", ""))


class _FetchResult(NamedTuple):
    html: Optional[str]
    status_code: Optional[int]
    final_url: str


# ─────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────

def _get_headers() -> dict:
    return {
        "User-Agent": "DeptoMonitor/1.0 (+https://github.com/mruanov/depto-monitor)",
        "Accept-Language": "es-CL,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }


def _fetch_html(url: str, retries: int = 3) -> _FetchResult:
    """Descarga HTML con reintentos acotados y devuelve el estado de la respuesta."""
    status_code = None
    final_url = url
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
            status_code = resp.status_code
            final_url = resp.url
            if final_url != url:
                logger.warning(
                    "El portal redirigió la búsqueda (HTTP %s): %s -> %s",
                    status_code, _url_sin_query(url), _url_sin_query(final_url),
                )
            if resp.status_code == 200:
                return _FetchResult(resp.text, status_code, final_url)

            logger.warning("HTTP %s para %s (intento %d/%d)", status_code, url, attempt, retries)
            if status_code in (401, 403, 404):
                break
            if status_code < 500 and status_code != 429:
                break
        except requests.RequestException as exc:
            logger.warning("Error de red (%d/%d): %s", attempt, retries, exc)
        if attempt < retries:
            sleep = random.uniform(1, 3)
            logger.info("Reintento %d/%d en %.1f s.", attempt + 1, retries, sleep)
            time.sleep(sleep)
    return _FetchResult(None, status_code, final_url)


def _fetch_html_playwright(url: str) -> Optional[str]:
    """Fallback con Playwright + stealth para páginas con JS pesado."""
    try:
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
            use_stealth = True
        except ImportError:
            use_stealth = False
            logger.warning("playwright-stealth no disponible, usando Playwright sin stealth")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="es-CL",
                timezone_id="America/Santiago",
            )
            page = context.new_page()

            if use_stealth:
                stealth_sync(page)

            page.set_extra_http_headers({"Accept-Language": "es-CL,es;q=0.9"})
            page.goto("https://www.portalinmobiliario.com/", timeout=REQUEST_TIMEOUT * 1000)
            page.wait_for_timeout(2000)
            page.goto(url, timeout=REQUEST_TIMEOUT * 1000)
            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        logger.error("Playwright falló: %s", exc)
        return None


# ─────────────────────────────────────────────────────────
# EXTRACCIÓN DE PRECIO Y GC DESDE TEXTO
# ─────────────────────────────────────────────────────────

def _extraer_numero(texto: str) -> Optional[float]:
    """Extrae el primer número (con puntos/comas) de un string."""
    limpio = texto.replace(".", "").replace(",", ".")
    m = re.search(r"[\d]+(?:\.\d+)?", limpio)
    return float(m.group()) if m else None


def _parse_precio(texto_precio: str, uf_valor: float) -> Tuple[Optional[float], Optional[int]]:
    """
    Retorna (precio_uf, precio_clp).
    Detecta si el precio está en UF o en $ (CLP).
    """
    if not texto_precio:
        return None, None

    texto = texto_precio.strip().upper()
    numero = _extraer_numero(texto_precio)
    if numero is None:
        return None, None

    if "UF" in texto:
        return numero, int(numero * uf_valor)
    elif "$" in texto or "CLP" in texto or "PESOS" in texto:
        return None, int(numero)
    else:
        if numero < 500:
            return numero, int(numero * uf_valor)
        return None, int(numero)


def _extraer_gc_de_descripcion(descripcion: str, uf_valor: float) -> Optional[int]:
    """Busca en el texto de la descripción patrones de gastos comunes."""
    if not descripcion:
        return None

    desc_lower = descripcion.lower()
    if re.search(
        r"\bno\s+(?:se\s+)?(?:paga|pagan|incluye)\s+(?:los\s+)?gastos comunes\b",
        desc_lower,
    ):
        return 0

    patrones = [
        r"(?:gastos comunes|\bgc\b|g\.c\.)[^\d\n.!]{0,20}(\d[\d\.,]*)\s*(uf|clp|\$|pesos|mil)?",
        r"\$\s*(\d[\d\.]*)\s*(?:de\s+)?(?:gastos\s+comunes|gc)",
    ]

    for patron in patrones:
        m = re.search(patron, desc_lower)
        if m:
            raw_num = m.group(1).replace(".", "").replace(",", ".")
            try:
                numero = float(raw_num)
            except ValueError:
                continue

            unidad = (m.group(2) or "").strip()
            if "uf" in unidad:
                return int(numero * uf_valor)
            elif numero < 500:
                return int(numero * uf_valor)
            elif "mil" in desc_lower[max(0, m.start()-5):m.end()+10]:
                return int(numero * 1000)
            else:
                return int(numero)

    return None


# ─────────────────────────────────────────────────────────
# PARSEO DE TARJETAS DE LISTADO
# ─────────────────────────────────────────────────────────

def _obtener_texto_tarjeta(card) -> str:
    """Combina el texto visible y atributos del DOM para detectar avisos no válidos."""
    fragmentos = [card.get_text(" ", strip=True)]
    for elemento in card.find_all(True):
        for valor in elemento.attrs.values():
            if isinstance(valor, (list, tuple)):
                fragmentos.extend(str(item) for item in valor)
            else:
                fragmentos.append(str(valor))
    return re.sub(r"\s+", " ", " ".join(fragmentos)).lower()


def _contiene_comuna(texto: str, comuna: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9]){re.escape(comuna.lower())}(?![a-z0-9])",
        texto.lower(),
    ) is not None


def _card_es_publicacion_valida(card, comuna: str, ubicacion: str) -> bool:
    """Descarta rápidamente avisos patrocinados, proyectos o propiedades fuera de la comuna."""
    texto = _obtener_texto_tarjeta(card)
    if not texto:
        return False

    if not _contiene_comuna(ubicacion, comuna):
        return False

    if any(palabra in texto for palabra in PALABRAS_CLAVE_NO_VALIDAS):
        return False

    if comuna == "Providencia":
        context = f"{ubicacion} {texto}"
        for sector in SECTORES_EXCLUIDOS_PROVIDENCIA:
            if sector.lower() in context:
                return False

    return True


def _parsear_tarjeta(card, comuna: str, uf_valor: float) -> Optional[dict]:
    """Extrae datos de una tarjeta de propiedad del HTML de listado."""
    try:
        enlace = card.select_one("a.poly-component__title, a[href*='/MLC-']")
        if not enlace:
            enlace = card.find("a", href=re.compile(r"/MLC-\d+"))
        if not enlace:
            return None

        url = enlace.get("href", "")
        if not url.startswith("http"):
            url = "https://www.portalinmobiliario.com" + url

        m_id = re.search(r"MLC-(\d+)", url)
        pub_id = m_id.group(0) if m_id else url.split("/")[-1].split("?")[0]

        titulo_tag = card.select_one(
            ".poly-component__title, h2.ui-search-item__title, "
            "h2[class*='title'], .ui-search-item__title"
        )
        titulo = titulo_tag.get_text(strip=True) if titulo_tag else "Sin título"

        ubic_tag = card.select_one(
            ".poly-component__location, .ui-search-item__location, "
            "span[class*='location']"
        )
        ubicacion = ubic_tag.get_text(strip=True) if ubic_tag else ""

        if not _card_es_publicacion_valida(card, comuna, ubicacion):
            logger.debug("Descartada tarjeta no válida para %s: %s", comuna, titulo)
            return None

        precio_tag = card.select_one(
            ".poly-price__current, .price-tag-fraction, "
            "span[class*='price-tag'], .andes-money-amount__fraction"
        )
        precio_txt = precio_tag.get_text(strip=True) if precio_tag else ""

        precio_contenedor = card.select_one(
            ".poly-price__current, [class*='price']"
        )
        contenedor_txt = precio_contenedor.get_text(" ", strip=True) if precio_contenedor else ""

        precio_uf, precio_clp = _parse_precio(contenedor_txt, uf_valor)
        if precio_uf is None and precio_clp is None and precio_txt:
            precio_uf, precio_clp = _parse_precio(precio_txt, uf_valor)

        if precio_clp is not None and precio_clp > MAX_PRECIO_TOTAL_CLP:
            logger.debug("Descartado por precio superior al máximo: %s / $%s", titulo, f"{precio_clp:,}")
            return None

        if precio_uf is not None and precio_uf > MAX_PRECIO_TOTAL_CLP / max(uf_valor, 1):
            logger.debug("Descartado por UF superior al máximo: %s / %.0f UF", titulo, precio_uf)
            return None

        superficie, dormitorios, banos = None, None, None

        attrs = card.select(
            ".poly-attributes-list__item, "
            "li[class*='attribute'], "
            "ul[class*='attributes'] li, "
            ".ui-search-card-attributes__attribute"
        )
        for attr in attrs:
            txt = attr.get_text(" ", strip=True).lower()
            if "m²" in txt or "m2" in txt:
                nm = re.search(r"([\d,.]+)\s*m[²2]", txt)
                if nm and superficie is None:
                    superficie = float(nm.group(1).replace(",", "."))
            elif "dorm" in txt or "hab" in txt or "pieza" in txt:
                nd = re.search(r"(\d+)", txt)
                if nd and dormitorios is None:
                    dormitorios = int(nd.group(1))
            elif "baño" in txt or "bano" in txt:
                nb = re.search(r"(\d+)", txt)
                if nb and banos is None:
                    banos = int(nb.group(1))

        img_tag = card.select_one(
            "img.poly-component__picture, img[src*='http'], img[data-src*='http']"
        )
        imagen_url = None
        if img_tag:
            imagen_url = img_tag.get("data-src") or img_tag.get("src")
            if imagen_url and imagen_url.startswith("data:"):
                imagen_url = None

        return {
            "id":           pub_id,
            "url":          url,
            "titulo":       titulo,
            "ubicacion":    ubicacion,
            "comuna":       comuna,
            "precio_uf":    precio_uf,
            "precio_clp":   precio_clp,
            "gc_clp":       None,
            "precio_total": None,
            "superficie":   superficie,
            "dormitorios":  dormitorios,
            "banos":        banos,
            "imagen_url":   imagen_url,
            "score":        0.0,
        }

    except Exception as exc:
        logger.warning("Error parseando tarjeta: %s", exc)
        return None


# ─────────────────────────────────────────────────────────
# DETALLE DE PUBLICACIÓN
# ─────────────────────────────────────────────────────────

def _obtener_gc_desde_detalle(url: str, uf_valor: float) -> Optional[int]:
    html = _fetch_html(url).html
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")

    for row in soup.select("tr, li, .ui-pdp-feature"):
        txt = row.get_text(" ", strip=True).lower()
        if "gastos" in txt or "gasto comun" in txt or " gc " in txt:
            gc = _extraer_gc_de_descripcion(txt, uf_valor)
            if gc is not None:
                return gc

    desc_tag = soup.select_one(
        ".ui-pdp-description__content, p[class*='description'], "
        "[data-testid='description']"
    )
    if desc_tag:
        gc = _extraer_gc_de_descripcion(desc_tag.get_text(" ", strip=True), uf_valor)
        if gc:
            return gc

    return None


# ─────────────────────────────────────────────────────────
# SCRAPING PRINCIPAL
# ─────────────────────────────────────────────────────────

def scrapear_comuna(comuna: str) -> List[dict]:
    """
    Itera páginas del listado de Portal Inmobiliario para una comuna,
    forzando filtros nativos de arriendo residencial y precio máximo.
    """
    slug = COMUNA_SLUGS.get(comuna)
    if not slug:
        logger.error("Slug no encontrado para la comuna: %s", comuna)
        return []

    uf_valor = obtener_valor_uf()
    publicaciones = []

    for pagina in range(MAX_PAGINAS):
        offset = pagina * ITEMS_PER_PAGE + 1 if pagina > 0 else 1

        url = BASE_URL_TEMPLATE.format(
            comuna=slug,
            max_precio=MAX_PRECIO_TOTAL_CLP,
        )
        if pagina > 0:
            url += f"_Desde_{offset}"
        url += "_NoIndex_True"

        logger.info("[%s] Scrapeando página %d — %s", comuna, pagina + 1, url)

        resultado = _fetch_html(url)
        html = resultado.html

        if not html and resultado.status_code not in (401, 403, 429):
            logger.warning("Fallback a Playwright para: %s", url)
            html = _fetch_html_playwright(url)
        elif not html:
            logger.warning(
                "No se usará Playwright tras HTTP %s para evitar repetir una solicitud bloqueada: %s",
                resultado.status_code, resultado.final_url,
            )

        if not html:
            logger.error("No se pudo obtener HTML para: %s", url)
            break

        soup = BeautifulSoup(html, "lxml")

        cards = soup.select("li.ui-search-layout__item")
        if not cards:
            cards = soup.select("div.poly-card")

        if not cards:
            texto_html = html.lower()
            indicadores_bloqueo = (
                "captcha",
                "access denied",
                "acceso denegado",
                "verify you are human",
                "security check",
                "unusual traffic",
                "robot check",
                "temporarily blocked",
            )
            titulo = soup.title.get_text(" ", strip=True) if soup.title else "(sin título)"
            if any(indicador in texto_html for indicador in indicadores_bloqueo):
                logger.warning(
                    "[%s] Posible bloqueo/CAPTCHA: HTTP 200 sin tarjetas; título=%r, URL final=%s",
                    comuna, titulo, _url_sin_query(resultado.final_url),
                )
            elif "/gz/account-verification" in resultado.final_url.lower():
                logger.warning(
                    "[%s] El portal redirigió a verificación de cuenta y no devolvió tarjetas; "
                    "la búsqueda está bloqueada para este request. URL final=%s",
                    comuna, _url_sin_query(resultado.final_url),
                )
            elif resultado.final_url != url:
                logger.warning(
                    "[%s] Redirección sin resultados: HTTP 200 sin tarjetas; "
                    "posible verificación, bloqueo o página inesperada. URL final=%s",
                    comuna, _url_sin_query(resultado.final_url),
                )
            else:
                logger.warning(
                    "[%s] Respuesta HTTP 200 sin tarjetas ui-search-layout__item ni poly-card "
                    "(%d poly-card detectadas); posible página vacía o cambio de DOM. "
                    "título=%r, URL final=%s",
                    comuna, len(soup.select("div[class*='poly-card']")), titulo,
                    _url_sin_query(resultado.final_url),
                )
            break

        for card in cards:
            pub = _parsear_tarjeta(card, comuna, uf_valor)
            if pub:
                publicaciones.append(pub)

        logger.info("[%s] Página %d: %d avisos encontrados", comuna, pagina + 1, len(cards))

        if len(cards) < ITEMS_PER_PAGE:
            break

        time.sleep(DELAY_ENTRE_PAGES + random.uniform(0, 1.5))

    logger.info("[%s] Total crudo: %d publicaciones", comuna, len(publicaciones))
    return publicaciones


def scrapear_todas_las_comunas() -> List[dict]:
    todas = []
    for comuna in COMUNAS:
        avisos = scrapear_comuna(comuna)
        todas.extend(avisos)
        time.sleep(DELAY_ENTRE_PAGES * 2)
    return todas


def enriquecer_con_gc(pub: dict) -> dict:
    if pub.get("gc_clp") is None:
        uf_valor = obtener_valor_uf()
        gc = _obtener_gc_desde_detalle(pub["url"], uf_valor)
        pub["gc_clp"] = gc
    return pub