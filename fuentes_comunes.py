import re

import requests

from config import REQUEST_TIMEOUT


class FuenteError(RuntimeError):
    pass


class FuenteAccessError(FuenteError):
    pass


def extraer_numero(texto):
    if not texto:
        return None
    texto = str(texto)
    crudo = re.search(r"\d[\d.,]*", texto)
    if not crudo:
        return None
    valor = crudo.group()
    if "UF" in texto.upper():
        if "," in valor and "." in valor:
            if valor.rfind(",") > valor.rfind("."):
                valor = valor.replace(".", "").replace(",", ".")
            else:
                valor = valor.replace(",", "")
        elif "," in valor:
            valor = valor.replace(",", ".")
        try:
            return float(valor)
        except ValueError:
            return None
    digitos = re.sub(r"\D", "", valor)
    return float(digitos) if digitos else None


def parse_precio(texto_precio, uf_valor):
    if not texto_precio:
        return None, None
    texto = str(texto_precio).upper()
    numero = extraer_numero(texto_precio)
    if numero is None:
        return None, None
    if "UF" in texto:
        return numero, int(numero * uf_valor)
    if "$" in texto or "CLP" in texto or "PESOS" in texto:
        return None, int(numero)
    if numero < 500:
        return numero, int(numero * uf_valor)
    return None, int(numero)


def parse_monto_clp(texto, uf_valor):
    if not texto:
        return None
    numero = extraer_numero(texto)
    if numero is None:
        return None
    if "UF" in str(texto).upper():
        return int(numero * uf_valor)
    return int(numero)


def extraer_gc_de_texto(texto, uf_valor):
    if not texto:
        return None
    texto_lower = str(texto).lower()
    if re.search(
        r"\bno\s+(?:se\s+)?(?:paga|pagan|incluye)\s+(?:los\s+)?gastos comunes\b",
        texto_lower,
    ):
        return 0
    patrones = [
        r"(?:gastos?\s+com[uú]n(?:es)?|\bgc\b|g\.c\.)[^\d\n.!]{0,20}(\d[\d\.,]*)\s*(uf|clp|\$|pesos|mil)?",
        r"\$\s*(\d[\d\.]*)\s*(?:de\s+)?(?:gastos?\s+com[uú]n(?:es)?|gc)",
    ]
    for patron in patrones:
        coincidencia = re.search(patron, texto_lower)
        if not coincidencia:
            continue
        try:
            numero = float(coincidencia.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            continue
        unidad = (
            coincidencia.group(2)
            if coincidencia.lastindex and coincidencia.lastindex >= 2
            else ""
        ).strip()
        if "uf" in unidad:
            return int(numero * uf_valor)
        if numero < 500:
            return int(numero * uf_valor)
        return int(numero)
    return None


def obtener_texto(url, headers=None):
    try:
        respuesta = requests.get(
            url,
            headers=headers or {},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FuenteError(f"No se pudo conectar a {url}: {exc}") from exc

    if respuesta.status_code in (401, 403, 429):
        raise FuenteAccessError(
            f"Acceso bloqueado o limitado en {url}: HTTP {respuesta.status_code}"
        )
    if respuesta.status_code >= 400:
        raise FuenteError(
            f"Respuesta HTTP {respuesta.status_code} al solicitar {url}"
        )
    return respuesta.text, respuesta.url


def obtener_json(url, params=None, headers=None):
    try:
        respuesta = requests.get(
            url,
            params=params,
            headers=headers or {},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FuenteError(f"No se pudo conectar a {url}: {exc}") from exc

    if respuesta.status_code in (401, 403, 429):
        raise FuenteAccessError(
            f"Acceso bloqueado o limitado en {url}: HTTP {respuesta.status_code}"
        )
    if respuesta.status_code >= 400:
        raise FuenteError(
            f"Respuesta HTTP {respuesta.status_code} al solicitar {url}"
        )
    try:
        return respuesta.json()
    except ValueError as exc:
        raise FuenteError(f"Respuesta JSON inválida desde {url}") from exc
