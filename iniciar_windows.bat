@echo off
chcp 65001 >nul
title NOVA Study Hub - Lanzador Local
echo ====================================================================
echo   🎓 NOVA STUDY HUB - EJECUCION LOCAL GRATUITA (100%% AUTONOMA)
echo ====================================================================
echo.

if not exist ".venv" (
    echo [1/3] Creando entorno virtual de Python ^(.venv^)...
    python -m venv .venv
)

echo [2/3] Verificando dependencias...
call .venv\Scripts\activate.bat
pip install --quiet --disable-pip-version-check -r requirements.txt

if not exist ".env" (
    copy .env.example .env >nul
    echo [INFO] Se ha creado el archivo .env a partir de .env.example
)

echo [3/3] Iniciando servidor web en http://localhost:8080 ...
start "" http://localhost:8080
python main.py --web
pause
