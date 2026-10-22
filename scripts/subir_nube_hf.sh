#!/usr/bin/env bash
# ==============================================================================
# Despliegue 100% Automático de UNIR Study Hub en Hugging Face Spaces (Nube 24/7)
# Crea el Space Docker, configura tus Secrets (.env) y sube el código + PDFs
# ==============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

HF_TOKEN="${1:-$HF_TOKEN}"

if [ -z "$HF_TOKEN" ]; then
    echo "☁️  =================================================================="
    echo "🚀 DESPLIEGUE AUTOMÁTICO EN HUGGING FACE SPACES (NUBE 24/7)"
    echo "======================================================================"
    echo "👉 Uso: ./scripts/subir_nube_hf.sh hf_TU_TOKEN_AQUI"
    exit 1
fi

export HF_TOKEN

.venv/bin/python - << 'PYEOF'
import os
import shutil
import tempfile
from pathlib import Path
from dotenv import dotenv_values
from huggingface_hub import HfApi

token = os.environ["HF_TOKEN"].strip()
api = HfApi(token=token)

user_info = api.whoami()
username = user_info["name"]
repo_id = f"{username}/unir-study-hub"

print(f"✔ Conectado a Hugging Face como: {username}")
print(f"1/4. Creando/verificando Space Docker '{repo_id}'...")

api.create_repo(
    repo_id=repo_id,
    repo_type="space",
    space_sdk="docker",
    private=False,
    exist_ok=True,
)

# Configurar Secrets desde .env local
env_path = Path(".env")
env_vars = dotenv_values(env_path) if env_path.exists() else {}
print("2/4. Configurando claves secretas (GEMINI_API_KEY, OPENROUTER_API_KEY, HUB_ACCESS_TOKEN) en la nube...")
for key in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "HUB_ACCESS_TOKEN"):
    val = (env_vars.get(key) or os.getenv(key) or "").strip()
    if val:
        api.add_space_secret(repo_id=repo_id, key=key, value=val)
        print(f"   🔒 Secret '{key}' configurado.")

# Preparar carpeta limpia con cabecera YAML en README.md
print("3/4. Preparando paquete de subida (código + temarios + transcripciones)...")
with tempfile.TemporaryDirectory(prefix="unir_hf_") as tmpdir:
    tmp_path = Path(tmpdir)
    for item in ["main.py", "requirements.txt", "Dockerfile", ".dockerignore", "src", "data"]:
        src_item = Path(item)
        if not src_item.exists():
            continue
        dst_item = tmp_path / item
        if src_item.is_dir():
            shutil.copytree(
                src_item,
                dst_item,
                symlinks=False,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.tmp"),
            )
        else:
            shutil.copy2(src_item, dst_item)

    readme_content = """---
title: UNIR Study Hub
emoji: 🎓
colorFrom: cyan
colorTo: indigo
sdk: docker
app_port: 7860
pinned: true
---

# 🎓 UNIR Study Hub — Cloud 24/7
Plataforma académica multi-titulación e inteligencia artificial NOVA.
"""
    (tmp_path / "README.md").write_text(readme_content, encoding="utf-8")

    print("4/4. Subiendo archivos y PDFs a Hugging Face Spaces (puede tardar 1-3 min según tu conexión)...")
    api.upload_folder(
        folder_path=str(tmp_path),
        repo_id=repo_id,
        repo_type="space",
        commit_message="deploy: UNIR Study Hub 24/7 Cloud sync",
    )

hub_token = (env_vars.get("HUB_ACCESS_TOKEN") or "").strip()
space_host = f"https://{username.lower()}-unir-study-hub.hf.space"
print("\n======================================================================")
print("🎉 ¡DESPLIEGUE EN LA NUBE COMPLETADO CON ÉXITO!")
print(f"🔗 Panel de construcción: https://huggingface.co/spaces/{repo_id}")
print(f"🌐 URL directa 24/7 (con tu llave privada):")
print(f"   {space_host}/?token={hub_token}")
print("======================================================================")
PYEOF
