"""
Servidor Web y Dashboard Gráfico Interactivo para UNIR Study Hub.
Plataforma Multi-Titulación:
  - FP Grado Superior DAM (1º y 2º Curso)
  - Máster en Ciberseguridad (Módulos M1 a M7)
  - PFU / Grado en Ingeniería del Software (UNIR)
  - Certificaciones Técnicas & Formación Continua
Proporciona una interfaz gráfica moderna (HTML5, Tailwind CSS, Alpine.js)
para explorar asignaturas sin mezclas, realizar tests oficiales y chatear con NOVA.
"""

import os
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

app = FastAPI(title="UNIR Study Hub — Hub Multi-Titulación", version="3.0.0")

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
    """Retorna la lista de programas académicos disponibles con sus métricas."""
    return obtener_resumen_programas()


@app.get("/api/catalogo")
def api_catalogo(programa: Optional[str] = None, curso: Optional[str] = None):
    """Retorna el catálogo de asignaturas filtrado por programa y curso."""
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
        "historial": historial[-10:],
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


class ChatMessage(BaseModel):
    mensaje: str
    asignatura: Optional[str] = None
    programa: Optional[str] = "dam"


@app.post("/api/chat")
def api_chat(msg: ChatMessage):
    """Responde preguntas mediante NOVA con especialización por titulación."""
    contexto_rol = ""
    if msg.programa == "ciber":
        contexto_rol = (
            "ROL: Eres NOVA, mentora y consultora senior en Ciberseguridad, Red Team, Blue Team, SOC, SIEM y Forense. "
            "Responde con rigor técnico citando estándares (OWASP, MITRE ATT&CK, NIST, RFC, ISO 27001). "
        )
    elif msg.programa == "grado":
        contexto_rol = (
            "ROL: Eres NOVA, profesora de Grado en Ingeniería del Software y PFU en UNIR. "
            "Enfócate en metodología de software, estructuras de datos, complejidad O(n) y arquitectura formal. "
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

    contexto_asig = f"\n[Asignatura / Módulo activo: {msg.asignatura}]" if msg.asignatura else ""
    prompt = f"{contexto_rol}{contexto_asig}\nAlumno: {msg.mensaje}\nNOVA:"
    
    respuesta = tutor_ia.client.generar_contenido(prompt, temperatura=0.4)
    return {"respuesta": respuesta or "No se pudo generar respuesta."}


# --- FRONTEND HTML SPA ---

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
    .glass { background: rgba(17, 24, 39, 0.75); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: rgba(31, 41, 55, 0.5); backdrop-filter: blur(8px); border: 1px solid rgba(255, 255, 255, 0.05); }
    .markdown-content h1 { font-size: 1.6rem; font-weight: 700; color: #38bdf8; margin-top: 1.2rem; margin-bottom: 0.6rem; }
    .markdown-content h2 { font-size: 1.3rem; font-weight: 600; color: #818cf8; margin-top: 1rem; margin-bottom: 0.5rem; }
    .markdown-content p { margin-bottom: 0.8rem; line-height: 1.6; }
    .markdown-content pre { background: #111827; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; margin-bottom: 1rem; border: 1px solid #374151; }
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
<body x-data="studyHubApp()" x-init="init()" class="min-h-screen flex flex-col">

  <!-- TOPBAR PRINCIPAL -->
  <header class="glass sticky top-0 z-50 px-6 py-3.5 flex flex-wrap items-center justify-between gap-4 border-b border-gray-800">
    <div class="flex items-center space-x-3">
      <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20">
        <i class="fa-solid fa-graduation-cap text-lg"></i>
      </div>
      <div>
        <div class="flex items-center space-x-2">
          <h1 class="text-lg font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-indigo-400">UNIR Study Hub</h1>
          <span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">Multi-Titulación</span>
        </div>
        <p class="text-xs text-gray-400 font-medium">Plataforma Académica y Copiloto de Estudio</p>
      </div>
    </div>

    <!-- SELECTOR DE PROGRAMAS / TITULACIONES -->
    <div class="flex items-center bg-gray-900/90 p-1 rounded-xl border border-gray-800 space-x-1 overflow-x-auto">
      <template x-for="prog in programas" :key="prog.id">
        <button @click="cambiarPrograma(prog.id)"
                :class="programaActivoId === prog.id ? 'bg-gray-800 text-white shadow-sm border border-gray-700' : 'text-gray-400 hover:text-gray-200'"
                class="px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center space-x-2 whitespace-nowrap">
          <i :class="'fa-solid ' + prog.icono" :style="'color: ' + obtenerColorHex(prog.color)"></i>
          <span x-text="prog.titulo_corto"></span>
          <span class="text-[10px] px-1.5 py-0.2 rounded-full bg-gray-800/80 text-gray-400 border border-gray-700" x-text="prog.total_asignaturas"></span>
        </button>
      </template>
    </div>

    <!-- NAVEGACIÓN DE PESTAÑAS (DASHBOARD / ASIGNATURAS / TUTOR) -->
    <nav class="flex items-center space-x-1 bg-gray-900/80 p-1 rounded-xl border border-gray-800">
      <button @click="tab = 'dashboard'" :class="tab === 'dashboard' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-3.5 py-1.5 rounded-lg text-xs font-medium transition border border-transparent flex items-center space-x-1.5">
        <i class="fa-solid fa-chart-pie"></i><span>Dashboard</span>
      </button>
      <button @click="tab = 'asignaturas'" :class="tab === 'asignaturas' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-3.5 py-1.5 rounded-lg text-xs font-medium transition border border-transparent flex items-center space-x-1.5">
        <i class="fa-solid fa-book-bookmark"></i><span>Temarios</span>
      </button>
      <button @click="tab = 'tutor'" :class="tab === 'tutor' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-3.5 py-1.5 rounded-lg text-xs font-medium transition border border-transparent flex items-center space-x-1.5">
        <i class="fa-solid fa-robot"></i><span>Tutor NOVA</span>
      </button>
    </nav>
  </header>

  <!-- CONTENIDO PRINCIPAL -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">

    <!-- BANNER DEL PROGRAMA ACTIVO -->
    <div class="glass p-5 rounded-2xl flex flex-wrap items-center justify-between gap-4 border border-gray-800/80">
      <div class="flex items-center space-x-4">
        <div class="w-12 h-12 rounded-xl flex items-center justify-center text-2xl shadow-lg"
             :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color)">
          <i :class="'fa-solid ' + (programaActual?.icono || 'fa-graduation-cap')"></i>
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h2 class="text-xl font-bold text-white" x-text="programaActual?.nombre"></h2>
            <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold border"
                  :style="'background: ' + obtenerFondoHex(programaActual?.color) + '; color: ' + obtenerColorHex(programaActual?.color) + '; border-color: ' + obtenerColorHex(programaActual?.color) + '33'"
                  x-text="programaActual?.badge"></span>
          </div>
          <p class="text-xs text-gray-400 mt-0.5" x-text="programaActual?.descripcion"></p>
        </div>
      </div>

      <!-- FILTRO DE CURSOS (SI EL PROGRAMA TIENE MÁS DE 1 CURSO, EJ. FP DAM 1º Y 2º) -->
      <div x-show="programaActual?.cursos && programaActual?.cursos.length > 1" class="flex items-center space-x-1 bg-gray-900/90 p-1 rounded-xl border border-gray-800">
        <button @click="filtrarCurso('todos')"
                :class="cursoFiltro === 'todos' ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                class="px-3 py-1 rounded-lg text-xs font-semibold transition">
          Todo el Ciclo
        </button>
        <template x-for="c in programaActual?.cursos" :key="c.id">
          <button @click="filtrarCurso(c.id)"
                  :class="cursoFiltro === c.id ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'"
                  class="px-3 py-1 rounded-lg text-xs font-semibold transition" x-text="c.nombre">
          </button>
        </template>
      </div>
    </div>

    <!-- 1. VISTA DASHBOARD -->
    <div x-show="tab === 'dashboard'" x-transition class="space-y-6">
      
      <!-- STATS HERO CARDS ADAPTATIVAS AL PROGRAMA -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-cyan-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Asignaturas / Módulos</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="catalogoFiltrado.length">0</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 text-cyan-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-layer-group"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-cyan-400">
            <i class="fa-solid fa-check-circle mr-1.5"></i>
            <span>Itinerario activo y organizado</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-indigo-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Temarios Oficiales (PDF)</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalMaterialesPrograma">0</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-indigo-500/10 text-indigo-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-file-pdf"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-indigo-400">
            <i class="fa-solid fa-folder-open mr-1.5"></i>
            <span>Indexados sin duplicar disco</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-amber-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Videoclases / Sesiones</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalClasesPrograma">0</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-video"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-amber-400">
            <i class="fa-solid fa-file-lines mr-1.5"></i>
            <span>Con transcripciones textuales</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-emerald-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Tests y Simulacros</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="totalTestsPrograma">0</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-bullseye"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-emerald-400">
            <i class="fa-solid fa-bolt mr-1.5"></i>
            <span>Simulador de examen listo</span>
          </div>
        </div>
      </div>

      <!-- SEMÁFORO DE ASIGNATURAS DEL PROGRAMA ACTIVO -->
      <div class="glass rounded-2xl p-6">
        <div class="flex items-center justify-between mb-6">
          <div>
            <h2 class="text-lg font-bold text-white flex items-center space-x-2">
              <i class="fa-solid fa-graduation-cap text-cyan-400"></i>
              <span>Semáforo de Preparación Académica</span>
            </h2>
            <p class="text-xs text-gray-400">Haz clic en cualquier asignatura o módulo para abrir sus temas, videoclases y tests</p>
          </div>
          <span class="text-xs text-gray-400 bg-gray-900 px-3 py-1.5 rounded-xl border border-gray-800" x-text="catalogoFiltrado.length + ' materias'"></span>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
            <div @click="abrirAsignatura(asig)" class="glass-card p-5 rounded-xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/60 transition group">
              <div class="flex items-start justify-between">
                <div class="pr-2">
                  <h4 class="font-bold text-white group-hover:text-cyan-400 transition" x-text="asig.nombre"></h4>
                  <span class="text-xs text-gray-400" x-text="asig.curso"></span>
                </div>
                <span class="px-2.5 py-1 rounded-full text-[11px] font-semibold shrink-0"
                      :class="asig.progreso.total_evaluados > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-gray-800 text-gray-400'"
                      x-text="asig.progreso.total_evaluados > 0 ? asig.progreso.estado : 'Sin evaluar'">
                </span>
              </div>

              <div class="mt-4 grid grid-cols-3 gap-2 text-xs text-gray-300 pt-3 border-t border-gray-800/80">
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

    <!-- 2. VISTA EXPLORADOR DE TEMARIOS Y ASIGNATURAS -->
    <div x-show="tab === 'asignaturas'" x-transition class="space-y-6">
      
      <!-- DETALLE DE ASIGNATURA SELECCIONADA -->
      <div x-show="asigSeleccionada" class="space-y-6">
        <div class="glass p-6 rounded-2xl flex flex-wrap items-center justify-between gap-4">
          <div class="flex items-center space-x-4">
            <button @click="asigSeleccionada = null" class="w-10 h-10 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center justify-center transition">
              <i class="fa-solid fa-arrow-left"></i>
            </button>
            <div>
              <div class="flex items-center space-x-2">
                <h2 class="text-2xl font-bold text-white" x-text="asigSeleccionada?.nombre"></h2>
                <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" x-text="asigSeleccionada?.curso"></span>
              </div>
              <p class="text-xs text-gray-400">Explora temarios oficiales, lecciones magistrales y simulacros de examen</p>
            </div>
          </div>
          <div class="flex items-center space-x-2">
            <button @click="abrirChatConAsignatura(asigSeleccionada?.nombre)" class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-xs font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
              <i class="fa-solid fa-robot"></i><span>Consultar con NOVA</span>
            </button>
          </div>
        </div>

        <!-- 3 COLUMNAS: VIDEOCLASES | TEMARIO/GUÍAS | TESTS -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          <!-- COLUMNA 1: VIDEOCLASES Y TRANSCRIPCIONES -->
          <div class="glass p-5 rounded-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 class="font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-video text-amber-400"></i>
                <span>Videoclases Oficiales</span>
              </h3>
              <span class="text-xs bg-amber-500/10 text-amber-400 px-2.5 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.clases.length + ' clases'"></span>
            </div>

            <div class="space-y-2 max-h-[500px] overflow-y-auto pr-1">
              <template x-for="clase in asigSeleccionada?.clases" :key="clase.nombre">
                <div class="glass-card p-3 rounded-xl hover:border-amber-500/30 transition flex items-center justify-between">
                  <div class="overflow-hidden pr-2">
                    <p class="text-[11px] text-amber-400 font-semibold" x-text="clase.fecha"></p>
                    <h5 class="text-xs font-medium text-white truncate" x-text="clase.titulo"></h5>
                  </div>
                  <button @click="verContenidoArchivo(clase.archivo)" class="px-2.5 py-1 bg-gray-800 hover:bg-amber-500/20 hover:text-amber-300 text-gray-300 rounded-lg text-xs font-semibold transition shrink-0">
                    Leer
                  </button>
                </div>
              </template>
              <div x-show="asigSeleccionada?.clases.length === 0" class="text-center py-8 text-gray-500 text-xs">
                <i class="fa-solid fa-video-slash text-2xl mb-2 opacity-50"></i>
                <p>No hay videoclases sincronizadas para esta materia.</p>
              </div>
            </div>
          </div>

          <!-- COLUMNA 2: TEMARIO Y GUÍAS DE ESTUDIO -->
          <div class="glass p-5 rounded-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 class="font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-book-open text-cyan-400"></i>
                <span>Guías de Estudio & PDFs</span>
              </h3>
              <span class="text-xs bg-cyan-500/10 text-cyan-400 px-2.5 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.total_materiales + ' docs'"></span>
            </div>

            <div class="space-y-2 max-h-[500px] overflow-y-auto pr-1">
              <!-- GUÍAS RESUMIDAS IA -->
              <template x-for="guia in asigSeleccionada?.guias" :key="guia">
                <div class="glass-card p-3 rounded-xl hover:border-cyan-500/30 transition flex items-center justify-between">
                  <div class="overflow-hidden pr-2">
                    <p class="text-[11px] text-cyan-400 font-semibold">Guía de Alto Rendimiento</p>
                    <h5 class="text-xs font-medium text-white truncate" x-text="guia.replace('_guia.md', '').replaceAll('_', ' ')"></h5>
                  </div>
                  <button @click="verContenidoArchivo('data/procesado/guias/' + asigSeleccionada.nombre + '/' + guia)" class="px-2.5 py-1 bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 rounded-lg text-xs font-semibold transition shrink-0">
                    Estudiar
                  </button>
                </div>
              </template>

              <!-- PDFs OFICIALES -->
              <div class="pt-3 border-t border-gray-800">
                <p class="text-[11px] uppercase text-gray-400 font-bold mb-2">Temarios Oficiales</p>
                <template x-for="m in asigSeleccionada?.materiales" :key="m">
                  <div class="p-2 text-xs text-gray-300 truncate hover:text-white flex items-center space-x-2 bg-gray-900/40 rounded-lg mb-1">
                    <i class="fa-regular fa-file-pdf text-rose-400 shrink-0"></i>
                    <span class="truncate" x-text="m"></span>
                  </div>
                </template>
                <div x-show="asigSeleccionada?.materiales.length === 0" class="text-center py-6 text-gray-500 text-xs">
                  <p>Sin documentos locales todavía.</p>
                </div>
              </div>
            </div>
          </div>

          <!-- COLUMNA 3: TESTS Y EXÁMENES INTERACTIVOS -->
          <div class="glass p-5 rounded-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 class="font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-bullseye text-emerald-400"></i>
                <span>Simulacros de Examen</span>
              </h3>
              <span class="text-xs bg-emerald-500/10 text-emerald-400 px-2.5 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.tests.length + ' tests'"></span>
            </div>

            <div class="space-y-3">
              <template x-for="test in asigSeleccionada?.tests" :key="test">
                <div class="glass-card p-4 rounded-xl border-l-4 border-emerald-400 hover:bg-gray-800/50 transition">
                  <h5 class="font-bold text-white text-xs" x-text="test.replace('_test', '').replaceAll('_', ' ')"></h5>
                  <p class="text-[11px] text-gray-400 mt-1">10 preguntas tipo test con formato oficial</p>
                  <button @click="iniciarTestWeb(asigSeleccionada.nombre, test)" class="mt-3 w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-gray-900 font-bold rounded-lg text-xs transition shadow-lg shadow-emerald-500/20">
                    Comenzar Simulacro
                  </button>
                </div>
              </template>
              <div x-show="asigSeleccionada?.tests.length === 0" class="text-center py-8 text-gray-500 text-xs">
                <i class="fa-solid fa-list-check text-2xl mb-2 opacity-50"></i>
                <p>No hay tests generados para esta materia aún.</p>
              </div>
            </div>
          </div>

        </div>
      </div>

      <!-- REJILLA GENERAL SI NO HAY ASIGNATURA SELECCIONADA -->
      <div x-show="!asigSeleccionada" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <template x-for="asig in catalogoFiltrado" :key="asig.nombre">
          <div @click="abrirAsignatura(asig)" class="glass p-6 rounded-2xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/40 transition group">
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

    <!-- 3. VISTA TUTOR NOVA EN DIRECTO (ADAPTATIVO) -->
    <div x-show="tab === 'tutor'" x-transition class="glass rounded-2xl flex flex-col h-[750px] overflow-hidden">
      <!-- HEADER CHAT -->
      <div class="p-4 bg-gray-900/80 border-b border-gray-800 flex items-center justify-between">
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

        <div>
          <select x-model="chatAsignatura" class="bg-gray-800 text-gray-200 text-xs px-3 py-1.5 rounded-lg border border-gray-700 outline-none">
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

      <!-- INPUT BAR -->
      <div class="p-4 bg-gray-900/90 border-t border-gray-800 flex items-center space-x-3">
        <input type="text" x-model="chatInput" @keydown.enter="enviarMensajeChat()"
               :placeholder="'Haz cualquier pregunta técnica sobre ' + programaActual?.titulo_corto + ', examen o código...'"
               class="flex-1 bg-gray-800 text-white placeholder-gray-500 text-xs px-4 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-cyan-500">
        <button @click="enviarMensajeChat()" :disabled="chatCargando || !chatInput.trim()"
                class="px-5 py-3 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white font-semibold rounded-xl text-xs hover:shadow-lg hover:shadow-cyan-500/20 disabled:opacity-50 transition">
          <i class="fa-solid fa-paper-plane mr-1.5"></i>Enviar
        </button>
      </div>
    </div>

  </main>

  <!-- MODAL: VISOR DE GUÍAS Y TRANSCRIPCIONES -->
  <div x-show="modalVisor" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-4xl max-h-[85vh] rounded-2xl flex flex-col overflow-hidden border border-gray-700 shadow-2xl">
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <h4 class="font-bold text-white text-sm truncate" x-text="modalTitulo"></h4>
        <button @click="modalVisor = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>
      <div class="flex-1 p-6 overflow-y-auto markdown-content text-sm" x-html="marked.parse(modalContenido)"></div>
    </div>
  </div>

  <!-- MODAL: SIMULADOR DE EXAMEN WEB -->
  <div x-show="modalTest" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-3xl max-h-[90vh] rounded-2xl flex flex-col overflow-hidden border border-emerald-500/30 shadow-2xl">
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
        catalogoTotal: [],
        catalogoFiltrado: [],
        progreso: {},
        asigSeleccionada: null,
        
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

        async init() {
          await this.cargarProgramas();
          await this.cargarCatalogo();
          await this.cargarProgreso();
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

        cambiarPrograma(progId) {
          this.programaActivoId = progId;
          this.cursoFiltro = 'todos';
          this.asigSeleccionada = null;
          this.aplicarFiltroCatalogo();
          this.actualizarMensajeBienvenidaChat();
        },

        filtrarCurso(cursoId) {
          this.cursoFiltro = cursoId;
          this.aplicarFiltroCatalogo();
        },

        aplicarFiltroCatalogo() {
          let lista = this.catalogoTotal.filter(a => a.programa_id === this.programaActivoId);
          if (this.programaActivoId === 'dam' && this.cursoFiltro !== 'todos') {
            const nombreCurso = this.cursoFiltro === '2dam' ? '2º Curso' : '1º Curso';
            lista = lista.filter(a => a.curso === nombreCurso);
          }
          this.catalogoFiltrado = lista;
        },

        actualizarMensajeBienvenidaChat() {
          const p = this.programaActual;
          let saludo = '';
          if (p?.id === 'ciber') {
            saludo = '¡Hola Alex! Soy **NOVA**, tu copiloto en **Ciberseguridad y Auditorías**. ¿Qué módulo o temática (OWASP, SIEM, Forense, Ingeniería Inversa o Hacking Ético) repasamos hoy?';
          } else if (p?.id === 'grado') {
            saludo = '¡Hola Alex! Soy **NOVA**, tu mentora en **Ingeniería del Software & PFU**. Lista para resolver dudas de algoritmia, estructuras de datos o diseño de sistemas.';
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
          const map = { cyan: 'rgba(34, 211, 238, 0.1)', rose: 'rgba(251, 113, 133, 0.1)', indigo: 'rgba(129, 140, 248, 0.1)', amber: 'rgba(245, 158, 11, 0.1)' };
          return map[color] || 'rgba(56, 189, 248, 0.1)';
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
            this.modalTitulo = data.nombre;
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
