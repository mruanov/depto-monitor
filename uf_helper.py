"""
=============================================================
  MÓDULO: UF HELPER (caché diaria con respaldo local)
=============================================================
"""

import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

import requests

from config import UF_FALLBACK, UF_REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

_uf_cache: dict = {}
_CACHE_MAX_AGE_DAYS = 7


def _ruta_cache() -> Path:
    if sys.platform == "darwin":
        cache_dir = Path.home() / "Library" / "Caches" / "depto-monitor"
    else:
        cache_dir = Path.home() / ".cache" / "depto-monitor"
    return cache_dir / "uf.json"


def _leer_cache(ruta: Path, hoy: date) -> Optional[Tuple[date, float]]:
    if not ruta.exists():
        return None

    try:
        cache = json.loads(ruta.read_text(encoding="utf-8"))
        fecha_cache = date.fromisoformat(cache["fecha"])
        valor = float(cache["valor"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("No se pudo leer la caché local de UF (%s).", exc)
        return None

    antiguedad = (hoy - fecha_cache).days
    cache_invalida = (
        not math.isfinite(valor)
        or valor <= 0
        or antiguedad < 0
        or antiguedad > _CACHE_MAX_AGE_DAYS
    )
    if cache_invalida:
        logger.warning("La caché local de UF es inválida o tiene más de %d días.", _CACHE_MAX_AGE_DAYS)
        return None

    return fecha_cache, valor


def _guardar_cache(ruta: Path, hoy: date, valor: float) -> None:
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps({"fecha": hoy.isoformat(), "valor": valor}),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("No se pudo guardar la caché local de UF (%s).", exc)


def obtener_valor_uf() -> float:
    """
    Retorna el valor de la UF del día.
    Ante una falla de red, usa una caché local de hasta siete días antes
    del valor estático de respaldo.
    """
    hoy = date.today()
    hoy_iso = hoy.isoformat()

    if _uf_cache.get("fecha") == hoy_iso:
        return _uf_cache["valor"]

    try:
        resp = requests.get(
            "https://mindicador.cl/api/uf",
            timeout=UF_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("La respuesta de Mindicador no es un objeto JSON.")
        serie = data.get("serie")
        if not serie or not isinstance(serie, list):
            raise ValueError("La respuesta de Mindicador no contiene una serie de UF.")
        valor = float(serie[0]["valor"])
        if not math.isfinite(valor) or valor <= 0:
            raise ValueError("Mindicador devolvió un valor de UF inválido.")
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        cache = _leer_cache(_ruta_cache(), hoy)
        if cache:
            fecha_cache, valor = cache
            logger.warning(
                "No se pudo consultar Mindicador (%s). Usando UF cacheada del %s: $%s CLP.",
                exc,
                fecha_cache.isoformat(),
                f"{valor:,.0f}",
            )
        else:
            valor = float(UF_FALLBACK)
            logger.warning(
                "No se pudo consultar Mindicador (%s) y no hay caché reciente. "
                "Usando respaldo estático: $%s CLP.",
                exc,
                f"{valor:,.0f}",
            )
    else:
        _guardar_cache(_ruta_cache(), hoy, valor)
        logger.info("UF actualizada exitosamente desde API: $%s CLP", f"{valor:,.0f}")

    _uf_cache["fecha"] = hoy_iso
    _uf_cache["valor"] = valor
    return valor
