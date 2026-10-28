"""
=============================================================
  AGENTE PRINCIPAL: main.py (Optimizado con Fast-Pass)
  Orquesta el ciclo completo de búsqueda y notificación
=============================================================
  Uso:
    python main.py            → ejecuta un ciclo completo
    python main.py --test     → modo dry-run (no notifica ni guarda)
    python main.py --verify   → solo verifica conexión Telegram
    python main.py --bot      → inicia el chat interactivo
=============================================================
"""

import sys
import logging
import argparse
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import (
    LOG_FILE,
    LOG_LEVEL,
    MAX_PRECIO_TOTAL_CLP,
    MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from database import (
    init_db,
    es_nueva,
    guardar_publicacion,
    marcar_notificado,
    registrar_ejecucion,
    obtener_publicacion,
    registrar_precio_observado,
    obtener_bajadas_pendientes,
    marcar_bajada_notificada,
)
from scraper import (
    ScrapeAccessError,
    ScrapePartialError,
    ScrapeUnavailableError,
    scrapear_todas_las_comunas,
    enriquecer_con_gc,
)
from filtro import aplicar_filtros_y_scoring
from execution_lock import ciclo_lock
from uf_helper import obtener_valor_uf
from notificador import (
    verificar_conexion_telegram,
    notificar_publicacion,
    notificar_bajada_precio,
    notificar_alerta_scraper,
)
from runtime_settings import obtener_precio_maximo

# ─────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LOGGING
# ─────────────────────────────────────────────────────────

def setup_logging(dry_run: bool = False) -> None:
    nivel = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    handlers = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            LOG_FILE,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        ),
    ]
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    if dry_run:
        logging.getLogger().setLevel(logging.DEBUG)


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# CICLO PRINCIPAL
# ─────────────────────────────────────────────────────────

def ejecutar_ciclo(dry_run: bool = False) -> Optional[bool]:
    """Ejecuta un ciclo solo si no hay otro en curso.

    Retorna ``True``/``False`` con el resultado del ciclo, o ``None`` cuando
    otro proceso ya está ejecutando uno.
    """
    with ciclo_lock() as adquirido:
        if not adquirido:
            logger.warning(
                "Ya hay otro ciclo de monitoreo en ejecución; se omite este."
            )
            return None
        return _ejecutar_ciclo(dry_run)


