FROM python:3.12-slim

# Usuario no-root requerido por Hugging Face Spaces (UID 1000)
RUN useradd -m -u 1000 user
WORKDIR /app

# Instalar dependencias web e IA ligeras para la nube
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    fastapi uvicorn pydantic python-dotenv rich pypdf pymupdf \
    google-genai requests beautifulsoup4 huggingface_hub edge-tts

# Copiar código y carpeta data/ con permisos para UID 1000
COPY --chown=user:user . /app

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860

EXPOSE 7860

CMD ["python", "main.py", "--web"]
