"""
Procesador inteligente de subtítulos y transcripciones (WebVTT) para UNIR Study Hub.
Descarga y estructura las transcripciones oficiales con minutaje exacto, hablantes y bloques temporales.
Permite búsqueda semántica/híbrida con citas de minutos para el tutor NOVA y el visor interactivo.
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests

from src.core.config import (
    console,
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
    ensure_directories,
)


def formatear_segundos(segundos: float) -> str:
    """Convierte segundos a formato HH:MM:SS o MM:SS."""
    seg = int(segundos)
    horas = seg // 3600
    minutos = (seg % 3600) // 60
    secs = seg % 60
    if horas > 0:
        return f"{horas:02d}:{minutos:02d}:{secs:02d}"
    return f"{minutos:02d}:{secs:02d}"


def parsear_tiempo_vtt(tiempo_str: str) -> float:
    """Parsea un timestamp de VTT (HH:MM:SS.mmm o MM:SS.mmm) a segundos flotantes."""
    partes = tiempo_str.strip().split(":")
    try:
        if len(partes) == 3:
            h = float(partes[0])
            m = float(partes[1])
            s = float(partes[2].replace(",", "."))
            return h * 3600 + m * 60 + s
        elif len(partes) == 2:
            m = float(partes[0])
            s = float(partes[1].replace(",", "."))
            return m * 60 + s
    except Exception:
        pass
    return 0.0


def procesar_vtt_a_cues(contenido_vtt: str) -> List[Dict[str, Any]]:
    """
    Parsea el contenido de un WebVTT en una lista de bloques estructurados (cues).
    Cada cue contiene: start, end, start_str, speaker, text.
    Agrupa frases contiguas del mismo hablante para evitar fragmentación excesiva.
    """
    lineas = contenido_vtt.splitlines()
    cues = []
    
    cue_actual = None
    tiempo_pattern = re.compile(r"((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})")

    for linea in lineas:
        linea = linea.strip()
        if not linea or linea == "WEBVTT":
            continue

        match_tiempo = tiempo_pattern.search(linea)
        if match_tiempo:
            t_inicio_str = match_tiempo.group(1)
            t_fin_str = match_tiempo.group(2)
            t_inicio_sec = parsear_tiempo_vtt(t_inicio_str)
            t_fin_sec = parsear_tiempo_vtt(t_fin_str)

            cue_actual = {
                "start": t_inicio_sec,
                "end": t_fin_sec,
                "start_str": formatear_segundos(t_inicio_sec),
                "end_str": formatear_segundos(t_fin_sec),
                "speaker": "Profesor",
                "text": ""
            }
            cues.append(cue_actual)
            continue

        if cue_actual is not None and not linea.isdigit():
            # Limpiar etiquetas VTT como <v Speaker> o <c.color>
            linea_limpia = re.sub(r"<[^>]+>", "", linea).strip()
            
            # Detectar formato "Hablante: Texto"
            match_speaker = re.match(r"^([^:]+):\s*(.*)", linea_limpia)
            if match_speaker:
                cue_actual["speaker"] = match_speaker.group(1).strip()
                texto_cue = match_speaker.group(2).strip()
            else:
                texto_cue = linea_limpia

            if texto_cue:
                if cue_actual["text"]:
                    cue_actual["text"] += " " + texto_cue
                else:
                    cue_actual["text"] = texto_cue

    # Filtrar cues vacíos y agrupar pequeños fragmentos contiguos del mismo hablante
    cues_validos = [c for c in cues if c["text"].strip()]
    cues_agrupados: List[Dict[str, Any]] = []

    for c in cues_validos:
        if cues_agrupados and cues_agrupados[-1]["speaker"] == c["speaker"] and (c["start"] - cues_agrupados[-1]["end"]) < 4.0 and len(cues_agrupados[-1]["text"]) < 400:
            cues_agrupados[-1]["end"] = c["end"]
            cues_agrupados[-1]["end_str"] = c["end_str"]
            cues_agrupados[-1]["text"] += " " + c["text"]
        else:
            cues_agrupados.append(c)

    return cues_agrupados


def cues_a_markdown(cues: List[Dict[str, Any]], nombre_clase: str, fecha: str, url_grabacion: str = "") -> str:
    """Genera un archivo Markdown enriquecido con minutaje exacto de cada intervención."""
    lineas = [
        f"# 🎙️ TRANSCRIPCIÓN OFICIAL CON MINUTAJE: {nombre_clase}",
        f"> **Fecha:** {fecha} | **Fuente:** Campus UNIR / Proeduca LTI",
    ]
    if url_grabacion:
        lineas.append(f"> **Enlace a grabación:** [Ver en UNIR]({url_grabacion})")
    lineas.append("\n---\n")

    for c in cues:
        lineas.append(f"⏱️ **`[{c['start_str']}]`** **🗣️ {c['speaker']}:** {c['text']}\n")

    return "\n".join(lineas)


def procesar_un_archivo(info_file: Path) -> bool:
    """Procesa una sola ficha de videoclase."""
    try:
        datos = json.loads(info_file.read_text(encoding="utf-8"))
    except Exception:
        return False

    captions_url = datos.get("captionsUrl")
    if not captions_url:
        return False

    asignatura = info_file.parent.name
    stem_base = info_file.stem.replace("_info", "")
    fecha = stem_base[:10] if len(stem_base) >= 10 else "2026"
    nombre_sesion = datos.get("sessionName") or datos.get("name") or stem_base

    dir_trans = TRANSCRIPCIONES_DIR / asignatura
    dir_trans.mkdir(parents=True, exist_ok=True)

    archivo_cues_json = dir_trans / f"{stem_base}_cues.json"
    archivo_md = dir_trans / f"{stem_base}.md"

    if archivo_cues_json.exists() and archivo_md.exists():
        return False

    try:
        r = requests.get(captions_url, timeout=15)
        if r.status_code == 200 and r.text:
            cues = procesar_vtt_a_cues(r.text)
            if cues:
                archivo_cues_json.write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
                md_text = cues_a_markdown(cues, nombre_sesion, fecha, datos.get("recordingUrl", ""))
                archivo_md.write_text(md_text, encoding="utf-8")
                return True
    except Exception:
        pass
    return False


def sincronizar_todas_las_transcripciones_vtt() -> int:
    """
    Descarga y genera las transcripciones estructuradas (.md y _cues.json)
    para todas las clases grabadas que tengan 'captionsUrl' de forma paralela.
    """
    from concurrent.futures import ThreadPoolExecutor

    ensure_directories()
    archivos_json = list(CLASES_GRABADAS_DIR.glob("**/*_info.json"))

    console.print(f"\n[cyan]🔍 Comprobando {len(archivos_json)} fichas de videoclases para descarga paralela de VTT...[/cyan]")

    total_procesadas = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        resultados = executor.map(procesar_un_archivo, archivos_json)
        for res in resultados:
            if res:
                total_procesadas += 1

    console.print(f"[bold green]✔ ¡Proceso finalizado! Se han estructurado {total_procesadas} transcripciones con minutaje exacto.[/bold green]\n")
    return total_procesadas
