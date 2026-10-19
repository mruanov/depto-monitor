"""
=============================================================
  MÓDULO: DATABASE
  Gestión del historial y deduplicación con SQLite
=============================================================
"""

import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        """)
    conn.close()
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
