"""
Sincronización en la Nube (Cloud Storage gratuito en Hugging Face Datasets).
Permite que una instancia gratuita de Render / Koyeb / Railway descargue automáticamente
las transcripciones, guías, exámenes y temarios desde el Dataset privado del usuario
('AlexLlorca/unir-study-hub-data') y persista las nuevas guías/tests generados en la nube.
"""

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from src.core.config import BASE_DIR, DATA_DIR, ENV_FILE, console
from dotenv import load_dotenv

DEFAULT_HF_DATASET = "AlexLlorca/unir-study-hub-data"
EXTENSIONES_SINCRONIZABLES = {".pdf", ".docx", ".md", ".json", ".txt"}


def get_hf_token() -> str:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("HF_TOKEN", "").strip()


def get_hf_dataset_repo() -> str:
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    return os.getenv("HF_DATASET_REPO", DEFAULT_HF_DATASET).strip()


def subir_datos_a_hf(token: Optional[str] = None, solo_activos: bool = False) -> bool:
    """
    Empaqueta y sube data/ (excluyendo vídeos .mp4/.zip pesados) al Dataset privado de Hugging Face.
    """
    from huggingface_hub import HfApi

    hf_token = (token or get_hf_token()).strip()
    if not hf_token:
        console.print("[red]❌ Falta HF_TOKEN para sincronizar con Hugging Face Dataset.[/red]")
        return False

    api = HfApi(token=hf_token)
    user_info = api.whoami()
    username = user_info["name"]
    repo_id = f"{username}/unir-study-hub-data"

    api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="unir_hf_data_") as tmpdir:
        tmp_data = Path(tmpdir) / "data"
        tmp_data.mkdir(parents=True, exist_ok=True)

        archivos_copiados = 0
        bytes_copiados = 0

        for ruta_origen in DATA_DIR.rglob("*"):
            try:
                if not ruta_origen.is_file():
                    continue
                if ruta_origen.suffix.lower() not in EXTENSIONES_SINCRONIZABLES:
                    continue
                rel = ruta_origen.relative_to(DATA_DIR)
                if any(part.startswith(".") for part in rel.parts):
                    continue

                ruta_real = ruta_origen.resolve()
                if not ruta_real.exists() or not ruta_real.is_file():
                    continue

                destino = tmp_data / rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ruta_real, destino)
                archivos_copiados += 1
                bytes_copiados += destino.stat().st_size
            except Exception:
                continue

        console.print(
            f"[cyan]☁️ Subiendo {archivos_copiados} archivos ({bytes_copiados / (1024*1024):.1f} MB) "
            f"al Dataset privado {repo_id}...[/cyan]"
        )
        api.upload_large_folder(
            folder_path=str(Path(tmpdir)),
            repo_id=repo_id,
            repo_type="dataset",
        )
        console.print(f"[bold green]✔ Biblioteca sincronizada en https://huggingface.co/datasets/{repo_id}[/bold green]")
        return True


def descargar_datos_desde_hf_si_vacio() -> bool:
    """
    Si el servidor arranca en un contenedor efímero en la nube (ej. Render Free) donde data/procesado
    o data/materiales no existen aún, descarga la biblioteca desde el Dataset privado de Hugging Face.
    """
    hf_token = get_hf_token()
    if not hf_token:
        return False

    # Si ya tenemos transcripciones y materiales locales, no hace falta descargar todo al arrancar
    trans_dir = DATA_DIR / "procesado" / "transcripciones"
    mats_dir = DATA_DIR / "materiales"
    tiene_local = trans_dir.exists() and any(trans_dir.rglob("*.md")) and mats_dir.exists() and any(mats_dir.rglob("*.pdf"))
    if tiene_local and not os.getenv("FORCE_HF_SYNC_ON_START"):
        return True

    repo_id = get_hf_dataset_repo()
    try:
        from huggingface_hub import snapshot_download

        console.print(f"[cyan]☁️ Descargando biblioteca académica desde {repo_id} al contenedor cloud...[/cyan]")
        snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            local_dir=str(BASE_DIR),
            token=hf_token,
            allow_patterns=["data/**"],
        )
        console.print("[bold green]✔ Biblioteca académica cargada desde la nube correctamente.[/bold green]")
        return True
    except Exception as e:
        console.print(f"[yellow]⚠ No se pudo sincronizar desde {repo_id} al iniciar: {e}[/yellow]")
        return False


def respaldar_archivo_en_hf_async(ruta_local: Path) -> None:
    """
    Sube en segundo plano un archivo recién generado (guía, test JSON, progreso o flashcards)
    al Dataset privado de Hugging Face para que nunca se pierda en contenedores cloud efímeros.
    """
    hf_token = get_hf_token()
    if not hf_token:
        return

    try:
        rel_to_base = ruta_local.resolve().relative_to(BASE_DIR.resolve())
    except Exception:
        return

    def _worker():
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=hf_token)
            api.upload_file(
                path_or_fileobj=str(ruta_local.resolve()),
                path_in_repo=str(rel_to_base).replace("\\", "/"),
                repo_id=get_hf_dataset_repo(),
                repo_type="dataset",
                commit_message=f"auto-backup: {rel_to_base.name}",
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()
