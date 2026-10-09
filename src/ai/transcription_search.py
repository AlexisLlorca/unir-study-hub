"""
Motor de Búsqueda Inteligente y RAG con Minutaje Exacto en Videoclases de UNIR Study Hub.
Localiza intervenciones literales de profesores y alumnos en 146+ clases grabadas,
devolviendo marcas de tiempo [MM:SS] o [HH:MM:SS], oradores y enlaces para NOVA y el visor web.
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.core.config import (
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
)

STOPWORDS = {
    'que', 'por', 'para', 'con', 'del', 'las', 'los', 'una', 'uno', 'unos', 'unas',
    'este', 'esta', 'estos', 'estas', 'como', 'sobre', 'aqui', 'hay', 'ver', 'dijo',
    'dicho', 'profe', 'profesor', 'profesora', 'hola', 'buenas', 'tardes', 'dias',
    'clase', 'tema', 'sesion', 'explicar', 'explico', 'cuenta', 'dice', 'decir', 'cuando'
}


def extraer_terminos_clave(texto: str) -> List[str]:
    """Extrae palabras clave ignorando stopwords."""
    palabras = [w.lower() for w in re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]{3,}\b', texto)]
    filtradas = [w for w in palabras if w not in STOPWORDS]
    return filtradas or palabras


def buscar_en_transcripciones(
    query: str,
    asignatura: Optional[str] = None,
    max_results: int = 6
) -> List[Dict[str, Any]]:
    """
    Busca coincidencias directas y temáticas en todos los _cues.json de las clases.
    Devuelve los fragmentos con minutaje exacto, hablante, asignatura y enlace oficial.
    """
    terminos = extraer_terminos_clave(query)
    if not terminos:
        return []

    asigs_dirs = []
    if asignatura:
        asig_lower = asignatura.lower()
        for d in TRANSCRIPCIONES_DIR.iterdir():
            if d.is_dir() and (asig_lower in d.name.lower() or d.name.lower() in asig_lower):
                asigs_dirs.append(d)
    if not asigs_dirs:
        asigs_dirs = [d for d in TRANSCRIPCIONES_DIR.iterdir() if d.is_dir()]

    resultados = []

    for d_asig in asigs_dirs:
        nombre_asig = d_asig.name
        dir_info_asig = CLASES_GRABADAS_DIR / nombre_asig

        for f_cues in d_asig.glob("*_cues.json"):
            stem_base = f_cues.stem.replace("_cues", "")
            f_info = dir_info_asig / f"{stem_base}_info.json"

            meta = {}
            if f_info.exists():
                try:
                    meta = json.loads(f_info.read_text(encoding="utf-8"))
                except Exception:
                    pass

            titulo_sesion = meta.get("sessionName") or meta.get("name") or stem_base
            fecha = stem_base[:10] if len(stem_base) >= 10 else "2026"
            rec_url = meta.get("recordingUrl", "")
            summary = meta.get("summary", "")

            try:
                cues = json.loads(f_cues.read_text(encoding="utf-8"))
            except Exception:
                continue

            for idx, cue in enumerate(cues):
                texto_cue = cue.get("text", "")
                texto_cue_lower = texto_cue.lower()
                hablante = cue.get("speaker", "Profesor")

                puntos = 0
                coincidencias_palabras = 0

                # Coincidencia de frase exacta
                if query.lower() in texto_cue_lower:
                    puntos += 50
                    coincidencias_palabras += len(terminos)

                for t in terminos:
                    if t in texto_cue_lower:
                        puntos += 15
                        coincidencias_palabras += 1
                    if t in summary.lower():
                        puntos += 3

                if coincidencias_palabras > 0:
                    # Incluir contexto (frase anterior o posterior si es relevante)
                    contexto_previo = cues[idx - 1]["text"] if idx > 0 else ""
                    contexto_posterior = cues[idx + 1]["text"] if idx < len(cues) - 1 else ""

                    # Si el orador es un alumno preguntando, enriquecer con la respuesta del profesor
                    texto_completo = f"{texto_cue} {contexto_posterior}".strip() if len(texto_cue) < 180 else texto_cue

                    resultados.append({
                        "asignatura": nombre_asig,
                        "clase": titulo_sesion,
                        "fecha": fecha,
                        "minuto": cue.get("start_str", "00:00"),
                        "segundos": cue.get("start", 0),
                        "hablante": hablante,
                        "texto": texto_completo,
                        "summary": summary,
                        "recording_url": rec_url,
                        "puntuacion": puntos + (coincidencias_palabras * 10),
                    })

    resultados.sort(key=lambda x: x["puntuacion"], reverse=True)
    return resultados[:max_results]


def formatear_contexto_para_tutor(query: str, asignatura: Optional[str] = None) -> str:
    """Construye un bloque de contexto enriquecido para el prompt de NOVA."""
    citas = buscar_en_transcripciones(query, asignatura=asignatura, max_results=4)
    if not citas:
        return ""

    lineas = [
        "\n[CITAS EXACTAS Y EVIDENCIAS DE CLASES GRABADAS OFICIALES UNIR]:",
        "Utiliza estas citas literales y menciona siempre el minuto exacto [MM:SS], el profesor y la fecha:",
    ]

    for c in citas:
        lineas.append(
            f"• Asignatura: {c['asignatura']} | Sesión: \"{c['clase']}\" ({c['fecha']})\n"
            f"  ⏱️ Minuto exacto: [{c['minuto']}] | Hablante: {c['hablante']}\n"
            f"  Declaración literal: \"{c['texto']}\""
        )
        if c.get("summary"):
            lineas.append(f"  Contexto general de la sesión: {c['summary']}")

    lineas.append("")
    return "\n".join(lineas)
