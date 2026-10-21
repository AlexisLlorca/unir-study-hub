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
    RESPALDO_DIR,
    ensure_directories,
)
from src.auth.session_manager import tiene_sesion_guardada, login_interactivo
from src.core.academic_structure import normalizar_nombre_asignatura, reorganizar_biblioteca_completa

# Ruta del respaldo local de estudios configurable por entorno (UNIR_RESPALDO_DIR)
RESPALDO_LOCAL_DAM = RESPALDO_DIR / "Proyectos" / "FP DAM unir"


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

        portales_canvas = [
            {"nombre": "Canvas FP (DAM)", "url": UNIR_CAMPUS_URL.rstrip("/")},
            {"nombre": "Canvas Universidad (Bootcamps & PFU)", "url": "https://micampus.unir.net"},
        ]

        total_nuevos_archivos = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--headless=new", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    storage_state=str(SESSION_STATE_FILE),
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                page = context.new_page()

                # Inicializar sesión de requests con las cookies activas
                req_session = requests.Session()
                req_session.headers.update({
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                })
                for ck in context.cookies():
                    req_session.cookies.set(ck["name"], ck["value"], domain=ck.get("domain", ""))

                table = Table(title="📥 Sincronización Multi-Campus UNIR (Canvas + Moodle ESIT)", border_style="cyan")
                table.add_column("Plataforma", style="yellow")
                table.add_column("Asignatura", style="cyan")
                table.add_column("Archivo Descargado", style="white")
                table.add_column("Tamaño", style="green")

                for portal in portales_canvas:
                    portal_nombre = portal["nombre"]
                    portal_url = portal["url"]
                    console.print(f"\n[cyan]🌐 Conectando a {portal_nombre}...[/cyan]")
                    console.print(f"[dim]URL:[/dim] {portal_url}")

                    try:
                        page.goto(portal_url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(1500)

                        # Verificar si fuimos redirigidos al login
                        if "login" in page.url.lower():
                            console.print(f"[yellow]⚠ Sesión no disponible para {portal_nombre}. Saltando...[/yellow]")
                            continue

                        console.print(f"[green]✔ Acceso concedido: {page.title()}[/green]")

                        # 1. Obtener lista de cursos vía Canvas API
                        cursos = page.evaluate("""async () => {
                            let list = [];
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
                            console.print(f"[dim]ℹ No se detectaron cursos activos en {portal_nombre}.[/dim]")
                            continue

                        console.print(f"[green]✔ Cursos detectados en {portal_nombre}:[/green] [bold]{len(cursos)}[/bold]")
                    except Exception as e_portal:
                        console.print(f"[dim]Nota conectando a {portal_nombre}: {e_portal}[/dim]")
                        continue

                    total_nuevos_archivos += self._descargar_archivos_de_cursos(
                        page, req_session, cursos, portal_nombre, portal_url, table
                    )

                # 2. Explorar y descargar desde Moodle UNIR-ESIT (campusonline.unir.net -> PFU, Python, AWS, AZ-900)
                total_nuevos_archivos += self._explorar_y_descargar_moodle_campusonline(
                    page, context, req_session, table
                )

                if total_nuevos_archivos > 0:
                    console.print(table)
                    console.print(f"\n[bold green]✔ ¡Se han descargado {total_nuevos_archivos} nuevos archivos del campus![/bold green]\n")
                else:
                    console.print("[dim]Todos los archivos del campus ya están descargados y al día.[/dim]\n")
            except Exception as e:
                console.print(f"[bold red]❌ Error navegando por el campus:[/bold red] {e}")
            finally:
                browser.close()

        return total_nuevos_archivos

    def _explorar_y_descargar_moodle_campusonline(self, page, context, req_session, table: Table) -> int:
        """
        Conecta a Moodle UNIR-ESIT (campusonline.unir.net) vía SSO de crosscutting.unir.net,
        descubre las asignaturas del PFU en Ingeniería de Software y cursos técnicos (Python, AWS, AZ-900),
        y descarga los PDFs completos desde el Visor Interactivo (visorinteractivo.unir.net / cms.unir.net).
        """
        nuevos_archivos = 0
        portal_nombre = "Moodle UNIR-ESIT (PFU)"
        console.print(f"\n[cyan]🌐 Conectando a {portal_nombre} (campusonline.unir.net)...[/cyan]")

        auth_visor = {"token": ""}

        def capturar_auth(req):
            if "visorinteractivo.unir.net/api/v" in req.url and req.headers.get("authorization"):
                auth_visor["token"] = req.headers.get("authorization")

        context.on("request", capturar_auth)

        try:
            page.goto("https://crosscutting.unir.net/loggedin?university=UNIR_ES", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1200)
            if page.locator("#ApplicationUrl").count() > 0:
                page.select_option("#ApplicationUrl", value="https://campusonline.unir.net")
                page.click("input[type='submit']")
                page.wait_for_load_state("domcontentloaded", timeout=20000)
                page.wait_for_timeout(2500)

            if "campusonline.unir.net" not in page.url:
                console.print("[yellow]⚠ No se pudo acceder a campusonline.unir.net.[/yellow]")
                return 0

            console.print(f"[green]✔ Acceso concedido: {page.title()}[/green]")

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

            console.print(f"[green]✔ Cursos detectados en {portal_nombre}:[/green] [bold]{len(cursos_moodle or [])}[/bold]")

            for cm in (cursos_moodle or []):
                c_name_limpio = normalizar_nombre_asignatura(cm["name"])
                dir_asig = MATERIALES_DIR / c_name_limpio
                dir_asig.mkdir(parents=True, exist_ok=True)

                try:
                    page.goto(cm["url"], wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)

                    url_temario = page.evaluate("""() => {
                        const links = Array.from(document.querySelectorAll("a[href*='/mod/lti/view.php'], a[href*='/mod/url/view.php']"));
                        const found = links.find(a => {
                            const t = a.innerText.trim().toLowerCase();
                            return t === "temario" || t.startsWith("temario");
                        });
                        return found ? found.href : null;
                    }""")
                    if not url_temario:
                        continue

                    page.goto(url_temario, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)

                    for frame in page.frames:
                        if "visorinteractivo.unir.net/lti/" in frame.url:
                            m_subj = re.search(r"/lti/(\d+)", frame.url)
                            if not m_subj:
                                continue
                            subject_id = m_subj.group(1)

                            np = None
                            try:
                                with context.expect_page(timeout=8000) as np_info:
                                    frame.click("button")
                                np = np_info.value
                                np.wait_for_load_state("domcontentloaded", timeout=15000)
                                np.wait_for_timeout(2500)

                                tok = auth_visor.get("token", "")
                                topics = np.evaluate("""async ({sid, token}) => {
                                    try {
                                        const r = await fetch(`/api/v3/subjects/${sid}/user-topics`, {
                                            headers: token ? {"Authorization": token} : {}
                                        });
                                        if (!r.ok) return [];
                                        const d = await r.json();
                                        return d.topics || [];
                                    } catch (e) { return []; }
                                }""", {"sid": subject_id, "token": tok})

                                for t_obj in (topics or []):
                                    t_id = t_obj.get("topicId")
                                    t_title = (t_obj.get("title") or f"Tema_{t_id}").strip()
                                    t_clean = re.sub(r'[\\/*?:"<>|]', "", t_title).strip()
                                    if not t_clean.lower().endswith(".pdf"):
                                        t_clean += ".pdf"
                                    dest_pdf = dir_asig / t_clean

                                    if dest_pdf.exists() and dest_pdf.stat().st_size > 0:
                                        continue

                                    dl_info = np.evaluate("""async ({tid, token}) => {
                                        try {
                                            const r = await fetch(`/api/v1/topics/${tid}/download`, {
                                                method: "POST",
                                                headers: token ? {"Authorization": token} : {}
                                            });
                                            if (!r.ok) return null;
                                            return await r.json();
                                        } catch (e) { return null; }
                                    }""", {"tid": t_id, "token": tok})

                                    dl_url = (dl_info or {}).get("downloadUrl")
                                    if dl_url:
                                        r_pdf = req_session.get(dl_url, timeout=30)
                                        if r_pdf.status_code == 200 and len(r_pdf.content) > 1000:
                                            dest_pdf.write_bytes(r_pdf.content)
                                            tam_mb = len(r_pdf.content) / (1024 * 1024)
                                            table.add_row(portal_nombre, c_name_limpio, t_clean, f"{tam_mb:.1f} MB (Visor)")
                                            nuevos_archivos += 1
                            except Exception as e_vis:
                                console.print(f"[dim]Nota en Visor Interactivo ({c_name_limpio}): {e_vis}[/dim]")
                            finally:
                                if np:
                                    try:
                                        np.close()
                                    except Exception:
                                        pass
                            break
                        elif "cms.unir.net" in frame.url:
                            try:
                                html_cms = frame.evaluate("() => document.body ? document.body.innerHTML : ''")
                                zip_matches = re.findall(r"/file/zip/[^'\"\s)]+", html_cms)
                                if zip_matches:
                                    zip_full_url = f"https://cms.unir.net{zip_matches[0]}"
                                    nuevos_archivos += self._extraer_zip_cms(
                                        zip_full_url, req_session, dir_asig, portal_nombre, c_name_limpio, table
                                    )
                                else:
                                    lti_links = frame.evaluate("""() => {
                                        const list = [];
                                        document.querySelectorAll('a[title*="Descargar PDF"], a[href*="/file/"]').forEach((a, idx) => {
                                            if (a.href) list.push({idx: idx + 1, href: a.href, title: (a.innerText || a.title || '').trim()});
                                        });
                                        return list;
                                    }""")
                                    for it in (lti_links or []):
                                        t_num = it.get("idx")
                                        t_file = f"Tema {t_num:02d}.pdf"
                                        t_dest = dir_asig / t_file
                                        if not t_dest.exists() or t_dest.stat().st_size == 0:
                                            r_pdf = req_session.get(it["href"], timeout=30)
                                            if r_pdf.status_code == 200 and len(r_pdf.content) > 1000:
                                                t_dest.write_bytes(r_pdf.content)
                                                tam_mb = len(r_pdf.content) / (1024 * 1024)
                                                table.add_row(portal_nombre, c_name_limpio, t_file, f"{tam_mb:.1f} MB (CMS)")
                                                nuevos_archivos += 1
                            except Exception:
                                pass
                            break
                        elif "moodle-campusonline.s3.amazonaws.com" in frame.url:
                            nuevos_archivos += self._descargar_modulos_s3_moodle(
                                frame.url, req_session, dir_asig, portal_nombre, c_name_limpio, table
                            )
                            break
                except Exception as e_curso:
                    console.print(f"[dim]Error procesando curso Moodle {c_name_limpio}: {e_curso}[/dim]")

        except Exception as e_moodle:
            console.print(f"[dim]Nota conectando a {portal_nombre}: {e_moodle}[/dim]")

        return nuevos_archivos

    def _extraer_zip_cms(self, zip_url: str, req_session, dir_asig: Path, portal_nombre: str, c_name_limpio: str, table: Table) -> int:
        """Descarga un paquete ZIP de cms.unir.net y extrae todos los PDFs con nombres limpios."""
        import zipfile
        import io
        nuevos = 0
        try:
            r_zip = req_session.get(zip_url, timeout=30)
            if r_zip.status_code == 200 and len(r_zip.content) > 1000:
                with zipfile.ZipFile(io.BytesIO(r_zip.content)) as zf:
                    for zname in zf.namelist():
                        if not zname.lower().endswith(".pdf"):
                            continue
                        clean_name = Path(zname).name
                        clean_name = re.sub(r"^\d+_full_\d+_", "", clean_name)
                        clean_name = re.sub(r"_[a-z]{3}-[A-Z]{2}\.pdf$", ".pdf", clean_name)
                        clean_name = clean_name.replace("_", " ").strip()
                        dest_pdf = dir_asig / clean_name
                        if not dest_pdf.exists() or dest_pdf.stat().st_size == 0:
                            pdf_bytes = zf.read(zname)
                            dest_pdf.write_bytes(pdf_bytes)
                            tam_mb = len(pdf_bytes) / (1024 * 1024)
                            table.add_row(portal_nombre, c_name_limpio, clean_name, f"{tam_mb:.1f} MB (CMS ZIP)")
                            nuevos += 1
        except Exception as e:
            console.print(f"[dim]Nota extrayendo ZIP CMS ({c_name_limpio}): {e}[/dim]")
        return nuevos

    def _descargar_modulos_s3_moodle(self, s3_url: str, req_session, dir_asig: Path, portal_nombre: str, c_name_limpio: str, table: Table) -> int:
        """Descarga los módulos PDF alojados en moodle-campusonline.s3.amazonaws.com (AWS, AZ-900)."""
        nuevos = 0
        hdrs = {"Referer": "https://campusonline.unir.net/", "User-Agent": "Mozilla/5.0"}
        try:
            r_html = req_session.get(s3_url, headers=hdrs, timeout=15)
            r_html.encoding = "utf-8"
            titulos_modulos = {}
            if r_html.status_code == 200:
                for m in re.finditer(r"<h1[^>]*class=[\"']table-title[\"'][^>]*>\s*<span>([^<]+)</span>", r_html.text, re.I):
                    t_str = re.sub(r"M([óo])dulo\s*(\d+)\.\s*", r"M\1dulo \2. ", m.group(1).strip(), flags=re.I)
                    m_num = re.search(r"M[óo]dulo\s*(\d+)", t_str, re.I)
                    if m_num:
                        titulos_modulos[int(m_num.group(1))] = re.sub(r'[\\/*?:"<>|]', "", t_str).strip()

            for idx in range(1, 10):
                mod_url = urljoin(s3_url, f"pdf/modulo{idx}.pdf")
                nombre_mod = titulos_modulos.get(idx, f"Módulo {idx}")
                if not nombre_mod.lower().endswith(".pdf"):
                    nombre_mod += ".pdf"
                dest_pdf = dir_asig / nombre_mod
                if dest_pdf.exists() and dest_pdf.stat().st_size > 0:
                    continue
                r_mod = req_session.get(mod_url, headers=hdrs, timeout=20)
                if r_mod.status_code == 200 and len(r_mod.content) > 1000 and r_mod.content[:4] == b"%PDF":
                    dest_pdf.write_bytes(r_mod.content)
                    tam_mb = len(r_mod.content) / (1024 * 1024)
                    table.add_row(portal_nombre, c_name_limpio, nombre_mod, f"{tam_mb:.1f} MB (S3)")
                    nuevos += 1
        except Exception as e:
            console.print(f"[dim]Nota descargando módulos S3 ({c_name_limpio}): {e}[/dim]")
        return nuevos

    def _descargar_archivos_de_cursos(self, page, req_session, cursos, portal_nombre: str, portal_url: str, table: Table) -> int:
        """Descarga archivos y temarios externos de una lista de cursos de Canvas."""
        nuevos_archivos = 0
        for c in cursos:
            c_id = c.get("id")
            c_name = c.get("name") or f"Curso_{c_id}"
            c_name_limpio = normalizar_nombre_asignatura(c_name)

            dir_asig = MATERIALES_DIR / c_name_limpio
            dir_asig.mkdir(parents=True, exist_ok=True)

            # 1. Consultar archivos del curso vía Canvas API
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

            if isinstance(archivos, list):
                for f in archivos:
                    nombre_archivo = f.get("display_name") or f.get("filename")
                    url_descarga = f.get("url")
                    tam = f.get("size", 0)

                    if not nombre_archivo or not url_descarga:
                        continue

                    ruta_local = dir_asig / nombre_archivo
                    if not ruta_local.exists() or ruta_local.stat().st_size == 0:
                        try:
                            resp = req_session.get(url_descarga, timeout=30)
                            if resp.status_code == 200:
                                with open(ruta_local, "wb") as out:
                                    out.write(resp.content)
                                tam_mb = tam / (1024 * 1024)
                                table.add_row(portal_nombre, c_name_limpio, nombre_archivo, f"{tam_mb:.1f} MB")
                                nuevos_archivos += 1
                        except Exception as err:
                            console.print(f"[dim]Error descargando {nombre_archivo}: {err}[/dim]")

            # 2. Comprobar temarios teóricos en herramientas externas (CMS/LTI)
            temas_actuales = list(dir_asig.glob("Tema *.pdf"))
            if (len(temas_actuales) < 10 or "Proyecto" in c_name_limpio) and not c_name_limpio.startswith("M") and "Inglés" not in c_name_limpio:
                try:
                    tabs = page.evaluate("""async (courseId) => {
                        try {
                            const res = await fetch(`/api/v1/courses/${courseId}/tabs`);
                            if (res.ok) return await res.json();
                        } catch(e) {}
                        return [];
                    }""", c_id)

                    url_temas = None
                    for t in tabs:
                        t_label = t.get("label", "").lower()
                        if any(k in t_label for k in ["tema", "contenido", "material"]) and "context_external_tool" in t.get("id", ""):
                            url_temas = t.get("full_url")
                            break
                    if not url_temas:
                        url_temas = f"{portal_url.rstrip('/')}/courses/{c_id}/external_tools/6206"

                    page.goto(url_temas, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3500)

                    for frame in page.frames:
                        if "cms.unir.net" in frame.url:
                            try:
                                html_cms = frame.evaluate("() => document.body ? document.body.innerHTML : ''")
                                zip_matches = re.findall(r"/file/zip/[^'\"\s)]+", html_cms)
                                if zip_matches:
                                    n_zip = self._extraer_zip_cms(
                                        f"https://cms.unir.net{zip_matches[0]}",
                                        req_session, dir_asig, portal_nombre, c_name_limpio, table
                                    )
                                    if n_zip > 0:
                                        nuevos_archivos += n_zip
                                        break
                            except Exception:
                                pass
                            lti_links = frame.evaluate("""() => {
                                const list = [];
                                document.querySelectorAll('a[title*="Descargar PDF"], a[href*="/file/"]').forEach((a, idx) => {
                                    if (a.href && !a.href.includes('/zip/')) list.push({idx: idx + 1, href: a.href});
                                });
                                return list;
                            }""")
                            for it in lti_links:
                                t_num = it.get("idx")
                                t_file = f"Tema {t_num}.pdf"
                                t_dest = dir_asig / t_file
                                if not t_dest.exists() or t_dest.stat().st_size == 0:
                                    b64 = frame.evaluate("""async (u) => {
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
                                        table.add_row(portal_nombre, c_name_limpio, t_file, f"{tam_mb:.1f} MB (CMS)")
                                        nuevos_archivos += 1
                        elif "unirfp.s3.amazonaws.com" in frame.url:
                            docs = frame.evaluate("""() => {
                                const list = [];
                                document.querySelectorAll('a').forEach(a => {
                                    const h = a.href || '';
                                    if (h.endsWith('.pdf') || h.endsWith('.docx') || h.endsWith('.ppt') || h.endsWith('.pptx')) {
                                        list.push({href: h, name: h.split('/').pop()});
                                    }
                                });
                                return list;
                            }""")
                            for doc in docs:
                                d_href = doc.get("href")
                                d_name = doc.get("name")
                                if d_href and d_name:
                                    d_dest = dir_asig / d_name
                                    if not d_dest.exists() or d_dest.stat().st_size == 0:
                                        try:
                                            r_doc = req_session.get(d_href, timeout=20)
                                            if r_doc.status_code == 200:
                                                d_dest.write_bytes(r_doc.content)
                                                tam_mb = len(r_doc.content) / (1024 * 1024)
                                                table.add_row(portal_nombre, c_name_limpio, d_name, f"{tam_mb:.1f} MB (S3)")
                                                nuevos_archivos += 1
                                        except Exception:
                                            pass
                except Exception:
                    pass

        return nuevos_archivos

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
