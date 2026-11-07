import logging
import re
import time

from scraper import ScrapePartialError, _crear_soup
from config import (
    COMUNAS,
    GOPLACEIT_API_URL,
    GOPLACEIT_BASE_URL,
    GOPLACEIT_COMUNAS,
    GOPLACEIT_DELAY_ENTRE_REQUESTS,
    GOPLACEIT_HEADERS,
    GOPLACEIT_MAX_PAGINAS,
    GOPLACEIT_PER_PAGE,
    MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    MAX_PRECIO_TOTAL_CLP,
)
from fuentes_comunes import FuenteError, obtener_json
from runtime_settings import obtener_comunas, obtener_precio_maximo
from uf_helper import obtener_valor_uf

logger = logging.getLogger(__name__)

_M2 = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:2|²)?\b", re.IGNORECASE)
_DORMITORIOS = re.compile(r"(\d+)\s*dormitorios?\b", re.IGNORECASE)
_BANOS = re.compile(r"(\d+)\s*ba(?:n|ñ)os?\b", re.IGNORECASE)


def _texto_plano(html):
    if not html:
        return ""
    return _crear_soup(str(html)).get_text(" ", strip=True)


def _obtener_detalle(propiedad_id):
    return obtener_json(
        f"{GOPLACEIT_API_URL}/properties/{propiedad_id}",
        headers=GOPLACEIT_HEADERS,
    )


def _parsear_precio(item, uf_valor):
    moneda = item.get("moneda") or {}
    codigo = str(
        moneda.get("codigo_moneda") or moneda.get("iso_code") or ""
    ).upper()
    id_moneda = item.get("id_moneda") or moneda.get("id")
    valor = item.get("precio")
    if valor in (None, ""):
        return None, None
    valor = float(valor)
    if id_moneda == 2 or codigo in ("UF", "CLF"):
        return valor, None
    return None, int(round(valor))


def _parsear_gastos(detalle, uf_valor):
    valor = detalle.get("gastos_comunes")
    if valor in (None, ""):
        return None
    valor = float(valor)
    id_moneda = detalle.get("id_moneda_gastos")
    if id_moneda == 2 or (id_moneda not in (1, 2) and valor < 500):
        return int(round(valor * uf_valor))
    return int(round(valor))


def _parsear_superficie(detalle, descripcion, item=None):
    for origen in (detalle, item or {}):
        for valor in (origen.get("dimension_propiedad"), origen.get("superficie")):
            if valor not in (None, ""):
                try:
                    return float(valor)
                except (TypeError, ValueError):
                    pass
    coincidencia = _M2.search(descripcion)
    return float(coincidencia.group(1).replace(",", ".")) if coincidencia else None


def _parsear_entero(detalle, campo, texto, patron, item=None):
    for origen in (detalle, item or {}):
        valor = origen.get(campo)
        if valor not in (None, ""):
            try:
                return int(valor)
            except (TypeError, ValueError):
                pass
    coincidencia = patron.search(texto)
    return int(coincidencia.group(1)) if coincidencia else None


