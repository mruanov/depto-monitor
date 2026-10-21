"""
=============================================================
  AGENTE PRINCIPAL: main.py (Optimizado con Fast-Pass)
  Orquesta el ciclo completo de búsqueda y notificación
=============================================================
  Uso:
    python main.py            → ejecuta un ciclo completo
    python main.py --test     → modo dry-run (no notifica ni guarda)
    python main.py --verify   → solo verifica conexión Telegram
=============================================================
"""

import sys
import logging
import argparse
from datetime import datetime

from config import (
    LOG_FILE,
    LOG_LEVEL,
    MAX_PRECIO_TOTAL_CLP,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from database import init_db, es_nueva, guardar_publicacion, marcar_notificado, registrar_ejecucion
from scraper import (
    ScrapeAccessError,
    ScrapePartialError,
    ScrapeUnavailableError,
    scrapear_todas_las_comunas,
    enriquecer_con_gc,
)
from filtro import aplicar_filtros_y_scoring
from uf_helper import obtener_valor_uf
from notificador import (
    verificar_conexion_telegram,
    notificar_publicacion,
)

# ─────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LOGGING
# ─────────────────────────────────────────────────────────

def setup_logging(dry_run: bool = False) -> None:
    nivel = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
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

def ejecutar_ciclo(dry_run: bool = False) -> bool:
    inicio = datetime.now().isoformat()
    logger.info("=" * 60)
    logger.info("INICIO DE CICLO — %s%s", inicio, " [DRY RUN]" if dry_run else "")
    logger.info("=" * 60)

    total_analizadas = 0
    total_nuevas = 0
    status = "OK"
    ciclo_ok = True

    try:
        # ── 1. SCRAPING ──────────────────────────────────
        logger.info("PASO 1: Scrapeando Portal Inmobiliario...")
        try:
            publicaciones_crudas = scrapear_todas_las_comunas()
        except ScrapePartialError as exc:
            publicaciones_crudas = exc.publicaciones
            status = "WARN_PARTIAL"
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
            return True

        # ── 2. PRE-FILTRADO RÁPIDO Y ENRIQUECIMIENTO ───────
        logger.info("PASO 2: Aplicando Fast-Pass por precio base antes de enriquecer...")
        uf_actual = obtener_valor_uf()
        publicaciones_candidateables = []

        for pub in publicaciones_crudas:
            precio_clp = pub.get("precio_clp", 0)
            precio_uf = pub.get("precio_uf", 0)

            # Convertir UF a CLP si la publicación viene en UF
            if not precio_clp and precio_uf:
                precio_clp = precio_uf * uf_actual

            # FAST-PASS: Si el puro valor base de arriendo se pasa del límite, descartar
            if precio_clp and precio_clp > MAX_PRECIO_TOTAL_CLP:
                continue

            publicaciones_candidateables.append(pub)

        logger.info(
            "Pre-filtro completado: de %d avisos, solo %d son candidateables (≤ $%s CLP).",
            total_analizadas,
            len(publicaciones_candidateables),
            f"{MAX_PRECIO_TOTAL_CLP:,}",
        )

        logger.info("PASO 3: Deduplicando antes de consultar detalles...")
        publicaciones_pendientes = []
        procesados_en_ciclo = set()
        for pub in publicaciones_candidateables:
            pub_id = pub["id"]
            if pub_id in procesados_en_ciclo:
                logger.debug("Omitiendo aviso duplicado o ya notificado: %s", pub_id)
                continue
            procesados_en_ciclo.add(pub_id)
            if not es_nueva(pub_id):
                logger.debug("Omitiendo aviso duplicado o ya notificado: %s", pub_id)
                continue
            publicaciones_pendientes.append(pub)

        logger.info(
            "Pendientes de procesamiento: %d de %d candidateables.",
            len(publicaciones_pendientes),
            len(publicaciones_candidateables),
        )

        logger.info("PASO 4: Enriqueciendo solo publicaciones nuevas...")
        publicaciones_enriquecidas = []
        for i, pub in enumerate(publicaciones_pendientes):
            enriquecida = enriquecer_con_gc(pub)
            publicaciones_enriquecidas.append(enriquecida)
            # Log de progreso cada 10 avisos
            if (i + 1) % 10 == 0 or (i + 1) == len(publicaciones_pendientes):
                logger.info("  Enriquecidos: %d / %d", i + 1, len(publicaciones_pendientes))

        # ── 5. FILTRAR Y PUNTUAR ──────────────────────────
        logger.info("PASO 5: Aplicando filtros duros y scoring...")
        publicaciones_filtradas = aplicar_filtros_y_scoring(publicaciones_enriquecidas)
        logger.info("Pasan el filtro final: %d", len(publicaciones_filtradas))

        # ── 6. NOTIFICAR ──────────────────────────────────
        logger.info("PASO 6: Notificando...")
        for pub in publicaciones_filtradas:
            pub_id = pub["id"]

            logger.info(
                "[NUEVA] %s | $%s | Score: %.0f | %s",
                pub_id,
                f"{pub.get('precio_total', 0):,}" if pub.get("precio_total") else "?",
                pub.get("score", 0),
                pub.get("titulo", "")[:60],
            )

            if not dry_run:
                guardar_publicacion(pub)
                ok = notificar_publicacion(pub)
                if ok:
                    marcar_notificado(pub_id)
                    total_nuevas += 1
                    logger.info("  ✓ Notificado por Telegram")
                else:
                    logger.warning("  ✗ Fallo al notificar: %s", pub_id)
            else:
                total_nuevas += 1
                logger.info("  [DRY RUN] No se guarda ni notifica")

    except KeyboardInterrupt:
        logger.info("Ciclo interrumpido por el usuario.")
        status = "INTERRUPTED"
        ciclo_ok = False
    except ScrapeAccessError as exc:
        logger.error("El portal bloqueó o requiere verificar la búsqueda: %s", exc)
        status = "BLOCKED"
        ciclo_ok = False
    except ScrapeUnavailableError as exc:
        logger.error("No se pudo obtener una respuesta válida del portal: %s", exc)
        status = "SCRAPE_ERROR"
        ciclo_ok = False
    except Exception as exc:
        logger.exception("Error inesperado en el ciclo: %s", exc)
        status = "ERROR"
        ciclo_ok = False
    finally:
        fin = datetime.now().isoformat()
        if not dry_run:
            registrar_ejecucion(inicio, fin, total_nuevas, total_analizadas, status)
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
    parser.add_argument(
        "--test", action="store_true",
        help="Modo dry-run: no guarda en DB ni envía notificaciones"
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verifica la conexión con Telegram y sale"
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

    # Ejecutar ciclo
    if not ejecutar_ciclo(dry_run=args.test):
        sys.exit(1)


if __name__ == "__main__":
    main()