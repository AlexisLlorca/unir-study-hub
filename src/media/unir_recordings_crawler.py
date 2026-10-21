"""
Extractor y Sincronizador Automático de Clases Grabadas de la UNIR.
Se conecta a la herramienta LTI de Videoclases (pv.lti.unir.net) de cada asignatura,
recupera el catálogo de todas las clases grabadas, descarga las transcripciones
oficiales completas (.vtt) y genera los enlaces de descarga directa de vídeo MP4.
"""

import sys
import re
import json
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from playwright.sync_api import sync_playwright
from rich.table import Table
from rich.panel import Panel

from src.core.config import (
    console,
    SESSION_STATE_FILE,
    CLASES_GRABADAS_DIR,
    TRANSCRIPCIONES_DIR,
    UNIR_CAMPUS_URL,
    ensure_directories,
)
from src.ai.tutor import TutorIA


def limpiar_vtt_a_markdown(contenido_vtt: str, nombre_clase: str, fecha: str) -> str:
    """Convierte un archivo de subtítulos WebVTT en una transcripción legible de clase."""
    lineas = contenido_vtt.splitlines()
    bloques_texto = []
    ultimo_hablante = ""

    for linea in lineas:
        linea = linea.strip()
        if not linea or linea == "WEBVTT" or "-->" in linea or linea.isdigit():
            continue

        # Detectar "Nombre: Texto"
        match_hablante = re.match(r"^([^:]+):\s*(.*)", linea)
        if match_hablante:
            hablante = match_hablante.group(1).strip()
            texto = match_hablante.group(2).strip()

            if hablante != ultimo_hablante:
                bloques_texto.append(f"\n\n**🗣️ {hablante}:** {texto}")
                ultimo_hablante = hablante
            else:
                bloques_texto.append(f" {texto}")
        else:
            bloques_texto.append(f" {linea}")

    md = [
        f"# 🎙️ TRANSCRIPCIÓN OFICIAL DE CLASE: {nombre_clase}",
        f"> **Fecha de la sesión:** {fecha} | **Fuente:** Proeduca / UNIR LTI\n",
        "---\n",
        "".join(bloques_texto).strip(),
    ]
    return "\n".join(md)


