"""
Módulo de Inteligencia Artificial para UNIR Study Hub.
Proporciona el cliente LLM, el Asistente Tutor Académico y generadores basados en IA.
"""

from .llm_client import LLMClient
from .tutor import TutorIA

__all__ = ["LLMClient", "TutorIA"]
