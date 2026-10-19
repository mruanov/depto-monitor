"""
=============================================================
  MÓDULO: UF HELPER (Optimizado con Caché Anti-Bloqueos)
=============================================================
"""

import logging
import requests
from datetime import date
from config import UF_FALLBACK, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

_uf_cache: dict = {}


def obtener_valor_uf() -> float:
    """
    Retorna el valor de la UF del día actual.
    Guarda en memoria tanto el resultado exitoso como el valor de respaldo
    para consultar la API únicamente 1 vez por ejecución.
    """
    hoy = date.today().isoformat()

    # 1. Si ya tenemos la UF calculada en este ciclo, retornar desde memoria
    if _uf_cache.get("fecha") == hoy:
        return _uf_cache["valor"]

    # 2. Intentar consultar API principal (mindicador.cl sin subrutas de fecha)
    try:
        url = "https://mindicador.cl/api/uf"
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        valor = float(data["serie"][0]["valor"])

        _uf_cache["fecha"] = hoy
        _uf_cache["valor"] = valor
        logger.info("UF actualizada exitosamente desde API: $%s CLP", f"{valor:,.0f}")
        return valor

    except Exception as exc:
        # 3. Guardar el valor de respaldo en caché para NO volver a intentar la red en esta sesión
        logger.warning("No se pudo conectar a la API de UF (%s). Usando respaldo estático: $%s CLP", exc, UF_FALLBACK)
        valor_fallback = float(UF_FALLBACK)
        _uf_cache["fecha"] = hoy
        _uf_cache["valor"] = valor_fallback
        return valor_fallback