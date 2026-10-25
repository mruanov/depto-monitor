"""Chat interactivo ligero para consultar el monitor desde Telegram."""

import html
import logging
import re
import sqlite3
import time
import unicodedata
from typing import Callable, Optional

import config
from config import TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN
from database import (
    guardar_parametro,
    obtener_estado_actual,
    obtener_metricas_mercado,
    obtener_parametros_guardados,
    obtener_publicacion_para_analisis,
    obtener_resumen_publicaciones,
    restablecer_parametros,
)
from filtro import detallar_score
from notificador import (
    _enviar_mensaje,
    _limpiar_url,
    obtener_actualizaciones,
    verificar_conexion_telegram,
)
from runtime_settings import obtener_comunas, obtener_precio_maximo

logger = logging.getLogger(__name__)


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(caracter for caracter in texto if not unicodedata.combining(caracter))


def _interpretar_consulta(texto: str) -> tuple:
    """Clasifica comandos y consultas simples en español sin servicios externos."""
    texto = texto.strip()
    if not texto:
        return "ayuda", None

    if texto.startswith("/"):
        comando = texto.split()[0].split("@", 1)[0].lower()
        if comando in ("/start", "/help", "/ayuda"):
            return "ayuda", None
        if comando == "/status":
            return "status", None
        if comando == "/force_run":
            return "force_run", None
        if comando == "/resumen":
            resto = _normalizar(texto[len(texto.split()[0]):])
            return "resumen", _orden_resumen(resto)
        if comando in ("/score", "/analisis"):
            return "score", " ".join(texto.split()[1:]).strip() or None
        if comando == "/mercado":
            return "mercado", None
        if comando in ("/parametros", "/config"):
            return "parametros", None
        if comando == "/precio":
            return "set_precio", " ".join(texto.split()[1:]).strip()
        if comando == "/comunas":
            return "set_comunas", " ".join(texto.split()[1:]).strip()
        if comando == "/reset_parametros":
            return "reset_parametros", None
        return "ayuda", None

    consulta = _normalizar(texto)
    if any(palabra in consulta for palabra in ("estado", "status", "como va", "ultima ejecucion")):
        return "status", None
    if any(palabra in consulta for palabra in ("ejecuta ahora", "busca ahora", "forzar busqueda")):
        return "force_run", None
    if any(palabra in consulta for palabra in ("cuantos", "cuantas", "total de", "cuenta")):
        return "conteo", None
    if "score" in consulta and any(palabra in consulta for palabra in ("por que", "porque")):
        coincidencia = re.search(r"\bMLC-\d+\b", texto, re.IGNORECASE)
        return "score", coincidencia.group(0) if coincidencia else None
    if any(palabra in consulta for palabra in (
        "resumen", "departamento", "deptos", "avisos",
        "mas barato", "mas baratos", "menor precio", "mejor score", "mejor puntuacion",
    )):
        return "resumen", _orden_resumen(consulta)
    if "mercado" in consulta or "precio por metro" in consulta or "precio por m2" in consulta:
        return "mercado", None
    if any(palabra in consulta for palabra in ("parametros", "configuracion", "limite de precio")):
        return "parametros", None
    return "ayuda", None


def _orden_resumen(texto: str) -> str:
    consulta = _normalizar(texto)
    if any(palabra in consulta for palabra in ("barato", "economico", "menor precio", "precio mas bajo")):
        return "precio"
    if any(palabra in consulta for palabra in ("reciente", "ultimo", "nuevos", "encontrados")):
        return "recientes"
    return "score"


