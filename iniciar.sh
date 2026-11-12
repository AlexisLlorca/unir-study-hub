#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "===================================================================="
echo "  🎓 NOVA STUDY HUB — EJECUCIÓN LOCAL GRATUITA (100% AUTÓNOMA)"
echo "===================================================================="

if [ ! -d ".venv" ]; then
    echo "[1/3] Creando entorno virtual de Python (.venv)..."
    python3 -m venv .venv
fi

echo "[2/3] Verificando dependencias..."
source .venv/bin/activate
pip install --quiet --disable-pip-version-check -r requirements.txt

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "[INFO] Se ha creado el archivo .env a partir de .env.example"
fi

echo "[3/3] Iniciando servidor web en http://localhost:8080 ..."
python main.py --web
