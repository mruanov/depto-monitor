import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import database
import filtro
import notificador
import runtime_settings
import telegram_bot


class TelegramBotTests(unittest.TestCase):
    def test_long_poll_passes_offset_and_validates_updates(self):
        response = Mock()
        response.json.return_value = {"ok": True, "result": [{"update_id": 10}]}
        with patch.object(notificador, "TELEGRAM_BOT_TOKEN", "test-token"):
            with patch.object(notificador.requests, "get", return_value=response) as get:
                self.assertEqual(notificador.obtener_actualizaciones(11), [{"update_id": 10}])

        self.assertEqual(get.call_args.kwargs["params"]["offset"], 11)
        self.assertEqual(get.call_args.kwargs["params"]["timeout"], 25)
        self.assertEqual(get.call_args.kwargs["timeout"], 35)

    def test_parses_market_score_and_parameter_commands(self):
        self.assertEqual(telegram_bot._interpretar_consulta("/mercado"), ("mercado", None))
        self.assertEqual(telegram_bot._interpretar_consulta("/score MLC-123"), ("score", "MLC-123"))
        self.assertEqual(telegram_bot._interpretar_consulta("/precio 500000"), ("set_precio", "500000"))
        self.assertEqual(
            telegram_bot._interpretar_consulta("/comunas Las Condes, Providencia"),
            ("set_comunas", "Las Condes, Providencia"),
        )
        self.assertEqual(
            telegram_bot._interpretar_consulta("¿Por qué tiene score bajo?"),
            ("score", None),
        )

    def test_interprets_commands_and_natural_language(self):
        self.assertEqual(telegram_bot._interpretar_consulta("/status"), ("status", None))
        self.assertEqual(
            telegram_bot._interpretar_consulta("/resumen baratos"),
            ("resumen", "precio"),
        )
        self.assertEqual(
            telegram_bot._interpretar_consulta("¿Cuántos departamentos hay?"),
            ("conteo", None),
        )
        self.assertEqual(
            telegram_bot._interpretar_consulta("Muéstrame los más baratos"),
            ("resumen", "precio"),
        )
        self.assertEqual(
            telegram_bot._interpretar_consulta("¿Cómo va el monitor?"),
            ("status", None),
        )

    def test_database_status_and_summary_queries(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "deptos.db"
            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                conn = sqlite3.connect(db_path)
                conn.executemany(
                    """
                    INSERT INTO publicaciones
                        (id, url, titulo, comuna, precio_total, score, primera_vista)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("1", "https://ejemplo.test/1", "Depto A", "Providencia", 450000, 90, "2026-10-01"),
                        ("2", "https://ejemplo.test/2", "Depto B", "Las Condes", 400000, 70, "2026-10-02"),
                        ("3", "https://ejemplo.test/3", "Depto C", "Vitacura", 420000, 95, "2026-10-03"),
                        ("4", "https://ejemplo.test/4", "Depto D", "Providencia", 430000, 80, "2026-10-04"),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO ejecuciones (inicio, fin, nuevas, total_scrapeados, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("inicio", "fin", 2, 12, "OK"),
                )
                conn.commit()
                conn.close()

                estado = database.obtener_estado_actual()
                self.assertEqual(estado["total_publicaciones"], 4)
                self.assertEqual(estado["ultima_ejecucion"]["status"], "OK")
                self.assertEqual(
                    [pub["titulo"] for pub in database.obtener_resumen_publicaciones("precio")],
                    ["Depto B", "Depto C", "Depto D"],
                )
                self.assertEqual(
                    [pub["titulo"] for pub in database.obtener_resumen_publicaciones("score")],
                    ["Depto C", "Depto A", "Depto D"],
                )

    def test_market_metrics_and_price_drop_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "deptos.db"
            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                database.guardar_publicacion({
                    "id": "MLC-1", "url": "https://ejemplo.test/1",
                    "titulo": "Depto", "ubicacion": "Apoquindo",
                    "comuna": "Las Condes", "precio_uf": None,
                    "precio_clp": 500000, "gc_clp": 50000,
                    "precio_total": 550000, "superficie": 50,
                    "dormitorios": 1, "banos": 1, "imagen_url": None,
                    "score": 45,
                })
                bajada = database.registrar_precio_observado(
                    "MLC-1", 450000, None, 500000
                )
                self.assertEqual(bajada["precio_anterior"], 550000)
                self.assertEqual(bajada["precio_nuevo"], 500000)
                self.assertIsNone(database.registrar_precio_observado(
                    "MLC-1", 450000, None, 500000
                ))
                pendientes = database.obtener_bajadas_pendientes()
                self.assertEqual(len(pendientes), 1)
                self.assertEqual(pendientes[0]["titulo"], "Depto")
                metricas = database.obtener_metricas_mercado()
                self.assertEqual(metricas["total"], 1)
                self.assertEqual(metricas["comunas"][0]["cantidad"], 1)
                self.assertEqual(metricas["comunas"][0]["precio_m2_promedio"], 10000)
                database.guardar_publicacion({
                    "id": "MLC-UF", "url": "https://ejemplo.test/uf",
                    "titulo": "Depto UF", "ubicacion": "Providencia",
                    "comuna": "Providencia", "precio_uf": 10,
                    "precio_clp": None, "gc_clp": 0,
                    "precio_total": 500000, "superficie": 40,
                    "dormitorios": 1, "banos": 1, "imagen_url": None,
                    "score": 40,
                })
                self.assertIsNone(database.registrar_precio_observado(
                    "MLC-UF", None, 10, 490000
                ))

    def test_dynamic_parameters_persist_in_database_until_reset(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "deptos.db"
            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                database.guardar_parametro("max_precio_total_clp", 600000)
                database.guardar_parametro("comunas", ["Providencia"])
                self.assertEqual(runtime_settings.obtener_precio_maximo(470000), 600000)
                self.assertEqual(runtime_settings.obtener_comunas(["default"]), ["Providencia"])

                database.init_db()
                self.assertEqual(runtime_settings.obtener_precio_maximo(470000), 600000)
                database.restablecer_parametros()
                self.assertEqual(runtime_settings.obtener_precio_maximo(470000), 470000)
                self.assertEqual(runtime_settings.obtener_comunas(["default"]), ["default"])

    def test_score_breakdown_matches_scoring_rules(self):
        detail = filtro.detallar_score({
            "precio_total": 300000,
            "gc_clp": None,
            "superficie": None,
            "dormitorios": 1,
            "banos": None,
            "imagen_url": None,
        })
        self.assertEqual(detail["score"], 15)
        self.assertIn(
            {"regla": "Gastos comunes no informados", "puntos": -15},
            detail["reglas"],
        )
        self.assertIn(
            {"regla": "Superficie no informada", "puntos": -10},
            detail["reglas"],
        )

    def test_ignores_messages_from_other_chats(self):
        update = {"message": {"chat": {"id": 456}, "text": "/status"}}
        with patch.object(telegram_bot, "TELEGRAM_CHAT_ID", "123"):
            with patch.object(telegram_bot, "_enviar_mensaje") as send:
                telegram_bot._procesar_actualizacion(update, Mock())

        send.assert_not_called()

    def test_force_run_sends_started_and_finished_responses(self):
        update = {"message": {"chat": {"id": 123}, "text": "/force_run"}}
        latest = {"status": "OK", "nuevas": 2}
        run = Mock(return_value=True)
        with patch.object(telegram_bot, "TELEGRAM_CHAT_ID", "123"):
            with patch.object(telegram_bot, "_enviar_mensaje") as send:
                with patch.object(
                    telegram_bot,
                    "obtener_estado_actual",
                    return_value={"ultima_ejecucion": latest},
                ):
                    telegram_bot._procesar_actualizacion(update, run)

        run.assert_called_once_with()
        self.assertEqual(send.call_count, 2)
        self.assertIn("Iniciando", send.call_args_list[0].args[0])
        self.assertIn("Nuevas publicaciones notificadas: <b>2</b>", send.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