def _mensaje_estado() -> str:
    estado = obtener_estado_actual()
    ultima = estado["ultima_ejecucion"]
    if ultima:
        ejecucion = (
            f"Estado: <b>{html.escape(str(ultima['status'] or 'desconocido'))}</b>\n"
            f"Inicio: {html.escape(str(ultima['inicio'] or '—'))}\n"
            f"Fin: {html.escape(str(ultima['fin'] or '—'))}\n"
            f"Nuevas: {ultima['nuevas']} | Scrapeadas: {ultima['total_scrapeados']}"
        )
    else:
        ejecucion = "Última ejecución: todavía no hay registros."
    return (
        "📊 <b>Estado del monitor</b>\n"
        f"Publicaciones en la base: <b>{estado['total_publicaciones']}</b>\n"
        f"{ejecucion}"
    )


def _mensaje_resumen(orden: str) -> str:
    publicaciones = obtener_resumen_publicaciones(orden, 3)
    if not publicaciones:
        return "Todavía no hay departamentos guardados en la base de datos."

    etiqueta = {
        "precio": "más baratos",
        "score": "con mejor score",
        "recientes": "encontrados recientemente",
    }[orden]
    lineas = [f"🏠 <b>Últimos departamentos ({etiqueta})</b>"]
    for indice, publicacion in enumerate(publicaciones, 1):
        titulo = html.escape(publicacion.get("titulo") or "Sin título")
        comuna = html.escape(publicacion.get("comuna") or "Comuna no indicada")
        precio = publicacion.get("precio_total")
        precio_txt = f"${precio:,.0f}".replace(",", ".") if precio is not None else "Sin precio"
        score = float(publicacion.get("score") or 0)
        linea = f"{indice}. <b>{titulo}</b>\n   {comuna} · {precio_txt} · Score {score:.0f}/100"
        url = _limpiar_url(publicacion.get("url"))
        if url:
            linea += f'\n   <a href="{html.escape(url, quote=True)}">Ver aviso</a>'
        lineas.append(linea)
    return "\n".join(lineas)


def _mensaje_ayuda() -> str:
    return (
        "Puedo consultar el monitor y sus publicaciones. Prueba:\n"
        "/status — estado, conteo y última ejecución\n"
        "/resumen — tres departamentos con mejor score\n"
        "/resumen baratos — los tres de menor precio\n"
        "/force_run — iniciar un ciclo de búsqueda\n"
        "/mercado — métricas por comuna\n"
        "/score [id] — desglose del score\n"
        "/parametros — consultar ajustes\n"
        "/precio <CLP> — cambiar el precio máximo\n"
        "/comunas <lista> — ajustar comunas monitoreadas\n"
        "/reset_parametros — volver a config.py\n\n"
        "También puedes preguntar, por ejemplo: “¿cuántos departamentos hay?”, "
        "“¿cuáles son los más baratos?” o “¿cómo va el monitor?”."
    )


def _responder(texto: str) -> str:
    tipo, dato = _interpretar_consulta(texto)
    if tipo == "status":
        return _mensaje_estado()
    if tipo == "conteo":
        estado = obtener_estado_actual()
        return f"Hay <b>{estado['total_publicaciones']}</b> publicaciones guardadas en la base."
    if tipo == "resumen":
        return _mensaje_resumen(dato)
    if tipo == "score":
        return _mensaje_score(dato)
    if tipo == "mercado":
        return _mensaje_mercado()
    if tipo == "parametros":
        return _mensaje_parametros()
    if tipo == "set_precio":
        return _ajustar_precio(dato)
    if tipo == "set_comunas":
        return _ajustar_comunas(dato)
    if tipo == "reset_parametros":
        restablecer_parametros()
        return "Parámetros restaurados a los valores definidos en config.py."
    if tipo == "force_run":
        return "force_run"
    return _mensaje_ayuda()


