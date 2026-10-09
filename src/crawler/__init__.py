"""
Módulo de Sincronización y Crawler del Campus Virtual de la UNIR.
Automatiza la navegación autenticada con Playwright y la descarga de temarios y recursos.
"""

from .campus_scraper import CampusScraper

__all__ = ["CampusScraper"]
