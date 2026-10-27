"""
=============================================================
  MÓDULO: SCRAPER (Optimizado con Filtros Nativos de URL)
  Extracción de publicaciones desde Portal Inmobiliario
=============================================================
Estrategia:
- Requests + BeautifulSoup para el listado (rápido, sin JS)
- Playwright estándar como fallback si requests no obtiene HTML
- Retry con backoff exponencial ante errores de red
- Identificación explícita del cliente; sin ocultar la automatización
- Filtrado nativo en URL por precio máximo y arriendo tradicional
=============================================================
"""

import re
import time
import random
import logging
import importlib.util
import requests
from typing import NamedTuple, Optional, Tuple, List
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, FeatureNotFound

try:
    from curl_cffi import requests as curl_requests
    from curl_cffi.requests.errors import RequestsError as CurlRequestsError
except ImportError:  # pragma: no cover - curl_cffi se instala vía requirements.txt
    curl_requests = None
    CurlRequestsError = None

from config import (
    COMUNAS, COMUNA_SLUGS, BASE_URL_TEMPLATE,
    ITEMS_PER_PAGE, MAX_PAGINAS,
    DELAY_ENTRE_PAGES, REQUEST_TIMEOUT,
    SECTORES_EXCLUIDOS_PROVIDENCIA, MAX_PRECIO_TOTAL_CLP,
    MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
)
from uf_helper import obtener_valor_uf
from runtime_settings import obtener_comunas, obtener_precio_maximo

logger = logging.getLogger(__name__)


def _url_sin_query(url: str) -> str:
    partes = urlsplit(url)
    return urlunsplit((partes.scheme, partes.netloc, partes.path, "", ""))


def _crear_soup(html: str) -> BeautifulSoup:
    """Usa lxml si está instalado; en Termux cae a html.parser."""
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html, "html.parser")


def _playwright_disponible() -> bool:
    return importlib.util.find_spec("playwright") is not None


class _FetchResult(NamedTuple):
    html: Optional[str]
    status_code: Optional[int]
    final_url: str


class ScrapeAccessError(RuntimeError):
    """Portal denied the request or returned an access-verification page."""


class ScrapeUnavailableError(RuntimeError):
    """No usable response was received from the portal."""


class ScrapePartialError(ScrapeUnavailableError):
    """Some communes failed, but publications were collected from the others."""

    def __init__(self, publicaciones: List[dict], errores: List[Exception]):
        super().__init__(
            f"Scraping parcial: {len(errores)} comuna(s) no pudieron consultarse."
        )
        self.publicaciones = publicaciones
        self.errores = errores


# ─────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────

_REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
_IMPERSONATE_TARGETS = ("chrome", "safari", "chrome124")
_REQUEST_EXCEPTIONS = (requests.RequestException,)
if CurlRequestsError is not None:
    _REQUEST_EXCEPTIONS += (CurlRequestsError,)