def _parsear_publicacion(item, comuna_objetivo, uf_valor, precio_maximo):
    propiedad_id = item.get("id")
    if not propiedad_id:
        return None

    precio_uf, precio_clp = _parsear_precio(item, uf_valor)
    if precio_clp is None and precio_uf is None:
        return None
    precio_equivalente = (
        precio_clp if precio_clp is not None else int(precio_uf * uf_valor)
    )
    if precio_equivalente > precio_maximo:
        return None

    try:
        detalle = _obtener_detalle(propiedad_id)
    except FuenteError as exc:
        logger.warning("goplaceit %s sin detalle: %s", propiedad_id, exc)
        detalle = {}
    finally:
        time.sleep(GOPLACEIT_DELAY_ENTRE_REQUESTS)

    titulo = detalle.get("titulo") or item.get("titulo") or ""
    descripcion = _texto_plano(detalle.get("descripcion"))
    if not descripcion:
        descripcion = titulo
    superficie = _parsear_superficie(detalle, descripcion, item)
    dormitorios = _parsear_entero(
        detalle, "habitaciones", descripcion, _DORMITORIOS, item
    )
    banos = _parsear_entero(detalle, "banos", descripcion, _BANOS, item)

    if precio_clp is None and detalle.get("precio") not in (None, ""):
        detalle_uf, detalle_clp = _parsear_precio(detalle, uf_valor)
        precio_uf = precio_uf if precio_uf is not None else detalle_uf
        precio_clp = detalle_clp

    comuna_api = (detalle.get("commune") or item.get("commune") or {}).get("nombre")
    if not comuna_api:
        comuna_api = "Región Metropolitana"
    ubicacion = f"{comuna_api}, Región Metropolitana"

    imagenes = detalle.get("property_images") or []
    imagen_url = item.get("imagen")
    if not imagen_url and imagenes:
        imagen_url = imagenes[0].get("imagen_960x720") or imagenes[0].get("imagen")

    return {
        "id": f"GP-{propiedad_id}",
        "titulo": titulo,
        "descripcion": descripcion,
        "descripcion_corta": descripcion[:200],
        "precio_clp": precio_clp,
        "precio_uf": precio_uf,
        "gc_clp": _parsear_gastos(detalle, uf_valor),
        "superficie": superficie,
        "dormitorios": dormitorios,
        "banos": banos,
        "comuna": comuna_objetivo,
        "ubicacion": ubicacion,
        "url": f"{GOPLACEIT_BASE_URL}/cl/propiedad/{propiedad_id}",
        "imagen_url": imagen_url,
        "fuente": "goplaceit",
    }


def scrapear_comuna(comuna):
    comuna_id = GOPLACEIT_COMUNAS.get(comuna)
    if not comuna_id:
        raise FuenteError(f"Sin ID de goplaceit para {comuna}")

    uf_valor = obtener_valor_uf()
    precio_maximo = max(
        obtener_precio_maximo(MAX_PRECIO_TOTAL_CLP),
        MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    )
    publicaciones = []

    for pagina in range(1, GOPLACEIT_MAX_PAGINAS + 1):
        respuesta = obtener_json(
            f"{GOPLACEIT_API_URL}/search/all_properties",
            params={
                "country": 1,
                "mode": 2,
                "page": pagina,
                "per_page": GOPLACEIT_PER_PAGE,
                "type": 2,
                "commune": comuna_id,
                "id_currency_rent": 1,
                "to_price_rent": precio_maximo,
            },
            headers=GOPLACEIT_HEADERS,
        )
        items = respuesta.get("response") or []
        if not items:
            break

        for item in items:
            publicacion = _parsear_publicacion(
                item, comuna, uf_valor, precio_maximo
            )
            if publicacion:
                publicaciones.append(publicacion)

        paginacion = (respuesta.get("meta") or {}).get("pagination") or {}
        paginas = paginacion.get("pages") or pagina
        logger.info(
            "[%s] goplaceit página %d/%d: %d resultados",
            comuna,
            pagina,
            paginas,
            len(items),
        )
        if pagina >= paginas or not paginacion.get("next_page"):
            break
        time.sleep(GOPLACEIT_DELAY_ENTRE_REQUESTS)

    logger.info(
        "[%s] goplaceit total crudo: %d publicaciones", comuna, len(publicaciones)
    )
    return publicaciones


def scrapear_todas_las_comunas():
    todas = []
    errores = []
    comunas = obtener_comunas(COMUNAS)

    for comuna in comunas:
        try:
            avisos = scrapear_comuna(comuna)
        except Exception as exc:
            logger.warning("[%s] goplaceit falló: %s", comuna, exc)
            errores.append(exc)
            continue
        todas.extend(avisos)
        time.sleep(GOPLACEIT_DELAY_ENTRE_REQUESTS)

    if errores and len(errores) == len(comunas):
        raise errores[0]
    if errores:
        raise ScrapePartialError(todas, errores)
    return todas
