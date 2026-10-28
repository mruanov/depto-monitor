import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import database
import execution_lock
import main
import telegram_bot


class ExecutionLockTests(unittest.TestCase):
    def test_lock_is_exclusive_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "ciclo.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl, sys, time\n"
                        "archivo = open(sys.argv[1], 'a+')\n"
                        "fcntl.flock(archivo.fileno(), fcntl.LOCK_EX)\n"
                        "print('locked', flush=True)\n"
                        "time.sleep(10)\n"
                    ),
                    str(lock_path),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                with patch.dict(
                    os.environ,
                    {
                        "DEPTO_LOCK_FILE": str(lock_path),
                        "DEPTO_LOCK_HELD": "",
                    },
                ):
                    with execution_lock.ciclo_lock() as acquired:
                        self.assertFalse(acquired)
            finally:
                holder.terminate()
                holder.wait(timeout=5)
                if holder.stdout is not None:
                    holder.stdout.close()

    def test_lock_can_be_acquired_when_free(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "DEPTO_LOCK_FILE": str(Path(directory) / "ciclo.lock"),
                    "DEPTO_LOCK_HELD": "",
                },
            ):
                with execution_lock.ciclo_lock() as acquired:
                    self.assertTrue(acquired)
                with execution_lock.ciclo_lock() as acquired_again:
                    self.assertTrue(acquired_again)

    def test_shell_marker_skips_python_lock(self):
        with patch.dict(os.environ, {"DEPTO_LOCK_HELD": "1"}):
            with execution_lock.ciclo_lock() as acquired:
                self.assertTrue(acquired)

    def test_busy_lock_skips_cycle(self):
        @contextlib.contextmanager
        def lock_ocupado():
            yield False

        with patch.object(main, "ciclo_lock", lock_ocupado):
            with patch.object(main, "_ejecutar_ciclo") as ejecutar:
                self.assertIsNone(main.ejecutar_ciclo())
        ejecutar.assert_not_called()


class TelegramResilienceTests(unittest.TestCase):
    def test_initial_connection_retries_with_backoff(self):
        with patch.object(
            telegram_bot,
            "verificar_conexion_telegram",
            side_effect=[False, False, True],
        ):
            with patch.object(telegram_bot.time, "sleep") as sleep:
                telegram_bot._esperar_conexion_telegram()

        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    def test_unexpected_update_error_does_not_kill_bot_loop(self):
        with patch.object(telegram_bot, "TELEGRAM_BOT_TOKEN", "token"):
            with patch.object(telegram_bot, "TELEGRAM_CHAT_ID", "123"):
                with patch.object(
                    telegram_bot, "_esperar_conexion_telegram"
                ):
                    with patch.object(
                        telegram_bot,
                        "obtener_actualizaciones",
                        side_effect=[[{"update_id": 1}], KeyboardInterrupt],
                    ) as actualizaciones:
                        with patch.object(
                            telegram_bot,
                            "_procesar_actualizacion",
                            side_effect=RuntimeError("fallo de prueba"),
                        ) as procesar:
                            with patch.object(
                                telegram_bot, "_enviar_mensaje"
                            ) as send:
                                with patch.object(
                                    telegram_bot.logger, "exception"
                                ):
                                    telegram_bot.iniciar_bot(Mock())

        procesar.assert_called_once()
        self.assertIn("error procesando", send.call_args.args[0])
        self.assertEqual(actualizaciones.call_count, 2)

    def test_force_run_reports_busy_cycle(self):
        update = {"message": {"chat": {"id": 123}, "text": "/force_run"}}
        with patch.object(telegram_bot, "TELEGRAM_CHAT_ID", "123"):
            with patch.object(telegram_bot, "_enviar_mensaje") as send:
                with patch.object(
                    telegram_bot, "obtener_estado_actual"
                ) as estado:
                    telegram_bot._procesar_actualizacion(
                        update, Mock(return_value=None)
                    )

        self.assertEqual(send.call_count, 2)
        self.assertIn("ciclo de búsqueda en ejecución", send.call_args_list[1].args[0])
        estado.assert_not_called()


class LoggingStabilityTests(unittest.TestCase):
    def test_file_log_rotates_at_five_megabytes(self):
        with patch.object(main, "RotatingFileHandler") as handler:
            with patch.object(main.logging, "basicConfig"):
                main.setup_logging()

        handler.assert_called_once_with(
            main.LOG_FILE,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )


class DatabaseStabilityTests(unittest.TestCase):
    def test_connection_uses_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                database, "DB_PATH", str(Path(directory) / "deptos.db")
            ):
                conn = database.get_connection()
                try:
                    modo = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                finally:
                    conn.close()

        self.assertEqual(modo.lower(), "wal")
        self.assertEqual(timeout, 5000)


if __name__ == "__main__":
    unittest.main()
