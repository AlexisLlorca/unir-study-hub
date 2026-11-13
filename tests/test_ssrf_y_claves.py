"""
Tests de las defensas anti-SSRF de Canvas y del enmascaramiento de claves.

Comprueban que:
1. No se puede apuntar el Campus o el calendario .ics a destinos internos.
2. Las URLs del Campus quedan normalizadas a HTTPS.
3. Las claves de API nunca se devuelven completas.

Se ejecutan sin instalar nada:
    .venv/bin/python -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import patch

from src.core.config import enmascarar_clave
from src.crawler.student_canvas import _normalizar_url_campus, _url_bloqueada


class TestAntiSsrf(unittest.TestCase):
    def test_bloquea_localhost(self):
        for url in ("http://localhost:8080/api/v1", "https://localhost/.ics"):
            with self.subTest(url=url):
                self.assertIsNotNone(_url_bloqueada(url))

    def test_bloquea_ips_internas(self):
        for url in (
            "http://127.0.0.1/x.ics",
            "http://10.0.0.5/x.ics",
            "http://192.168.1.10/x.ics",
            "http://172.16.0.1/x.ics",
            "http://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(_url_bloqueada(url))

    def test_bloquea_esquemas_peligrosos(self):
        self.assertIsNotNone(_url_bloqueada("file:///etc/passwd"))
        self.assertIsNotNone(_url_bloqueada("ftp://ejemplo.com/x.ics"))

    def test_permite_campus_unir(self):
        self.assertIsNone(_url_bloqueada("https://campusfp.unir.net/api/v1/users/self/profile"))

    def test_normaliza_sin_esquema_a_https(self):
        url, motivo = _normalizar_url_campus("campusfp.unir.net")
        self.assertEqual(motivo, "")
        self.assertEqual(url, "https://campusfp.unir.net")

    def test_asciende_http_a_https(self):
        url, motivo = _normalizar_url_campus("http://campusfp.unir.net")
        self.assertEqual(motivo, "")
        self.assertEqual(url, "https://campusfp.unir.net")

    def test_rechaza_dominio_inexistente(self):
        url, motivo = _normalizar_url_campus("dominio-inexistente-xyz-abc.local")
        self.assertIsNone(url)
        self.assertTrue(motivo)


class TestEnmascaradoClaves(unittest.TestCase):
    def test_muestra_solo_una_parte(self):
        clave = "AIzaSyA1234567890abcdefghij"
        mascara = enmascarar_clave(clave)
        self.assertTrue(mascara.endswith(clave[-4:]))
        self.assertNotIn(clave, mascara)
        self.assertIn("...", mascara)

    def test_cortas_quedan_ocultas(self):
        self.assertEqual(enmascarar_clave("corta"), "••••••")
        self.assertEqual(enmascarar_clave(""), "")
        self.assertEqual(enmascarar_clave(None), "")


if __name__ == "__main__":
    unittest.main()
