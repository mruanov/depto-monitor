"""
=============================================================
  MÓDULO: NOTIFICACIONES TELEGRAM
  Envía fichas compactas por cada depto nuevo encontrado
=============================================================
"""

import html
import logging
import requests
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _error_sin_credenciales(exc: Exception) -> str:
    mensaje = str(exc)
    if TELEGRAM_BOT_TOKEN:
        mensaje = mensaje.replace(TELEGRAM_BOT_TOKEN, "[REDACTED]")
    return mensaje


def _limpiar_url(url: Optional[str]) -> Optional[str]:
    if not isinstance(url, str) or not url:
        return None

    url = url.strip()
    if any(ord(caracter) < 32 or caracter.isspace() for caracter in url):
        return None

    try:
        partes = urlsplit(url)
        if partes.scheme.lower() not in ("http", "https") or not partes.hostname:
            return None
        partes.port
        if partes.username or partes.password:
            return None
        return urlunsplit((
            partes.scheme.lower(),
            partes.netloc,
            partes.path,
            partes.query,
            "",
        ))
    except ValueError:
        return None


def _formato_clp(valor: Optional[int]) -> str:
    if valor is None:
        return "—"
    return f"${valor:,.0f}".replace(",", ".")


def _formato_uf(valor: Optional[float]) -> str:
    if valor is None:
        return "—"
    return f"{valor:.1f} UF"


def _construir_mensaje(pub: dict) -> str:
    """Construye la ficha compacta como texto plano para Telegram."""
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

    titulo = html.escape(pub.get("titulo", "Sin título") or "Sin título")
    ubicacion = html.escape(pub.get("ubicacion", pub.get("comuna", "")) or "")
    url_publicacion = _limpiar_url(pub.get("url"))
    enlace = (
        f'<a href="{html.escape(url_publicacion, quote=True)}">Ver publicación</a>'
        if url_publicacion
        else "URL no disponible"
    )

    lines = [
        f"{emoji_comuna} <b>{titulo}</b>",
        f"📍 {ubicacion}",
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
        f"🔗 {enlace}",
    ]
    return "\n".join(lines)


def _enviar_mensaje(texto: str) -> bool:
    """Envía un mensaje de texto plano al chat de Telegram."""
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
        logger.error("Error enviando mensaje Telegram: %s", _error_sin_credenciales(exc))
        return False


def obtener_actualizaciones(offset: Optional[int] = None, timeout: int = 25) -> list:
    """Obtiene actualizaciones entrantes con long polling de Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN.")

    parametros = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        parametros["offset"] = offset

    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params=parametros,
            timeout=timeout + 10,
        )
        resp.raise_for_status()
        respuesta = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(_error_sin_credenciales(exc)) from exc
    except ValueError as exc:
        raise RuntimeError("Telegram devolvió una respuesta JSON inválida.") from exc

    if (
        not isinstance(respuesta, dict)
        or not respuesta.get("ok")
        or not isinstance(respuesta.get("result"), list)
        or any(not isinstance(actualizacion, dict) for actualizacion in respuesta["result"])
    ):
        raise RuntimeError("Telegram rechazó la solicitud de actualizaciones.")
    return respuesta["result"]


def _enviar_foto_con_caption(imagen_url: str, caption: str) -> bool:
    """Envía una imagen limpia con caption de texto plano."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("No se puede enviar Telegram: faltan las credenciales de entorno.")
        return False

    imagen_url = _limpiar_url(imagen_url)
    if not imagen_url:
        logger.warning("URL de foto inválida; enviando solo texto.")
        return _enviar_mensaje(caption)

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
        logger.warning(
            "Error enviando foto: %s. Enviando solo texto.",
            _error_sin_credenciales(exc),
        )
        return _enviar_mensaje(caption)


def notificar_publicacion(pub: dict) -> bool:
    """
    Envía la ficha de una publicación por Telegram.
    Prioriza envío con imagen; si falla, envía solo texto.
    """
    mensaje = _construir_mensaje(pub)

    imagen_url = _limpiar_url(pub.get("imagen_url"))
    if imagen_url:
        return _enviar_foto_con_caption(imagen_url, mensaje)
    if pub.get("imagen_url"):
        logger.warning("URL de foto inválida; enviando solo texto.")
        return _enviar_mensaje(mensaje)
    else:
        return _enviar_mensaje(mensaje)


def notificar_bajada_precio(pub: dict, anterior: int, nuevo: int) -> bool:
    """Envía una alerta especial cuando baja el precio total de un aviso."""
    titulo = html.escape(pub.get("titulo") or "Departamento")
    comuna = html.escape(pub.get("comuna") or "Comuna no indicada")
    url = _limpiar_url(pub.get("url"))
    enlace = (
        f'\n<a href="{html.escape(url, quote=True)}">Ver publicación</a>'
        if url else ""
    )
    diferencia = anterior - nuevo
    mensaje = (
        f"📉 <b>¡Bajada de precio!</b>\n{titulo}\n{comuna}\n"
        f"Antes: <s>{_formato_clp(anterior)}</s>\n"
        f"Ahora: <b>{_formato_clp(nuevo)}</b> "
        f"(bajó {_formato_clp(diferencia)}){enlace}"
    )
    return _enviar_mensaje(mensaje)


def notificar_alerta_scraper(estado: str, detalle: str) -> bool:
    """Avisa sobre ciclos vacíos, bloqueos o fallos del scraper."""
    mensaje = (
        "⚠️ <b>Alerta del monitor</b>\n"
        f"Estado del ciclo: <code>{html.escape(estado)}</code>\n"
        f"{html.escape(detalle)}\n"
        "Puede deberse a un bloqueo temporal o a un cambio en la página."
    )
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
        logger.error("No se puede conectar a Telegram: %s", _error_sin_credenciales(exc))
        return False
