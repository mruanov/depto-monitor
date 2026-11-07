import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import database
import fuentes
import fuentes_comunes
import scraper_goplaceit
import scraper_yapo


YAPO_HTML = """
<html><body>
  <div class="d3-ad-tile">
    <div class="d3-ad-tile__cover">
      <img class="d3-ad-tile__photo" data-original="https://photos.encuentra24.com/foto.jpg" />
    </div>
    <div class="d3-ad-tile__content">
      <a class="d3-ad-tile__description" href="/bienes-raices-alquiler-apartamentos/depto-las-condes/12345678">
        <div class="d3-ad-tile__location"><span>Las Condes</span></div>
        <span class="d3-ad-tile__title">Departamento 2D 2B Las Condes</span>
        <div class="d3-ad-tile__short-description">Departamento amoblado.</div>
        <div class="d3-ad-tile__price">UF 10</div>
        <ul class="d3-ad-tile__details">
          <li class="d3-ad-tile__details-item"><svg><use xlink:href="#bed"></use></svg>2</li>
          <li class="d3-ad-tile__details-item"><svg><use xlink:href="#parking"></use></svg>1</li>
          <li class="d3-ad-tile__details-item"><svg><use xlink:href="#bath"></use></svg>2</li>
        </ul>
      </a>
    </div>
  </div>
</body></html>
"""


class YapoAdapterTests(unittest.TestCase):
    def test_parses_recent_card_and_prices_in_uf(self):
        with patch.object(
            scraper_yapo, "obtener_texto", return_value=(YAPO_HTML, "https://public-api.yapo.cl/test")
        ):
            with patch.object(scraper_yapo, "obtener_valor_uf", return_value=41000):
                with patch.object(scraper_yapo, "obtener_precio_maximo", return_value=470000):
                    with patch.object(
                        scraper_yapo,
                        "_enriquecer_con_detalle",
                        side_effect=lambda pub, uf: {**pub, "gc_clp": 40000, "superficie": 82.0},
                    ):
                        publicaciones = scraper_yapo.scrapear_comuna("Las Condes")

        self.assertEqual(len(publicaciones), 1)
        publicacion = publicaciones[0]
        self.assertEqual(publicacion["id"], "YAPO-12345678")
        self.assertEqual(publicacion["fuente"], "Yapo")
        self.assertEqual(publicacion["precio_uf"], 10.0)
        self.assertEqual(publicacion["precio_clp"], 410000)
        self.assertEqual(publicacion["gc_clp"], 40000)
        self.assertEqual(publicacion["superficie"], 82.0)
        self.assertEqual(publicacion["dormitorios"], 2)
        self.assertEqual(publicacion["banos"], 2)
        self.assertEqual(publicacion["comuna"], "Las Condes")
        self.assertIn("Las Condes", publicacion["ubicacion"])

    def test_skips_card_above_search_limit(self):
        html = YAPO_HTML.replace("UF 10", "$ 600.000")
        with patch.object(
            scraper_yapo, "obtener_texto", return_value=(html, "https://public-api.yapo.cl/test")
        ):
            with patch.object(scraper_yapo, "obtener_valor_uf", return_value=41000):
                with patch.object(scraper_yapo, "obtener_precio_maximo", return_value=470000):
                    publicaciones = scraper_yapo.scrapear_comuna("Las Condes")

        self.assertEqual(publicaciones, [])

    def test_detail_stream_extracts_description_gc_surface_and_rooms(self):
        descripcion = "<p>Departamento 58 m2 con estacionamiento.</p>"
        stream = (
            '"description":"$7" '
            '"maintenanceCost":{"currency":{},"discount":{},"amount":{"value":40000}} '
            '"square":58 '
            '"rooms":"2" "bathrooms":"2" '
            '"id":"123" '
            f"\n7:T{len(descripcion)},{descripcion}"
        )
        html = (
            "<html><script>self.__next_f.push([1,"
            + json.dumps(stream)
            + "])</script></html>"
        )
        resultado = scraper_yapo._parsear_detalle(html, 41000, "Las Condes", "123")

        self.assertIn("estacionamiento", resultado[0])
        self.assertEqual(resultado[1], 40000)
        self.assertEqual(resultado[2], 58.0)
        self.assertEqual(resultado[3], "Las Condes")
        self.assertEqual(resultado[4], 2)
        self.assertEqual(resultado[5], 2)


class CommonParsingTests(unittest.TestCase):
    def test_parses_clp_thousands_with_commas(self):
        self.assertEqual(fuentes_comunes.parse_precio("$2,000,000", 41000), (None, 2000000))
        self.assertEqual(fuentes_comunes.parse_monto_clp("$ 620.000", 41000), 620000)

    def test_extracts_singular_common_expense_text(self):
        self.assertEqual(
            fuentes_comunes.extraer_gc_de_texto("Gasto Común: 80.000", 41000),
            80000,
        )
        self.assertEqual(
            fuentes_comunes.extraer_gc_de_texto("gastos comunes UF 2,5", 41000),
            102500,
        )


