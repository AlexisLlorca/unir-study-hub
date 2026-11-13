"""
Sincronización Inteligente en la Nube (Cloud Storage gratuito en Hugging Face Datasets).
Diseñado específicamente para que en servidores gratuitos como Render Free (512 MB RAM):
1. Al arrancar (en < 1 segundo), descargue `procesado_bundle.tar.gz` (~5 MB) con todo el progreso,
   guías, exámenes, flashcards y las 145+ transcripciones de clases, además del índice de los 580+ PDFs.
2. Cree placeholders ligeros de los PDFs para que todo el catálogo aparezca al instante sin gastar RAM ni disco.
3. Cuando el usuario abra un PDF o genere una guía/test con IA sobre un PDF, descargue ese PDF concreto
   bajo demanda desde `AlexLlorca/unir-study-hub-data` en milisegundos.
4. Persista en segundo plano cualquier nueva guía, test o progreso generado en la nube.
"""

import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from src.core.config import BASE_DIR, DATA_DIR, ENV_FILE, console

DEFAULT_HF_DATASET = "AlexLlorca/unir-study-hub-data"
DEFAULT_HF_TOKEN = ""
EXTENSIONES_SINCRONIZABLES = {".pdf", ".docx", ".md", ".json", ".txt"}
EXTENSIONES_BUNDLE_LIGERO = {".md", ".json", ".txt"}
BUNDLE_FILENAME = "procesado_bundle.tar.gz"

# Archivos que NUNCA deben salir del equipo: credenciales, sesiones y bases de datos.
_NOMBRES_SENSIBLES = {
    "usuarios.json",
    "usuarios.db",
    "session_state.json",
    "canvas_config.json",
    ".env",
}
_EXTENSIONES_SENSIBLES = {".db", ".sqlite", ".sqlite3", ".pem", ".key"}
_CARPETAS_SENSIBLES = {"auth", ".git", ".venv", "__pycache__"}


def _es_ruta_sensible(ruta_relativa) -> bool:
    """True si la ruta contiene credenciales o sesiones que no deben subirse a la nube."""
    rel = Path(str(ruta_relativa).replace("\\", "/"))
    partes = [p.lower() for p in rel.parts]
    if not partes:
        return False
    if any(p in _CARPETAS_SENSIBLES for p in partes):
        return True
    nombre = partes[-1]
    if nombre in _NOMBRES_SENSIBLES or nombre.startswith(".env"):
        return True
    if nombre.startswith(("usuarios.db", "usuarios.json", "canvas_config")):
        return True
    if rel.suffix.lower() in _EXTENSIONES_SENSIBLES:
        return True
    return False


def _miembro_tar_seguro(m: tarfile.TarInfo) -> bool:
    """Rechaza rutas absolutas, '..', enlaces y dispositivos dentro de un tar."""
    nombre = m.name.replace("\\", "/")
    if nombre.startswith("/") or ".." in Path(nombre).parts:
        return False
    if m.issym() or m.islnk() or m.isdev():
        return False
    return True