def _get_headers() -> dict:
    return {
        "User-Agent": _REAL_CHROME_UA,
        "Accept-Language": "es-CL,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Sec-Ch-Ua": '"Chromium";v="136", "Not(A:Brand";v="24", "Google Chrome";v="136"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _impersonate_target(attempt: int) -> str:
    return _IMPERSONATE_TARGETS[(attempt - 1) % len(_IMPERSONATE_TARGETS)]


def _es_verificacion_cuenta(final_url: str) -> bool:
    return "/gz/account-verification" in final_url.lower()


def _http_get(url: str, attempt: int = 1):
    """GET con huella TLS de navegador real cuando curl_cffi está disponible."""
    if curl_requests is not None:
        return curl_requests.get(
            url,
            headers={"Accept-Language": "es-CL,es;q=0.9,en-US;q=0.8,en;q=0.7"},
            impersonate=_impersonate_target(attempt),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    return requests.get(url, headers=_get_headers(), timeout=REQUEST_TIMEOUT)


def _fetch_html(url: str, retries: int = 3) -> _FetchResult:
    """Descarga HTML con reintentos acotados y devuelve el estado de la respuesta."""
    status_code = None
    final_url = url
    error_html = None
    for attempt in range(1, retries + 1):
        try:
            resp = _http_get(url, attempt)
            status_code = resp.status_code
            final_url = resp.url
            if final_url != url:
                log = logger.info if status_code == 200 else logger.warning
                log(
                    "El portal normalizó/redirigió la URL (HTTP %s): %s -> %s",
                    status_code, _url_sin_query(url), _url_sin_query(final_url),
                )
            if status_code == 200 and not _es_verificacion_cuenta(final_url):
                return _FetchResult(resp.text, status_code, final_url)
            if status_code == 200:
                logger.warning(
                    "El portal pidió verificación de cuenta; se reintentará con otro "
                    "perfil TLS (%s, intento %d/%d).",
                    _impersonate_target(attempt), attempt, retries,
                )
            else:
                if status_code == 404:
                    error_html = resp.text

                log = logger.info if status_code == 404 else logger.warning
                log("HTTP %s para %s (intento %d/%d)", status_code, url, attempt, retries)
                if status_code in (401, 403, 404, 429):
                    break
                if status_code < 500:
                    break
        except _REQUEST_EXCEPTIONS as exc:
            logger.warning("Error de red (%d/%d): %s", attempt, retries, exc)
        if attempt < retries:
            sleep = random.uniform(1, 3)
            logger.info("Reintento %d/%d en %.1f s.", attempt + 1, retries, sleep)
            time.sleep(sleep)
    return _FetchResult(error_html, status_code, final_url)


def _fetch_html_playwright(url: str) -> _FetchResult:
    """Fallback con Playwright estándar para páginas que requieren JavaScript."""
    status_code = None
    final_url = url
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    locale="es-CL",
                    timezone_id="America/Santiago",
                    user_agent=_REAL_CHROME_UA,
                    viewport={"width": 1366, "height": 768},
                    extra_http_headers={
                        "Accept-Language": "es-CL,es;q=0.9,en-US;q=0.8,en;q=0.7"
                    },
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"
                )
                page = context.new_page()
                response = page.goto(url, timeout=REQUEST_TIMEOUT * 1000)
                if response:
                    status_code = response.status
                final_url = page.url
                page.wait_for_timeout(3000)
                return _FetchResult(page.content(), status_code, final_url)
            finally:
                browser.close()
    except Exception as exc:
        logger.error("Playwright falló: %s", exc)
        return _FetchResult(None, status_code, final_url)


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


def _construir_url_busqueda(slug: str, pagina: int) -> str:
    maximo_busqueda = max(
        obtener_precio_maximo(MAX_PRECIO_TOTAL_CLP),
        MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    )
    url = BASE_URL_TEMPLATE.format(
        comuna=slug,
        max_precio=maximo_busqueda,
    )
    if pagina > 0:
        url += f"_Desde_{pagina * ITEMS_PER_PAGE + 1}"
    return url


def _es_pagina_sin_resultados(html: str) -> bool:
    soup = _crear_soup(html)
    return (
        soup.select_one(".ui-search-rescue--zrp") is not None
        or "no hay inmuebles que coincidan con tu búsqueda"
        in soup.get_text(" ", strip=True).lower()
    )


def _card_es_publicacion_valida(card, comuna: str, ubicacion: str) -> bool:
    """Descarta rápidamente avisos patrocinados, proyectos o propiedades fuera de la comuna."""
    texto = _obtener_texto_tarjeta(card)
    if not texto:
        return False

    # Ciudad Empresarial se busca con el slug de Huechuraba; el filtro final
    # exige que el título o la descripción mencione explícitamente el sector.
    comunas_validas = (
        ("Ciudad Empresarial", "Huechuraba")
        if comuna == "Ciudad Empresarial"
        else (comuna,)
    )
    if not any(_contiene_comuna(ubicacion, objetivo) for objetivo in comunas_validas):
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

        maximo = max(
            obtener_precio_maximo(MAX_PRECIO_TOTAL_CLP),
            MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
        )
        if precio_clp is not None and precio_clp > maximo:
            logger.debug("Descartado por precio superior al máximo: %s / $%s", titulo, f"{precio_clp:,}")
            return None

        if precio_uf is not None and precio_uf > maximo / max(uf_valor, 1):
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

def _obtener_detalle_publicacion(
    url: str,
    uf_valor: float,
) -> Tuple[Optional[int], Optional[str]]:
    html = _fetch_html(url).html
    if not html:
        return None, None

    soup = _crear_soup(html)
    descripcion = None
    desc_tag = soup.select_one(
        ".ui-pdp-description__content, p[class*='description'], "
        "[data-testid='description']"
    )
    if desc_tag:
        descripcion = desc_tag.get_text(" ", strip=True)
    else:
        meta_desc = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta_desc:
            descripcion = meta_desc.get("content")

    gc_clp = None
    for row in soup.select("tr, li, .ui-pdp-feature"):
        txt = row.get_text(" ", strip=True).lower()
        if "gastos" in txt or "gasto comun" in txt or " gc " in txt:
            gc = _extraer_gc_de_descripcion(txt, uf_valor)
            if gc is not None:
                gc_clp = gc
                break
    if gc_clp is None and descripcion:
        gc_clp = _extraer_gc_de_descripcion(descripcion, uf_valor)
    return gc_clp, descripcion


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
        url = _construir_url_busqueda(slug, pagina)

        logger.info("[%s] Scrapeando página %d — %s", comuna, pagina + 1, url)

        resultado = _fetch_html(url)
        html = resultado.html

        if resultado.status_code in (401, 403, 429):
            raise ScrapeAccessError(
                f"Portal rechazó la búsqueda de {comuna} con HTTP {resultado.status_code}."
            )
        if _es_verificacion_cuenta(resultado.final_url):
            raise ScrapeAccessError(
                f"Portal redirigió la búsqueda de {comuna} a verificación de cuenta."
            )

        if not html and resultado.status_code not in (401, 403, 404, 429):
            if not _playwright_disponible():
                logger.warning(
                    "Playwright no está instalado; se omite el fallback para: %s", url
                )
            else:
                logger.warning("Fallback a Playwright para: %s", url)
                resultado = _fetch_html_playwright(url)
                html = resultado.html
                if resultado.status_code in (401, 403, 429):
                    raise ScrapeAccessError(
                        f"Portal rechazó la búsqueda de {comuna} con HTTP {resultado.status_code}."
                    )
                if _es_verificacion_cuenta(resultado.final_url):
                    raise ScrapeAccessError(
                        f"Portal redirigió la búsqueda de {comuna} a verificación de cuenta."
                    )
        elif not html:
            logger.warning(
                "No se usará Playwright tras HTTP %s para evitar repetir una solicitud bloqueada: %s",
                resultado.status_code, resultado.final_url,
            )

        if not html:
            logger.error("No se pudo obtener HTML para: %s", url)
            raise ScrapeUnavailableError(
                f"No se obtuvo HTML para la búsqueda de {comuna}."
            )

        soup = _crear_soup(html)

        cards = soup.select("li.ui-search-layout__item")
        if not cards:
            cards = soup.select("div.poly-card")

        if not cards:
            if resultado.status_code == 404 and _es_pagina_sin_resultados(html):
                logger.info(
                    "[%s] El portal confirmó que no hay avisos que cumplan la búsqueda.",
                    comuna,
                )
                break
            if resultado.status_code == 404:
                logger.warning(
                    "[%s] HTTP 404 sin el indicador de búsqueda vacía; URL final=%s",
                    comuna,
                    _url_sin_query(resultado.final_url),
                )
                raise ScrapeUnavailableError(
                    f"Portal devolvió HTTP 404 inesperado para {comuna}."
                )

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
                raise ScrapeAccessError(
                    f"Portal devolvió una pantalla de bloqueo/CAPTCHA para {comuna}."
                )
            elif _es_verificacion_cuenta(resultado.final_url):
                logger.warning(
                    "[%s] El portal redirigió a verificación de cuenta y no devolvió tarjetas; "
                    "la búsqueda está bloqueada para este request. URL final=%s",
                    comuna, _url_sin_query(resultado.final_url),
                )
                raise ScrapeAccessError(
                    f"Portal redirigió la búsqueda de {comuna} a verificación de cuenta."
                )
            elif resultado.final_url != url:
                logger.warning(
                    "[%s] Redirección sin resultados: HTTP 200 sin tarjetas; "
                    "posible verificación, bloqueo o página inesperada. URL final=%s",
                    comuna, _url_sin_query(resultado.final_url),
                )
                raise ScrapeAccessError(
                    f"Portal redirigió inesperadamente la búsqueda de {comuna}."
                )
            else:
                logger.warning(
                    "[%s] Respuesta HTTP 200 sin tarjetas ui-search-layout__item ni poly-card "
                    "(%d poly-card detectadas); posible página vacía o cambio de DOM. "
                    "título=%r, URL final=%s",
                    comuna, len(soup.select("div[class*='poly-card']")), titulo,
                    _url_sin_query(resultado.final_url),
                )
                raise ScrapeUnavailableError(
                    f"Respuesta sin tarjetas reconocibles para {comuna}; "
                    "no se puede distinguir entre cero resultados y un cambio de página."
                )

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
    errores = []
    comunas = obtener_comunas(COMUNAS)
    for comuna in comunas:
        try:
            avisos = scrapear_comuna(comuna)
        except ScrapeAccessError:
            logger.warning(
                "[%s] El portal denegó el acceso; se detendrá el ciclo para no insistir.",
                comuna,
            )
            raise
        except ScrapeUnavailableError as exc:
            logger.warning(
                "[%s] No se pudo completar el scraping; se continuará con las demás comunas: %s",
                comuna,
                exc,
            )
            errores.append(exc)
            time.sleep(DELAY_ENTRE_PAGES * 2)
            continue

        todas.extend(avisos)
        time.sleep(DELAY_ENTRE_PAGES * 2)

    if errores and len(errores) == len(comunas):
        raise errores[0]
    if errores:
        raise ScrapePartialError(todas, errores)

    return todas


def enriquecer_con_gc(pub: dict) -> dict:
    if pub.get("gc_clp") is None or not pub.get("descripcion"):
        uf_valor = obtener_valor_uf()
        gc, descripcion = _obtener_detalle_publicacion(pub["url"], uf_valor)
        if pub.get("gc_clp") is None:
            pub["gc_clp"] = gc
        if descripcion:
            pub["descripcion"] = descripcion
    return pub