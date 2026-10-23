"""
Sincronizador de Agenda, PACs y Calendario Académico de Canvas para UNIR Study Hub.
Extrae fechas límite de entrega, actividades individuales y grupales, y próximas clases en directo.
Genera alertas de plazos con cuenta atrás para el Dashboard de estudio.
"""

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from rich.table import Table

from src.core.config import (
    console,
    BASE_DIR,
    SESSION_STATE_FILE,
    UNIR_CAMPUS_URL,
    PROCESADO_DIR,
    ensure_directories,
)
from src.core.academic_structure import normalizar_nombre_asignatura

AGENDA_CACHE_FILE = PROCESADO_DIR / "agenda_cache.json"
AGENDA_BACKUP_FILE = BASE_DIR / "data" / "agenda_backup.json"


def calcular_dias_restantes(fecha_iso: Optional[str]) -> tuple[int, str]:
    """Calcula días restantes hasta la fecha límite y una etiqueta legible."""
    if not fecha_iso:
        return 999, "Sin fecha límite"
    try:
        # Parse ISO fecha (ej: 2026-10-12T21:59:59Z)
        limite = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
        ahora = datetime.now(timezone.utc)
        diff = limite - ahora
        dias = diff.days
        horas = diff.seconds // 3600

        if diff.total_seconds() < 0:
            return -1, "Vencida"
        elif dias == 0:
            return 0, f"¡Hoy! (en {horas}h)"
        elif dias == 1:
            return 1, "Mañana"
        elif dias <= 7:
            return dias, f"En {dias} días"
        else:
            return dias, f"En {dias} días ({limite.strftime('%d/%m')})"
    except Exception:
        return 999, "Fecha no disponible"


def sincronizar_agenda_canvas() -> List[Dict[str, Any]]:
    """
    Se conecta a Canvas vía Playwright y extrae los eventos, tareas y PACs del usuario.
    Guarda el resultado en cache local para acceso ultrarrápido desde el servidor web.
    """
    ensure_directories()
    eventos_limpios = []

    if not SESSION_STATE_FILE.exists():
        console.print("[yellow]⚠ No hay sesión de Canvas activa para sincronizar la agenda.[/yellow]")
        return cargar_agenda_cache()

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(UNIR_CAMPUS_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1200)

            if "login" in page.url.lower():
                console.print("[yellow]⚠ La sesión de Canvas ha caducado.[/yellow]")
                browser.close()
                return cargar_agenda_cache()

            # Consultar upcoming_events y calendario
            datos_raw = page.evaluate("""async () => {
                let items = [];
                try {
                    const res = await fetch('/api/v1/users/self/upcoming_events');
                    if (res.ok) {
                        const data = await res.json();
                        if (Array.isArray(data)) items = items.concat(data);
                    }
                } catch(e) {}

                try {
                    // Consultar calendario de próximos 45 días
                    const hoy = new Date().toISOString().split('T')[0];
                    const en45 = new Date(Date.now() + 45*86400000).toISOString().split('T')[0];
                    const resCal = await fetch(`/api/v1/calendar_events?start_date=${hoy}&end_date=${en45}&per_page=50`);
                    if (resCal.ok) {
                        const dataCal = await resCal.json();
                        if (Array.isArray(dataCal)) items = items.concat(dataCal);
                    }
                } catch(e) {}

                return items;
            }""")
            browser.close()

            # Procesar y desduplicar eventos
            vistos = set()
            for ev in datos_raw:
                ev_id = str(ev.get("id"))
                if ev_id in vistos:
                    continue
                vistos.add(ev_id)

                tipo = "tarea" if (ev.get("type") == "assignment" or "assignment" in ev) else "clase_directo"
                asig_raw = ev.get("context_name") or ""
                asignatura = normalizar_nombre_asignatura(asig_raw)

                asig_obj = ev.get("assignment", {})
                titulo = asig_obj.get("name") or ev.get("title") or "Evento académico"
                fecha_iso = asig_obj.get("due_at") or ev.get("start_at") or ev.get("end_at")
                url_html = ev.get("html_url") or asig_obj.get("html_url") or ""
                puntos = asig_obj.get("points_possible")

                desc_html = asig_obj.get("description") or ev.get("description") or ""
                desc_texto = re.sub(r"<[^>]+>", " ", desc_html).strip()
                desc_resumida = (desc_texto[:180] + "...") if len(desc_texto) > 180 else desc_texto

                dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso)

                if dias_restantes < 0:
                    urgencia = "vencida"
                elif dias_restantes <= 2:
                    urgencia = "critica"
                elif dias_restantes <= 7:
                    urgencia = "alta"
                elif dias_restantes <= 15:
                    urgencia = "media"
                else:
                    urgencia = "baja"

                eventos_limpios.append({
                    "id": ev_id,
                    "tipo": tipo,
                    "titulo": titulo,
                    "asignatura": asignatura,
                    "fecha_iso": fecha_iso,
                    "dias_restantes": dias_restantes,
                    "plazo_texto": plazo_texto,
                    "urgencia": urgencia,
                    "puntos": puntos,
                    "url": url_html,
                    "descripcion": desc_resumida,
                })

            # Ordenar por fecha más próxima (las más urgentes primero)
            eventos_limpios.sort(key=lambda x: (x["dias_restantes"] if x["dias_restantes"] >= 0 else 9999))

            # Guardar en cache y en backup para la nube
            raw_json = json.dumps(eventos_limpios, indent=2, ensure_ascii=False)
            AGENDA_CACHE_FILE.write_text(raw_json, encoding="utf-8")
            try:
                AGENDA_BACKUP_FILE.write_text(raw_json, encoding="utf-8")
            except Exception:
                pass
            console.print(f"[bold green]✔ Sincronizados {len(eventos_limpios)} eventos y entregas de Canvas.[/bold green]")
            return eventos_limpios

    except Exception as e:
        console.print(f"[red]Error al sincronizar agenda de Canvas: {e}[/red]")
        return cargar_agenda_cache()


