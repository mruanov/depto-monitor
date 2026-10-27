import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import filtro
import notificador
import scraper
import uf_helper


class TelegramNotificationTests(unittest.TestCase):
    def test_message_escapes_user_content_and_uses_safe_link(self):
        message = notificador._construir_mensaje(
            {
                "titulo": "Depto <nuevo>",
                "ubicacion": 'Calle "Uno" & Dos',
                "url": "https://example.test/depto?id=123&source=search",
            }
        )

        self.assertIn("Depto &lt;nuevo&gt;", message)
        self.assertIn("Calle &quot;Uno&quot; &amp; Dos", message)
        self.assertIn(
            '<a href="https://example.test/depto?id=123&amp;source=search">',
            message,
        )

    def test_text_message_uses_html_parse_mode(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {"ok": True}
        with patch.object(notificador, "TELEGRAM_BOT_TOKEN", "test-token"):
            with patch.object(notificador, "TELEGRAM_CHAT_ID", "test-chat"):
                with patch.object(
                    notificador.requests, "post", return_value=response
                ) as post:
                    self.assertTrue(notificador._enviar_mensaje("<b>test</b>"))

        self.assertEqual(post.call_args.kwargs["json"]["parse_mode"], "HTML")


class KeywordFilterTests(unittest.TestCase):
    def publication(self, title, description=""):
        return {
            "id": "MLC-123",
            "comuna": "Las Condes",
            "ubicacion": "Las Condes, Apoquindo 123",
            "titulo": title,
            "descripcion": description,
        }

    def test_keeps_department_with_parking_and_storage(self):
        publication = self.publication(
            "Departamento independiente en arriendo con estacionamiento y bodega",
            "Incluye estacionamiento y bodega.",
        )
        self.assertTrue(filtro.filtrar_ubicacion(publication))

    def test_keeps_department_described_by_number_of_rooms(self):
        self.assertTrue(
            filtro.filtrar_ubicacion(self.publication("Departamento de 1 habitación"))
        )
        self.assertTrue(
            filtro.filtrar_ubicacion(
                self.publication("1D1B en arriendo con bodega y estacionamiento")
            )
        )

    def test_rejects_room_and_shared_accommodation(self):
        titles = (
            "Habitación individual en arriendo",
            "Pieza amoblada",
            "Departamento compartido",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(filtro.filtrar_ubicacion(self.publication(title)))

    def test_rejects_non_residential_property_types_in_title(self):
        titles = (
            "Oficina en arriendo",
            "Terreno en Las Condes",
            "Estacionamiento en arriendo",
            "Bodega en arriendo",
            "Local comercial disponible",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertFalse(filtro.filtrar_ubicacion(self.publication(title)))

    def test_rejects_service_keywords_in_title_or_description(self):
        for text in ("Administración de propiedades", "Corretaje", "COMISION"):
            with self.subTest(text=text):
                self.assertFalse(filtro.filtrar_ubicacion(self.publication(text)))
        self.assertFalse(
            filtro.filtrar_ubicacion(
                self.publication(
                    "Departamento en arriendo",
                    "Servicio de gestión de arriendo y asesoría.",
                )
            )
        )

    def test_allows_promotion_terms_without_over_budget_future_price(self):
        publications = (
            self.publication("Departamento en promoción"),
            self.publication(
                "Departamento en arriendo",
                "Descuento del 20% durante el primer mes.",
            ),
            self.publication(
                "Departamento en arriendo",
                "Promoción: a partir del segundo mes, precio normal $450.000.",
            ),
        )
        for publication in publications:
            with self.subTest(description=publication["descripcion"]):
                self.assertTrue(filtro.filtrar_ubicacion(publication))

    def test_rejects_future_price_over_limit_in_clp_or_uf(self):
        publications = (
            self.publication(
                "Departamento en arriendo",
                "Promoción: a partir del segundo mes, $500.000.",
            ),
            self.publication(
                "Departamento en arriendo",
                "Descuento inicial; luego 12 UF.",
            ),
        )
        for publication in publications:
            with self.subTest(description=publication["descripcion"]):
                with patch("filtro.obtener_valor_uf", return_value=41_189):
                    self.assertFalse(filtro.filtrar_ubicacion(publication))

    def test_future_price_comparison_includes_common_expenses(self):
        publication = self.publication(
            "Departamento en arriendo",
            "Precio normal desde el segundo mes: $450.000.",
        )
        publication["gc_clp"] = 30_000
        self.assertFalse(filtro.filtrar_ubicacion(publication))

    def test_future_price_within_limit_is_allowed_in_final_filter(self):
        publication = self.publication(
            "Departamento en arriendo",
            "Luego el arriendo queda en 10 UF.",
        )
        publication.update({"precio_clp": 400_000, "gc_clp": 0})
        with patch("filtro.obtener_valor_uf", return_value=41_189):
            self.assertTrue(filtro.filtrar_publicacion(publication))

    def test_final_filter_keeps_valid_price_and_rejects_over_limit(self):
        with patch("filtro.obtener_valor_uf", return_value=41_189):
            valid = self.publication("Departamento independiente")
            valid["precio_clp"] = 400_000
            self.assertTrue(filtro.filtrar_publicacion(valid))

            expensive = self.publication("Departamento independiente")
            expensive["precio_clp"] = 500_000
            self.assertFalse(filtro.filtrar_publicacion(expensive))

    def test_pipeline_filters_enriched_description_without_rejecting_accessories(self):
        departamento = self.publication(
            "Departamento 1D1B en arriendo con bodega",
            "Incluye estacionamiento.",
        )
        departamento.update({"precio_clp": 400_000, "gc_clp": 0})
        servicio = self.publication(
            "Departamento en arriendo",
            "Gestión de arriendo y administración de propiedades.",
        )
        servicio.update({"precio_clp": 400_000, "gc_clp": 0})

        with patch("filtro.obtener_valor_uf", return_value=41_189):
            resultado = filtro.aplicar_filtros_y_scoring([departamento, servicio])

        self.assertEqual([pub["id"] for pub in resultado], ["MLC-123"])

    def test_accepts_city_business_sector_but_not_other_huechuraba_areas(self):
        publication = self.publication("Departamento en Ciudad Empresarial")
        publication.update({
            "comuna": "Ciudad Empresarial",
            "ubicacion": "Ciudad Empresarial, Huechuraba",
        })
        self.assertTrue(filtro.filtrar_ubicacion(publication))

        publication["titulo"] = "Departamento en Huechuraba"
        publication["ubicacion"] = "Pedro Fontova, Huechuraba"
        self.assertFalse(filtro.filtrar_ubicacion(publication))

    def test_huechuraba_offer_requires_explicit_city_business_mention(self):
        publication = self.publication("Departamento en arriendo")
        publication.update({
            "comuna": "Huechuraba",
            "ubicacion": "Huechuraba, Región Metropolitana",
        })
        self.assertFalse(filtro.filtrar_ubicacion(publication))

        publication["titulo"] = "Departamento en CIUDAD EMPRESARIAL"
        self.assertTrue(filtro.filtrar_ubicacion(publication))

    def test_huechuraba_offer_accepts_city_business_in_description(self):
        publication = self.publication(
            "Departamento en arriendo",
            "A pasos de Ciudad Empresarial, ideal para profesionales.",
        )
        publication.update({
            "comuna": "Huechuraba",
            "ubicacion": "Huechuraba, Región Metropolitana",
        })
        self.assertTrue(filtro.filtrar_ubicacion(publication))

    def test_exception_to_price_limit_requires_both_extras_explicitly_included(self):
        cases = (
            ("Departamento en arriendo", 470_000, True),
            ("Departamento en arriendo", 470_001, False),
            (
                "Departamento incluye estacionamiento y bodega",
                490_000,
                True,
            ),
            (
                "Departamento incluye estacionamiento y bodega",
                490_001,
                False,
            ),
            (
                "Departamento incluye estacionamiento",
                480_000,
                False,
            ),
            (
                "Departamento con estacionamiento y bodega disponibles por separado",
                480_000,
                False,
            ),
            (
                "Departamento con estacionamiento incluido y bodega no incluida",
                480_000,
                False,
            ),
        )
        for titulo, precio, esperado in cases:
            with self.subTest(titulo=titulo, precio=precio):
                publication = self.publication(titulo)
                publication.update({
                    "precio_clp": precio,
                    "gc_clp": 0,
                    "precio_total": precio,
                })
                self.assertEqual(filtro.filtrar_publicacion(publication), esperado)

    def test_explicit_inclusion_can_be_confirmed_in_description(self):
        publication = self.publication("Departamento en arriendo")
        publication.update({
            "precio_clp": 480_000,
            "gc_clp": 0,
            "precio_total": 480_000,
            "descripcion": "Incluye estacionamiento y bodega sin costo adicional.",
        })
        self.assertTrue(filtro.filtrar_publicacion(publication))

        publication["descripcion"] = (
            "Incluye estacionamiento; bodega disponible con pago adicional."
        )
        self.assertFalse(filtro.filtrar_publicacion(publication))


class ScraperTests(unittest.TestCase):
    def test_search_url_uses_portal_canonical_filter_order(self):
        first_page = scraper._construir_url_busqueda("lo-barnechea-metropolitana", 0)
        next_page = scraper._construir_url_busqueda("lo-barnechea-metropolitana", 1)
        self.assertTrue(
            first_page.endswith(
                "_PriceRange_0-490000_NoIndex_True_O_DateCreatedDESC"
            )
        )
        self.assertTrue(next_page.endswith("_Desde_49"))

    def test_city_business_uses_huechuraba_commune_slug(self):
        slug = scraper.COMUNA_SLUGS["Ciudad Empresarial"]
        self.assertEqual(slug, "huechuraba-metropolitana")
        url = scraper._construir_url_busqueda(slug, 0)
        self.assertIn(
            "/arriendo/departamento/huechuraba-metropolitana/",
            url,
        )
        card = scraper._crear_soup("<div>Departamento en arriendo</div>").div
        self.assertTrue(scraper._card_es_publicacion_valida(
            card,
            "Ciudad Empresarial",
            "Ciudad Empresarial, Huechuraba",
        ))
        self.assertTrue(scraper._card_es_publicacion_valida(
            card,
            "Ciudad Empresarial",
            "Los Libertadores, Huechuraba",
        ))
        self.assertFalse(scraper._card_es_publicacion_valida(
            card,
            "Ciudad Empresarial",
            "Las Condes, Apoquindo",
        ))

    def test_soup_falls_back_to_html_parser_without_lxml(self):
        real_soup = scraper.BeautifulSoup

        def fake_soup(html, features=None, **kwargs):
            if features == "lxml":
                raise scraper.FeatureNotFound("lxml no disponible")
            return real_soup(html, features, **kwargs)

        with patch.object(scraper, "BeautifulSoup", side_effect=fake_soup) as soup:
            result = scraper._crear_soup("<div>ok</div>")

        self.assertEqual(result.div.get_text(strip=True), "ok")
        self.assertEqual(soup.call_args_list[0].args[1], "lxml")
        self.assertEqual(soup.call_args_list[1].args[1], "html.parser")

    def test_card_attributes_about_accessories_do_not_drop_department(self):
        card = scraper._crear_soup(
            "<div><span>Bodega y estacionamiento disponibles</span></div>"
        ).div
        self.assertTrue(
            scraper._card_es_publicacion_valida(
                card,
                "Las Condes",
                "Las Condes, Apoquindo",
            )
        )

    def test_detail_enrichment_extracts_description_and_common_expenses(self):
        html = """
        <html><body>
          <div class="ui-pdp-description__content">
            Departamento residencial; gastos comunes $80.000.
          </div>
        </body></html>
        """
        with patch.object(
            scraper,
            "_fetch_html",
            return_value=scraper._FetchResult(html, 200, "https://example.test/listing"),
        ):
            gc, description = scraper._obtener_detalle_publicacion(
                "https://example.test/listing", 41_189
            )
        self.assertEqual(gc, 80_000)
        self.assertIn("Departamento residencial", description)

    def test_not_found_search_does_not_trigger_browser_fallback(self):
        with patch.object(scraper, "obtener_valor_uf", return_value=41_189):
            with patch.object(
                scraper,
                "_fetch_html",
                return_value=scraper._FetchResult(None, 404, "https://example.test/search"),
            ):
                with patch.object(scraper, "_fetch_html_playwright") as playwright:
                    with self.assertRaises(scraper.ScrapeUnavailableError):
                        scraper.scrapear_comuna("Lo Barnechea")
        playwright.assert_not_called()

    def test_portal_zero_results_404_is_a_successful_empty_search(self):
        html = """
        <html><body>
          <div class="ui-search-rescue ui-search-rescue--zrp">
            <h3>En esta categoría no hay inmuebles que coincidan con tu búsqueda.</h3>
          </div>
        </body></html>
        """
        with patch.object(scraper, "obtener_valor_uf", return_value=41_189):
            with patch.object(
                scraper,
                "_fetch_html",
                return_value=scraper._FetchResult(
                    html,
                    404,
                    scraper._construir_url_busqueda(
                        scraper.COMUNA_SLUGS["Lo Barnechea"], 0
                    ),
                ),
            ):
                with patch.object(scraper, "_fetch_html_playwright") as playwright:
                    self.assertEqual(scraper.scrapear_comuna("Lo Barnechea"), [])
        playwright.assert_not_called()

    def test_other_404_pages_are_still_reported_as_errors(self):
        html = "<html><body>Unexpected not found response</body></html>"
        with patch.object(scraper, "obtener_valor_uf", return_value=41_189):
            with patch.object(
                scraper,
                "_fetch_html",
                return_value=scraper._FetchResult(
                    html,
                    404,
                    scraper._construir_url_busqueda(
                        scraper.COMUNA_SLUGS["Lo Barnechea"], 0
                    ),
                ),
            ):
                with self.assertRaises(scraper.ScrapeUnavailableError):
                    scraper.scrapear_comuna("Lo Barnechea")

    def test_rate_limit_response_is_not_retried(self):
        response = Mock(status_code=429, url="https://example.test/search", text="")
        with patch.object(scraper, "_http_get", return_value=response) as get:
            with patch.object(scraper.time, "sleep") as sleep:
                result = scraper._fetch_html("https://example.test/search")

        self.assertEqual(result.status_code, 429)
        get.assert_called_once()
        sleep.assert_not_called()

    def test_account_verification_response_is_retried_with_another_profile(self):
        verification = Mock(
            status_code=200,
            url="https://www.portalinmobiliario.com/gz/account-verification",
            text="",
        )
        success = Mock(
            status_code=200,
            url="https://example.test/search",
            text="<html></html>",
        )
        with patch.object(
            scraper, "_http_get", side_effect=[verification, success]
        ) as get:
            with patch.object(scraper.time, "sleep"):
                result = scraper._fetch_html("https://example.test/search")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.html, "<html></html>")
        self.assertEqual(get.call_count, 2)
        self.assertNotEqual(
            get.call_args_list[0].args[1], get.call_args_list[1].args[1]
        )

    def test_partial_commune_failures_keep_successful_publications(self):
        publication = {"id": "MLC-123"}

        def scrape_one(comuna):
            if comuna == "Vitacura":
                raise scraper.ScrapeUnavailableError("temporary failure")
            return [publication]

        with patch.object(scraper, "COMUNAS", ["Las Condes", "Vitacura"]):
            with patch.object(scraper, "scrapear_comuna", side_effect=scrape_one):
                with patch.object(scraper.time, "sleep"):
                    with self.assertRaises(scraper.ScrapePartialError) as caught:
                        scraper.scrapear_todas_las_comunas()

        self.assertEqual(caught.exception.publicaciones, [publication])
        self.assertEqual(len(caught.exception.errores), 1)

    def test_access_denial_stops_before_requesting_more_communes(self):
        def scrape_one(comuna):
            if comuna == "Vitacura":
                raise scraper.ScrapeAccessError("HTTP 403")
            return [{"id": comuna}]

        with patch.object(
            scraper, "COMUNAS", ["Las Condes", "Vitacura", "Providencia"]
        ):
            with patch.object(
                scraper, "scrapear_comuna", side_effect=scrape_one
            ) as scrape:
                with patch.object(scraper.time, "sleep"):
                    with self.assertRaises(scraper.ScrapeAccessError):
                        scraper.scrapear_todas_las_comunas()

        self.assertEqual(
            [call.args[0] for call in scrape.call_args_list],
            ["Las Condes", "Vitacura"],
        )


class UFCacheTests(unittest.TestCase):
    def tearDown(self):
        uf_helper._uf_cache.clear()

    def test_network_failure_uses_recent_persisted_value(self):
        yesterday = date.today() - timedelta(days=1)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "uf.json"
            cache_path.write_text(
                json.dumps({"fecha": yesterday.isoformat(), "valor": 41_189}),
                encoding="utf-8",
            )
            with patch.object(uf_helper, "_ruta_cache", return_value=cache_path):
                with patch.object(
                    uf_helper.requests,
                    "get",
                    side_effect=requests.Timeout("timeout"),
                ) as get:
                    self.assertEqual(uf_helper.obtener_valor_uf(), 41_189)
            self.assertEqual(get.call_args.kwargs["timeout"], uf_helper.UF_REQUEST_TIMEOUT)

    def test_successful_api_value_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "uf.json"
            response = Mock()
            response.json.return_value = {"serie": [{"valor": 41_190.5}]}
            with patch.object(uf_helper, "_ruta_cache", return_value=cache_path):
                with patch.object(uf_helper.requests, "get", return_value=response):
                    self.assertEqual(uf_helper.obtener_valor_uf(), 41_190.5)
            self.assertEqual(
                json.loads(cache_path.read_text(encoding="utf-8"))["valor"],
                41_190.5,
            )


if __name__ == "__main__":
    unittest.main()