def _mensaje_score(identificador: Optional[str]) -> str:
    publicacion = obtener_publicacion_para_analisis(identificador)
    if not publicacion:
        return "No encontré esa publicación. Usa /score MLC-123 o /score para la de menor puntuación."

    detalle = detallar_score(publicacion)
    score_guardado = float(publicacion.get("score") or 0)
    maximo = obtener_precio_maximo(config.MAX_PRECIO_TOTAL_CLP)
    precio = publicacion.get("precio_total")
    precio_txt = f"${precio:,.0f}".replace(",", ".") if precio is not None else "sin precio"
    lineas = [
        f"🔎 <b>{html.escape(publicacion.get('titulo') or 'Sin título')}</b>",
        f"ID: <code>{html.escape(publicacion['id'])}</code> · "
        f"Score guardado: <b>{score_guardado:.0f}/100</b>",
        f"Precio total: {precio_txt} · Máximo actual: ${maximo:,.0f}".replace(",", "."),
        "",
        "<b>Reglas del score actual:</b>",
    ]
    lineas.extend(
        f"• {html.escape(regla['regla'])}: {regla['puntos']:+d}"
        for regla in detalle["reglas"]
    )
    lineas.append(f"Score recalculado con parámetros actuales: <b>{detalle['score']:.0f}/100</b>.")
    comuna = publicacion.get("comuna") or ""
    comunas = obtener_comunas(config.COMUNAS)
    if comuna not in comunas:
        lineas.append("⚠️ La comuna no está en la lista actual y no pasaría ese filtro.")
    elif precio is not None and precio > maximo:
        lineas.append("⚠️ El total supera el máximo actual y no pasaría ese filtro.")
    else:
        lineas.append("La comuna y el precio están dentro de los parámetros actuales.")
    if comuna == "Providencia":
        lineas.append(
            "Sectores excluidos de Providencia: "
            f"{html.escape(', '.join(config.SECTORES_EXCLUIDOS_PROVIDENCIA))}."
        )
    lineas.append(
        "También se filtran avisos de servicios/proyectos, arriendos temporales, "
        "publicidad y tipos no residenciales según config.py."
    )
    url = _limpiar_url(publicacion.get("url"))
    if url:
        lineas.append(f'<a href="{html.escape(url, quote=True)}">Ver publicación</a>')
    return "\n".join(lineas)


def _mensaje_mercado() -> str:
    metricas = obtener_metricas_mercado()
    if not metricas["total"]:
        return "No hay publicaciones guardadas para analizar."
    if not metricas["comunas"]:
        return (
            f"Hay {metricas['total']} publicaciones, pero ninguna tiene una comuna "
            "válida para agrupar métricas."
        )
    lineas = [
        f"📈 <b>Mercado en base de datos</b> · {metricas['total']} publicaciones",
    ]
    for comuna in metricas["comunas"]:
        nombre = html.escape(comuna["comuna"])
        minimo, maximo = comuna["precio_minimo"], comuna["precio_maximo"]
        rango = (
            f"${minimo:,.0f}–${maximo:,.0f}".replace(",", ".")
            if minimo is not None and maximo is not None
            else "sin precios"
        )
        promedio = comuna["precio_m2_promedio"]
        precio_m2 = (
            f"${promedio:,.0f}/m²".replace(",", ".")
            if promedio is not None
            else "sin datos de superficie"
        )
        lineas.append(
            f"• <b>{nombre}</b>: {comuna['cantidad']} avisos · "
            f"{precio_m2} promedio · rango {rango}"
        )
    lineas.append(
        f"Más publicaciones: <b>{html.escape(metricas['comunas'][0]['comuna'])}</b>."
    )
    return "\n".join(lineas)


def _mensaje_parametros() -> str:
    guardados = obtener_parametros_guardados()
    maximo = obtener_precio_maximo(config.MAX_PRECIO_TOTAL_CLP)
    comunas = obtener_comunas(config.COMUNAS)
    return "\n".join([
        "⚙️ <b>Parámetros activos</b>",
        f"Precio máximo total: ${maximo:,.0f}".replace(",", "."),
        f"Comunas: {html.escape(', '.join(comunas))}",
        f"Overrides SQLite: {', '.join(sorted(guardados)) or 'ninguno'}",
        "Ejemplos: /precio 500000 · /comunas Las Condes, Providencia · /reset_parametros",
    ])