def cargar_agenda_cache() -> List[Dict[str, Any]]:
    """Carga los eventos cacheados y recalcula dinámicamente los días restantes y la urgencia."""
    items = []
    archivo_origen = None
    if AGENDA_CACHE_FILE.exists() and AGENDA_CACHE_FILE.stat().st_size > 10:
        archivo_origen = AGENDA_CACHE_FILE
    elif AGENDA_BACKUP_FILE.exists() and AGENDA_BACKUP_FILE.stat().st_size > 10:
        archivo_origen = AGENDA_BACKUP_FILE

    if archivo_origen:
        try:
            items = json.loads(archivo_origen.read_text(encoding="utf-8"))
        except Exception:
            items = []

    if items:
        actualizados = []
        for ev in items:
            fecha_iso = ev.get("fecha_iso")
            dias_restantes, plazo_texto = calcular_dias_restantes(fecha_iso)
            # Omitir eventos finalizados hace más de 1 día para mantener la agenda limpia
            if dias_restantes < -1:
                continue

            if dias_restantes < 0:
                urgencia = "vencida"
            elif dias_restantes <= 2:
                urgencia = "critica"
            elif dias_restantes <= 7:
                urgencia = "alta"
            elif dias_restantes <= 15:
                urgencia = "media"
            else:
                urgencia = "baja"

            ev["dias_restantes"] = dias_restantes
            ev["plazo_texto"] = plazo_texto
            ev["urgencia"] = urgencia
            actualizados.append(ev)

        actualizados.sort(key=lambda x: (x["dias_restantes"] if x["dias_restantes"] >= 0 else 9999))
        return actualizados

    # Eventos predeterminados del calendario FP DAM UNIR
    eventos_base = [
        {
            "id": "anteproyecto_dam",
            "tipo": "tarea",
            "titulo": "Entrega de Anteproyecto - Proyecto Intermodular",
            "asignatura": "Proyecto Intermodular DAM",
            "fecha_iso": "2026-10-12T21:59:59Z",
            "dias_restantes": 2,
            "plazo_texto": "En 2 días (12 de Octubre)",
            "urgencia": "alta",
            "puntos": 10,
            "url": "https://campusfp.unir.net",
            "descripcion": "Entrega de la memoria inicial del equipo y planteamiento tecnológico del proyecto."
        },
        {
            "id": "act1_psp",
            "tipo": "tarea",
            "titulo": "Actividad 1 Individual - Hilos y Procesos",
            "asignatura": "Programación de Servicios y Procesos",
            "fecha_iso": "2026-10-12T21:59:59Z",
            "dias_restantes": 2,
            "plazo_texto": "En 2 días (12 de Octubre)",
            "urgencia": "alta",
            "puntos": 3,
            "url": "https://campusfp.unir.net",
            "descripcion": "Implementación práctica de concurrencia y gestión de subprocesos en Java."
        },
        {
            "id": "clase_digitalizacion",
            "tipo": "clase_directo",
            "titulo": "Clase en Directo: Digitalización Aplicada (David Cantos)",
            "asignatura": "Digitalización Aplicada",
            "fecha_iso": "2026-10-13T15:00:00Z",
            "dias_restantes": 3,
            "plazo_texto": "En 3 días (13 de Octubre a las 17:00)",
            "urgencia": "media",
            "puntos": None,
            "url": "https://campusfp.unir.net",
            "descripcion": "Sesión presencial interactiva con resolución de casos de digitalización empresarial."
        }
    ]
    return eventos_base
