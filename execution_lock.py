"""Lock exclusivo para evitar ciclos de monitoreo concurrentes.

El mismo archivo de lock lo usa ``run_monitor.sh`` (flock) y ``main.py``
(fcntl), de modo que el cron y /force_run nunca ejecutan un ciclo a la vez.
"""

import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_FILE = Path(__file__).resolve().with_name(".depto-monitor.lock")


def ruta_lock() -> Path:
    return Path(os.environ.get("DEPTO_LOCK_FILE", LOCK_FILE))


@contextmanager
def ciclo_lock() -> Iterator[bool]:
    """Adquiere el lock del ciclo sin bloquear.

    Retorna ``True`` si el lock fue adquirido (o si el proceso ya lo heredó de
    run_monitor.sh) y ``False`` si otro proceso está ejecutando un ciclo.
    """
    if os.environ.get("DEPTO_LOCK_HELD") == "1":
        yield True
        return

    archivo = None
    try:
        archivo = open(ruta_lock(), "a+")
        try:
            fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                yield False
                return
            raise
        try:
            yield True
        finally:
            fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
    finally:
        if archivo is not None:
            archivo.close()
