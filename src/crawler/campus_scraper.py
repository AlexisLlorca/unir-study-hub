"""
Crawler y Sincronizador del Campus Virtual de la UNIR (Canvas LMS - campusfp.unir.net).
Utiliza la sesión autenticada con Playwright (Zero-Knowledge) para recorrer
las aulas virtuales de las asignaturas, descargar temarios y recursos automáticamente,
e importar materiales existentes del respaldo local del alumno.
"""

import sys
import shutil
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin

# Importación modular
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from playwright.sync_api import sync_playwright
import requests
from rich.table import Table
from rich.panel import Panel

from src.core.config import (
    console,
    SESSION_STATE_FILE,
    MATERIALES_DIR,
    BANDEJA_DAM_DIR,
    UNIR_CAMPUS_URL,
    ensure_directories,
)
from src.auth.session_manager import tiene_sesion_guardada, login_interactivo
from src.core.academic_structure import normalizar_nombre_asignatura, reorganizar_biblioteca_completa

# Ruta del respaldo local de estudios de Alex
RESPALDO_LOCAL_DAM = Path("/home/alexllorca/Respaldo_Estudios/Proyectos/FP DAM unir")


def limpiar_nombre_carpeta(nombre: str) -> str:
    """Limpia caracteres inválidos para crear carpetas legibles."""
    nombre = re.sub(r'[\\/*?:"<>|]', "", nombre)
    return nombre.strip()[:60]


