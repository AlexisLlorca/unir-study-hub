"""
Servidor Web y Dashboard Gráfico Interactivo para UNIR Study Hub.
Proporciona una interfaz gráfica moderna (HTML5, Tailwind CSS, Alpine.js)
para explorar asignaturas, leer guías de estudio, realizar tests interactivos,
ver transcripciones de videoclases y chatear con NOVA.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
    ASIGNATURAS_2DAM,
    ASIGNATURAS_1DAM,
    obtener_catalogo_academico,
    reorganizar_biblioteca_completa,
)
from src.core.progress_tracker import GestorProgreso
from src.ai.tutor import TutorIA
from src.generators.study_guide_generator import GeneradorGuiaEstudio

app = FastAPI(title="UNIR Study Hub — Copiloto Académico 2º DAM", version="2.5.0")

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

@app.get("/api/catalogo")
def api_catalogo():
    """Retorna la lista consolidada de asignaturas con sus recursos."""
    catalogo = obtener_catalogo_academico()
    resultado = []

    for asig in ASIGNATURAS_2DAM:
        if asig in catalogo:
            info = catalogo[asig]
            dir_guias = GUIAS_DIR / asig
            dir_examenes = EXAMENES_DIR / asig

            guias = [f.name for f in dir_guias.glob("*.md")] if dir_guias.exists() else []
            tests = [f.stem for f in dir_examenes.glob("*.json")] if dir_examenes.exists() else []

            resumen_progreso = tracker.obtener_resumen_asignatura(asig)

            resultado.append({
                "nombre": asig,
                "curso": "2º DAM",
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


@app.post("/api/chat")
def api_chat(msg: ChatMessage):
    """Responde preguntas mediante NOVA."""
    prompt = f"[Asignatura: {msg.asignatura}]\nAlumno: {msg.mensaje}\nNOVA:" if msg.asignatura else msg.mensaje
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
  <title>UNIR Study Hub — 2º DAM & PFU</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #0b0f19; color: #f3f4f6; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgba(17, 24, 39, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glass-card { background: rgba(31, 41, 55, 0.5); backdrop-filter: blur(8px); border: 1px solid rgba(255, 255, 255, 0.05); }
    .markdown-content h1 { font-size: 1.6rem; font-weight: 700; color: #38bdf8; margin-top: 1.2rem; margin-bottom: 0.6rem; }
    .markdown-content h2 { font-size: 1.3rem; font-weight: 600; color: #818cf8; margin-top: 1rem; margin-bottom: 0.5rem; }
    .markdown-content p { margin-bottom: 0.8rem; line-height: 1.6; }
    .markdown-content pre { background: #111827; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; margin-bottom: 1rem; border: 1px solid #374151; }
    .markdown-content code { background: #1f2937; padding: 0.15rem 0.35rem; border-radius: 0.25rem; font-size: 0.875rem; color: #38bdf8; }
    .markdown-content table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
    .markdown-content th, .markdown-content td { border: 1px solid #374151; padding: 0.5rem 0.75rem; text-align: left; }
    .markdown-content th { background: #1f2937; color: #e5e7eb; }
  </style>
</head>
<body x-data="studyHubApp()" x-init="init()" class="min-h-screen flex flex-col">

  <!-- TOPBAR -->
  <header class="glass sticky top-0 z-50 px-6 py-4 flex items-center justify-between border-b border-gray-800">
    <div class="flex items-center space-x-3">
      <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white shadow-lg shadow-cyan-500/20">
        <i class="fa-solid fa-graduation-cap text-lg"></i>
      </div>
      <div>
        <h1 class="text-lg font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 to-indigo-400">UNIR Study Hub</h1>
        <p class="text-xs text-gray-400 font-medium">2º DAM & PFU Ingeniería del Software</p>
      </div>
    </div>

    <!-- NAVEGACIÓN PRINCIPAL -->
    <nav class="flex items-center space-x-1 bg-gray-900/80 p-1.5 rounded-xl border border-gray-800">
      <button @click="tab = 'dashboard'" :class="tab === 'dashboard' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-4 py-1.5 rounded-lg text-sm font-medium transition border border-transparent flex items-center space-x-2">
        <i class="fa-solid fa-chart-pie"></i><span>Dashboard</span>
      </button>
      <button @click="tab = 'asignaturas'" :class="tab === 'asignaturas' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-4 py-1.5 rounded-lg text-sm font-medium transition border border-transparent flex items-center space-x-2">
        <i class="fa-solid fa-book-bookmark"></i><span>Asignaturas</span>
      </button>
      <button @click="tab = 'tutor'" :class="tab === 'tutor' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' : 'text-gray-400 hover:text-gray-200'" class="px-4 py-1.5 rounded-lg text-sm font-medium transition border border-transparent flex items-center space-x-2">
        <i class="fa-solid fa-robot"></i><span>Tutor NOVA</span>
      </button>
    </nav>

    <!-- ESTADO / ACCIONES -->
    <div class="flex items-center space-x-4">
      <div class="hidden sm:flex items-center space-x-2 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-3 py-1 rounded-full text-xs font-semibold">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span>49 Videoclases Sincronizadas</span>
      </div>
    </div>
  </header>

  <!-- CONTENIDO PRINCIPAL -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-6">

    <!-- 1. VISTA DASHBOARD -->
    <div x-show="tab === 'dashboard'" x-transition class="space-y-6">
      
      <!-- STATS HERO CARDS -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-cyan-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Asignaturas 2º DAM</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="catalogo.length">12</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-cyan-500/10 text-cyan-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-layer-group"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-cyan-400">
            <i class="fa-solid fa-check-circle mr-1.5"></i>
            <span>Todas normalizadas y limpias</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-indigo-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Videoclases Oficiales</p>
              <h3 class="text-3xl font-extrabold text-white mt-1">49</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-indigo-500/10 text-indigo-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-video"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-indigo-400">
            <i class="fa-solid fa-file-lines mr-1.5"></i>
            <span>Transcripciones palabra por palabra</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-emerald-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Nota Media Tests</p>
              <h3 class="text-3xl font-extrabold text-white mt-1" x-text="(progreso.media_global || 8.5) + ' / 10'">8.5 / 10</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-trophy"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-emerald-400">
            <i class="fa-solid fa-arrow-trend-up mr-1.5"></i>
            <span>Rendimiento de examen alto</span>
          </div>
        </div>

        <div class="glass-card p-5 rounded-2xl relative overflow-hidden group hover:border-amber-500/30 transition">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs uppercase tracking-wider text-gray-400 font-semibold">Guías y Tests IA</p>
              <h3 class="text-3xl font-extrabold text-white mt-1">Activo</h3>
            </div>
            <div class="w-12 h-12 rounded-xl bg-amber-500/10 text-amber-400 flex items-center justify-center text-xl">
              <i class="fa-solid fa-wand-magic-sparkles"></i>
            </div>
          </div>
          <div class="mt-4 flex items-center text-xs text-amber-400">
            <i class="fa-solid fa-bolt mr-1.5"></i>
            <span>Generador de estudio disponible</span>
          </div>
        </div>
      </div>

      <!-- SEMÁFORO DE ASIGNATURAS -->
      <div class="glass rounded-2xl p-6">
        <div class="flex items-center justify-between mb-6">
          <div>
            <h2 class="text-lg font-bold text-white">Semáforo de Preparación por Asignatura</h2>
            <p class="text-xs text-gray-400">Haz clic en cualquier asignatura para abrir sus temas, videoclases y tests</p>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <template x-for="asig in catalogo" :key="asig.nombre">
            <div @click="abrirAsignatura(asig)" class="glass-card p-5 rounded-xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/60 transition group">
              <div class="flex items-start justify-between">
                <div>
                  <h4 class="font-bold text-white group-hover:text-cyan-400 transition" x-text="asig.nombre"></h4>
                  <span class="text-xs text-gray-400" x-text="asig.curso"></span>
                </div>
                <span class="px-2.5 py-1 rounded-full text-xs font-semibold"
                      :class="asig.progreso.total_evaluados > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-gray-800 text-gray-400'"
                      x-text="asig.progreso.total_evaluados > 0 ? asig.progreso.estado : 'Pendiente'">
                </span>
              </div>

              <div class="mt-4 grid grid-cols-2 gap-2 text-xs text-gray-300">
                <div class="flex items-center space-x-1.5">
                  <i class="fa-solid fa-file-pdf text-rose-400"></i>
                  <span x-text="asig.total_materiales + ' temas/docs'"></span>
                </div>
                <div class="flex items-center space-x-1.5">
                  <i class="fa-solid fa-video text-amber-400"></i>
                  <span x-text="asig.total_clases + ' videoclases'"></span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 2. VISTA EXPLORADOR DE ASIGNATURAS -->
    <div x-show="tab === 'asignaturas'" x-transition class="space-y-6">
      
      <!-- DETALLE DE ASIGNATURA SELECCIONADA -->
      <div x-show="asigSeleccionada" class="space-y-6">
        <div class="glass p-6 rounded-2xl flex items-center justify-between">
          <div class="flex items-center space-x-4">
            <button @click="asigSeleccionada = null" class="w-10 h-10 rounded-xl bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center justify-center transition">
              <i class="fa-solid fa-arrow-left"></i>
            </button>
            <div>
              <h2 class="text-2xl font-bold text-white" x-text="asigSeleccionada?.nombre"></h2>
              <p class="text-xs text-gray-400">Explora temarios oficiales, lecciones magistrales y realiza exámenes tipo test</p>
            </div>
          </div>
          <div class="flex items-center space-x-2">
            <button @click="abrirChatConAsignatura(asigSeleccionada?.nombre)" class="px-4 py-2 bg-gradient-to-r from-cyan-500 to-indigo-600 rounded-xl text-sm font-semibold hover:shadow-lg hover:shadow-cyan-500/20 transition flex items-center space-x-2">
              <i class="fa-solid fa-robot"></i><span>Consultar con NOVA</span>
            </button>
          </div>
        </div>

        <!-- PESTAÑAS DENTRO DE LA ASIGNATURA -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          <!-- COLUMNA 1: VIDEOCLASES Y TRANSCRIPCIONES -->
          <div class="glass p-5 rounded-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 class="font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-video text-amber-400"></i>
                <span>Videoclases Magistrales</span>
              </h3>
              <span class="text-xs bg-amber-500/10 text-amber-400 px-2.5 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.clases.length + ' clases'"></span>
            </div>

            <div class="space-y-2 max-h-[500px] overflow-y-auto pr-1">
              <template x-for="clase in asigSeleccionada?.clases" :key="clase.nombre">
                <div class="glass-card p-3.5 rounded-xl hover:border-amber-500/30 transition flex items-center justify-between">
                  <div class="overflow-hidden pr-2">
                    <p class="text-xs text-amber-400 font-semibold" x-text="clase.fecha"></p>
                    <h5 class="text-sm font-medium text-white truncate" x-text="clase.titulo"></h5>
                  </div>
                  <button @click="verContenidoArchivo(clase.archivo)" class="px-3 py-1.5 bg-gray-800 hover:bg-amber-500/20 hover:text-amber-300 text-gray-300 rounded-lg text-xs font-semibold transition shrink-0">
                    Leer
                  </button>
                </div>
              </template>
            </div>
          </div>

          <!-- COLUMNA 2: TEMARIO Y GUÍAS DE ESTUDIO -->
          <div class="glass p-5 rounded-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-gray-800 pb-3">
              <h3 class="font-bold text-white flex items-center space-x-2">
                <i class="fa-solid fa-book-open text-cyan-400"></i>
                <span>Guías de Estudio y Temas</span>
              </h3>
              <span class="text-xs bg-cyan-500/10 text-cyan-400 px-2.5 py-0.5 rounded-full font-semibold" x-text="asigSeleccionada?.guias.length + ' listas'"></span>
            </div>

            <div class="space-y-2 max-h-[500px] overflow-y-auto pr-1">
              <template x-for="guia in asigSeleccionada?.guias" :key="guia">
                <div class="glass-card p-3.5 rounded-xl hover:border-cyan-500/30 transition flex items-center justify-between">
                  <div class="overflow-hidden pr-2">
                    <p class="text-xs text-cyan-400 font-semibold">Guía de Alto Rendimiento</p>
                    <h5 class="text-sm font-medium text-white truncate" x-text="guia.replace('_guia.md', '').replaceAll('_', ' ')"></h5>
                  </div>
                  <button @click="verContenidoArchivo('data/procesado/guias/' + asigSeleccionada.nombre + '/' + guia)" class="px-3 py-1.5 bg-cyan-500/20 text-cyan-300 hover:bg-cyan-500/30 rounded-lg text-xs font-semibold transition shrink-0">
                    Estudiar
                  </button>
                </div>
              </template>

              <!-- PDFs OFICIALES -->
              <div class="pt-4 border-t border-gray-800">
                <p class="text-xs uppercase text-gray-400 font-bold mb-2">Temarios Oficiales (PDF)</p>
                <template x-for="m in asigSeleccionada?.materiales.slice(0, 8)" :key="m">
                  <div class="p-2 text-xs text-gray-300 truncate hover:text-white flex items-center space-x-2">
                    <i class="fa-regular fa-file-pdf text-rose-400"></i>
                    <span x-text="m"></span>
                  </div>
                </template>
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
                  <h5 class="font-bold text-white text-sm" x-text="test.replace('_test', '').replaceAll('_', ' ')"></h5>
                  <p class="text-xs text-gray-400 mt-1">10 preguntas tipo test con formato oficial UNIR</p>
                  <button @click="iniciarTestWeb(asigSeleccionada.nombre, test)" class="mt-3 w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-gray-900 font-bold rounded-lg text-xs transition shadow-lg shadow-emerald-500/20">
                    Comenzar Simulacro
                  </button>
                </div>
              </template>
            </div>
          </div>

        </div>
      </div>

      <!-- SI NO HAY ASIGNATURA SELECCIONADA, MOSTRAR REJILLA -->
      <div x-show="!asigSeleccionada" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <template x-for="asig in catalogo" :key="asig.nombre">
          <div @click="abrirAsignatura(asig)" class="glass p-6 rounded-2xl cursor-pointer hover:border-cyan-500/40 hover:bg-gray-800/40 transition group">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-cyan-400 bg-cyan-500/10 px-2.5 py-1 rounded-full" x-text="asig.curso"></span>
              <i class="fa-solid fa-chevron-right text-gray-500 group-hover:text-cyan-400 transition"></i>
            </div>
            <h3 class="text-lg font-bold text-white mt-3 group-hover:text-cyan-300 transition" x-text="asig.nombre"></h3>
            
            <div class="mt-5 pt-4 border-t border-gray-800 flex items-center justify-between text-xs text-gray-400">
              <span x-text="asig.total_materiales + ' materiales'"></span>
              <span x-text="asig.total_clases + ' clases grabadas'"></span>
              <span class="text-emerald-400 font-semibold" x-text="asig.tests.length + ' tests listos'"></span>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 3. VISTA TUTOR NOVA EN DIRECTO -->
    <div x-show="tab === 'tutor'" x-transition class="glass rounded-2xl flex flex-col h-[750px] overflow-hidden">
      <!-- HEADER CHAT -->
      <div class="p-4 bg-gray-900/60 border-b border-gray-800 flex items-center justify-between">
        <div class="flex items-center space-x-3">
          <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center text-white">
            <i class="fa-solid fa-robot"></i>
          </div>
          <div>
            <h3 class="font-bold text-white">NOVA — Copiloto Académico</h3>
            <p class="text-xs text-emerald-400 flex items-center">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5"></span>
              Gemini 3 Flash Conectado
            </p>
          </div>
        </div>

        <div>
          <select x-model="chatAsignatura" class="bg-gray-800 text-gray-300 text-xs px-3 py-1.5 rounded-lg border border-gray-700 outline-none">
            <option value="">General (Todas las asignaturas)</option>
            <template x-for="asig in catalogo" :key="asig.nombre">
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
            <i class="fa-solid fa-spinner fa-spin"></i><span>NOVA está razonando la respuesta...</span>
          </div>
        </div>
      </div>

      <!-- INPUT BAR -->
      <div class="p-4 bg-gray-900/80 border-t border-gray-800 flex items-center space-x-3">
        <input type="text" x-model="chatInput" @keydown.enter="enviarMensajeChat()" placeholder="Haz cualquier pregunta técnica sobre 2º DAM, examen o código..." class="flex-1 bg-gray-800 text-white placeholder-gray-500 text-sm px-4 py-3 rounded-xl border border-gray-700 focus:outline-none focus:border-cyan-500">
        <button @click="enviarMensajeChat()" :disabled="chatCargando || !chatInput.trim()" class="px-5 py-3 bg-gradient-to-r from-cyan-500 to-indigo-600 text-white font-semibold rounded-xl text-sm hover:shadow-lg hover:shadow-cyan-500/20 disabled:opacity-50 transition">
          <i class="fa-solid fa-paper-plane mr-1.5"></i>Enviar
        </button>
      </div>
    </div>

  </main>

  <!-- MODAL: VISOR DE GUÍAS Y TRANSCRIPCIONES -->
  <div x-show="modalVisor" x-transition class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
    <div class="glass w-full max-w-4xl max-h-[85vh] rounded-2xl flex flex-col overflow-hidden border border-gray-700 shadow-2xl">
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <h4 class="font-bold text-white text-base truncate" x-text="modalTitulo"></h4>
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
      <!-- HEADER TEST -->
      <div class="p-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <div>
          <h4 class="font-bold text-white text-base" x-text="'Simulacro: ' + testActivoAsig"></h4>
          <p class="text-xs text-gray-400" x-text="'Pregunta ' + (testIndice + 1) + ' de ' + (testPreguntas.length)"></p>
        </div>
        <button @click="modalTest = false" class="text-gray-400 hover:text-white p-2 rounded-lg">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- CONTENIDO PREGUNTA -->
      <div class="flex-1 p-6 overflow-y-auto space-y-5" x-show="!testFinalizado && testPreguntas.length > 0">
        <h3 class="text-lg font-bold text-white" x-text="testPreguntas[testIndice]?.enunciado"></h3>

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

      <!-- RESULTADO FINAL DEL TEST -->
      <div class="flex-1 p-8 text-center space-y-4" x-show="testFinalizado">
        <div class="w-20 h-20 rounded-full mx-auto flex items-center justify-center text-3xl"
             :class="testNota >= 5 ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-rose-500/20 text-rose-400 border border-rose-500/40'">
          <i :class="testNota >= 5 ? 'fa-solid fa-check' : 'fa-solid fa-xmark'"></i>
        </div>
        <h3 class="text-2xl font-extrabold text-white" x-text="testNota >= 5 ? '¡Enhorabuena, has aprobado!' : 'Necesitas repasar este tema'"></h3>
        <p class="text-gray-400 text-sm">Tu puntuación oficial en el simulacro:</p>
        <p class="text-5xl font-black" :class="testNota >= 5 ? 'text-emerald-400' : 'text-rose-400'" x-text="testNota.toFixed(2) + ' / 10'"></p>
        <p class="text-xs text-gray-500" x-text="testAciertos + ' aciertos de ' + testPreguntas.length + ' preguntas.'"></p>
        <button @click="modalTest = false" class="px-6 py-2.5 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-sm font-semibold transition">
          Cerrar y Guardar Nota
        </button>
      </div>

      <!-- FOOTER TEST NAVEGACIÓN -->
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
        catalogo: [],
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
        chatHistorial: [
          { rol: 'model', texto: '¡Hola Alex! Soy **NOVA**, tu copiloto de IA para 2º DAM. ¿Qué duda de examen o de código quieres que resolvamos hoy?' }
        ],

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

        async init() {
          await this.cargarCatalogo();
          await this.cargarProgreso();
        },

        async cargarCatalogo() {
          try {
            const res = await fetch('/api/catalogo');
            this.catalogo = await res.json();
          } catch(e) { console.error(e); }
        },

        async cargarProgreso() {
          try {
            const res = await fetch('/api/progreso');
            this.progreso = await res.json();
          } catch(e) { console.error(e); }
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

          // Guardar resultado en el backend
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
              body: JSON.stringify({ mensaje: texto, asignatura: this.chatAsignatura })
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
    console.print(f"\n[bold green]🚀 Iniciando Interfaz Gráfica Web de UNIR Study Hub...[/bold green]")
    console.print(f"[bold cyan]👉 Abre en tu navegador:[/bold cyan] [bold white underline]http://{host}:{port}[/bold white underline]\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    iniciar_servidor_web()
