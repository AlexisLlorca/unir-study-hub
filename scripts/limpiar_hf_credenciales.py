#!/usr/bin/env python3
"""
Limpia de Hugging Face los archivos que nunca deberían haberse subido:
usuarios.json, usuarios.db, canvas_config.json y el bundle antiguo.

Por defecto SOLO simula (no borra nada). Para borrar de verdad:
    .venv/bin/python scripts/limpiar_hf_credenciales.py --confirmar

Y para reconstruir después un bundle limpio sin credenciales:
    .venv/bin/python scripts/limpiar_hf_credenciales.py --confirmar --reconstruir-bundle
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.core.cloud_sync import (  # noqa: E402
    BUNDLE_FILENAME,
    _avisar_si_repo_publico,
    _es_ruta_sensible,
    crear_y_subir_bundle_rapido,
    get_hf_dataset_repo,
    get_hf_token,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Elimina credenciales y bases de datos del dataset de Hugging Face."
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="Borra de verdad (sin esta opción solo muestra qué haría).",
    )
    parser.add_argument(
        "--reconstruir-bundle",
        action="store_true",
        help="Vuelve a crear el bundle limpio después de borrar.",
    )
    args = parser.parse_args()

    token = get_hf_token()
    if not token:
        print("❌ Falta HF_TOKEN en .env. No se puede conectar con Hugging Face.")
        return 1

    repo_id = get_hf_dataset_repo()
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    _avisar_si_repo_publico(api, repo_id)

    try:
        remotos = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    except Exception as e:
        print(f"❌ No se pudo listar {repo_id}: {e}")
        return 1

    a_borrar = sorted({rf for rf in remotos if _es_ruta_sensible(rf)})
    if BUNDLE_FILENAME in remotos:
        # El bundle antiguo puede contener canvas_config.json; se reconstruye limpio.
        a_borrar.append(BUNDLE_FILENAME)

    if not a_borrar:
        print(f"✔ No hay archivos sensibles en {repo_id}.")
    else:
        print(f"Archivos sensibles encontrados en {repo_id}:")
        for rf in a_borrar:
            print(f"  - {rf}")

    if not args.confirmar:
        print("\nModo simulación: no se ha borrado nada.")
        print("Para borrar de verdad, repite el comando con --confirmar.")
        return 0

    for rf in a_borrar:
        try:
            api.delete_file(
                path_in_repo=rf,
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"security: eliminar {rf}",
            )
            print(f"✔ eliminado: {rf}")
        except Exception as e:
            print(f"⚠ no se pudo eliminar {rf}: {e}")

    if args.reconstruir_bundle:
        print("📦 Reconstruyendo bundle limpio (sin credenciales)...")
        crear_y_subir_bundle_rapido(token=token)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