def _ajustar_precio(valor: Optional[str]) -> str:
    try:
        precio = int((valor or "").replace(".", "").replace("$", "").strip())
    except ValueError:
        return "Formato: /precio 500000 (monto en CLP)."
    if not 100_000 <= precio <= 20_000_000:
        return "El máximo debe estar entre $100.000 y $20.000.000 CLP."
    guardar_parametro("max_precio_total_clp", precio)
    return f"Precio máximo actualizado a <b>${precio:,.0f} CLP</b>.".replace(",", ".")


def _ajustar_comunas(valor: Optional[str]) -> str:
    if not valor:
        return "Formato: /comunas Las Condes, Providencia"
    permitidas = {_normalizar(nombre): nombre for nombre in config.COMUNA_SLUGS}
    comunas = []
    for entrada in valor.split(","):
        comuna = permitidas.get(_normalizar(entrada.strip()))
        if not comuna:
            nombres = html.escape(", ".join(config.COMUNA_SLUGS))
            return f"Comuna no reconocida. Opciones: {nombres}."
        if comuna not in comunas:
            comunas.append(comuna)
    guardar_parametro("comunas", comunas)
    return f"Comunas actualizadas: <b>{html.escape(', '.join(comunas))}</b>."


def _procesar_actualizacion(
    actualizacion: dict,
    ejecutar_ciclo: Callable[[], bool],
) -> None:
    mensaje = actualizacion.get("message")
    if not isinstance(mensaje, dict):
        return
    chat = mensaje.get("chat")
    if not isinstance(chat, dict) or str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
        logger.warning("Se ignoró un mensaje de un chat no autorizado.")
        return
    texto = mensaje.get("text")
    if not isinstance(texto, str):
        return

    try:
        respuesta = _responder(texto)
    except sqlite3.Error:
        logger.exception("No se pudo responder una consulta de Telegram.")
        _enviar_mensaje("No pude consultar la base de datos. Revisa el log del monitor.")
        return

    if respuesta != "force_run":
        _enviar_mensaje(respuesta)
        return

    _enviar_mensaje("🔄 Iniciando un ciclo de búsqueda. Te aviso cuando termine.")
    correcto = ejecutar_ciclo()
    try:
        estado = obtener_estado_actual()["ultima_ejecucion"]
    except sqlite3.Error:
        logger.exception("No se pudo leer el resultado del ciclo manual.")
        _enviar_mensaje("El ciclo terminó, pero no pude leer su resultado de la base.")
        return

    status = estado["status"] if estado else "sin registro"
    nuevas = estado["nuevas"] if estado else 0
    detalle = "✅" if correcto else "⚠️"
    _enviar_mensaje(
        f"{detalle} Ciclo terminado.\nEstado: <b>{html.escape(str(status))}</b>\n"
        f"Nuevas publicaciones notificadas: <b>{nuevas}</b>"
    )


def iniciar_bot(ejecutar_ciclo: Callable[[], bool]) -> None:
    """Escucha mensajes autorizados hasta que el proceso se interrumpa."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID.")
    if not verificar_conexion_telegram():
        raise RuntimeError("No se pudo verificar la conexión con Telegram.")

    logger.info("Chat interactivo iniciado para el chat configurado.")
    offset: Optional[int] = None
    try:
        while True:
            try:
                actualizaciones = obtener_actualizaciones(offset)
            except RuntimeError as exc:
                logger.error("No se pudieron obtener actualizaciones de Telegram: %s", exc)
                time.sleep(5)
                continue

            for actualizacion in actualizaciones:
                update_id = actualizacion.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                _procesar_actualizacion(actualizacion, ejecutar_ciclo)
    except KeyboardInterrupt:
        logger.info("Chat interactivo detenido por el usuario.")
