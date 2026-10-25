import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import filtro
import main


class MonitorAnalyticsTests(unittest.TestCase):
    def test_empty_scrape_sends_preventive_alert(self):
        with patch.object(main, "scrapear_todas_las_comunas", return_value=[]):
            with patch.object(main, "registrar_ejecucion") as register:
                with patch.object(main, "notificar_alerta_scraper") as alert:
                    self.assertTrue(main.ejecutar_ciclo())

        register.assert_called_once()
        self.assertEqual(register.call_args.args[-1], "WARN_EMPTY")
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], "WARN_EMPTY")

    def test_cycle_detects_existing_listing_price_drop(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "deptos.db"
            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                database.guardar_publicacion({
                    "id": "MLC-123", "url": "https://ejemplo.test/123",
                    "titulo": "Departamento en arriendo", "ubicacion": "Las Condes, Apoquindo",
                    "comuna": "Las Condes", "precio_uf": None,
                    "precio_clp": 420000, "gc_clp": 30000,
                    "precio_total": 450000, "superficie": 50,
                    "dormitorios": 1, "banos": 1, "imagen_url": None,
                    "score": 50,
                })
                database.marcar_notificado("MLC-123")
                pub = {
                    "id": "MLC-123", "url": "https://ejemplo.test/123",
                    "titulo": "Departamento en arriendo", "ubicacion": "Las Condes, Apoquindo",
                    "comuna": "Las Condes", "precio_uf": None,
                    "precio_clp": 400000, "gc_clp": None, "precio_total": None,
                    "superficie": 50, "dormitorios": 1, "banos": 1,
                    "imagen_url": None, "score": 0,
                }
                with patch.object(main, "scrapear_todas_las_comunas", return_value=[pub]):
                    with patch.object(main, "obtener_valor_uf", return_value=41000):
                        with patch.object(filtro, "obtener_valor_uf", return_value=41000):
                            with patch.object(main, "enriquecer_con_gc") as enrich:
                                with patch.object(main, "notificar_bajada_precio", return_value=True) as alert:
                                    with patch.object(main, "registrar_ejecucion"):
                                        self.assertTrue(main.ejecutar_ciclo())

                enrich.assert_not_called()
                alert.assert_called_once()
                self.assertEqual(alert.call_args.args[1:], (450000, 430000))
                self.assertEqual(database.obtener_publicacion("MLC-123")["precio_total"], 430000)
                self.assertEqual(database.obtener_bajadas_pendientes(), [])


if __name__ == "__main__":
    unittest.main()