class GoplaceitAdapterTests(unittest.TestCase):
    def test_parses_detail_and_converts_uf_common_expenses(self):
        item = {
            "id": 6307135,
            "titulo": "Departamento 2D 2B Las Condes",
            "precio": 350000.0,
            "id_moneda": 1,
            "moneda": {"codigo_moneda": "CLP", "id": 1},
            "habitaciones": 2,
            "banos": 2,
            "dimension_propiedad": 63.0,
            "commune": {"id": 288, "nombre": "Las Condes"},
            "imagen": "https://img.test/foto.jpg",
        }
        detalle = {
            "titulo": "Departamento 2D 2B Las Condes",
            "descripcion": "Lindo departamento.<br />Incluye estacionamiento y bodega.",
            "gastos_comunes": 2.5,
            "id_moneda_gastos": 2,
            "commune": {"id": 288, "nombre": "Las Condes"},
        }
        with patch.object(
            scraper_goplaceit, "_obtener_detalle", return_value=detalle
        ):
            with patch.object(scraper_goplaceit.time, "sleep"):
                publicacion = scraper_goplaceit._parsear_publicacion(
                    item, "Las Condes", 41000, 490000
                )

        self.assertIsNotNone(publicacion)
        self.assertEqual(publicacion["id"], "GP-6307135")
        self.assertEqual(publicacion["fuente"], "goplaceit")
        self.assertEqual(publicacion["precio_clp"], 350000)
        self.assertEqual(publicacion["gc_clp"], 102500)
        self.assertEqual(publicacion["superficie"], 63.0)
        self.assertEqual(publicacion["dormitorios"], 2)
        self.assertEqual(publicacion["banos"], 2)
        self.assertIn("estacionamiento", publicacion["descripcion"])
        self.assertNotIn("<br", publicacion["descripcion"])


class SourceAggregationTests(unittest.TestCase):
    def test_continues_when_one_source_fails(self):
        portal = Mock(side_effect=RuntimeError("portal caído"))
        yapo = Mock(return_value=[{"id": "YAPO-1", "fuente": "Yapo"}])
        goplaceit = Mock(return_value=[])
        with patch.dict(
            fuentes._FUENTES,
            {
                "Portal Inmobiliario": portal,
                "Yapo": yapo,
                "goplaceit": goplaceit,
            },
        ):
            publicaciones, errores = fuentes.scrapear_todas_las_fuentes()

        self.assertEqual([pub["id"] for pub in publicaciones], ["YAPO-1"])
        self.assertEqual(len(errores), 1)
        self.assertEqual(errores[0]["fuente"], "Portal Inmobiliario")
        yapo.assert_called_once()
        goplaceit.assert_called_once()

    def test_deduplicates_repeated_ids(self):
        repetida = {"id": "YAPO-1", "fuente": "Yapo"}
        with patch.dict(
            fuentes._FUENTES,
            {
                "Portal Inmobiliario": Mock(return_value=[]),
                "Yapo": Mock(return_value=[repetida, repetida]),
                "goplaceit": Mock(return_value=[]),
            },
        ):
            publicaciones, errores = fuentes.scrapear_todas_las_fuentes()

        self.assertEqual(len(publicaciones), 1)
        self.assertEqual(errores, [])


class DatabaseSourceMigrationTests(unittest.TestCase):
    def test_saves_inferred_source_for_new_ids(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "deptos.db"
            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                database.guardar_publicacion({
                    "id": "YAPO-123",
                    "url": "https://www.yapo.cl/aviso",
                    "titulo": "Departamento",
                    "ubicacion": "Las Condes",
                    "comuna": "Las Condes",
                    "precio_uf": None,
                    "precio_clp": 400000,
                    "gc_clp": 30000,
                    "precio_total": 430000,
                    "superficie": 50,
                    "dormitorios": 1,
                    "banos": 1,
                    "imagen_url": None,
                    "score": 50,
                })
                publicacion = database.obtener_publicacion("YAPO-123")

        self.assertEqual(publicacion["fuente"], "Yapo")

    def test_init_db_adds_source_column_to_legacy_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE publicaciones (id TEXT PRIMARY KEY, url TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO publicaciones (id, url) VALUES ('MLC-1', 'https://test/1')"
            )
            connection.commit()
            connection.close()

            with patch.object(database, "DB_PATH", str(db_path)):
                database.init_db()
                publicacion = database.obtener_publicacion("MLC-1")

        self.assertEqual(publicacion["fuente"], "Portal Inmobiliario")


if __name__ == "__main__":
    unittest.main()
