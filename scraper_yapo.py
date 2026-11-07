import json
import logging
import re
import time
from urllib.parse import urljoin

from scraper import ScrapePartialError, _crear_soup
from config import (
    COMUNAS,
    MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    MAX_PRECIO_TOTAL_CLP,
    YAPO_API_BASE_URL,
    YAPO_BASE_URL,
    YAPO_COMUNA_SLUGS,
    YAPO_DELAY_ENTRE_DETALLES,
    YAPO_DELAY_ENTRE_PAGINAS,
    YAPO_MAX_PAGINAS,
)
from fuentes_comunes import (
    FuenteError,
    extraer_gc_de_texto,
    obtener_texto,
    parse_monto_clp,
    parse_precio,
)
from runtime_settings import obtener_comunas, obtener_precio_maximo
from uf_helper import obtener_valor_uf

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
    ),
}

_YAPO_ID = re.compile(r"/(\d+)(?:\?.*)?$")
_M2 = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:2|²)?\b", re.IGNORECASE)
_DORMITORIOS = re.compile(r"(\d+)\s*dormitorios?\b", re.IGNORECASE)
_BANOS = re.compile(r"(\d+)\s*ba(?:n|ñ)os?\b", re.IGNORECASE)
_RSC_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')

_SIN_RESULTADOS = (
    "no encontramos",
    "no hay resultados",
    "sin resultados",
    "no se encontraron",
)


def _construir_url(slug, pagina):
    base = f"{YAPO_API_BASE_URL}/bienes-raices-alquiler-apartamentos/{slug}"
    if pagina:
        base = f"{base}.{pagina + 1}"
    return f"{base}?sort=f_added&dir=desc"


def _texto(elemento):
    return elemento.get_text(" ", strip=True) if elemento else None


def _texto_plano(html):
    if not html:
        return ""
    return _crear_soup(str(html)).get_text(" ", strip=True)


def _stream_rsc(html):
    partes = []
    for coincidencia in _RSC_PUSH.finditer(html):
        try:
            partes.append(json.loads(coincidencia.group(1)))
        except json.JSONDecodeError:
            continue
    return "".join(partes)


def _segmento_actual(stream, yapo_id):
    posicion = stream.find(f'"id":"{yapo_id}"')
    if posicion < 0:
        return ""
    return stream[max(0, posicion - 12000):posicion + 2000]


def _descripcion_actual(stream, segmento):
    referencias = list(
        re.finditer(r'"description":"((?:\\.|[^"\\])*)"', segmento)
    )
    if not referencias:
        return ""
    valor = referencias[-1].group(1)
    referencia = re.fullmatch(r"\$(\d+)", valor)
    if not referencia:
        return _texto_plano(valor)
    marcador = re.search(
        rf'(?:^|\n){re.escape(referencia.group(1))}:T(\d+),', stream
    )
    if not marcador:
        return ""
    inicio = marcador.end()
    return _texto_plano(stream[inicio:inicio + int(marcador.group(1))])


def _parsear_detalle(html, uf_valor, ubicacion_actual, yapo_id):
    stream = _stream_rsc(html)
    segmento = _segmento_actual(stream, yapo_id)
    descripcion = _descripcion_actual(stream, segmento)

    if not descripcion:
        soup = _crear_soup(html)
        meta = soup.find("meta", attrs={"name": "description"})
        descripcion = meta.get("content") if meta else ""

    gc_clp = None
    coincidencia = re.search(
        r'"maintenanceCost":\{"currency".*?"amount":\{"value":(\d+)',
        segmento,
        re.DOTALL,
    )
    if coincidencia:
        gc_clp = int(coincidencia.group(1))
        if 0 < gc_clp < 1000:
            gc_clp = None
    if gc_clp is None:
        gc_clp = extraer_gc_de_texto(descripcion, uf_valor)

    superficie = None
    coincidencia = re.search(r'"square":(\d+(?:\.\d+)?)', segmento)
    if coincidencia:
        superficie = float(coincidencia.group(1))
    if superficie is None:
        coincidencia = _M2.search(descripcion)
        if coincidencia:
            superficie = float(coincidencia.group(1).replace(",", "."))

    ubicacion = ubicacion_actual
    coincidencia = re.search(
        r'"location":\{[^{}]*?"locality":"(.*?)"', segmento
    )
    if coincidencia:
        ubicacion = coincidencia.group(1).replace("\\/", "/")

    dormitorios = None
    coincidencia = re.search(r'"rooms":"(\d+)"', segmento)
    if coincidencia:
        dormitorios = int(coincidencia.group(1))
    if dormitorios is None:
        coincidencia = _DORMITORIOS.search(descripcion)
        if coincidencia:
            dormitorios = int(coincidencia.group(1))

    banos = None
    coincidencia = re.search(r'"bathrooms":"(\d+)"', segmento)
    if coincidencia:
        banos = int(coincidencia.group(1))
    if banos is None:
        coincidencia = _BANOS.search(descripcion)
        if coincidencia:
            banos = int(coincidencia.group(1))

    return descripcion, gc_clp, superficie, ubicacion, dormitorios, banos


def _enriquecer_con_detalle(publicacion, uf_valor):
    try:
        html, _ = obtener_texto(publicacion["url"], headers=_HEADERS)
    except FuenteError as exc:
        logger.warning("Yapo %s sin detalle: %s", publicacion["id"], exc)
        return publicacion

    yapo_id = publicacion["id"].split("-", 1)[-1]
    descripcion, gc_clp, superficie, ubicacion, dormitorios, banos = _parsear_detalle(
        html, uf_valor, publicacion.get("ubicacion"), yapo_id
    )
    if descripcion:
        publicacion["descripcion"] = descripcion
        publicacion["descripcion_corta"] = descripcion[:200]
    if gc_clp is not None:
        publicacion["gc_clp"] = gc_clp
    if superficie is not None:
        publicacion["superficie"] = superficie
    if ubicacion:
        publicacion["ubicacion"] = ubicacion
    if publicacion.get("dormitorios") is None and dormitorios is not None:
        publicacion["dormitorios"] = dormitorios
    if publicacion.get("banos") is None and banos is not None:
        publicacion["banos"] = banos
    time.sleep(YAPO_DELAY_ENTRE_DETALLES)
    return publicacion


