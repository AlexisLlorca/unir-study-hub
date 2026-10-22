#!/usr/bin/env bash
# ==============================================================================
# Script de Despliegue Automático de UNIR Study Hub a Hugging Face Spaces (Nube 24/7)
# ==============================================================================
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "☁️  =================================================================="
echo "🚀 DESPLIEGUE DE UNIR STUDY HUB EN LA NUBE (Hugging Face Spaces)"
echo "======================================================================"

if [ -z "$1" ]; then
    echo ""
    echo "👉 Uso: ./scripts/subir_nube_hf.sh https://huggingface.co/spaces/TU_USUARIO/unir-study-hub"
    echo ""
    exit 1
fi

HF_URL="$1"
CLOUD_DIR="$HOME/.cache/unir-study-hub-cloud-deploy"

echo "1/4. Preparando directorio de sincronización en $CLOUD_DIR..."
rm -rf "$CLOUD_DIR"
mkdir -p "$CLOUD_DIR"

echo "2/4. Copiando código y biblioteca académica (excluyendo .env y auth por seguridad)..."
rsync -a --exclude='.venv' --exclude='.git' --exclude='.env' --exclude='auth' "$REPO_DIR/" "$CLOUD_DIR/"

cd "$CLOUD_DIR"

# Cabecera YAML exigida por Hugging Face Spaces en README.md
cat > README.md << 'EOF'
---
title: UNIR Study Hub
emoji: 🎓
colorFrom: cyan
colorTo: indigo
sdk: docker
app_port: 7860
pinned: true
---

# 🎓 UNIR Study Hub — Cloud Edition
Plataforma académica multi-titulación e inteligencia artificial NOVA.
EOF

git init -b main
git lfs install
git lfs track "*.pdf" "*.docx" "*.zip" "*.mp3" "*.mp4"
git add .gitattributes
git add .
git commit -m "deploy: UNIR Study Hub Cloud 24/7"

echo "3/4. Conectando con Hugging Face Space: $HF_URL"
git remote add space "$HF_URL"

echo "4/4. Subiendo proyecto y materiales a la nube (te pedirá tu usuario y Access Token de Hugging Face)..."
git push --force space main

echo ""
echo "✅ ¡Despliegue completado! Tu web estará activa 24/7 en tu Space de Hugging Face."