def _ejecutar_ciclo(dry_run: bool = False) -> bool:
    inicio = datetime.now().isoformat()
    logger.info("=" * 60)
    logger.info("INICIO DE CICLO — %s%s", inicio, " [DRY RUN]" if dry_run else "")
    logger.info("=" * 60)

    total_analizadas = 0
    total_nuevas = 0
    status = "OK"
    ciclo_ok = True
    detalle_error = ""

    try:
        logger.info("PASO 1: Scrapeando Portal Inmobiliario...")
        try:
            publicaciones_crudas = scrapear_todas_las_comunas()
        except ScrapePartialError as exc:
            publicaciones_crudas = exc.publicaciones
            status = "WARN_PARTIAL"
            detalle_error = str(exc)
            logger.warning(
                "%s Fallaron %d comuna(s); se procesarán los avisos obtenidos.",
                exc,
                len(exc.errores),
            )
        total_analizadas = len(publicaciones_crudas)
        logger.info("Total publicaciones scraped: %d", total_analizadas)

        if not publicaciones_crudas:
            logger.warning("No se obtuvieron publicaciones. Verifique la conectividad.")
            if status != "WARN_PARTIAL":
                status = "WARN_EMPTY"
                detalle_error = "El scraper terminó sin encontrar publicaciones."
            return True

        logger.info("PASO 2: Preparando avisos nuevos y conocidos...")
        uf_actual = obtener_valor_uf()
        maximo = obtener_precio_maximo(MAX_PRECIO_TOTAL_CLP)
        maximo_busqueda = max(
            maximo,
            MAX_PRECIO_CON_ESTACIONAMIENTO_Y_BODEGA_CLP,
        )
        publicaciones_candidateables = []
        procesados_en_ciclo = set()

        for pub in publicaciones_crudas:
            pub_id = pub["id"]
            if pub_id in procesados_en_ciclo:
                continue
            procesados_en_ciclo.add(pub_id)
            anterior = obtener_publicacion(pub_id)
            pub["_es_nueva"] = anterior is None or es_nueva(pub_id)

            if anterior:
                if pub.get("gc_clp") is None:
                    pub["gc_clp"] = anterior.get("gc_clp")
                pub["score"] = anterior.get("score") or 0
                precio_clp = pub.get("precio_clp")
                precio_uf = pub.get("precio_uf")
                arriendo_clp = precio_clp
                if arriendo_clp is None and precio_uf is not None:
                    arriendo_clp = int(precio_uf * uf_actual)
                precio_total = (
                    arriendo_clp + (pub.get("gc_clp") or 0)
                    if arriendo_clp is not None else None
                )
                if not dry_run:
                    registrar_precio_observado(
                        pub_id, precio_clp, precio_uf, precio_total
                    )
                publicaciones_candidateables.append(pub)
                continue

            precio_clp = pub.get("precio_clp", 0)
            precio_uf = pub.get("precio_uf", 0)
            if not precio_clp and precio_uf:
                precio_clp = precio_uf * uf_actual

            if precio_clp and precio_clp > maximo_busqueda:
                continue
            publicaciones_candidateables.append(pub)

        logger.info(
            "Avisos preparados: %d de %d (máximo $%s CLP).",
            total_analizadas,
            len(publicaciones_candidateables),
            f"{maximo:,}",
        )

        logger.info("PASO 3: Enriqueciendo avisos nuevos...")
        publicaciones_enriquecidas = []
        for pub in publicaciones_candidateables:
            precio_clp = pub.get("precio_clp") or 0
            precio_uf = pub.get("precio_uf") or 0
            precio_base_clp = precio_clp or int(precio_uf * uf_actual)
            if (
                pub.get("_es_nueva")
                or pub.get("gc_clp") is None
                or precio_base_clp > maximo
            ):
                enriquecer_con_gc(pub)
            publicaciones_enriquecidas.append(pub)

        logger.info("PASO 4: Aplicando filtros y scoring...")
        publicaciones_filtradas = aplicar_filtros_y_scoring(publicaciones_enriquecidas)
        logger.info("Pasan el filtro final: %d", len(publicaciones_filtradas))

        for pub in publicaciones_filtradas:
            pub_id = pub["id"]
            logger.info(
                "[AVISO] %s | $%s | Score: %.0f | %s",
                pub_id,
                f"{pub.get('precio_total', 0):,}" if pub.get("precio_total") else "?",
                pub.get("score", 0),
                pub.get("titulo", "")[:60],
            )

            if not dry_run:
                guardar_publicacion(pub)
                if pub.get("_es_nueva"):
                    if notificar_publicacion(pub):
                        marcar_notificado(pub_id)
                        total_nuevas += 1
                        logger.info("  ✓ Notificado por Telegram")
                    else:
                        logger.warning("  ✗ Fallo al notificar: %s", pub_id)

        if not dry_run:
            for bajada in obtener_bajadas_pendientes():
                pub = {
                    "id": bajada["publicacion_id"],
                    "titulo": bajada["titulo"],
                    "comuna": bajada["comuna"],
                    "url": bajada["url"],
                }
                if notificar_bajada_precio(
                    pub, bajada["precio_anterior"], bajada["precio_nuevo"]
                ):
                    marcar_bajada_notificada(bajada["id"])
                else:
                    logger.warning(
                        "No se pudo notificar la bajada de precio para %s",
                        bajada["publicacion_id"],
                    )

    except KeyboardInterrupt:
        logger.info("Ciclo interrumpido por el usuario.")
        status = "INTERRUPTED"
        ciclo_ok = False
        detalle_error = "Ciclo interrumpido antes de terminar."
    except ScrapeAccessError as exc:
        logger.error("El portal bloqueó o requiere verificar la búsqueda: %s", exc)
        status = "BLOCKED"
        ciclo_ok = False
        detalle_error = str(exc)
    except ScrapeUnavailableError as exc:
        logger.error("No se pudo obtener una respuesta válida del portal: %s", exc)
        status = "SCRAPE_ERROR"
        ciclo_ok = False
        detalle_error = str(exc)
    except Exception as exc:
        logger.exception("Error inesperado en el ciclo: %s", exc)
        status = "ERROR"
        ciclo_ok = False
        detalle_error = str(exc)
    finally:
        fin = datetime.now().isoformat()
        if not dry_run:
            try:
                registrar_ejecucion(
                    inicio, fin, total_nuevas, total_analizadas, status
                )
            except Exception:
                logger.exception("No se pudo registrar el resultado del ciclo.")

            try:
                if status == "WARN_EMPTY":
                    notificar_alerta_scraper(
                        status,
                        "No se encontraron publicaciones. Revisa conectividad, "
                        "bloqueo o cambios de estructura web.",
                    )
                elif status in ("BLOCKED", "SCRAPE_ERROR", "ERROR", "WARN_PARTIAL"):
                    notificar_alerta_scraper(
                        status,
                        detalle_error
                        or "El scraping no pudo completarse correctamente.",
                    )
            except Exception:
                logger.exception("No se pudo enviar la alerta del ciclo.")
        logger.info("FIN DE CICLO — nuevas: %d — status: %s", total_nuevas, status)
        logger.info("=" * 60)
    return ciclo_ok


# ─────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agente de búsqueda de departamentos en Portal Inmobiliario"
    )
    modos = parser.add_mutually_exclusive_group()
    modos.add_argument(
        "--test", action="store_true",
        help="Modo dry-run: no guarda en DB ni envía notificaciones"
    )
    modos.add_argument(
        "--verify", action="store_true",
        help="Verifica la conexión con Telegram y sale"
    )
    modos.add_argument(
        "--bot", action="store_true",
        help="Inicia el chat interactivo de Telegram"
    )
    args = parser.parse_args()

    setup_logging(dry_run=args.test)

    if not args.test and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        logger.error(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en las variables de entorno."
        )
        sys.exit(2)

    if args.verify:
        ok = verificar_conexion_telegram()
        sys.exit(0 if ok else 1)

    # Inicializar base de datos
    init_db()

    if args.bot:
        from telegram_bot import iniciar_bot

        iniciar_bot(ejecutar_ciclo)
        return

    # Ejecutar ciclo (None = otro proceso ya tenía el lock)
    resultado = ejecutar_ciclo(dry_run=args.test)
    if resultado is False:
        sys.exit(1)


if __name__ == "__main__":
    main()