def _parsear_especificaciones(tile):
    dormitorios = None
    banos = None
    for elemento in tile.select(".d3-ad-tile__details-item"):
        icono = elemento.select_one("use")
        referencia = ""
        if icono:
            referencia = icono.get("xlink:href") or icono.get("href") or ""
        numero = re.search(r"(\d+)", elemento.get_text(" ", strip=True))
        if not numero:
            continue
        valor = int(numero.group(1))
        if "bed" in referencia and dormitorios is None:
            dormitorios = valor
        elif "bath" in referencia and banos is None:
            banos = valor
    return dormitorios, banos


def _parsear_tarjeta(tile, comuna_objetivo, uf_valor, precio_maximo):
    enlace = tile.select_one(".d3-ad-tile__description")
    if not enlace:
        enlace = tile.select_one("a[href*='/bienes-raices-alquiler-apartamentos/']")
    href = enlace.get("href") if enlace else ""
    coincidencia = _YAPO_ID.search(href or "")
    if not coincidencia:
        return None

    titulo = _texto(tile.select_one(".d3-ad-tile__title")) or ""
    descripcion = _texto(tile.select_one(".d3-ad-tile__short-description")) or ""
    ubicacion = _texto(tile.select_one(".d3-ad-tile__location")) or ""
    precio_texto = _texto(tile.select_one(".d3-ad-tile__price"))
    precio_uf, precio_clp = parse_precio(precio_texto, uf_valor)
    if precio_clp is None and precio_uf is None:
        return None
    if precio_clp is not None and precio_clp > precio_maximo:
        return None

    gc_clp = parse_monto_clp(
        _texto(tile.select_one(".d3-ad-tile__common-expenses")), uf_valor
    )
    if gc_clp is None:
        gc_clp = extraer_gc_de_texto(descripcion, uf_valor)

    dormitorios, banos = _parsear_especificaciones(tile)
    imagen = tile.select_one("img.d3-ad-tile__photo")
    imagen_url = None
    if imagen:
        imagen_url = imagen.get("data-original") or imagen.get("src")
        if imagen_url and imagen_url.startswith("data:"):
            imagen_url = None
    yapo_id = coincidencia.group(1)

    return {
        "id": f"YAPO-{yapo_id}",
        "titulo": titulo,
        "descripcion": descripcion,
        "descripcion_corta": descripcion[:200],
        "precio_clp": precio_clp,
        "precio_uf": precio_uf,
        "gc_clp": gc_clp,
        "superficie": None,
        "dormitorios": dormitorios,
        "banos": banos,
        "comuna": comuna_objetivo,
        "ubicacion": ubicacion,
        "url": urljoin(YAPO_BASE_URL, href),
        "imagen_url": imagen_url,
        "fuente": "Yapo",
    }


def scrapear_comuna(comuna):
    slug = YAPO_COMUNA_SLUGS.get(comuna)
    if not slug:
        raise FuenteError(f"Sin slug de Yapo para {comuna}")

    uf_valor = obtener_valor_uf()
    precio_maximo = max(
        obtener_precio_maximo(MAX_PRECIO_TOTAL_CLP),
        MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    )
    publicaciones = []

    for pagina in range(YAPO_MAX_PAGINAS):
        url = _construir_url(slug, pagina)
        html, url_final = obtener_texto(url, headers=_HEADERS)
        soup = _crear_soup(html)
        tarjetas = soup.select(".d3-ad-tile")

        if not tarjetas:
            texto_pagina = soup.get_text(" ", strip=True).lower()
            if any(marcador in texto_pagina for marcador in _SIN_RESULTADOS):
                logger.info("[%s] Yapo no devolvió resultados en la página %d", comuna, pagina + 1)
                break
            raise FuenteError(
                f"Yapo devolvió una página sin tarjetas para {comuna} "
                f"(página {pagina + 1}, URL {url_final})"
            )

        for tile in tarjetas:
            publicacion = _parsear_tarjeta(
                tile, comuna, uf_valor, precio_maximo
            )
            if not publicacion:
                continue
            publicaciones.append(_enriquecer_con_detalle(publicacion, uf_valor))

        logger.info(
            "[%s] Yapo página %d: %d tarjetas, %d avisos útiles",
            comuna,
            pagina + 1,
            len(tarjetas),
            len(publicaciones),
        )

        if len(tarjetas) < 30:
            break
        if pagina + 1 < YAPO_MAX_PAGINAS:
            time.sleep(YAPO_DELAY_ENTRE_PAGINAS)

    logger.info("[%s] Yapo total crudo: %d publicaciones", comuna, len(publicaciones))
    return publicaciones


def scrapear_todas_las_comunas():
    todas = []
    errores = []
    comunas = obtener_comunas(COMUNAS)

    for comuna in comunas:
        try:
            avisos = scrapear_comuna(comuna)
        except Exception as exc:
            logger.warning("[%s] Yapo falló: %s", comuna, exc)
            errores.append(exc)
            continue
        todas.extend(avisos)
        time.sleep(YAPO_DELAY_ENTRE_PAGINAS)

    if errores and len(errores) == len(comunas):
        raise errores[0]
    if errores:
        raise ScrapePartialError(todas, errores)
    return todas
