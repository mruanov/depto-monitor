import logging

import scraper
import scraper_goplaceit
import scraper_yapo
from config import FUENTES_ACTIVAS

logger = logging.getLogger(__name__)

_FUENTES = {
    "Portal Inmobiliario": scraper.scrapear_todas_las_comunas,
    "Yapo": scraper_yapo.scrapear_todas_las_comunas,
    "goplaceit": scraper_goplaceit.scrapear_todas_las_comunas,
}


def scrapear_todas_las_fuentes():
    publicaciones = []
    errores = []
    ids_vistos = set()

    def agregar(avisos):
        for publicacion in avisos or []:
            identificador = publicacion.get("id")
            if identificador and identificador in ids_vistos:
                continue
            if identificador:
                ids_vistos.add(identificador)
            publicaciones.append(publicacion)

    for nombre in FUENTES_ACTIVAS:
        funcion = _FUENTES.get(nombre)
        if funcion is None:
            logger.warning("Fuente desconocida en FUENTES_ACTIVAS: %s", nombre)
            continue

        try:
            avisos = funcion()
        except scraper.ScrapePartialError as exc:
            agregar(exc.publicaciones)
            errores.append({"fuente": nombre, "error": exc})
            logger.warning(
                "%s devolvió resultados parciales: %s", nombre, exc
            )
            continue
        except Exception as exc:
            errores.append({"fuente": nombre, "error": exc})
            logger.warning("%s falló y se omite del ciclo: %s", nombre, exc)
            continue

        agregar(avisos)
        logger.info("%s: %d avisos recolectados", nombre, len(avisos or []))

    return publicaciones, errores