class CampusScraper:
    """Navega por el campus virtual de Canvas y descarga materiales docentes."""

    def __init__(self):
        ensure_directories()

    def sincronizar_desde_respaldo_local(self) -> int:
        """
        Escanea la carpeta de respaldo local de Alex e importa todos los PDFs
        y temarios oficiales existentes a la bandeja de trabajo de UNIR Study Hub.
        """
        if not RESPALDO_LOCAL_DAM.exists():
            console.print(f"[yellow]⚠ No se encontró el directorio de respaldo local en:[/yellow] {RESPALDO_LOCAL_DAM}")
            return 0

        console.print(f"\n[cyan]📂 Escaneando respaldo local en:[/cyan] [bold]{RESPALDO_LOCAL_DAM}[/bold]...")

        pdfs_encontrados = list(RESPALDO_LOCAL_DAM.glob("**/*.pdf"))
        if not pdfs_encontrados:
            console.print("[yellow]No se encontraron archivos PDF en el respaldo local.[/yellow]")
            return 0

        total_importados = 0
        table = Table(title="📦 Materiales Importados a UNIR Study Hub", border_style="cyan")
        table.add_column("Asignatura", style="cyan")
        table.add_column("Archivo PDF", style="white")
        table.add_column("Destino en Hub", style="green")

        from src.core.academic_structure import normalizar_nombre_asignatura

        for pdf in pdfs_encontrados:
            try:
                rel = pdf.relative_to(RESPALDO_LOCAL_DAM)
                top_folder = rel.parts[0]
                asignatura = normalizar_nombre_asignatura(top_folder)
            except Exception:
                asignatura = "General"

            dir_destino = MATERIALES_DIR / asignatura
            dir_destino.mkdir(parents=True, exist_ok=True)

            archivo_destino = dir_destino / pdf.name
            if not archivo_destino.exists():
                shutil.copy2(pdf, archivo_destino)
                total_importados += 1
                table.add_row(asignatura, pdf.name, f"materiales/{asignatura}")

        if total_importados > 0:
            console.print(table)
            console.print(f"[bold green]✔ ¡Se han importado {total_importados} temarios y PDFs al Hub con éxito![/bold green]\n")
        else:
            console.print(f"[dim]Todos los {len(pdfs_encontrados)} PDFs ya estaban sincronizados en el Hub.[/dim]\n")

        return total_importados

    def explorar_y_descargar_campus(self) -> int:
        """
        Abre la sesión de Canvas LMS (campusfp.unir.net), consulta la API de Canvas
        para descubrir asignaturas activas y descarga todos los archivos nuevos.
        """
        if not tiene_sesion_guardada():
            console.print("[yellow]⚠ No hay ninguna sesión guardada. Iniciando login interactivo...[/yellow]")
            if not login_interactivo():
                return 0

        console.print(f"\n[cyan]🌐 Conectando al Campus Virtual de FP UNIR (Canvas)...[/cyan]")
        console.print(f"[dim]URL:[/dim] {UNIR_CAMPUS_URL}")

        total_nuevos_archivos = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(SESSION_STATE_FILE),
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                page.goto(UNIR_CAMPUS_URL, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)

                # Verificar si fuimos redirigidos al login
                if "login" in page.url.lower():
                    console.print("[bold red]❌ La sesión guardada ha caducado o requiere volver a iniciar sesión.[/bold red]")
                    console.print("[dim]Ejecuta la opción 1 del menú para renovar tu sesión con 2FA.[/dim]\n")
                    return 0

                console.print(f"[green]✔ Acceso concedido al campus: {page.title()}[/green]")

                # Inicializar sesión de requests con las cookies activas de Canvas
                req_session = requests.Session()
                req_session.headers.update({
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                })
                for ck in context.cookies():
                    req_session.cookies.set(ck["name"], ck["value"], domain=ck.get("domain", ""))

                # 1. Obtener lista completa de cursos vía Canvas API interna (1º y 2º año)
                cursos = page.evaluate("""async () => {
                    try {
                        let res = await fetch('/api/v1/courses?per_page=100&include[]=concluded&include[]=term&state[]=available&state[]=completed');
                        if (res.ok) {
                            let data = await res.json();
                            if (Array.isArray(data) && data.length > 0) return data;
                        }
                    } catch (e) {}

                    try {
                        let res = await fetch('/api/v1/users/self/courses?per_page=100&include[]=concluded');
                        if (res.ok) {
                            let data = await res.json();
                            if (Array.isArray(data) && data.length > 0) return data;
                        }
                    } catch (e) {}

                    try {
                        let res = await fetch('/api/v1/users/self/favorites/courses');
                        if (res.ok) {
                            let data = await res.json();
                            if (Array.isArray(data) && data.length > 0) return data;
                        }
                        res = await fetch('/api/v1/courses?enrollment_state=active');
                        if (res.ok) {
                            let data = await res.json();
                            if (Array.isArray(data)) return data;
                        }
                    } catch (e) {}
                    return [];
                }""")

                if not cursos:
                    console.print("[yellow]ℹ No se detectaron cursos activos mediante la API de Canvas.[/yellow]")
                    return 0

                console.print(f"[green]✔ Cursos activos detectados:[/green] [bold]{len(cursos)} asignaturas[/bold]\n")

                table = Table(title="📥 Sincronización de Archivos del Campus", border_style="cyan")
                table.add_column("Asignatura", style="cyan")
                table.add_column("Archivo Descargado", style="white")
                table.add_column("Tamaño", style="green")

                for c in cursos:
                    c_id = c.get("id")
                    c_name = c.get("name") or f"Curso_{c_id}"
                    c_name_limpio = normalizar_nombre_asignatura(c_name)

                    dir_asig = MATERIALES_DIR / c_name_limpio
                    dir_asig.mkdir(parents=True, exist_ok=True)

                    # 2. Consultar archivos del curso vía Canvas API (con paginación y módulos)
                    archivos = page.evaluate("""async (courseId) => {
                        let allFiles = [];
                        try {
                            let pageNum = 1;
                            while (pageNum <= 5) {
                                const res = await fetch(`/api/v1/courses/${courseId}/files?per_page=100&page=${pageNum}`);
                                if (!res.ok) break;
                                const items = await res.json();
                                if (!Array.isArray(items) || items.length === 0) break;
                                allFiles = allFiles.concat(items);
                                if (items.length < 100) break;
                                pageNum++;
                            }
                        } catch (e) {}

                        // Si la carpeta de archivos está vacía u oculta por el docente, escanear módulos
                        if (allFiles.length === 0) {
                            try {
                                const modRes = await fetch(`/api/v1/courses/${courseId}/modules?include[]=items&per_page=50`);
                                if (modRes.ok) {
                                    const modules = await modRes.json();
                                    if (Array.isArray(modules)) {
                                        for (const mod of modules) {
                                            if (Array.isArray(mod.items)) {
                                                for (const it of mod.items) {
                                                    if (it.type === 'File' && it.url) {
                                                        try {
                                                            const fRes = await fetch(it.url);
                                                            if (fRes.ok) {
                                                                const fData = await fRes.json();
                                                                if (fData && (fData.url || fData.download_url)) {
                                                                    allFiles.push(fData);
                                                                }
                                                            }
                                                        } catch(e) {}
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            } catch(e) {}
                        }
                        return allFiles;
                    }""", c_id)

                    if not isinstance(archivos, list):
                        continue

                    for f in archivos:
                        nombre_archivo = f.get("display_name") or f.get("filename")
                        url_descarga = f.get("url")
                        tam = f.get("size", 0)

                        if not nombre_archivo or not url_descarga:
                            continue

                        ruta_local = dir_asig / nombre_archivo
                        if not ruta_local.exists() or ruta_local.stat().st_size == 0:
                            # Descargar archivo con sesión autenticada
                            try:
                                resp = req_session.get(url_descarga, timeout=30)
                                if resp.status_code == 200:
                                    with open(ruta_local, "wb") as out:
                                        out.write(resp.content)
                                    tam_mb = tam / (1024 * 1024)
                                    table.add_row(c_name_limpio, nombre_archivo, f"{tam_mb:.1f} MB")
                                    total_nuevos_archivos += 1
                            except Exception as err:
                                console.print(f"[dim]Error descargando {nombre_archivo}: {err}[/dim]")

                    # 3. Comprobar temarios teóricos en la herramienta LTI de UNIR CMS si faltan unidades
                    temas_actuales = list(dir_asig.glob("Tema *.pdf"))
                    if len(temas_actuales) < 10 and not c_name_limpio.startswith("M") and "Inglés" not in c_name_limpio:
                        try:
                            page.goto(f"{UNIR_CAMPUS_URL.rstrip('/')}/courses/{c_id}/external_tools/6206", wait_until="domcontentloaded", timeout=12000)
                            page.wait_for_timeout(3500)
                            cms_frame = next((f for f in page.frames if "cms.unir.net" in f.url), None)
                            if cms_frame:
                                lti_links = cms_frame.evaluate("""() => {
                                    const list = [];
                                    document.querySelectorAll('a[title*="Descargar PDF"]').forEach((a, idx) => {
                                        list.push({idx: idx + 1, href: a.href});
                                    });
                                    return list;
                                }""")
                                for it in lti_links:
                                    t_num = it.get("idx")
                                    t_file = f"Tema {t_num}.pdf"
                                    t_dest = dir_asig / t_file
                                    if not t_dest.exists() or t_dest.stat().st_size == 0:
                                        b64 = cms_frame.evaluate("""async (u) => {
                                            try {
                                                const r = await fetch(u);
                                                if (!r.ok) return null;
                                                const b = await r.arrayBuffer();
                                                let s = '';
                                                const u8 = new Uint8Array(b);
                                                for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
                                                return btoa(s);
                                            } catch (e) { return null; }
                                        }""", it["href"])
                                        if b64:
                                            import base64
                                            pdf_data = base64.b64decode(b64)
                                            t_dest.write_bytes(pdf_data)
                                            tam_mb = len(pdf_data) / (1024 * 1024)
                                            table.add_row(c_name_limpio, t_file, f"{tam_mb:.1f} MB (CMS)")
                                            total_nuevos_archivos += 1
                        except Exception:
                            pass

                if total_nuevos_archivos > 0:
                    console.print(table)
                    console.print(f"\n[bold green]✔ ¡Se han descargado {total_nuevos_archivos} nuevos archivos del campus![/bold green]\n")
                else:
                    console.print("[dim]Todos los archivos del campus ya están descargados y al día.[/dim]\n")

            except Exception as e:
                console.print(f"[bold red]❌ Error navegando por Canvas:[/bold red] {e}")
            finally:
                browser.close()

        return total_nuevos_archivos

    def listar_materiales_locales(self):
        """Muestra un resumen de todos los materiales organizados por asignatura."""
        table = Table(title="📚 Biblioteca de Materiales y Temarios en el Hub", border_style="cyan")
        table.add_column("Asignatura", style="cyan")
        table.add_column("PDFs Oficiales", style="white", justify="right")
        table.add_column("Ruta Local", style="dim")

        asignaturas = [d for d in MATERIALES_DIR.iterdir() if d.is_dir()]
        if not asignaturas:
            console.print("[dim]No hay materiales organizados todavía. Sincroniza desde el campus o el respaldo local.[/dim]\n")
            return

        for asig in sorted(asignaturas, key=lambda x: x.name):
            pdfs = list(asig.glob("*.pdf"))
            table.add_row(asig.name, f"{len(pdfs)} archivo(s)", str(asig.relative_to(BASE_DIR)))

        console.print(table)
        console.print("")

    def ejecutar_sincronizacion_completa(self) -> int:
        """
        Ejecuta la sincronización completa (respaldo local + Canvas LMS + videoclases) y emite
        notificaciones de escritorio en caso de nuevos archivos.
        """
        console.print("\n[bold cyan]🔄 Iniciando ciclo de sincronización automática...[/bold cyan]")
        importados = self.sincronizar_desde_respaldo_local()
        descargados = self.explorar_y_descargar_campus()

        total_grabaciones = 0
        try:
            from src.media.unir_recordings_crawler import UnirRecordingsCrawler
            crawler = UnirRecordingsCrawler()
            total_grabaciones = crawler.sincronizar_todas_las_clases()
        except Exception as e:
            console.print(f"[dim]Nota: Sincronización de clases grabadas: {e}[/dim]")

        total = importados + descargados + total_grabaciones
        if total > 0:
            try:
                import subprocess
                subprocess.run(
                    [
                        "notify-send",
                        "-a", "UNIR Study Hub",
                        "-i", "dialog-information",
                        "🎓 UNIR Study Hub Actualizado",
                        f"Se han incorporado {total} nuevos recursos (temas, actividades o transcripciones de clase).",
                    ],
                    check=False,
                )
            except Exception:
                pass

        return total
