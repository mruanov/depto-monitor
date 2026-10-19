"""
=============================================================
  MÓDULO: NOTIFICACIONES TELEGRAM
  Envía fichas compactas por cada depto nuevo encontrado
=============================================================
"""

import logging
import requests
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _formato_clp(valor: Optional[int]) -> str:
    if valor is None:
        return "—"
    return f"${valor:,.0f}".replace(",", ".")


def _formato_uf(valor: Optional[float]) -> str:
    if valor is None:
        return "—"
    return f"{valor:.1f} UF"


def _construir_mensaje(pub: dict) -> str:
    """
    Construye la ficha compacta en formato Telegram (HTML).
    Usa HTML parse_mode para evitar fallos con URLs (regla GEMINI.md de Claude_Projects).
    """
    emoji_comuna = {
        "Las Condes":  "🏙️",
        "Vitacura":    "🌿",
        "Providencia": "🏡",
    }.get(pub.get("comuna", ""), "📍")

    arriendo_txt = ""
    if pub.get("precio_uf"):
        arriendo_txt = f"{_formato_uf(pub['precio_uf'])} ({_formato_clp(pub.get('precio_clp'))})"
    elif pub.get("precio_clp"):
        arriendo_txt = _formato_clp(pub["precio_clp"])
    else:
        arriendo_txt = "Sin precio"

    gc_txt       = _formato_clp(pub.get("gc_clp")) if pub.get("gc_clp") else "No especificado"
    total_txt    = _formato_clp(pub.get("precio_total"))
    sup_txt      = f"{pub['superficie']:.0f} m²" if pub.get("superficie") else "—"
    dorm_txt     = str(pub["dormitorios"]) if pub.get("dormitorios") is not None else "—"
    banos_txt    = str(pub["banos"])       if pub.get("banos")       is not None else "—"
    score_stars  = "⭐" * max(1, min(5, int(pub.get("score", 0) / 20)))

    titulo_safe  = (pub.get('titulo', 'Sin título') or '').replace('<', '&lt;').replace('>', '&gt;')
    ubic_safe    = (pub.get('ubicacion', pub.get('comuna', '')) or '').replace('<', '&lt;').replace('>', '&gt;')

    lines = [
        f"{emoji_comuna} <b>{titulo_safe}</b>",
        f"📍 {ubic_safe}",
        "",
        f"💰 Arriendo: {arriendo_txt}",
        f"🏦 GC: {gc_txt}",
        f"📊 <b>Total estimado: {total_txt}</b>",
        "",
        f"📐 Superficie: {sup_txt}",
        f"🛍 Dormitorios: {dorm_txt}",
        f"🚿 Baños: {banos_txt}",
        "",
        f"{score_stars} Score: {pub.get('score', 0):.0f}/100",
        "",
        f"🔗 <a href=\"{pub.get('url', '')}\">Ver publicación</a>",
    ]
    return "\n".join(lines)


def _enviar_mensaje(texto: str) -> bool:
    """Envía un mensaje de texto al chat de Telegram (HTML parse_mode)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("No se puede enviar Telegram: faltan las credenciales de entorno.")
        return False

    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        if not resp.json().get("ok"):
            logger.error("Telegram rechazó el mensaje.")
            return False
        return True
    except Exception as exc:
        logger.error("Error enviando mensaje Telegram: %s", exc)
        return False


def _enviar_foto_con_caption(imagen_url: str, caption: str) -> bool:
    """Envía una imagen con caption al chat de Telegram (HTML parse_mode)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("No se puede enviar Telegram: faltan las credenciales de entorno.")
        return False

    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "photo":      imagen_url,
                "caption":    caption,
                "parse_mode": "HTML",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        logger.warning("Foto no enviada (HTTP %s), enviando texto solo", resp.status_code)
        return _enviar_mensaje(caption)
    except Exception as exc:
        logger.warning("Error enviando foto: %s. Enviando solo texto.", exc)
        return _enviar_mensaje(caption)


def notificar_publicacion(pub: dict) -> bool:
    """
    Envía la ficha de una publicación por Telegram.
    Prioriza envío con imagen; si falla, envía solo texto.
    """
    mensaje = _construir_mensaje(pub)

    if pub.get("imagen_url"):
        return _enviar_foto_con_caption(pub["imagen_url"], mensaje)
    else:
        return _enviar_mensaje(mensaje)


def verificar_conexion_telegram() -> bool:
    """
    Verifica que el token y chat_id de Telegram sean válidos.
    Útil para correr antes del primer ciclo real.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getMe", timeout=REQUEST_TIMEOUT
        )
        if resp.status_code == 200:
            bot_name = resp.json().get("result", {}).get("username", "?")
            logger.info("Bot conectado: @%s", bot_name)
            return True
        logger.error("Token Telegram inválido (HTTP %s)", resp.status_code)
        return False
    except Exception as exc:
        logger.error("No se puede conectar a Telegram: %s", exc)
        return False