def _destino_repo_seguro(ruta_repo: str) -> Optional[Path]:
    """Convierte una ruta del repositorio en una ruta local que no salga de BASE_DIR."""
    rel = Path(str(ruta_repo).replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or any(p.startswith(".") for p in rel.parts):
        return None
    destino = (BASE_DIR / rel).resolve()
    if not destino.is_relative_to(BASE_DIR.resolve()):
        return None
    return destino


def _avisar_si_repo_publico(api, repo_id: str) -> None:
    """Avisa si el dataset de Hugging Face es público, porque ahí no debe haber datos privados."""
    try:
        info = api.repo_info(repo_id=repo_id, repo_type="dataset")
        if not getattr(info, "private", False):
            console.print(
                "[bold red]⚠ El dataset de Hugging Face es PÚBLICO.[/bold red] "
                "Cámbialo a privado antes de sincronizar datos personales."
            )
    except Exception:
        pass


def get_hf_token() -> str:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    token = os.getenv("HF_TOKEN", "").strip()
    return token or DEFAULT_HF_TOKEN


def get_hf_dataset_repo() -> str:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("HF_DATASET_REPO", DEFAULT_HF_DATASET).strip()


def crear_y_subir_bundle_rapido(token: Optional[str] = None) -> bool:
    """
    Crea `procesado_bundle.tar.gz` con:
    - Todo `data/procesado/` (guías, tests, transcripciones, flashcards, progreso_academico.json)
    - Todo `data/clases_grabadas/`
    - `data/catalogo_materiales.json` (lista de todos los PDFs/DOCX en `data/materiales/` para crear placeholders instantáneos)
    Y lo sube a la raíz del Dataset privado en Hugging Face.
    """
    from huggingface_hub import HfApi

    hf_token = (token or get_hf_token()).strip()
    if not hf_token:
        console.print("[red]❌ Falta HF_TOKEN para crear el bundle cloud.[/red]")
        return False

    api = HfApi(token=hf_token)
    repo_id = get_hf_dataset_repo()
    _avisar_si_repo_publico(api, repo_id)

    # Recopilar lista de todos los materiales (PDF/DOCX/MD/TXT)
    mats_dir = DATA_DIR / "materiales"
    lista_materiales = []
    if mats_dir.exists():
        for p in sorted(mats_dir.rglob("*")):
            try:
                if p.is_file() and p.suffix.lower() in EXTENSIONES_SINCRONIZABLES:
                    rel = p.relative_to(DATA_DIR)
                    if not any(part.startswith(".") for part in rel.parts) and not _es_ruta_sensible(rel):
                        lista_materiales.append(str(rel).replace("\\", "/"))
            except Exception:
                continue

    with tempfile.TemporaryDirectory(prefix="unir_bundle_") as tmpdir:
        tmp_path = Path(tmpdir)
        bundle_path = tmp_path / BUNDLE_FILENAME
        manifest_path = tmp_path / "catalogo_materiales.json"
        manifest_path.write_text(json.dumps(lista_materiales, indent=2, ensure_ascii=False), encoding="utf-8")

        with tarfile.open(bundle_path, "w:gz") as tar:
            tar.add(str(manifest_path), arcname="data/catalogo_materiales.json")
            for sub in ("procesado", "clases_grabadas", "usuarios_espacios"):
                sub_dir = DATA_DIR / sub
                if sub_dir.exists():
                    for f in sub_dir.rglob("*"):
                        if f.is_file() and f.suffix.lower() in EXTENSIONES_BUNDLE_LIGERO:
                            rel_arc = f.relative_to(BASE_DIR)
                            if _es_ruta_sensible(rel_arc):
                                continue
                            tar.add(str(f.resolve()), arcname=str(rel_arc).replace("\\", "/"))

        sz_mb = bundle_path.stat().st_size / (1024 * 1024)
        console.print(f"[cyan]📦 Subiendo {BUNDLE_FILENAME} ({sz_mb:.2f} MB, {len(lista_materiales)} materiales indexados) a {repo_id}...[/cyan]")
        api.upload_file(
            path_or_fileobj=str(bundle_path),
            path_in_repo=BUNDLE_FILENAME,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="sync: actualizar procesado_bundle.tar.gz para arranque instantáneo en cloud",
        )
        console.print(f"[bold green]✔ Bundle instantáneo subido a {repo_id} ({sz_mb:.2f} MB)[/bold green]")
        return True


def _descargar_archivo_hf_directo(path_in_repo: str, destino_local: Path) -> bool:
    """Descarga un único archivo desde el Dataset privado de Hugging Face usando HTTP directo ligero."""
    # Nunca restauramos credenciales desde la nube, aunque queden copias antiguas.
    if _es_ruta_sensible(path_in_repo):
        return False
    hf_token = get_hf_token()
    if not hf_token:
        return False
    repo_id = get_hf_dataset_repo()
    partes_encoded = "/".join(urllib.parse.quote(p, safe="") for p in path_in_repo.split("/"))
    url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/{partes_encoded}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {hf_token}", "User-Agent": "UNIR-Study-Hub/3.3"})
    destino_local.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destino_local.parent, delete=False, suffix=".tmp") as tmp_f:
        tmp_name = tmp_f.name
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                shutil.copyfileobj(resp, tmp_f)
        except urllib.error.HTTPError as e:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if e.code != 404:
                console.print(f"[yellow]⚠ Error descargando {path_in_repo} desde HF: {e}[/yellow]")
            return False
        except Exception as e:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            console.print(f"[yellow]⚠ Error descargando {path_in_repo} desde HF: {e}[/yellow]")
            return False
    os.replace(tmp_name, destino_local)
    return True


