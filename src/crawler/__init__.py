"""
Módulo de Sincronización y Crawler del Campus Virtual de la UNIR.
Automatiza la navegación autenticada con Playwright y la descarga de temarios y recursos.
"""

def __getattr__(name: str):
    if name == "CampusScraper":
        from .campus_scraper import CampusScraper
        return CampusScraper
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["CampusScraper"]
