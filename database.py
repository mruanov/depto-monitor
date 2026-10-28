"""
=============================================================
  MÓDULO: DATABASE
  Gestión del historial y deduplicación con SQLite
=============================================================
"""

import sqlite3
import logging
import json
from datetime import datetime
from typing import Optional
from config import DB_PATH
import runtime_settings

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError as exc:
        logger.warning("No se pudo activar WAL en SQLite: %s", exc)
    return conn


def init_db() -> None:
    """Crea las tablas si no existen."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS publicaciones (
                id              TEXT PRIMARY KEY,
                url             TEXT NOT NULL,
                titulo          TEXT,
                ubicacion       TEXT,
                comuna          TEXT,
                precio_uf       REAL,
                precio_clp      INTEGER,
                gc_clp          INTEGER,
                precio_total    INTEGER,
                superficie      REAL,
                dormitorios     INTEGER,
                banos           INTEGER,
                imagen_url      TEXT,
                score           REAL DEFAULT 0,
                primera_vista   TEXT,  -- ISO datetime
                ultima_vista    TEXT,  -- ISO datetime
                notificado      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ejecuciones (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                inicio          TEXT,
                fin             TEXT,
                nuevas          INTEGER DEFAULT 0,
                total_scrapeados INTEGER DEFAULT 0,
                status          TEXT
            );

            CREATE TABLE IF NOT EXISTS parametros (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bajadas_precio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                publicacion_id TEXT NOT NULL,
                precio_anterior INTEGER NOT NULL,
                precio_nuevo INTEGER NOT NULL,
                detectado TEXT NOT NULL,
                notificado INTEGER NOT NULL DEFAULT 0
            );
        """)
    overrides = {
        fila["clave"]: json.loads(fila["valor"])
        for fila in conn.execute("SELECT clave, valor FROM parametros")
    }
    conn.close()
    runtime_settings.cargar(overrides)
    logger.info("Base de datos inicializada: %s", DB_PATH)


def es_nueva(pub_id: str) -> bool:
    """Retorna True si el aviso no existe o todavía no se notificó."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(notificado, 0) AS notificado FROM publicaciones WHERE id = ?",
        (pub_id,),
    ).fetchone()
    conn.close()
    return row is None or not row["notificado"]


def guardar_publicacion(pub: dict) -> None:
    """Inserta o actualiza una publicación en la base de datos."""
    ahora = datetime.now().isoformat()
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO publicaciones
                (id, url, titulo, ubicacion, comuna, precio_uf, precio_clp,
                 gc_clp, precio_total, superficie, dormitorios, banos,
                 imagen_url, score, primera_vista, ultima_vista, notificado)
            VALUES
                (:id, :url, :titulo, :ubicacion, :comuna, :precio_uf, :precio_clp,
                 :gc_clp, :precio_total, :superficie, :dormitorios, :banos,
                 :imagen_url, :score, :primera_vista, :ultima_vista, :notificado)
            ON CONFLICT(id) DO UPDATE SET
                ultima_vista = excluded.ultima_vista,
                precio_uf    = excluded.precio_uf,
                precio_clp   = excluded.precio_clp,
                gc_clp       = excluded.gc_clp,
                precio_total = excluded.precio_total,
                score        = excluded.score
        """, {
            **pub,
            "primera_vista": ahora,
            "ultima_vista":  ahora,
            "notificado":    0,
        })
    conn.close()


def obtener_publicacion(pub_id: str):
    conn = get_connection()
    try:
        fila = conn.execute(
            "SELECT * FROM publicaciones WHERE id = ?", (pub_id,)
        ).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def registrar_precio_observado(
    pub_id: str, precio_clp, precio_uf, precio_total
) -> Optional[dict]:
    """Actualiza el precio observado y guarda una bajada para notificar."""
    conn = get_connection()
    try:
        with conn:
            anterior = conn.execute(
                "SELECT precio_total, precio_uf FROM publicaciones WHERE id = ?",
                (pub_id,),
            ).fetchone()
            if not anterior:
                return None

            precio_anterior = anterior["precio_total"]
            bajada = None
            if (
                precio_anterior is not None
                and precio_total is not None
                and precio_total < precio_anterior
                and not (
                    anterior["precio_uf"] is not None
                    and precio_uf is not None
                    and anterior["precio_uf"] == precio_uf
                )
            ):
                ahora = datetime.now().isoformat()
                cursor = conn.execute("""
                    INSERT INTO bajadas_precio
                        (publicacion_id, precio_anterior, precio_nuevo, detectado)
                    VALUES (?, ?, ?, ?)
                """, (pub_id, precio_anterior, precio_total, ahora))
                bajada = {
                    "id": cursor.lastrowid,
                    "precio_anterior": precio_anterior,
                    "precio_nuevo": precio_total,
                }
            conn.execute("""
                UPDATE publicaciones
                SET precio_clp = COALESCE(?, precio_clp),
                    precio_uf = COALESCE(?, precio_uf),
                    precio_total = COALESCE(?, precio_total),
                    ultima_vista = ?
                WHERE id = ?
            """, (precio_clp, precio_uf, precio_total, datetime.now().isoformat(), pub_id))
        return bajada
    finally:
        conn.close()