def asegurar_archivo_local(ruta: Path) -> Path:
    """
    Si `ruta` es un placeholder de 0 bytes (o no existe aún en el contenedor cloud),
    lo descarga bajo demanda desde el Dataset privado de Hugging Face antes de servirlo o leerlo.
    """
    try:
        if ruta.exists() and ruta.is_file() and ruta.stat().st_size > 0:
            return ruta
    except Exception:
        pass

    try:
        rel_to_base = ruta.absolute().relative_to(BASE_DIR.absolute())
    except Exception:
        return ruta

    path_in_repo = str(rel_to_base).replace("\\", "/")
    if not path_in_repo.startswith("data/"):
        return ruta

    console.print(f"[cyan]☁️ Descargando bajo demanda desde la nube: {ruta.name}...[/cyan]")
    _descargar_archivo_hf_directo(path_in_repo, ruta)
    return ruta


def _fusionar_progreso_remoto(hf_token: str) -> None:
    """Descarga y fusiona progreso.json y organizador_notion.json desde HF sin perder ningún avance local ni remoto."""
    for rel_p in ("data/procesado/progreso.json", "data/usuarios_espacios/alexking44/progreso.json"):
        p_local = DATA_DIR / "procesado" / "progreso.json"
        p_tmp = p_local.with_suffix(".remote.json")
        if not _descargar_archivo_hf_directo(rel_p, p_tmp) or not p_tmp.exists():
            continue
        try:
            remoto = json.loads(p_tmp.read_text(encoding="utf-8"))
            local = json.loads(p_local.read_text(encoding="utf-8")) if p_local.exists() else {}
            if not isinstance(remoto, dict):
                continue
            combinado = local.copy() if isinstance(local, dict) else {}
            ht_map = {
                (x.get("fecha"), x.get("asignatura"), x.get("tema")): x
                for x in combinado.get("historial_tests", [])
                if isinstance(x, dict)
            }
            for x in remoto.get("historial_tests", []):
                if isinstance(x, dict):
                    ht_map[(x.get("fecha"), x.get("asignatura"), x.get("tema"))] = x
            if ht_map:
                combinado["historial_tests"] = sorted(list(ht_map.values()), key=lambda x: str(x.get("fecha", "")))
            for k in ("ultimas_calificaciones", "preguntas_falladas"):
                if isinstance(remoto.get(k), dict):
                    combinado.setdefault(k, {}).update(remoto[k])
            if str(remoto.get("fecha_actualizacion", "")) > str(combinado.get("fecha_actualizacion", "")):
                combinado["fecha_actualizacion"] = remoto["fecha_actualizacion"]
            p_local.parent.mkdir(parents=True, exist_ok=True)
            p_local.write_text(json.dumps(combinado, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        finally:
            p_tmp.unlink(missing_ok=True)

    # Fusionar también organizador_notion.json (tareas vistas, estado de temas y overrides)
    org_local = DATA_DIR / "usuarios_espacios" / "alexking44" / "organizador_notion.json"
    org_tmp = org_local.with_suffix(".remote.json")
    if _descargar_archivo_hf_directo("data/usuarios_espacios/alexking44/organizador_notion.json", org_tmp) and org_tmp.exists():
        try:
            rem_org = json.loads(org_tmp.read_text(encoding="utf-8"))
            loc_org = json.loads(org_local.read_text(encoding="utf-8")) if org_local.exists() else {}
            if isinstance(rem_org, dict):
                comb_org = loc_org.copy() if isinstance(loc_org, dict) else {}
                for k in ("overrides", "temas_estado"):
                    if isinstance(rem_org.get(k), dict):
                        comb_org.setdefault(k, {}).update(rem_org[k])
                if isinstance(rem_org.get("items_propios"), list):
                    by_id = {x.get("id", str(x)): x for x in comb_org.get("items_propios", []) if isinstance(x, dict)}
                    for x in rem_org["items_propios"]:
                        if isinstance(x, dict):
                            by_id[x.get("id", str(x))] = x
                    comb_org["items_propios"] = list(by_id.values())
                org_local.parent.mkdir(parents=True, exist_ok=True)
                org_local.write_text(json.dumps(comb_org, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        finally:
            org_tmp.unlink(missing_ok=True)


def _reconciliar_estado_ligero_hf(hf_token: str, actualizar_existentes: bool = False) -> None:
    """Sincroniza archivos ligeros (.md/.json y estado vivo) entre PC Local y Render."""
    try:
        _fusionar_progreso_remoto(hf_token)
        from huggingface_hub import HfApi
        api = HfApi(token=hf_token)
        repo_id = get_hf_dataset_repo()
        remote_files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
        rutas_a_reconciliar = [
            (DATA_DIR / "usuarios_espacios", "data/usuarios_espacios/"),
            (DATA_DIR / "procesado" / "guias", "data/procesado/guias/"),
            (DATA_DIR / "procesado" / "examenes", "data/procesado/examenes/"),
            (DATA_DIR / "procesado" / "resumenes_clases", "data/procesado/resumenes_clases/"),
            (DATA_DIR / "procesado" / "flashcards", "data/procesado/flashcards/"),
        ]
        for local_sub_dir, prefix in rutas_a_reconciliar:
            remote_sub = {rf for rf in remote_files if rf.startswith(prefix)}
            for rf in remote_sub:
                if rf.endswith((".pdf", ".docx", ".bak", "progreso.json", "organizador_notion.json")):
                    continue
                # Nunca descargamos credenciales ni dejamos que una ruta del repo salga de BASE_DIR.
                if _es_ruta_sensible(rf):
                    continue
                dest_loc = _destino_repo_seguro(rf)
                if dest_loc is None:
                    continue
                es_estado_vivo = rf.endswith(("canvas_sync.json", "mazos.json"))
                if actualizar_existentes or es_estado_vivo or not dest_loc.exists() or dest_loc.stat().st_size == 0:
                    _descargar_archivo_hf_directo(rf, dest_loc)
    except Exception as e:
        console.print(f"[yellow]⚠ Aviso reconciliando estado ligero desde HF: {e}[/yellow]")


def descargar_datos_desde_hf_si_vacio(forzar: bool = False) -> bool:
    """
    Se ejecuta de forma síncrona y ultrarrápida (~1 seg) al iniciar el servidor en la nube
    o al pulsar 'Sincronizar' desde la web cloud:
    1. Si la biblioteca base ya existe (p. ej. en PC local), lanza en segundo plano la reconciliación ligera
       para traer cualquier avance/test hecho en el móvil (Render) sin gastar ancho de banda.
    2. Si está vacía (arranque en frío en Render), descarga `procesado_bundle.tar.gz` y crea placeholders.
    """
    hf_token = get_hf_token()
    if not hf_token:
        return False

    trans_dir = DATA_DIR / "procesado" / "transcripciones"
    num_trans = len(list(trans_dir.rglob("*.md"))) if trans_dir.exists() else 0
    if not forzar and num_trans > 10 and not os.getenv("FORCE_HF_SYNC_ON_START"):
        threading.Thread(target=_reconciliar_estado_ligero_hf, args=(hf_token,), daemon=True).start()
        return True

    console.print("[cyan]☁️ Sincronizando biblioteca desde Hugging Face Cloud (modo instantáneo)...[/cyan]")
    with tempfile.TemporaryDirectory(prefix="unir_init_") as tmpdir:
        bundle_local = Path(tmpdir) / BUNDLE_FILENAME
        if not _descargar_archivo_hf_directo(BUNDLE_FILENAME, bundle_local):
            return False

        try:
            with tarfile.open(bundle_local, "r:gz") as tar:
                # Nunca sobrescribir cuentas, feedback ni credenciales con el bundle general,
                # y rechazar rutas absolutas, "..", enlaces y dispositivos.
                miembros_seguros = [
                    m for m in tar.getmembers()
                    if not m.name.startswith(("data/usuarios.json", "data/usuarios.db", "data/feedback"))
                    and not _es_ruta_sensible(m.name)
                    and _miembro_tar_seguro(m)
                ]
                try:
                    # filter="data" es la protección oficial de Python 3.12+ contra tar maliciosos.
                    tar.extractall(path=str(BASE_DIR), members=miembros_seguros, filter="data")
                except TypeError:
                    # Python < 3.12: los miembros ya vienen validados a mano.
                    tar.extractall(path=str(BASE_DIR), members=miembros_seguros)
        except Exception as e:
            console.print(f"[red]❌ Error extrayendo {BUNDLE_FILENAME}: {e}[/red]")
            return False

    # Reconciliar progreso, agenda, flashcards y guías/tests recientes con Hugging Face
    _reconciliar_estado_ligero_hf(hf_token)

    manifest_file = DATA_DIR / "catalogo_materiales.json"
    if manifest_file.exists():
        try:
            materiales_rel = json.loads(manifest_file.read_text(encoding="utf-8"))
            set_validos = {str((DATA_DIR / rel_str).resolve()) for rel_str in materiales_rel}

            # Limpiar cualquier archivo antiguo que ya no exista en el catálogo limpio
            mats_dir = DATA_DIR / "materiales"
            if mats_dir.exists():
                for f_old in list(mats_dir.rglob("*")):
                    if f_old.is_file() and str(f_old.resolve()) not in set_validos:
                        f_old.unlink(missing_ok=True)

            creados = 0
            for rel_str in materiales_rel:
                p_mat = DATA_DIR / rel_str
                if not p_mat.exists():
                    p_mat.parent.mkdir(parents=True, exist_ok=True)
                    p_mat.touch()
                    creados += 1

            from src.core.academic_structure import normalizar_y_limpiar_biblioteca
            normalizar_y_limpiar_biblioteca()

            console.print(
                f"[bold green]✔ Biblioteca cloud lista al 100% ({len(materiales_rel)} documentos activos y {creados} nuevos indexados).[/bold green]"
            )
        except Exception as e:
            console.print(f"[yellow]⚠ Error creando índice de materiales: {e}[/yellow]")

    return True


def subir_datos_a_hf(token: Optional[str] = None, solo_activos: bool = False) -> bool:
    """
    Sincroniza tanto las carpetas completas en el Dataset privado de Hugging Face
    como el paquete instantáneo `procesado_bundle.tar.gz`.
    """
    from huggingface_hub import HfApi

    hf_token = (token or get_hf_token()).strip()
    if not hf_token:
        console.print("[red]❌ Falta HF_TOKEN para sincronizar con Hugging Face Dataset.[/red]")
        return False

    api = HfApi(token=hf_token)
    repo_id = get_hf_dataset_repo()

    api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)
    _avisar_si_repo_publico(api, repo_id)
    crear_y_subir_bundle_rapido(token=hf_token)

    with tempfile.TemporaryDirectory(prefix="unir_hf_data_") as tmpdir:
        tmp_data = Path(tmpdir) / "data"
        tmp_data.mkdir(parents=True, exist_ok=True)

        for ruta_origen in DATA_DIR.rglob("*"):
            try:
                if not ruta_origen.is_file() or ruta_origen.suffix.lower() not in EXTENSIONES_SINCRONIZABLES:
                    continue
                rel = ruta_origen.relative_to(DATA_DIR)
                if any(part.startswith(".") for part in rel.parts) or _es_ruta_sensible(rel):
                    continue
                ruta_real = ruta_origen.resolve()
                if not ruta_real.exists() or not ruta_real.is_file() or ruta_real.stat().st_size == 0:
                    continue
                destino = tmp_data / rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ruta_real, destino)
            except Exception:
                continue

        for sub in ["clases_grabadas", "procesado", "materiales", "usuarios_espacios"]:
            sub_path = tmp_data / sub
            if sub_path.exists():
                api.upload_folder(
                    folder_path=str(sub_path),
                    path_in_repo=f"data/{sub}",
                    repo_id=repo_id,
                    repo_type="dataset",
                    commit_message=f"sync: data/{sub} (biblioteca limpia y deduplicada)",
                )

        # Purgar archivos obsoletos/duplicados remotos que ya no existan localmente
        try:
            from huggingface_hub import CommitOperationDelete
            remote_files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
            local_valid = {
                str(p.relative_to(BASE_DIR)).replace("\\", "/")
                for p in DATA_DIR.rglob("*")
                if p.is_file()
            }
            deletions = [
                CommitOperationDelete(path_in_repo=rf)
                for rf in remote_files
                if rf.startswith("data/materiales/") and rf not in local_valid
            ]
            if deletions:
                api.create_commit(
                    repo_id=repo_id,
                    repo_type="dataset",
                    operations=deletions,
                    commit_message=f"clean: purgar {len(deletions)} archivos obsoletos o duplicados en data/materiales",
                )
        except Exception as e:
            console.print(f"[yellow]⚠ Aviso en purga remota de Hugging Face: {e}[/yellow]")

    return True


_bundle_lock = threading.Lock()
_bundle_dirty = False


def _actualizar_bundle_seguro(token: str) -> None:
    """Actualiza y sube procesado_bundle.tar.gz de forma segura garantizando que ningún cambio quede sin empaquetar."""
    global _bundle_dirty
    _bundle_dirty = True
    if not _bundle_lock.acquire(blocking=False):
        # Otro hilo ya está subiendo el bundle; al ver _bundle_dirty=True hará una pasada final con los nuevos cambios
        return
    try:
        while _bundle_dirty:
            _bundle_dirty = False
            time.sleep(2)  # Agrupar archivos si se generaron o borraron varios seguidos
            crear_y_subir_bundle_rapido(token=token)
    except Exception as e:
        console.print(f"[yellow]⚠ Aviso al actualizar bundle cloud: {e}[/yellow]")
    finally:
        _bundle_lock.release()


def respaldar_archivo_en_hf_async(ruta_local: Path) -> None:
    """
    Sube en segundo plano únicamente el archivo individual recién modificado
    al Dataset privado de Hugging Face (sin re-subir el bundle completo para no gastar ancho de banda).
    """
    hf_token = get_hf_token()
    if not hf_token:
        return

    try:
        rel_to_base = ruta_local.resolve().relative_to(BASE_DIR.resolve())
    except Exception:
        return

    rel_str = str(rel_to_base).replace("\\", "/")

    # Nunca subimos credenciales, sesiones ni bases de datos de usuarios.
    if _es_ruta_sensible(rel_str):
        return

    def _worker():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=hf_token)
            api.upload_file(
                path_or_fileobj=str(ruta_local.resolve()),
                path_in_repo=rel_str,
                repo_id=get_hf_dataset_repo(),
                repo_type="dataset",
                commit_message=f"auto-backup: {rel_to_base.name}",
            )
        except Exception as e:
            console.print(f"[yellow]⚠ Error en respaldar_archivo_en_hf_async ({ruta_local.name}): {e}[/yellow]")

    threading.Thread(target=_worker, daemon=True).start()


def eliminar_archivo_en_hf_async(ruta_local: Path) -> None:
    """
    Elimina en segundo plano el archivo individual del Dataset privado de Hugging Face
    (sin reconstruir ni re-subir procesado_bundle.tar.gz para evitar consumo de ancho de banda).
    """
    hf_token = get_hf_token()
    if not hf_token:
        return

    try:
        rel_to_base = ruta_local.resolve().relative_to(BASE_DIR.resolve())
    except Exception:
        try:
            rel_to_base = Path(str(ruta_local).replace(str(BASE_DIR) + "/", ""))
        except Exception:
            return

    path_in_repo = str(rel_to_base).replace("\\", "/")

    def _worker_del():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=hf_token)
            repo_id = get_hf_dataset_repo()
            try:
                api.delete_file(
                    path_in_repo=path_in_repo,
                    repo_id=repo_id,
                    repo_type="dataset",
                    commit_message=f"delete: eliminar {rel_to_base.name}",
                )
                console.print(f"[cyan]🗑️ Archivo {rel_to_base.name} eliminado de Hugging Face[/cyan]")
            except Exception as e:
                console.print(f"[yellow]⚠ Aviso eliminando {path_in_repo} en HF: {e}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]⚠ Error en eliminar_archivo_en_hf_async: {e}[/yellow]")

    threading.Thread(target=_worker_del, daemon=True).start()
