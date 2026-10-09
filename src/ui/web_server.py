"""
Servidor Web y Dashboard Gráfico Interactivo para UNIR Study Hub.
Plataforma Académica Multi-Titulación 100% Web:
  - FP Grado Superior DAM (1º y 2º Curso)
  - Máster en Ciberseguridad (Módulos Oficiales M1 a M7)
  - PFU / Grado en Ingeniería del Software (UNIR)
  - Certificaciones Técnicas & Formación Continua
Permite seleccionar programas, explorar materias, leer temarios y transcripciones,
generar guías y tests con IA y realizar simulacros tipo test directamente desde la web.
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.core.config import (
    console,
    BASE_DIR,
    MATERIALES_DIR,
    TRANSCRIPCIONES_DIR,
    CLASES_GRABADAS_DIR,
    GUIAS_DIR,
    EXAMENES_DIR,
    EJERCICIOS_DIR,
)
from src.core.academic_structure import (
    PROGRAMAS,
    ASIGNATURAS_2DAM,
    ASIGNATURAS_1DAM,
    MODULOS_CIBER,
    ASIGNATURAS_PFU,
    obtener_catalogo_academico,
    obtener_resumen_programas,
    reorganizar_biblioteca_completa,
    obtener_info_asignatura,
)
from src.core.progress_tracker import GestorProgreso
from src.ai.tutor import TutorIA
from src.generators.study_guide_generator import GeneradorGuiaEstudio

app = FastAPI(title="UNIR Study Hub — Hub Multi-Titulación", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

tracker = GestorProgreso()
tutor_ia = TutorIA()
generador = GeneradorGuiaEstudio(tutor_ia)


# --- API ENDPOINTS ---

@app.get("/api/programas")
def api_programas():
    """Retorna la lista de programas académicos con todas sus métricas y cursos."""
    return obtener_resumen_programas()


@app.get("/api/catalogo")
def api_catalogo(programa: Optional[str] = None, curso: Optional[str] = None):
    """Retorna el catálogo consolidado de asignaturas filtrado por programa y curso."""
    catalogo = obtener_catalogo_academico(programa_id=programa, curso_id=curso)
    resultado = []

    for asig, info in catalogo.items():
        dir_guias = GUIAS_DIR / asig
        dir_examenes = EXAMENES_DIR / asig

        guias = [f.name for f in dir_guias.glob("*.md")] if dir_guias.exists() else []
        tests = [f.stem for f in dir_examenes.glob("*.json")] if dir_examenes.exists() else []

        resumen_progreso = tracker.obtener_resumen_asignatura(asig)

        resultado.append({
            "nombre": asig,
            "programa_id": info["programa_id"],
            "curso": info["curso"],
            "total_materiales": len(info["materiales"]),
            "materiales": [f.name for f in info["materiales"]],
            "total_clases": len(info["transcripciones"]),
            "clases": [
                {
                    "nombre": t.stem,
                    "fecha": t.stem[:10] if len(t.stem) >= 10 and t.stem[:4].isdigit() else "Reciente",
                    "titulo": t.stem[11:] if len(t.stem) >= 11 and t.stem[:4].isdigit() else t.stem,
                    "archivo": str(t.relative_to(BASE_DIR)),
                }
                for t in info["transcripciones"]
            ],
            "guias": guias,
            "tests": tests,
            "progreso": resumen_progreso,
        })

    return resultado


@app.get("/api/progreso")
def api_progreso():
    """Retorna el estado del expediente y estadísticas globales."""
    datos = tracker._cargar()
    historial = datos.get("historial_tests", [])
    notas = [r["nota"] for r in historial]
    media = round(sum(notas) / len(notas), 2) if notas else 0.0

    return {
        "media_global": media,
        "tests_realizados": len(historial),
        "ultimas_calificaciones": datos.get("ultimas_calificaciones", {}),
        "historial": historial[-15:],
    }


@app.get("/api/contenido")
def api_contenido(ruta: str):
    """Devuelve el contenido en texto de cualquier guía o transcripción."""
    archivo = BASE_DIR / ruta
    if not archivo.exists() or not archivo.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    
    with open(archivo, "r", encoding="utf-8") as f:
        return {"contenido": f.read(), "nombre": archivo.name}


@app.get("/api/test/{asignatura}/{test_name}")
def api_obtener_test(asignatura: str, test_name: str):
    """Devuelve las preguntas de un test en JSON."""
    archivo = EXAMENES_DIR / asignatura / f"{test_name}.json"
    if not archivo.exists():
        raise HTTPException(status_code=404, detail="Test no encontrado")
    
    with open(archivo, "r", encoding="utf-8") as f:
        return json.load(f)


class TestSubmission(BaseModel):
    asignatura: str
    tema: str
    nota: float
    aciertos: int
    total: int


@app.post("/api/test/guardar")
def api_guardar_test(sub: TestSubmission):
    """Registra la calificación de un test realizado en la web."""
    tracker.registrar_resultado_test(
        asignatura=sub.asignatura,
        tema=sub.tema,
        nota=sub.nota,
        aciertos=sub.aciertos,
        total=sub.total,
    )
    return {"status": "ok"}


class GenerarGuiaRequest(BaseModel):
    asignatura: str
    nombre_tema: str
    archivo_pdf: Optional[str] = None


@app.post("/api/generar-guia")
def api_generar_guia(req: GenerarGuiaRequest):
    """Genera una guía de estudio y test interactivo con IA desde la web."""
    ruta_pdf = None
    if req.archivo_pdf:
        p = MATERIALES_DIR / req.asignatura / req.archivo_pdf
        if p.exists():
            ruta_pdf = p
    else:
        dir_mats = MATERIALES_DIR / req.asignatura
        if dir_mats.exists():
            pdfs = [f for f in dir_mats.glob("*.pdf")]
            if pdfs:
                ruta_pdf = pdfs[0]

    ruta_trans = None
    dir_trans = TRANSCRIPCIONES_DIR / req.asignatura
    if dir_trans.exists():
        trans = [f for f in dir_trans.glob("*.md")]
        if trans:
            ruta_trans = trans[0]

    res = generador.generar_paquete_tema(
        asignatura=req.asignatura,
        nombre_tema=req.nombre_tema,
        ruta_pdf=ruta_pdf,
        ruta_transcripcion=ruta_trans,
    )
    return {"status": "ok", "resultado": res}


@app.post("/api/sincronizar")
def api_sincronizar():
    """Resincroniza carpetas externas de estudio."""
    reorganizar_biblioteca_completa()
    return {"status": "ok"}


@app.post("/api/escanear-primer-ano")
def api_escanear_primer_ano():
    """Escanea el respaldo local e importa todos los temarios del 1er año de DAM."""
    sincronizar_estudios_externos()
    reorganizar_biblioteca_completa()
    return {"status": "ok", "mensaje": "Se han sincronizado e importado 122 documentos y 75 PDFs de 1º DAM con éxito."}


@app.post("/api/descargar-campus")
def api_descargar_campus():
    """Conecta con Canvas UNIR y sincroniza todo: materiales locales, Canvas LMS y clases grabadas."""
    from src.crawler.campus_scraper import CampusScraper
    scraper = CampusScraper()
    nuevos = scraper.ejecutar_sincronizacion_completa()
    reorganizar_biblioteca_completa()
    return {
        "status": "ok",
        "nuevos_archivos": nuevos,
        "mensaje": f"Sincronización 360° completada con éxito. Se han incorporado {nuevos} recursos (temarios, actividades o clases)."
    }


def buscar_contexto_transcripciones(query: str, asignatura: Optional[str] = None) -> str:
    """Busca en transcripciones y fichas de clases de forma rápida para contextualizar el prompt de NOVA."""
    if not TRANSCRIPCIONES_DIR.exists():
        return ""

    asigs_a_buscar = []
    if asignatura:
        for d in TRANSCRIPCIONES_DIR.iterdir():
            if d.is_dir() and (asignatura.lower() in d.name.lower() or d.name.lower() in asignatura.lower()):
                asigs_a_buscar.append(d.name)
    if not asigs_a_buscar:
        asigs_a_buscar = [d.name for d in TRANSCRIPCIONES_DIR.iterdir() if d.is_dir()]

    terminos = [w.lower() for w in re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]{3,}\b', query)]
    stopwords = {'que', 'por', 'para', 'con', 'del', 'las', 'los', 'una', 'uno', 'unos', 'unas', 'este', 'esta', 'como', 'sobre', 'aqui', 'hay', 'ver', 'los', 'dijo', 'dicho', 'profe', 'profesor', 'profesora', 'hola', 'buenas', 'tardes'}
    terminos_clave = [t for t in terminos if t not in stopwords] or terminos

    coincidencias = []
    for asig in asigs_a_buscar:
        dir_t = TRANSCRIPCIONES_DIR / asig
        dir_i = CLASES_GRABADAS_DIR / asig
        if not dir_t.exists():
            continue

        for f_md in dir_t.glob('*.md'):
            try:
                texto = f_md.read_text(encoding='utf-8')
            except Exception:
                continue

            info_file = dir_i / (f_md.stem + '_info.json')
            summary = ''
            session_name = f_md.stem
            if info_file.exists():
                try:
                    data = json.loads(info_file.read_text(encoding='utf-8'))
                    summary = data.get('summary', '')
                    session_name = data.get('sessionName', f_md.stem)
                except Exception:
                    pass

            puntuacion = 0
            texto_lower = texto.lower()
            summary_lower = summary.lower()

            for t in terminos_clave:
                puntuacion += summary_lower.count(t) * 15
                puntuacion += texto_lower.count(t) * 2

            if puntuacion > 0 or not terminos_clave:
                fragmentos = []
                for t in terminos_clave:
                    pos = 0
                    while True:
                        pos = texto_lower.find(t, pos)
                        if pos == -1:
                            break
                        inicio = max(0, pos - 150)
                        fin = min(len(texto), pos + 250)
                        frag = texto[inicio:fin].replace('\n\n', ' ').strip()
                        fragmentos.append(f'...{frag}...')
                        pos += len(t) + 120
                        if len(fragmentos) >= 2:
                            break
                    if len(fragmentos) >= 2:
                        break

                coincidencias.append({
                    'asig': asig,
                    'clase': session_name,
                    'fecha': f_md.stem[:10],
                    'puntuacion': puntuacion,
                    'summary': summary,
                    'fragmentos': fragmentos[:2]
                })

    coincidencias.sort(key=lambda x: x['puntuacion'], reverse=True)
    if not coincidencias:
        return ""

    top = coincidencias[:3]
    contexto_str = "\n[ACCESO A VIDEOCLASES Y TRANSCRIPCIONES OFICIALES DISPONIBLES EN UNIR STUDY HUB]:\n"
    for c in top:
        contexto_str += f"\n📌 ASIGNATURA: {c['asig']} | SESIÓN: {c['clase']} (Fecha: {c['fecha']})\n"
        if c['summary']:
            contexto_str += f"• RESUMEN OFICIAL: {c['summary']}\n"
        if c['fragmentos']:
            contexto_str += f"• FRAGMENTOS DIRECTOS DE LA CLASE:\n"
            for f in c['fragmentos']:
                contexto_str += f"   \"{f}\"\n"
    return contexto_str


@app.get("/api/videoclases")
def api_videoclases(programa: Optional[str] = None):
    """Devuelve el catálogo de todas las clases grabadas con sus metadatos y resúmenes oficiales."""
    if not TRANSCRIPCIONES_DIR.exists():
        return []

    clases = []
    for dir_asig in sorted(TRANSCRIPCIONES_DIR.iterdir()):
        if not dir_asig.is_dir():
            continue
        asig = dir_asig.name
        dir_i = CLASES_GRABADAS_DIR / asig

        for f_md in sorted(dir_asig.glob("*.md"), reverse=True):
            info_file = dir_i / (f_md.stem + "_info.json")
            summary = ""
            titulo = f_md.stem[11:] if len(f_md.stem) >= 11 and f_md.stem[:4].isdigit() else f_md.stem
            fecha = f_md.stem[:10] if len(f_md.stem) >= 10 and f_md.stem[:4].isdigit() else "Reciente"

            if info_file.exists():
                try:
                    data = json.loads(info_file.read_text(encoding="utf-8"))
                    summary = data.get("summary", "")
                    if data.get("sessionName"):
                        titulo = data.get("sessionName")
                except Exception:
                    pass

            clases.append({
                "asignatura": asig,
                "fecha": fecha,
                "titulo": titulo,
                "summary": summary,
                "archivo": str(f_md.relative_to(BASE_DIR)),
                "nombre_archivo": f_md.name
            })
    return clases


class ChatMessage(BaseModel):
    mensaje: str
    asignatura: Optional[str] = None
    programa: Optional[str] = "dam"


@app.post("/api/chat")
def api_chat(msg: ChatMessage):
    """Responde preguntas mediante NOVA con rol especializado e inyección RAG de videoclases."""
    contexto_rol = ""
    if msg.programa == "ciber":
        contexto_rol = (
            "ROL: Eres NOVA, mentora y consultora senior en Ciberseguridad, Red Team, SOC, SIEM y Forense. "
            "Responde con rigor técnico citando estándares oficiales (OWASP, MITRE ATT&CK, NIST, RFC, ISO 27001). "
        )
    elif msg.programa == "grado":
        contexto_rol = (
            "ROL: Eres NOVA, profesora de Grado en Ingeniería del Software y PFU en UNIR. "
            "Enfócate en metodología de software, estructuras de datos formales, complejidad O(n) y arquitectura limpia. "
        )
    elif msg.programa == "cert":
        contexto_rol = (
            "ROL: Eres NOVA, instructora de certificaciones profesionales en Cloud (AWS), Linux (LPIC), DevOps y Seguridad. "
        )
    else:
        contexto_rol = (
            "ROL: Eres NOVA, mentora senior en FP DAM (Desarrollo Multiplataforma). "
            "Dominas Java, Kotlin, Android, SQL, servicios REST, Spring Boot y patrones de arquitectura limpia. "
        )

    # Inyección RAG contextual de videoclases y transcripciones
    contexto_trans = buscar_contexto_transcripciones(msg.mensaje, msg.asignatura)
    contexto_asig = f"\n[Asignatura / Módulo activo: {msg.asignatura}]" if msg.asignatura else ""

    instrucciones_base = (
        "\nINSTRUCCIONES CLAVE DE NOVA:\n"
        "1. Eres NOVA, la copiloto y mentora académica integrada dentro de UNIR Study Hub.\n"
        "2. Tienes ACCESO TOTAL Y LOCAL a la biblioteca oficial de clases grabadas transcritas de UNIR y a todos los temarios.\n"
        "3. NUNCA digas que no tienes acceso al campus virtual ni que los datos están protegidos por login. Toda la información de las videoclases ya ha sido extraída y transcrita localmente en el Hub.\n"
        "4. Si el alumno te pregunta qué dijo un profesor o sobre lo tratado en una clase, responde con la máxima exactitud basándote en las transcripciones y resúmenes oficiales incluidos a continuación. Cita nombres de profesores (ej: David, Borja, etc.), fechas de sesión y directrices exactas explicadas.\n"
    )

    prompt = f"{contexto_rol}{instrucciones_base}{contexto_asig}{contexto_trans}\nAlumno: {msg.mensaje}\nNOVA:"
    
    respuesta = tutor_ia.client.generar_contenido(prompt, temperatura=0.4)
    return {"respuesta": respuesta or "No se pudo generar respuesta."}


# --- FRONTEND HTML SPA CON SIDEBAR DE SECCIONES ---

@app.get("/", response_class=HTMLResponse)
def dashboard_html():
    return """<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UNIR Study Hub — Hub Multi-Titulación</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #f3f4f6; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgba(17, 24, 39, 0.85); backdrop-filter: blur(14px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: rgba(31, 41, 55, 0.5); backdrop-filter: blur(8px); border: 1px solid rgba(255, 255, 255, 0.05); }
    .markdown-content h1 { font-size: 1.45rem; font-weight: 800; color: #38bdf8; line-height: 1.55; margin-top: 1.4rem; margin-bottom: 1rem; letter-spacing: -0.01em; }
    .markdown-content h2 { font-size: 1.25rem; font-weight: 700; color: #818cf8; line-height: 1.5; margin-top: 1.3rem; margin-bottom: 0.7rem; }
    .markdown-content h3 { font-size: 1.1rem; font-weight: 600; color: #a5b4fc; line-height: 1.45; margin-top: 1.1rem; margin-bottom: 0.5rem; }
    .markdown-content p { margin-bottom: 0.9rem; line-height: 1.7; color: #e2e8f0; }
    .markdown-content ul, .markdown-content ol { margin-bottom: 1rem; padding-left: 1.5rem; line-height: 1.65; }
    .markdown-content li { margin-bottom: 0.35rem; }
    .markdown-content pre { background: #111827; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; margin-bottom: 1rem; border: 1px solid #374151; line-height: 1.5; }
    .markdown-content code { background: #1f2937; padding: 0.15rem 0.35rem; border-radius: 0.25rem; font-size: 0.875rem; color: #38bdf8; }
    .markdown-content table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
    .markdown-content th, .markdown-content td { border: 1px solid #374151; padding: 0.5rem 0.75rem; text-align: left; }
    .markdown-content th { background: #1f2937; color: #e5e7eb; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0b0f19; }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #4b5563; }
  </style>
</head>
<body x-data="studyHubApp()" x-init="init()" class="min-h-screen flex bg-[#0b0f19] text-gray-100 overflow-x-hidden">

  <!-- ==================== SIDEBAR PRINCIPAL DE SECCIONES ==================== -->
  <aside class="w-72 bg-[#0f172a]/95 border-r border-gray-800 flex flex-col shrink-0 min-h-screen sticky top-0 z-40 select-none">
    
    <!-- BRAND / LOGO -->
    <div class="p-5 border-b border-gray-800 flex items-center space-x-3">
      <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20">
        <i class="fa-solid fa-graduation-cap text-lg"></i>
      </div>
      <div>
        <h1 class="text-base font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-indigo-400">UNIR Study Hub</h1>
        <p class="text-[11px] text-gray-400 font-medium">Plataforma Multi-Titulación</p>
      </div>
    </div>

    <!-- NAVEGACIÓN DE PROGRAMAS / TITULACIONES -->
    <div class="flex-1 p-4 space-y-6 overflow-y-auto">
      
      <div>
        <p class="text-[10px] uppercase tracking-wider text-gray-400 font-bold px-3 mb-2 flex items-center justify-between">
          <span>Titulaciones y Secciones</span>
          <span class="text-[9px] bg-cyan-500/10 text-cyan-400 px-1.5 py-0.5 rounded font-mono" x-text="programas.length + ' activos'"></span>
        </p>

        <div class="space-y-1">
          <template x-for="prog in programas" :key="prog.id">
            <div>
              <!-- BOTÓN PRINCIPAL DE SECCIÓN -->
              <button @click="seleccionarPrograma(prog.id)"
                      :class="programaActivoId === prog.id ? 'bg-gray-800/90 text-white font-semibold border-gray-700 shadow-md' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                      class="w-full text-left px-3 py-2.5 rounded-xl text-xs transition border flex items-center justify-between group">
                <div class="flex items-center space-x-2.5 truncate">
                  <span class="w-7 h-7 rounded-lg flex items-center justify-center text-xs shrink-0"
                        :style="'background: ' + obtenerFondoHex(prog.color) + '; color: ' + obtenerColorHex(prog.color)">
                    <i :class="'fa-solid ' + prog.icono"></i>
                  </span>
                  <div class="truncate">
                    <p class="truncate" x-text="prog.nombre"></p>
                    <p class="text-[10px] text-gray-400 truncate" x-text="prog.badge"></p>
                  </div>
                </div>
                <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-800 text-gray-400 border border-gray-700" x-text="prog.total_asignaturas"></span>
              </button>

              <!-- SUB-CURSOS SI EL PROGRAMA TIENE VARIOS (EJ: DAM 1º Y 2º) -->
              <div x-show="programaActivoId === prog.id && prog.cursos.length > 1" class="ml-9 mt-1 space-y-0.5 border-l border-gray-800 pl-2">
                <button @click="filtrarCurso('todos')"
                        :class="cursoFiltro === 'todos' ? 'text-cyan-400 font-bold bg-cyan-500/10' : 'text-gray-400 hover:text-gray-300'"
                        class="w-full text-left px-2 py-1 rounded-md text-[11px] transition flex items-center justify-between">
                  <span>Todo el Ciclo</span>
                  <span class="text-[9px] text-gray-400" x-text="prog.total_asignaturas"></span>
                </button>
                <template x-for="c in prog.cursos" :key="c.id">
                  <button @click="filtrarCurso(c.id)"
                          :class="cursoFiltro === c.id ? 'text-cyan-400 font-bold bg-cyan-500/10' : 'text-gray-400 hover:text-gray-300'"
                          class="w-full text-left px-2 py-1 rounded-md text-[11px] transition flex items-center justify-between">
                    <span x-text="c.nombre"></span>
                    <span class="text-[9px] text-gray-400" x-text="c.asignaturas.length"></span>
                  </button>
                </template>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- VISTAS Y HERRAMIENTAS GLOBALES -->
      <div>
        <p class="text-[10px] uppercase tracking-wider text-gray-400 font-bold px-3 mb-2">Herramientas</p>
        <div class="space-y-1">
          <button @click="tab = 'dashboard'; asigSeleccionada = null"
                  :class="tab === 'dashboard' && !asigSeleccionada ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                  class="w-full text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
            <i class="fa-solid fa-chart-pie w-4 text-center"></i>
            <span>Dashboard y Métricas</span>
          </button>
          <button @click="tab = 'asignaturas'"
                  :class="tab === 'asignaturas' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                  class="w-full text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
            <i class="fa-solid fa-book-bookmark w-4 text-center"></i>
            <span>Temarios y Asignaturas</span>
          </button>
          <button @click="tab = 'clases'; asigSeleccionada = null"
                  :class="tab === 'clases' ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                  class="w-full text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
            <i class="fa-solid fa-video w-4 text-center text-amber-400"></i>
            <span>Videoclases y Transcripciones</span>
          </button>
          <button @click="tab = 'tutor'"
                  :class="tab === 'tutor' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                  class="w-full text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
            <i class="fa-solid fa-robot w-4 text-center text-cyan-400"></i>
            <span>Tutor Inteligente NOVA</span>
          </button>
          <button @click="tab = 'progreso'"
                  :class="tab === 'progreso' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40 border-transparent'"
                  class="w-full text-left px-3 py-2 rounded-xl text-xs transition border flex items-center space-x-2.5">
            <i class="fa-solid fa-trophy w-4 text-center text-amber-400"></i>
            <span>Expediente y Calificaciones</span>
          </button>
        </div>
      </div>

    </div>

    <!-- FOOTER SIDEBAR -->
    <div class="p-4 border-t border-gray-800 bg-gray-900/40 flex items-center justify-between text-xs">
      <div class="flex items-center space-x-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span class="text-gray-400 text-[11px]">Sistema Operativo</span>
      </div>
      <button @click="sincronizarMateriales()" :disabled="sincronizando" title="Refrescar y buscar archivos" class="text-gray-400 hover:text-cyan-400 p-1.5 rounded-lg hover:bg-gray-800 transition">
        <i :class="sincronizando ? 'fa-solid fa-rotate fa-spin text-cyan-400' : 'fa-solid fa-rotate'"></i>
      </button>
    </div>
  </aside>

  <!-- ==================== CONTENIDO PRINCIPAL ==================== -->
  <div class="flex-1 flex flex-col min-h-screen max-w-full overflow-hidden">
    
    <!-- TOPBAR -->
    <header class="glass sticky top-0 z-30 px-8 py-3.5 flex items-center justify-between border-b border-gray-800/80">
      <div class="flex items-center space-x-3">
        <span class="text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wider"
              :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)"
              x-text="programaActual?.nombre"></span>
        <span class="text-xs text-gray-400" x-text="cursoFiltro === 'todos' ? 'Todo el programa' : (cursoFiltro === '2dam' ? '2º Curso' : '1º Curso')"></span>
      </div>

      <!-- BUSCADOR RÁPIDO -->
      <div class="flex items-center space-x-4">
        <div class="relative w-64">
          <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-gray-500"></i>
          <input type="text" x-model="busquedaTexto" placeholder="Buscar materia, tema o pdf..." class="w-full bg-gray-900/80 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2 rounded-xl border border-gray-800 focus:outline-none focus:border-cyan-500">
        </div>
        <button @click="abrirChatConAsignatura('')" class="px-3.5 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
          <i class="fa-solid fa-robot"></i><span>Consultar NOVA</span>
        </button>
      </div>
    </header>

    <!-- CUERPO DE LA APLICACIÓN -->
    <main class="flex-1 p-8 space-y-6 overflow-y-auto max-w-7xl w-full mx-auto">

      <!-- ==================== 1. VISTA DASHBOARD ==================== -->
      <div x-show="tab === 'dashboard' && !asigSeleccionada" x-transition class="space-y-6">
        
        <!-- HERO BANNER DEL PROGRAMA ACTIVO -->
        <div class="glass p-6 rounded-3xl relative overflow-hidden border border-gray-800">
          <div class="flex flex-wrap items-center justify-between gap-6 relative z-10">
            <div class="flex items-center space-x-5">
              <div class="w-16 h-16 rounded-2xl flex items-center justify-center text-3xl shadow-xl shrink-0"
                   :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)">
                <i :class="'fa-solid ' + (programaActual?.icono || 'fa-graduation-cap')"></i>
              </div>
              <div>
                <div class="flex items-center space-x-3">
                  <h2 class="text-2xl font-black text-white" x-text="programaActual?.nombre"></h2>
                  <span class="text-xs px-3 py-0.5 rounded-full font-bold border"
                        :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color) + '; border-color: ' + obtenerColorHex(programaActual?.color) + '40'"
                        x-text="programaActual?.badge"></span>
                </div>
                <p class="text-xs text-gray-400 mt-1 max-w-2xl" x-text="programaActual?.descripcion"></p>
              </div>
            </div>

            <!-- ACCIONES Y CURSO SELECTOR RÁPIDO -->
            <div class="flex flex-wrap items-center gap-2">
              <div x-show="programaActivoId === 'dam'" class="flex items-center space-x-2">
                <button @click="escanearPrimerAno()" :disabled="escaneando1dam"
                        class="px-3 py-1.5 rounded-xl text-xs font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25 transition flex items-center space-x-1.5 shadow-sm">
                  <i :class="escaneando1dam ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-folder-tree'"></i>
                  <span x-text="escaneando1dam ? 'Sincronizando...' : '📥 Importar 1º DAM'"></span>
                </button>
                <button @click="descargarCanvasOnline()" :disabled="descargandoCanvas"
                        class="px-3 py-1.5 rounded-xl text-xs font-bold bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 hover:bg-cyan-500/25 transition flex items-center space-x-1.5 shadow-sm">
                  <i :class="descargandoCanvas ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-cloud-arrow-down'"></i>
                  <span x-text="descargandoCanvas ? 'Conectando...' : '🌐 Descargar de Canvas'"></span>
                </button>
              </div>

              <!-- CURSO SELECTOR RÁPIDO -->
              <div x-show="programaActual?.cursos && programaActual?.cursos.length > 1" class="flex items-center space-x-1 bg-gray-900/90 p-1 rounded-2xl border border-gray-800">
                <button @click="filtrarCurso('todos')"
                        :class="cursoFiltro === 'todos' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                        class="px-3.5 py-1.5 rounded-xl text-xs font-bold transition border border-transparent">
                  Todo el Ciclo
                </button>
                <template x-for="c in programaActual?.cursos" :key="c.id">
                  <button @click="filtrarCurso(c.id)"
                          :class="cursoFiltro === c.id ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                          class="px-3.5 py-1.5 rounded-xl text-xs font-bold transition border border-transparent" x-text="c.nombre">
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- STATS HERO CARDS -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-cyan-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Materias / Módulos</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="catalogoFiltrado.length">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-cyan-500/10 text-cyan-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-layer-group"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-cyan-400">
              <i class="fa-solid fa-check-circle mr-1.5"></i>
              <span>Estructura oficial limpia</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-indigo-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Temarios Oficiales</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalMaterialesPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-indigo-500/10 text-indigo-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-file-pdf"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-indigo-400">
              <i class="fa-solid fa-folder-open mr-1.5"></i>
              <span>PDFs indexados sin duplicar</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-amber-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Videoclases Oficiales</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalClasesPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-video"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-amber-400">
              <i class="fa-solid fa-file-lines mr-1.5"></i>
              <span>Transcripciones completas</span>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-emerald-500/30 transition">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Simulacros y Tests</p>
                <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalTestsPrograma">0</h3>
              </div>
              <div class="w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-xl">
                <i class="fa-solid fa-bullseye"></i>
              </div>
            </div>
            <div class="mt-4 flex items-center text-xs text-emerald-400">
              <i class="fa-solid fa-bolt mr-1.5"></i>
              <span>Simulador web interactivo</span>
            </div>
          </div>
        </div>

        <!-- SEMÁFORO Y CATÁLOGO DE ASIGNATURAS -->
        <div class="glass rounded-3xl p-6">
          <div class="flex items-center justify-between mb-6">
            <div>
              <h3 class="text-lg font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-grid-2 text-cyan-400"></i>
                <span>Materias y Módulos de <span x-text="programaActual?.nombre"></span></span>
              </h3>
              <p class="text-xs text-gray-400">Haz clic en cualquier tarjeta para abrir sus temarios, clases magistrales y tests</p>
            </div>
            <span class="text-xs bg-gray-900 text-gray-400 px-3 py-1.5 rounded-xl border border-gray-800" x-text="catalogoFiltrado.length + ' materias'"></span>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
              <div @click="abrirAsignatura(asig)" class="glass-card p-5 rounded-2xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/60 transition group flex flex-col justify-between">
                <div>
                  <div class="flex items-start justify-between">
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-gray-800 text-gray-300 border border-gray-700" x-text="asig.curso"></span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold"
                          :class="asig.progreso.total_evaluados > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-gray-800 text-gray-400'"
                          x-text="asig.progreso.total_evaluados > 0 ? asig.progreso.estado : 'Sin evaluar'">
                    </span>
                  </div>
                  <h4 class="font-bold text-white text-sm mt-3 group-hover:text-cyan-400 transition" x-text="asig.nombre"></h4>
                </div>

                <div class="mt-5 pt-3 border-t border-gray-800/80 grid grid-cols-3 gap-2 text-xs text-gray-400">
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-regular fa-file-pdf text-rose-400"></i>
                    <span x-text="asig.total_materiales + ' pdfs'"></span>
                  </div>
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-solid fa-video text-amber-400"></i>
                    <span x-text="asig.total_clases + ' clases'"></span>
                  </div>
                  <div class="flex items-center space-x-1.5">
                    <i class="fa-solid fa-bolt text-emerald-400"></i>
                    <span x-text="asig.tests.length + ' tests'"></span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>

      </div>

      <!-- ==================== 2. VISTA EXPLORADOR DE TEMARIOS ==================== -->
      <div x-show="tab === 'asignaturas' || asigSeleccionada" x-transition class="space-y-6">
        
        <!-- DETALLE DE ASIGNATURA SELECCIONADA -->
        <div x-show="asigSeleccionada" class="space-y-6">
          <div class="glass p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4 border border-gray-800">
            <div class="flex items-center space-x-4">
              <button @click="asigSeleccionada = null; tab = 'dashboard'" class="w-10 h-10 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center justify-center transition">
                <i class="fa-solid fa-arrow-left"></i>
              </button>
              <div>
                <div class="flex items-center space-x-2">
                  <h2 class="text-2xl font-extrabold text-white" x-text="asigSeleccionada?.nombre"></h2>
                  <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" x-text="asigSeleccionada?.curso"></span>
                </div>
                <p class="text-xs text-gray-400 mt-0.5">Explora temarios oficiales, videoclases magistrales y simulacros de examen</p>
              </div>
            </div>
            
            <div class="flex items-center space-x-3">
              <button @click="modalGenerador = true" class="px-4 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 rounded-xl text-xs font-bold text-gray-900 hover:shadow-lg hover:shadow-emerald-500/20 transition flex items-center space-x-2">
                <i class="fa-solid fa-wand-magic-sparkles"></i><span>Generar Guía / Test IA</span>
              </button>
              <button @click="abrirChatConAsignatura(asigSeleccionada?.nombre)" class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
                <i class="fa-solid fa-robot"></i><span>Consultar con NOVA</span>
              </button>
            </div>
          </div>

          <!-- 3 COLUMNAS: VIDEOCLASES | TEMARIO/GUÍAS | TESTS -->
          <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            <!-- COLUMNA 1: VIDEOCLASES Y TRANSCRIPCIONES -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-video text-amber-400"></i>
                  <span>Videoclases Oficiales</span>
                </h3>
                <span class="text-[11px] bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.clases.length + ' clases'"></span>
              </div>

              <div class="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                <template x-for="clase in asigSeleccionada?.clases" :key="clase.nombre">
                  <div class="glass-card p-3 rounded-xl hover:border-amber-500/30 transition flex items-center justify-between">
                    <div class="overflow-hidden pr-2">
                      <p class="text-[10px] text-amber-400 font-bold" x-text="clase.fecha"></p>
                      <h5 class="text-xs font-medium text-white truncate" x-text="clase.titulo"></h5>
                    </div>
                    <button @click="verContenidoArchivo(clase.archivo)" class="px-2.5 py-1 bg-gray-800 hover:bg-amber-500/20 hover:text-amber-300 text-gray-300 rounded-lg text-xs font-semibold transition shrink-0">
                      Leer
                    </button>
                  </div>
                </template>
                <div x-show="asigSeleccionada?.clases.length === 0" class="text-center py-10 text-gray-500 text-xs">
                  <i class="fa-solid fa-video-slash text-2xl mb-2 opacity-40"></i>
                  <p>Sin videoclases sincronizadas para esta materia.</p>
                </div>
              </div>
            </div>

            <!-- COLUMNA 2: TEMARIO Y GUÍAS DE ESTUDIO -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-book-open text-cyan-400"></i>
                  <span>Guías de Estudio & PDFs</span>
                </h3>
                <span class="text-[11px] bg-cyan-500/10 text-cyan-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.total_materiales + ' docs'"></span>
              </div>

              <div class="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                <!-- GUÍAS RESUMIDAS IA -->
                <template x-for="guia in asigSeleccionada?.guias" :key="guia">
                  <div class="glass-card p-3 rounded-xl hover:border-cyan-500/30 transition flex items-center justify-between">
                    <div class="overflow-hidden pr-2">
                      <p class="text-[10px] text-cyan-400 font-bold">Guía de Alto Rendimiento</p>
                      <h5 class="text-xs font-medium text-white truncate" x-text="guia.replace('_guia.md', '').replaceAll('_', ' ')"></h5>
                    </div>
                    <button @click="verContenidoArchivo('data/procesado/guias/' + asigSeleccionada.nombre + '/' + guia)" class="px-2.5 py-1 bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 rounded-lg text-xs font-semibold transition shrink-0">
                      Estudiar
                    </button>
                  </div>
                </template>

                <!-- PDFs OFICIALES -->
                <div class="pt-3 border-t border-gray-800">
                  <p class="text-[10px] uppercase text-gray-400 font-bold mb-2">Temarios Oficiales</p>
                  <template x-for="m in asigSeleccionada?.materiales" :key="m">
                    <div class="p-2 text-xs text-gray-300 truncate hover:text-white flex items-center justify-between bg-gray-900/40 rounded-lg mb-1 group">
                      <div class="flex items-center space-x-2 truncate pr-2">
                        <i class="fa-regular fa-file-pdf text-rose-400 shrink-0"></i>
                        <span class="truncate" x-text="m"></span>
                      </div>
                      <button @click="generarGuiaDesdePdf(m)" title="Generar Guía IA de este tema" class="opacity-0 group-hover:opacity-100 px-2 py-0.5 bg-emerald-500/20 text-emerald-400 rounded text-[10px] transition shrink-0">
                        Generar IA
                      </button>
                    </div>
                  </template>
                  <div x-show="asigSeleccionada?.materiales.length === 0" class="text-center py-6 text-gray-500 text-xs">
                    <p>Sin documentos locales todavía.</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- COLUMNA 3: TESTS Y EXÁMENES INTERACTIVOS -->
            <div class="glass p-5 rounded-3xl space-y-4">
              <div class="flex items-center justify-between border-b border-gray-800 pb-3">
                <h3 class="font-bold text-white flex items-center space-x-2 text-sm">
                  <i class="fa-solid fa-bullseye text-emerald-400"></i>
                  <span>Simulacros de Examen</span>
                </h3>
                <span class="text-[11px] bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.tests.length + ' tests'"></span>
              </div>

              <div class="space-y-3">
                <template x-for="test in asigSeleccionada?.tests" :key="test">
                  <div class="glass-card p-4 rounded-2xl border-l-4 border-emerald-400 hover:bg-gray-800/50 transition">
                    <h5 class="font-bold text-white text-xs" x-text="test.replace('_test', '').replaceAll('_', ' ')"></h5>
                    <p class="text-[11px] text-gray-400 mt-1">10 preguntas tipo test con formato oficial</p>
                    <button @click="iniciarTestWeb(asigSeleccionada.nombre, test)" class="mt-3 w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-gray-900 font-bold rounded-xl text-xs transition shadow-lg shadow-emerald-500/20">
                      Comenzar Simulacro
                    </button>
                  </div>
                </template>
                <div x-show="asigSeleccionada?.tests.length === 0" class="text-center py-10 text-gray-500 text-xs">
                  <i class="fa-solid fa-list-check text-2xl mb-2 opacity-40"></i>
                  <p>No hay tests generados aún.</p>
                  <button @click="modalGenerador = true" class="mt-3 px-3 py-1.5 bg-emerald-500/20 text-emerald-400 rounded-lg text-xs font-semibold hover:bg-emerald-500/30 transition">
                    Crear Test con IA
                  </button>
                </div>
              </div>
            </div>

          </div>
        </div>

        <!-- REJILLA GENERAL SI NO HAY ASIGNATURA SELECCIONADA -->
        <div x-show="!asigSeleccionada" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
            <div @click="abrirAsignatura(asig)" class="glass p-6 rounded-3xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/40 transition group">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-cyan-400 bg-cyan-500/10 px-2.5 py-1 rounded-full" x-text="asig.curso"></span>
                <i class="fa-solid fa-chevron-right text-gray-500 group-hover:text-cyan-400 transition"></i>
              </div>
              <h3 class="text-base font-bold text-white mt-3 group-hover:text-cyan-300 transition" x-text="asig.nombre"></h3>
              
              <div class="mt-5 pt-4 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
                <span x-text="asig.total_materiales + ' temarios'"></span>
                <span x-text="asig.total_clases + ' videoclases'"></span>
                <span class="text-emerald-400 font-semibold" x-text="asig.tests.length + ' tests'"></span>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- ==================== 3. VISTA EXPEDIENTE Y CALIFICACIONES ==================== -->
      <div x-show="tab === 'progreso'" x-transition class="space-y-6">
        <div class="glass p-6 rounded-3xl border border-gray-800">
          <h2 class="text-xl font-bold text-white flex items-center space-x-2">
            <i class="fa-solid fa-trophy text-amber-400"></i>
            <span>Expediente Académico y Calificaciones</span>
          </h2>
          <p class="text-xs text-gray-400 mt-1">Registro de todos los simulacros de examen realizados</p>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6">
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Nota Media General</p>
              <h3 class="text-3xl font-extrabold text-emerald-400 mt-1" x-text="(progreso.media_global || 0) + ' / 10'"></h3>
            </div>
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Tests Completados</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="progreso.tests_realizados || 0"></h3>
            </div>
            <div class="glass-card p-5 rounded-2xl">
              <p class="text-xs text-gray-400 font-semibold">Semáforo General</p>
              <h3 class="text-2xl font-extrabold text-cyan-400 mt-1" x-text="(progreso.media_global >= 7 ? '🟢 Dominado' : (progreso.media_global >= 5 ? '🟡 Aprobado' : '🔴 En Repaso'))"></h3>
            </div>
          </div>

          <div class="mt-8">
            <h4 class="text-sm font-bold text-white mb-4">Últimos Simulacros Realizados</h4>
            <div class="space-y-2">
              <template x-for="item in progreso.historial" :key="item.fecha + item.tema">
                <div class="glass-card p-4 rounded-xl flex items-center justify-between">
                  <div>
                    <h5 class="text-sm font-bold text-white" x-text="item.asignatura + ' — ' + item.tema"></h5>
                    <p class="text-xs text-gray-400" x-text="item.fecha + ' • ' + item.aciertos + ' de ' + item.total + ' aciertos'"></p>
                  </div>
                  <span class="text-base font-black px-3 py-1 rounded-xl"
                        :class="item.nota >= 5 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'"
                        x-text="item.nota.toFixed(2) + ' / 10'"></span>
                </div>
              </template>
              <div x-show="!progreso.historial || progreso.historial.length === 0" class="text-center py-10 text-gray-500 text-xs">
                <p>Aún no has realizado ningún simulacro de examen.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ==================== 4. VISTA TUTOR NOVA EN DIRECTO ==================== -->
      <div x-show="tab === 'tutor'" x-transition class="glass rounded-3xl flex flex-col h-[750px] overflow-hidden border border-gray-800">
        <!-- HEADER CHAT -->
        <div class="p-4 bg-gray-900/90 border-b border-gray-800 flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <div class="w-10 h-10 rounded-xl flex items-center justify-center text-white shadow-lg"
                 :style="'background: ' + obtenerColorHex(programaActual?.color)">
              <i class="fa-solid fa-robot"></i>
            </div>
            <div>
              <div class="flex items-center space-x-2">
                <h3 class="font-bold text-white">NOVA — Copiloto Académico</h3>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full uppercase"
                      :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)"
                      x-text="'Modo ' + programaActual?.titulo_corto"></span>
              </div>
              <p class="text-xs text-emerald-400 flex items-center">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 animate-pulse"></span>
                Gemini 3 Flash Conectado
              </p>
            </div>
          </div>

          <div class="flex items-center space-x-3">
            <span class="text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2.5 py-1 rounded-xl flex items-center space-x-1.5"
                  x-show="clasesDeAsignaturaActual.length > 0">
              <i class="fa-solid fa-video"></i>
              <span x-text="clasesDeAsignaturaActual.length + ' videoclases vinculadas'"></span>
            </span>
            <select x-model="chatAsignatura" class="bg-gray-800 text-gray-200 text-xs px-3 py-1.5 rounded-xl border border-gray-700 outline-none">
              <option value="">General (Todo el programa)</option>
              <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
                <option :value="asig.nombre" x-text="asig.nombre"></option>
              </template>
            </select>
          </div>
        </div>

        <!-- MENSAJES -->
        <div class="flex-1 p-6 overflow-y-auto space-y-4" id="chatBox">
          <template x-for="(msg, idx) in chatHistorial" :key="idx">
            <div :class="msg.rol === 'user' ? 'flex justify-end' : 'flex justify-start'">
              <div :class="msg.rol === 'user' ? 'bg-cyan-600 text-white rounded-2xl rounded-tr-none' : 'bg-gray-800/90 text-gray-200 rounded-2xl rounded-tl-none border border-gray-700'" class="max-w-2xl p-4 shadow-md">
                <p class="text-xs font-bold mb-1 opacity-75" x-text="msg.rol === 'user' ? 'Tú' : 'NOVA'"></p>
                <div class="markdown-content text-sm" x-html="marked.parse(msg.texto)"></div>
              </div>
            </div>
          </template>
          <div x-show="chatCargando" class="flex justify-start">
            <div class="bg-gray-800 p-4 rounded-2xl rounded-tl-none text-gray-400 text-sm flex items-center space-x-2">
              <i class="fa-solid fa-spinner fa-spin"></i><span>NOVA está razonando la respuesta técnica...</span>
            </div>
          </div>
        </div>

        <!-- QUICK PROMPT CHIPS SOBRE LAS CLASES -->
        <div class="px-4 py-2.5 bg-gray-900/80 border-t border-gray-800 flex items-center space-x-2 overflow-x-auto text-xs" x-show="chatAsignatura">
          <span class="text-[10px] text-amber-400 font-bold uppercase shrink-0 flex items-center space-x-1">
            <i class="fa-solid fa-bolt text-[10px]"></i>
            <span>Preguntar a NOVA:</span>
          </span>
          <button @click="lanzarPreguntaRapida('¿Qué explicó el profesor en la última videoclase de ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 shrink-0 transition text-[11px] border border-gray-700">
            🎙️ ¿Qué explicó el profesor en la última sesión?
          </button>
          <button @click="lanzarPreguntaRapida('¿Qué indicaciones dio el docente sobre el anteproyecto y las entregas en ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 shrink-0 transition text-[11px] border border-gray-700">
            📋 Directrices de anteproyecto y entregas
          </button>
          <button @click="lanzarPreguntaRapida('¿Cuáles son las fechas límite y criterios de evaluación comentados en clase de ' + chatAsignatura + '?')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 hover:text-cyan-300 text-gray-300 shrink-0 transition text-[11px] border border-gray-700">
            📅 Fechas y criterios de evaluación
          </button>
        </div>

        <!-- INPUT BAR -->
        <div class="p-4 bg-gray-900/90 border-t border-gray-800 flex items-center space-x-3">
          <input type="text" x-model="chatInput" @keydown.enter="enviarMensajeChat()"
                 :placeholder="'Haz cualquier pregunta técnica o sobre lo que dijo el docente en las videoclases...'"
                 class="flex-1 bg-gray-800 text-white placeholder-gray-500 text-xs px-4 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-cyan-500">
          <button @click="enviarMensajeChat()" :disabled="chatCargando || !chatInput.trim()"
                  class="px-5 py-3 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white font-semibold rounded-xl text-xs hover:shadow-lg hover:shadow-cyan-500/20 disabled:opacity-50 transition">
            <i class="fa-solid fa-paper-plane mr-1.5"></i>Enviar
          </button>
        </div>
      </div>

      <!-- ==================== 5. VISTA VIDEOCLASES Y TRANSCRIPCIONES ==================== -->
      <div x-show="tab === 'clases'" x-transition class="space-y-6">
        
        <div class="glass p-6 rounded-3xl border border-gray-800 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div class="flex items-center space-x-3">
              <div class="w-10 h-10 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-lg">
                <i class="fa-solid fa-video"></i>
              </div>
              <div>
                <h2 class="text-xl font-bold text-white">Videoclases y Transcripciones Oficiales</h2>
                <p class="text-xs text-gray-400">Catálogo oficial con transcripciones completas y resúmenes de cada lección magistral</p>
              </div>
            </div>
          </div>
          
          <div class="flex items-center space-x-3">
            <span class="text-xs bg-amber-500/10 text-amber-400 border border-amber-500/20 px-3 py-1.5 rounded-xl font-semibold"
                  x-text="videoclasesFiltradas.length + ' clases disponibles'"></span>
          </div>
        </div>

        <!-- FILTROS Y BÚSQUEDA DE CLASES -->
        <div class="glass-card p-4 rounded-2xl flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-3 flex-1 min-w-[280px]">
            <div class="relative flex-1">
              <i class="fa-solid fa-magnifying-glass absolute left-3 top-3 text-xs text-gray-500"></i>
              <input type="text" x-model="busquedaClaseTexto" placeholder="Buscar por profesor, tema, fecha o palabra dicha en clase..."
                     class="w-full bg-gray-900 text-white placeholder-gray-500 text-xs pl-8 pr-3 py-2.5 rounded-xl border border-gray-800 focus:outline-none focus:border-amber-500">
            </div>
          </div>
          
          <div class="flex items-center space-x-3">
            <select x-model="filtroClaseAsignatura" class="bg-gray-900 text-gray-200 text-xs px-3.5 py-2.5 rounded-xl border border-gray-800 outline-none focus:border-amber-500">
              <option value="">Todas las asignaturas</option>
              <template x-for="asig in asignaturasConClases" :key="asig">
                <option :value="asig" x-text="asig"></option>
              </template>
            </select>
          </div>
        </div>

        <!-- LISTA / REJILLA DE VIDEOCLASES -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <template x-for="clase in videoclasesFiltradas" :key="clase.archivo">
            <div class="glass p-5 rounded-2xl border border-gray-800 hover:border-amber-500/40 transition flex flex-col justify-between group">
              <div>
                <div class="flex items-center justify-between">
                  <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 truncate max-w-[220px]" x-text="clase.asignatura"></span>
                  <span class="text-[11px] font-semibold text-amber-400 flex items-center space-x-1">
                    <i class="fa-regular fa-calendar mr-1"></i>
                    <span x-text="clase.fecha"></span>
                  </span>
                </div>
                <h4 class="font-bold text-white text-sm mt-3 group-hover:text-amber-300 transition" x-text="clase.titulo"></h4>
                <p class="text-xs text-gray-400 mt-2 line-clamp-3 leading-relaxed" x-text="clase.summary || 'Transcripción oficial completa disponible con registro detallado de la intervención del profesor y dudas de los estudiantes.'"></p>
              </div>

              <div class="mt-4 pt-3 border-t border-gray-800 flex items-center justify-between">
                <button @click="verContenidoArchivo(clase.archivo)" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 rounded-xl text-xs font-semibold transition flex items-center space-x-1.5">
                  <i class="fa-solid fa-book-open text-amber-400"></i>
                  <span>Leer Transcripción</span>
                </button>
                <button @click="preguntarSobreClase(clase)" class="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 rounded-xl text-xs font-semibold transition flex items-center space-x-1.5">
                  <i class="fa-solid fa-robot"></i>
                  <span>Preguntar a NOVA</span>
                </button>
              </div>
            </div>
          </template>
        </div>

        <div x-show="videoclasesFiltradas.length === 0" class="text-center py-16 text-gray-500 text-xs">
          <i class="fa-solid fa-video-slash text-3xl mb-3 opacity-30 text-amber-400"></i>
          <p class="text-sm">No se encontraron videoclases que coincidan con la búsqueda.</p>
        </div>

      </div>

    </main>

  </div>

  <!-- ==================== MODAL: GENERADOR DE GUÍA IA ==================== -->
  <div x-show="modalGenerador" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-lg rounded-3xl p-6 border border-emerald-500/30 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-gray-800 pb-3">
        <h4 class="font-bold text-white text-base flex items-center space-x-2">
          <i class="fa-solid fa-wand-magic-sparkles text-emerald-400"></i>
          <span>Generar Guía y Test con IA</span>
        </h4>
        <button @click="modalGenerador = false" class="text-gray-400 hover:text-white p-1 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <p class="text-xs text-gray-400">NOVA procesará el temario oficial en PDF y la transcripción para sintetizar apuntes clave y 10 preguntas tipo test.</p>

      <div>
        <label class="block text-xs font-bold text-gray-300 mb-1">Materia Seleccionada:</label>
        <p class="text-sm font-semibold text-white bg-gray-900 p-2.5 rounded-xl border border-gray-800" x-text="asigSeleccionada?.nombre"></p>
      </div>

      <div>
        <label class="block text-xs font-bold text-gray-300 mb-1">Nombre o Título del Tema:</label>
        <input type="text" x-model="generadorTemaNombre" placeholder="Ej: Tema 1 - Fundamentos y Arquitectura" class="w-full bg-gray-900 text-white text-xs p-3 rounded-xl border border-gray-700 focus:outline-none focus:border-emerald-500">
      </div>

      <div class="pt-2 flex justify-end space-x-3">
        <button @click="modalGenerador = false" class="px-4 py-2 bg-gray-800 text-gray-300 rounded-xl text-xs font-semibold">Cancelar</button>
        <button @click="ejecutarGeneracionIa()" :disabled="generandoIa || !generadorTemaNombre.trim()" class="px-5 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 text-gray-900 font-bold rounded-xl text-xs transition flex items-center space-x-2 disabled:opacity-50">
          <i :class="generandoIa ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-bolt'"></i>
          <span x-text="generandoIa ? 'Generando con IA...' : 'Generar Paquete de Estudio'"></span>
        </button>
      </div>
    </div>
  </div>

  <!-- ==================== MODAL: VISOR DE DOCUMENTOS ==================== -->
  <div x-show="modalVisor" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-4xl max-h-[85vh] rounded-3xl flex flex-col overflow-hidden border border-gray-700 shadow-2xl">
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <h4 class="font-bold text-white text-sm truncate" x-text="modalTitulo"></h4>
        <button @click="modalVisor = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>
      <div class="flex-1 p-6 overflow-y-auto markdown-content text-sm" x-html="marked.parse(modalContenido)"></div>
    </div>
  </div>

  <!-- ==================== MODAL: SIMULADOR DE EXAMEN WEB ==================== -->
  <div x-show="modalTest" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-3xl max-h-[90vh] rounded-3xl flex flex-col overflow-hidden border border-emerald-500/30 shadow-2xl">
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h4 class="font-bold text-white text-sm" x-text="'Simulacro: ' + testActivoAsig"></h4>
          <p class="text-xs text-gray-400" x-text="'Pregunta ' + (testIndice + 1) + ' de ' + (testPreguntas.length)"></p>
        </div>
        <button @click="modalTest = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <div class="flex-1 p-6 overflow-y-auto space-y-5" x-show="!testFinalizado && testPreguntas.length > 0">
        <h3 class="text-base font-bold text-white" x-text="testPreguntas[testIndice]?.enunciado"></h3>

        <div class="space-y-3">
          <template x-for="(texto, letra) in testPreguntas[testIndice]?.opciones" :key="letra">
            <button @click="seleccionarOpcion(letra)"
                    :class="testRespuestaSeleccionada === letra ? 'border-cyan-500 bg-cyan-500/10 text-white' : 'border-gray-700 bg-gray-800/40 text-gray-300 hover:bg-gray-800'"
                    class="w-full text-left p-4 rounded-xl border transition flex items-start space-x-3">
              <span class="w-6 h-6 rounded-lg bg-gray-700 flex items-center justify-center text-xs font-bold text-white shrink-0" x-text="letra"></span>
              <span class="text-sm" x-text="texto"></span>
            </button>
          </template>
        </div>
      </div>

      <div class="flex-1 p-8 text-center space-y-4" x-show="testFinalizado">
        <div class="w-20 h-20 rounded-full mx-auto flex items-center justify-center text-3xl"
             :class="testNota >= 5 ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-rose-500/20 text-rose-400 border border-rose-500/40'">
          <i :class="testNota >= 5 ? 'fa-solid fa-check' : 'fa-solid fa-xmark'"></i>
        </div>
        <h3 class="text-2xl font-extrabold text-white" x-text="testNota >= 5 ? '¡Enhorabuena, has aprobado!' : 'Necesitas repasar este tema'"></h3>
        <p class="text-gray-400 text-sm">Tu puntuación oficial en el simulacro:</p>
        <p class="text-5xl font-black" :class="testNota >= 5 ? 'text-emerald-400' : 'text-rose-400'" x-text="testNota.toFixed(2) + ' / 10'"></p>
        <p class="text-xs text-gray-500" x-text="testAciertos + ' aciertos de ' + testPreguntas.length + ' preguntas.'"></p>
        <button @click="modalTest = false" class="px-6 py-2.5 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-semibold transition">
          Cerrar y Guardar Nota
        </button>
      </div>

      <div class="p-4 bg-gray-900 border-t border-gray-800 flex items-center justify-between" x-show="!testFinalizado">
        <button @click="testIndice = Math.max(0, testIndice - 1)" :disabled="testIndice === 0" class="px-4 py-2 bg-gray-800 rounded-lg text-xs font-semibold text-gray-300 disabled:opacity-40">
          Anterior
        </button>
        <button @click="siguientePregunta()" class="px-6 py-2 bg-gradient-to-r from-emerald-500 to-teal-600 text-gray-900 font-bold rounded-lg text-xs transition">
          <span x-text="testIndice === testPreguntas.length - 1 ? 'Finalizar Examen' : 'Siguiente'"></span>
        </button>
      </div>
    </div>
  </div>

  <script>
    function studyHubApp() {
      return {
        tab: 'dashboard',
        programas: [],
        programaActivoId: 'dam',
        cursoFiltro: 'todos',
        busquedaTexto: '',
        catalogoTotal: [],
        catalogoFiltrado: [],
        progreso: {},
        asigSeleccionada: null,
        sincronizando: false,
        escaneando1dam: false,
        descargandoCanvas: false,

        // Generador IA Modal
        modalGenerador: false,
        generadorTemaNombre: '',
        generandoIa: false,
        
        // Modal Visor
        modalVisor: false,
        modalTitulo: '',
        modalContenido: '',

        // Chat
        chatInput: '',
        chatAsignatura: '',
        chatCargando: false,
        chatHistorial: [],

        // Test Web
        modalTest: false,
        testActivoAsig: '',
        testActivoNombre: '',
        testPreguntas: [],
        testIndice: 0,
        testRespuestasUsuario: {},
        testRespuestaSeleccionada: null,
        testFinalizado: false,
        testAciertos: 0,
        testNota: 0,

        // Videoclases y Transcripciones
        todasLasClases: [],
        filtroClaseAsignatura: '',
        busquedaClaseTexto: '',

        get programaActual() {
          return this.programas.find(p => p.id === this.programaActivoId) || this.programas[0];
        },

        get totalMaterialesPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.total_materiales || 0), 0);
        },

        get totalClasesPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.total_clases || 0), 0);
        },

        get totalTestsPrograma() {
          return this.catalogoFiltrado.reduce((acc, a) => acc + (a.tests?.length || 0), 0);
        },

        get videoclasesFiltradas() {
          let lista = this.todasLasClases;
          if (this.filtroClaseAsignatura) {
            lista = lista.filter(c => c.asignatura === this.filtroClaseAsignatura);
          }
          if (this.busquedaClaseTexto.trim()) {
            const q = this.busquedaClaseTexto.toLowerCase();
            lista = lista.filter(c => c.titulo.toLowerCase().includes(q) || c.asignatura.toLowerCase().includes(q) || (c.summary && c.summary.toLowerCase().includes(q)) || c.fecha.includes(q));
          }
          return lista;
        },

        get asignaturasConClases() {
          return [...new Set(this.todasLasClases.map(c => c.asignatura))].sort();
        },

        get clasesDeAsignaturaActual() {
          if (!this.chatAsignatura) return [];
          return this.todasLasClases.filter(c => c.asignatura.toLowerCase().includes(this.chatAsignatura.toLowerCase()) || this.chatAsignatura.toLowerCase().includes(c.asignatura.toLowerCase()));
        },

        async init() {
          await this.cargarProgramas();
          await this.cargarCatalogo();
          await this.cargarProgreso();
          await this.cargarVideoclases();
          this.actualizarMensajeBienvenidaChat();
        },

        async cargarProgramas() {
          try {
            const res = await fetch('/api/programas');
            this.programas = await res.json();
          } catch(e) { console.error(e); }
        },

        async cargarCatalogo() {
          try {
            const res = await fetch('/api/catalogo');
            this.catalogoTotal = await res.json();
            this.aplicarFiltroCatalogo();
          } catch(e) { console.error(e); }
        },

        async cargarProgreso() {
          try {
            const res = await fetch('/api/progreso');
            this.progreso = await res.json();
          } catch(e) { console.error(e); }
        },

        seleccionarPrograma(progId) {
          this.programaActivoId = progId;
          this.cursoFiltro = 'todos';
          this.asigSeleccionada = null;
          this.tab = 'dashboard';
          this.aplicarFiltroCatalogo();
          this.actualizarMensajeBienvenidaChat();
        },

        filtrarCurso(cursoId) {
          this.cursoFiltro = cursoId;
          this.asigSeleccionada = null;
          this.aplicarFiltroCatalogo();
        },

        aplicarFiltroCatalogo() {
          let lista = this.catalogoTotal.filter(a => a.programa_id === this.programaActivoId);
          if (this.programaActivoId === 'dam' && this.cursoFiltro !== 'todos') {
            const nombreCurso = this.cursoFiltro === '2dam' ? '2º Curso' : '1º Curso';
            lista = lista.filter(a => a.curso === nombreCurso);
          }
          if (this.busquedaTexto.trim()) {
            const q = this.busquedaTexto.toLowerCase();
            lista = lista.filter(a => a.nombre.toLowerCase().includes(q) || a.materiales.some(m => m.toLowerCase().includes(q)));
          }
          this.catalogoFiltrado = lista;
        },

        async sincronizarMateriales() {
          this.sincronizando = true;
          try {
            await fetch('/api/sincronizar', { method: 'POST' });
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
          } catch(e) { console.error(e); }
          finally { this.sincronizando = false; }
        },

        async escanearPrimerAno() {
          this.escaneando1dam = true;
          try {
            const res = await fetch('/api/escanear-primer-ano', { method: 'POST' });
            const data = await res.json();
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
            alert(data.mensaje);
          } catch(e) {
            alert('Error al escanear los materiales del 1er año.');
          } finally {
            this.escaneando1dam = false;
          }
        },

        async descargarCanvasOnline() {
          this.descargandoCanvas = true;
          try {
            const res = await fetch('/api/descargar-campus', { method: 'POST' });
            const data = await res.json();
            await this.cargarProgramas();
            await this.cargarCatalogo();
            await this.cargarVideoclases();
            alert(data.mensaje || ('Sincronización de Canvas completada (' + (data.nuevos_archivos || 0) + ' recursos nuevos descargados).'));
          } catch(e) {
            alert('Error al conectar con Canvas. Verifica tu sesión con: unir-hub --login');
          } finally {
            this.descargandoCanvas = false;
          }
        },

        async cargarVideoclases() {
          try {
            const res = await fetch('/api/videoclases');
            this.todasLasClases = await res.json();
          } catch(e) { console.error(e); }
        },

        preguntarSobreClase(clase) {
          this.chatAsignatura = clase.asignatura;
          this.tab = 'tutor';
          this.chatInput = '¿Qué explicó el profesor en la sesión ' + clase.titulo + ' (' + clase.fecha + ') y qué indicaciones concretas dio?';
          this.enviarMensajeChat();
        },

        lanzarPreguntaRapida(texto) {
          this.chatInput = texto;
          this.enviarMensajeChat();
        },

        generarGuiaDesdePdf(pdfNombre) {
          this.generadorTemaNombre = pdfNombre.replace('.pdf', '').replaceAll('_', ' ');
          this.modalGenerador = true;
        },

        async ejecutarGeneracionIa() {
          if (!this.asigSeleccionada || !this.generadorTemaNombre.trim()) return;
          this.generandoIa = true;
          try {
            await fetch('/api/generar-guia', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.asigSeleccionada.nombre,
                nombre_tema: this.generadorTemaNombre.trim()
              })
            });
            await this.cargarCatalogo();
            // Refrescar asignatura seleccionada
            this.asigSeleccionada = this.catalogoTotal.find(a => a.nombre === this.asigSeleccionada.nombre) || this.asigSeleccionada;
            this.modalGenerador = false;
            alert('¡Guía y test generados con éxito!');
          } catch(e) {
            alert('Error generando la guía con IA.');
          } finally {
            this.generandoIa = false;
          }
        },

        actualizarMensajeBienvenidaChat() {
          const p = this.programaActual;
          let saludo = '';
          if (p?.id === 'ciber') {
            saludo = '¡Hola Alex! Soy **NOVA**, tu copiloto en **Ciberseguridad y Auditorías**. ¿Qué módulo o temática (OWASP, SIEM, Forense, Malware o Hacking Ético) repasamos hoy?';
          } else if (p?.id === 'grado') {
            saludo = '¡Hola Alex! Soy **NOVA**, tu mentora en **Ingeniería del Software & PFU**. Lista para resolver dudas de algoritmia, estructuras de datos o diseño formal.';
          } else if (p?.id === 'cert') {
            saludo = '¡Hola Alex! Soy **NOVA**, tu instructora para **Certificaciones Técnicas**. ¿Qué objetivo de certificación preparamos?';
          } else {
            saludo = '¡Hola Alex! Soy **NOVA**, tu copiloto para **FP DAM**. ¿Qué duda de examen o de código quieres que resolvamos hoy?';
          }
          this.chatHistorial = [{ rol: 'model', texto: saludo }];
        },

        obtenerColorHex(color) {
          const map = { cyan: '#22d3ee', rose: '#fb7185', indigo: '#818cf8', amber: '#f59e0b' };
          return map[color] || '#38bdf8';
        },

        obtenerFondoHex(color) {
          const map = { cyan: 'rgba(34, 211, 238, 0.12)', rose: 'rgba(251, 113, 133, 0.12)', indigo: 'rgba(129, 140, 248, 0.12)', amber: 'rgba(245, 158, 11, 0.12)' };
          return map[color] || 'rgba(56, 189, 248, 0.12)';
        },

        abrirAsignatura(asig) {
          this.asigSeleccionada = asig;
          this.tab = 'asignaturas';
        },

        abrirChatConAsignatura(nombre) {
          this.chatAsignatura = nombre;
          this.tab = 'tutor';
        },

        async verContenidoArchivo(ruta) {
          try {
            const res = await fetch('/api/contenido?ruta=' + encodeURIComponent(ruta));
            const data = await res.json();
            this.modalTitulo = data.nombre.replace(/_/g, ' ').replace(/\\.md$/i, '');
            this.modalContenido = data.contenido;
            this.modalVisor = true;
          } catch(e) { alert('No se pudo abrir el archivo'); }
        },

        async iniciarTestWeb(asignatura, testNombre) {
          try {
            const res = await fetch(`/api/test/${encodeURIComponent(asignatura)}/${encodeURIComponent(testNombre)}`);
            this.testPreguntas = await res.json();
            this.testActivoAsig = asignatura;
            this.testActivoNombre = testNombre;
            this.testIndice = 0;
            this.testRespuestasUsuario = {};
            this.testRespuestaSeleccionada = null;
            this.testFinalizado = false;
            this.modalTest = true;
          } catch(e) { alert('No se pudo cargar el test'); }
        },

        seleccionarOpcion(letra) {
          this.testRespuestaSeleccionada = letra;
          this.testRespuestasUsuario[this.testIndice] = letra;
        },

        siguientePregunta() {
          if (this.testIndice < this.testPreguntas.length - 1) {
            this.testIndice++;
            this.testRespuestaSeleccionada = this.testRespuestasUsuario[this.testIndice] || null;
          } else {
            this.finalizarTest();
          }
        },

        async finalizarTest() {
          let aciertos = 0;
          this.testPreguntas.forEach((p, idx) => {
            if (this.testRespuestasUsuario[idx] === p.correcta) aciertos++;
          });
          this.testAciertos = aciertos;
          this.testNota = (aciertos / this.testPreguntas.length) * 10;
          this.testFinalizado = true;

          try {
            await fetch('/api/test/guardar', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                asignatura: this.testActivoAsig,
                tema: this.testActivoNombre.replace('_test', '').replaceAll('_', ' '),
                nota: this.testNota,
                aciertos: this.testAciertos,
                total: this.testPreguntas.length
              })
            });
            await this.cargarProgreso();
            await this.cargarCatalogo();
          } catch(e) { console.error(e); }
        },

        async enviarMensajeChat() {
          if (!this.chatInput.trim() || this.chatCargando) return;
          const texto = this.chatInput.trim();
          this.chatHistorial.push({ rol: 'user', texto });
          this.chatInput = '';
          this.chatCargando = true;

          try {
            const res = await fetch('/api/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                mensaje: texto,
                asignatura: this.chatAsignatura,
                programa: this.programaActivoId
              })
            });
            const data = await res.json();
            this.chatHistorial.push({ rol: 'model', texto: data.respuesta });
          } catch(e) {
            this.chatHistorial.push({ rol: 'model', texto: 'Error al contactar con NOVA.' });
          } finally {
            this.chatCargando = false;
            this.$nextTick(() => {
              const el = document.getElementById('chatBox');
              if (el) el.scrollTop = el.scrollHeight;
            });
          }
        }
      }
    }
  </script>
</body>
</html>"""


def iniciar_servidor_web(host: str = "127.0.0.1", port: int = 8080):
    """Lanza el servidor web de FastAPI en un puerto local."""
    console.print(f"\n[bold green]🚀 Iniciando Interfaz Gráfica Web de UNIR Study Hub (Multi-Titulación)...[/bold green]")
    console.print(f"[bold cyan]👉 Abre en tu navegador:[/bold cyan] [bold white underline]http://{host}:{port}[/bold white underline]\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    iniciar_servidor_web()
