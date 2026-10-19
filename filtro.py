"""
=============================================================
  MÓDULO: FILTRO Y SCORING (Con validación estricta de comunas)
  Aplica criterios de filtrado y calcula un score de relevancia
=============================================================
"""

import logging
import re
from typing import Optional, List
from config import (
    MAX_PRECIO_TOTAL_CLP,
    COMUNAS,
    SECTORES_EXCLUIDOS_PROVIDENCIA,
    PALABRAS_CLAVE_NO_VALIDAS,
)
from uf_helper import obtener_valor_uf

logger = logging.getLogger(__name__)


def _contiene_comuna(texto: str, comuna: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9]){re.escape(comuna.lower())}(?![a-z0-9])",
        texto.lower(),
    ) is not None


# ─────────────────────────────────────────────────────────
# CÁLCULO DE PRECIO TOTAL
# ─────────────────────────────────────────────────────────

def calcular_precio_total(pub: dict) -> Optional[int]:
    """
    Retorna el precio total estimado en CLP:
      arriendo_clp + gc_clp
    Si no hay CLP pero hay UF, convierte primero.
    Si no hay GC, retorna solo el arriendo.
    """
    uf = obtener_valor_uf()

    precio_clp = pub.get("precio_clp")
    precio_uf  = pub.get("precio_uf")
    gc_clp     = pub.get("gc_clp") or 0

    if precio_clp is None and precio_uf is not None:
        precio_clp = int(precio_uf * uf)

    if precio_clp is None:
        return None

    return precio_clp + gc_clp


# ─────────────────────────────────────────────────────────
# FILTRO ESTRICTO DE UBICACIÓN Y COMUNAS
# ─────────────────────────────────────────────────────────

def filtrar_ubicacion(pub: dict) -> bool:
    """
    Valida estrictamente que la publicación pertenezca a Las Condes, Vitacura o Providencia,
    y que no caiga en los sectores excluidos de Providencia. Bloquea la publicidad externa.
    """
    comuna_publicacion = str(pub.get("comuna", ""))
    comuna_objetivo = next(
        (comuna for comuna in COMUNAS if _contiene_comuna(comuna_publicacion, comuna)),
        None,
    )
    texto_direccion = " ".join([
        str(pub.get("ubicacion", "")),
        str(pub.get("direccion", "")),
    ]).lower()

    # 1. Comprobar que pertenezca a una de las comunas permitidas
    if comuna_objetivo is None or not _contiene_comuna(texto_direccion, comuna_objetivo):
        logger.debug("Descartado por comuna no permitida o anuncio patrocinado: %s", pub.get("id"))
        return False

    # 2. Eliminar avisos claramente no residenciales o de publicidad masiva.
    texto_aviso = f"{texto_direccion} {pub.get('titulo', '')}".lower()
    if any(palabra in texto_aviso for palabra in PALABRAS_CLAVE_NO_VALIDAS):
        logger.debug("Descartado por palabra clave no válida: %s", pub.get("id"))
        return False

    # 3. Comprobar que no esté en sectores excluidos (ej. Providencia periférica)
    if comuna_objetivo == "Providencia":
        for sector in SECTORES_EXCLUIDOS_PROVIDENCIA:
            if sector.lower() in texto_aviso:
                logger.debug("Descartado por sector excluido de Providencia (%s): %s", sector, pub.get("id"))
                return False

    return True


# ─────────────────────────────────────────────────────────
# FILTRO PRINCIPAL
# ─────────────────────────────────────────────────────────

def filtrar_publicacion(pub: dict) -> bool:
    """
    Retorna True si la publicación PASA todos los filtros duros (ubicación y precio).
    """
    # Primero: Validar que sea de la comuna correcta (elimina publicidad externa)
    if not filtrar_ubicacion(pub):
        return False

    # Sin precio → descarta
    if pub.get("precio_clp") is None and pub.get("precio_uf") is None:
        logger.debug("Sin precio: %s", pub.get("id"))
        return False

    precio_total = calcular_precio_total(pub)
    if precio_total is None:
        return False

    # Supera el límite total CLP (arriendo + GC)
    if precio_total > MAX_PRECIO_TOTAL_CLP:
        logger.debug(
            "Precio total $%d > límite $%d: %s",
            precio_total, MAX_PRECIO_TOTAL_CLP, pub.get("id")
        )
        return False

    return True


# ─────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────

def calcular_score(pub: dict) -> float:
    """
    Calcula un score de relevancia (0-100) para ordenar los mejores deptos.
    """
    score = 0.0
    precio_total = calcular_precio_total(pub)

    # Premio por precio bajo (mientras más barato, más puntos)
    if precio_total:
        ratio = precio_total / MAX_PRECIO_TOTAL_CLP
        if ratio <= 0.70:
            score += 35
        elif ratio <= 0.80:
            score += 25
        elif ratio <= 0.90:
            score += 15

    # Premio por tener GC explícito (mayor confianza en precio total)
    if pub.get("gc_clp"):
        score += 20
    else:
        score -= 15

    # Penalización por información incompleta de superficie.
    if not pub.get("superficie"):
        score -= 10

    # Premio por superficie
    if pub.get("superficie"):
        score += 15
        sup = pub["superficie"]
        if sup >= 60:
            score += 10
        elif sup >= 45:
            score += 5

    # Premio por dormitorios y baños conocidos
    if pub.get("dormitorios") is not None:
        score += 5
    if pub.get("banos") is not None:
        score += 5

    # Premio por imagen
    if pub.get("imagen_url"):
        score += 10

    return max(0.0, min(score, 100.0))


# ─────────────────────────────────────────────────────────
# PIPELINE COMPLETO
# ─────────────────────────────────────────────────────────

def aplicar_filtros_y_scoring(publicaciones: List[dict]) -> List[dict]:
    """
    1. Calcula precio_total para cada publicación.
    2. Aplica filtros duros (comuna y precio).
    3. Calcula score.
    4. Ordena de mayor a menor score.
    """
    resultados = []
    for pub in publicaciones:
        # Calcular precio total y guardarlo en el dict
        precio_total = calcular_precio_total(pub)
        pub["precio_total"] = precio_total

        if not filtrar_publicacion(pub):
            continue

        pub["score"] = calcular_score(pub)
        resultados.append(pub)

    resultados.sort(key=lambda p: p["score"], reverse=True)
    logger.info("Publicaciones que pasan el filtro: %d / %d", len(resultados), len(publicaciones))
    return resultados