def obtener_bajadas_pendientes() -> list:
    conn = get_connection()
    try:
        filas = conn.execute("""
            SELECT b.id, b.precio_anterior, b.precio_nuevo,
                   p.id AS publicacion_id, p.titulo, p.comuna, p.url
            FROM bajadas_precio b
            JOIN publicaciones p ON p.id = b.publicacion_id
            WHERE b.notificado = 0
            ORDER BY b.id
        """).fetchall()
        return [dict(fila) for fila in filas]
    finally:
        conn.close()


def marcar_bajada_notificada(bajada_id: int) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE bajadas_precio SET notificado = 1 WHERE id = ?",
                (bajada_id,),
            )
    finally:
        conn.close()


def obtener_publicacion_para_analisis(identificador: Optional[str] = None):
    conn = get_connection()
    try:
        if identificador:
            fila = conn.execute("""
                SELECT * FROM publicaciones
                WHERE id = ? OR titulo LIKE ?
                ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, score ASC
                LIMIT 1
            """, (identificador, f"%{identificador}%", identificador)).fetchone()
        else:
            fila = conn.execute("""
                SELECT * FROM publicaciones
                ORDER BY score ASC, precio_total DESC
                LIMIT 1
            """).fetchone()
        return dict(fila) if fila else None
    finally:
        conn.close()


def obtener_metricas_mercado() -> dict:
    conn = get_connection()
    try:
        comunas = conn.execute("""
            SELECT comuna, COUNT(*) AS cantidad,
                   AVG(CASE WHEN superficie > 0 AND precio_total > 0
                       THEN CAST(precio_total AS REAL) / superficie END)
                       AS precio_m2_promedio,
                   MIN(precio_total) AS precio_minimo,
                   MAX(precio_total) AS precio_maximo
            FROM publicaciones
            WHERE comuna IS NOT NULL AND comuna != ''
            GROUP BY comuna
            ORDER BY cantidad DESC, comuna
        """).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM publicaciones").fetchone()[0]
        return {
            "total": total,
            "comunas": [dict(fila) for fila in comunas],
        }
    finally:
        conn.close()


def guardar_parametro(clave: str, valor) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO parametros (clave, valor) VALUES (?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
            """, (clave, json.dumps(valor, ensure_ascii=False)))
        overrides = {
            fila["clave"]: json.loads(fila["valor"])
            for fila in conn.execute("SELECT clave, valor FROM parametros")
        }
        runtime_settings.cargar(overrides)
    finally:
        conn.close()


def restablecer_parametros() -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM parametros")
        runtime_settings.cargar({})
    finally:
        conn.close()


def obtener_parametros_guardados() -> dict:
    return runtime_settings.obtener_overrides()


def marcar_notificado(pub_id: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE publicaciones SET notificado = 1 WHERE id = ?", (pub_id,)
        )
    conn.close()


def registrar_ejecucion(inicio: str, fin: str, nuevas: int,
                        total: int, status: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO ejecuciones (inicio, fin, nuevas, total_scrapeados, status)
            VALUES (?, ?, ?, ?, ?)
        """, (inicio, fin, nuevas, total, status))
    conn.close()


def obtener_estado_actual() -> dict:
    """Retorna conteos e información de la última ejecución."""
    conn = get_connection()
    try:
        total_publicaciones = conn.execute(
            "SELECT COUNT(*) FROM publicaciones"
        ).fetchone()[0]
        ultima_ejecucion = conn.execute("""
            SELECT inicio, fin, nuevas, total_scrapeados, status
            FROM ejecuciones
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()
        return {
            "total_publicaciones": total_publicaciones,
            "ultima_ejecucion": dict(ultima_ejecucion) if ultima_ejecucion else None,
        }
    finally:
        conn.close()


def obtener_resumen_publicaciones(orden: str = "score", limite: int = 3) -> list:
    """Retorna las publicaciones recientes, más baratas o mejor puntuadas."""
    ordenes = {
        "score": "score DESC, precio_total ASC",
        "precio": "(precio_total IS NULL), precio_total ASC, score DESC",
        "recientes": "primera_vista DESC",
    }
    if orden not in ordenes:
        raise ValueError(f"Orden de resumen no válido: {orden}")
    if limite < 1:
        raise ValueError("El límite debe ser positivo.")

    conn = get_connection()
    try:
        publicaciones = conn.execute(f"""
            SELECT titulo, comuna, precio_total, score, url
            FROM publicaciones
            ORDER BY {ordenes[orden]}
            LIMIT ?
        """, (limite,)).fetchall()
        return [dict(publicacion) for publicacion in publicaciones]
    finally:
        conn.close()