class UnirRecordingsCrawler:
    """Descubre y sincroniza todas las clases grabadas del campus virtual de UNIR."""

    def __init__(self, tutor_ia: Optional[TutorIA] = None):
        self.tutor = tutor_ia or TutorIA()
        ensure_directories()

    def escanear_clases_de_curso(self, course_id: int, nombre_asig: str, portal_url: str = UNIR_CAMPUS_URL) -> List[Dict[str, Any]]:
        """
        Entra en un curso de Canvas, localiza la herramienta 'Clases en directo' (LTI),
        e intercepta la lista completa de grabaciones disponibles con sus URLs.
        """
        clases_encontradas = []
        base_url = portal_url.rstrip("/")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--headless=new", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()

            def manejar_respuesta(resp):
                if "api/v1/recordings/?classroomId" in resp.url and resp.status == 200:
                    try:
                        data = resp.json()
                        if isinstance(data, list):
                            for r in data:
                                clases_encontradas.append(r)
                    except Exception:
                        pass

            page.on("response", manejar_respuesta)

            try:
                page.goto(f"{base_url}/courses/{course_id}", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1000)

                tabs = page.evaluate("""async (cId) => {
                    try {
                        const res = await fetch(`/api/v1/courses/${cId}/tabs`);
                        if (res.ok) return await res.json();
                    } catch(e) {}
                    return [];
                }""", course_id)

                url_clases = None
                for t in tabs:
                    if "clases en directo" in t.get("label", "").lower() or t.get("id") == "context_external_tool_366":
                        url_clases = t.get("full_url")
                        break

                if not url_clases:
                    url_clases = f"{base_url}/courses/{course_id}/external_tools/366"

                page.goto(url_clases, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(4500)

            except Exception as e:
                console.print(f"[dim]Error escaneando clases del curso {nombre_asig}: {e}[/dim]")
            finally:
                browser.close()

        grabaciones_unicas = {}
        for c in clases_encontradas:
            rec_id = c.get("id")
            if rec_id and rec_id not in grabaciones_unicas:
                grabaciones_unicas[rec_id] = c

        return list(grabaciones_unicas.values())

    def _guardar_grabaciones_asignatura(self, c_name_limpio: str, clases: List[Dict[str, Any]], table: Table) -> int:
        """Guarda metadatos JSON y descarga transcripciones VTT de una lista de grabaciones."""
        if not clases:
            return 0

        nuevas = 0
        dir_trans = TRANSCRIPCIONES_DIR / c_name_limpio
        dir_trans.mkdir(parents=True, exist_ok=True)

        dir_clases = CLASES_GRABADAS_DIR / c_name_limpio
        dir_clases.mkdir(parents=True, exist_ok=True)

        for rec in clases:
            rec_id = rec.get("id")
            nombre_sesion = rec.get("name") or rec.get("sessionName") or f"Clase_{rec_id}"
            nombre_seguro = re.sub(r'[\\/*?:"<>|]', "", nombre_sesion).replace(" ", "_")
            fecha = rec.get("startTime", "")[:10] or "2026"
            captions_url = rec.get("captionsUrl")

            estado_trans = "No disponible"

            archivo_info = dir_clases / f"{fecha}_{nombre_seguro}_info.json"
            if archivo_info.exists():
                try:
                    with open(archivo_info, "r", encoding="utf-8") as f_prev:
                        data_prev = json.load(f_prev)
                        if data_prev.get("id") and str(data_prev.get("id")) != str(rec_id):
                            nombre_seguro = f"{nombre_seguro}_{rec_id}"
                            archivo_info = dir_clases / f"{fecha}_{nombre_seguro}_info.json"
                except Exception:
                    pass

            if not archivo_info.exists():
                with open(archivo_info, "w", encoding="utf-8") as f_info:
                    json.dump(rec, f_info, indent=2, ensure_ascii=False)
                if not captions_url:
                    nuevas += 1

            if captions_url:
                archivo_md = dir_trans / f"{fecha}_{nombre_seguro}.md"
                if not archivo_md.exists():
                    try:
                        r_vtt = requests.get(captions_url, timeout=15)
                        if r_vtt.status_code == 200:
                            texto_md = limpiar_vtt_a_markdown(r_vtt.text, nombre_sesion, fecha)
                            with open(archivo_md, "w", encoding="utf-8") as f_out:
                                f_out.write(texto_md)
                            nuevas += 1
                            estado_trans = "✔ Descargada"
                    except Exception:
                        estado_trans = "Error"
                else:
                    estado_trans = "Ya existente"

            table.add_row(c_name_limpio, fecha, nombre_sesion[:40], estado_trans)

        return nuevas

    def sincronizar_todas_las_clases(self) -> int:
        """
        Recorre todos los cursos matriculados en Canvas (DAM, Bootcamp) y Moodle (PFU, Python, AWS, AZ-900),
        descarga las transcripciones (.vtt) y catálogos de vídeos de cada clase grabada.
        """
        console.print(Panel.fit(
            "[bold cyan]🎙️ ESCANEANDO Y SINCRONIZANDO CLASES GRABADAS DE LA UNIR[/bold cyan]\n"
            "[dim]Detectando lecciones magistrales en Canvas FP, Canvas Universidad y Moodle UNIR-ESIT (PFU)...[/dim]",
            border_style="cyan"
        ))

        from src.core.academic_structure import normalizar_nombre_asignatura

        total_transcripciones_guardadas = 0
        table = Table(title="📹 Clases Grabadas Sincronizadas", border_style="cyan")
        table.add_column("Asignatura", style="cyan")
        table.add_column("Fecha", style="green")
        table.add_column("Nombre de la Sesión", style="white")
        table.add_column("Transcripción VTT", style="yellow")

        portales_canvas = [
            UNIR_CAMPUS_URL.rstrip("/"),
            "https://micampus.unir.net",
        ]

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--headless=new", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()

            clases_actuales: List[Dict[str, Any]] = []

            def manejar_respuesta(resp):
                if "api/v1/recordings/?classroomId" in resp.url and resp.status == 200:
                    try:
                        data = resp.json()
                        if isinstance(data, list):
                            for r in data:
                                clases_actuales.append(r)
                    except Exception:
                        pass

            page.on("response", manejar_respuesta)

            try:
                # 1. Escanear portales Canvas (FP DAM + Bootcamp Universidad)
                for portal_url in portales_canvas:
                    try:
                        page.goto(portal_url, wait_until="domcontentloaded", timeout=25000)
                        if "login" in page.url.lower():
                            continue
                        cursos = page.evaluate("""async () => {
                            try {
                                let res = await fetch('/api/v1/courses?per_page=100&include[]=concluded&include[]=term&state[]=available&state[]=completed');
                                if (res.ok) {
                                    let data = await res.json();
                                    if (Array.isArray(data) && data.length > 0) return data;
                                }
                            } catch(e) {}
                            return [];
                        }""")
                        for c in (cursos or []):
                            c_id = c.get("id")
                            c_name = c.get("name", f"Curso_{c_id}")
                            c_name_limpio = normalizar_nombre_asignatura(c_name)
                            if c_name_limpio.startswith("1º DAM"):
                                continue
                            console.print(f"[cyan]Escaneando clases Canvas de:[/cyan] [bold]{c_name_limpio}[/bold]...")
                            clases_actuales.clear()
                            try:
                                page.goto(f"{portal_url}/courses/{c_id}", wait_until="domcontentloaded", timeout=20000)
                                tabs = page.evaluate("""async (cId) => {
                                    try {
                                        const res = await fetch(`/api/v1/courses/${cId}/tabs`);
                                        if (res.ok) return await res.json();
                                    } catch(e) {}
                                    return [];
                                }""", c_id)
                                url_clases = None
                                for t in tabs:
                                    if "clases en directo" in t.get("label", "").lower() or t.get("id") == "context_external_tool_366":
                                        url_clases = t.get("full_url")
                                        break
                                if not url_clases:
                                    url_clases = f"{portal_url}/courses/{c_id}/external_tools/366"
                                page.goto(url_clases, wait_until="domcontentloaded", timeout=20000)
                                page.wait_for_timeout(4000)
                            except Exception:
                                pass

                            unicas = {r.get("id"): r for r in clases_actuales if r.get("id")}
                            total_transcripciones_guardadas += self._guardar_grabaciones_asignatura(
                                c_name_limpio, list(unicas.values()), table
                            )
                    except Exception as e_canvas:
                        console.print(f"[dim]Nota en portal Canvas {portal_url}: {e_canvas}[/dim]")

                # 2. Escanear portal Moodle UNIR-ESIT (campusonline.unir.net -> PFU, Python, AWS, AZ-900)
                try:
                    console.print("[cyan]🌐 Conectando a Moodle UNIR-ESIT (campusonline.unir.net) vía SSO...[/cyan]")
                    page.goto("https://crosscutting.unir.net/loggedin?university=UNIR_ES", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1000)
                    if page.locator("#ApplicationUrl").count() > 0:
                        page.select_option("#ApplicationUrl", value="https://campusonline.unir.net")
                        page.click("input[type='submit']")
                        page.wait_for_load_state("domcontentloaded", timeout=20000)
                        page.wait_for_timeout(2500)

                    cursos_moodle = page.evaluate("""() => {
                        const seen = new Set();
                        const res = [];
                        document.querySelectorAll("a[href*='/course/view.php?id=']").forEach(a => {
                            const href = a.href;
                            const text = a.innerText.trim().replace(/\\s+/g, " ");
                            if (href && text && !seen.has(href)) {
                                seen.add(href);
                                res.push({name: text, url: href});
                            }
                        });
                        return res;
                    }""")

                    for cm in (cursos_moodle or []):
                        c_name_limpio = normalizar_nombre_asignatura(cm["name"])
                        console.print(f"[cyan]Escaneando clases Moodle (PFU/ESIT) de:[/cyan] [bold]{c_name_limpio}[/bold]...")
                        try:
                            page.goto(cm["url"], wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(1500)
                            url_lti_clases = page.evaluate("""() => {
                                const links = Array.from(document.querySelectorAll("a[href*='/mod/lti/view.php']"));
                                const found = links.find(a => a.innerText.trim().toLowerCase().includes("clases"));
                                return found ? found.href : null;
                            }""")
                            if not url_lti_clases:
                                continue

                            clases_actuales.clear()
                            page.goto(url_lti_clases, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(4500)

                            unicas = {r.get("id"): r for r in clases_actuales if r.get("id")}
                            total_transcripciones_guardadas += self._guardar_grabaciones_asignatura(
                                c_name_limpio, list(unicas.values()), table
                            )
                        except Exception as e_cm:
                            console.print(f"[dim]Error en curso Moodle {c_name_limpio}: {e_cm}[/dim]")
                except Exception as e_moodle:
                    console.print(f"[dim]Nota en Moodle campusonline.unir.net: {e_moodle}[/dim]")

            finally:
                browser.close()

        if total_transcripciones_guardadas > 0:
            console.print(table)
            console.print(f"\n[bold green]✔ ¡Se han guardado {total_transcripciones_guardadas} nuevas transcripciones/clases magistrales![/bold green]\n")
        else:
            console.print("\n[dim]Todas las clases grabadas ya estaban sincronizadas.[/dim]\n")

        return total_transcripciones_guardadas

