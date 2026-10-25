"""Overrides livianos de parámetros, cargados desde SQLite al iniciar."""

from typing import Any, Dict, List

_overrides: Dict[str, Any] = {}


def cargar(overrides: dict) -> None:
    _overrides.clear()
    _overrides.update(overrides)


def obtener_precio_maximo(default: int) -> int:
    return int(_overrides.get("max_precio_total_clp", default))


def obtener_comunas(default: list) -> List[str]:
    comunas = _overrides.get("comunas")
    return list(comunas) if comunas is not None else list(default)


def obtener_overrides() -> dict:
    return dict(_overrides)
