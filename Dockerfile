FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL="sqlite:////data/chessfen.db" \
    RECOGNITION_MODEL_PATH="/app/models/yolo26m-finetuned.onnx"

# onnxruntime needs the GNU OpenMP runtime on slim Linux images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN uv sync --frozen --no-dev

# Bake the pinned detection weights (ADR-0001) with a checksum gate.
ARG YOLO26M_URL="https://huggingface.co/AndrewSpano/2d-chess-ocr/resolve/03d9df9fc14fade1a3579683fd0de215b3864ee1/yolo26m-finetuned.onnx"
ARG YOLO26M_SHA256="a8e78afa8e00cd7ee39a941f888327bd85a12c3cf5c2140a4fa882ea3f7abff7"
RUN python -c "import hashlib, os, urllib.request; path='/app/models/yolo26m-finetuned.onnx'; os.makedirs('/app/models', exist_ok=True); urllib.request.urlretrieve(os.environ['YOLO26M_URL'], path); digest=hashlib.sha256(open(path,'rb').read()).hexdigest(); assert digest == os.environ['YOLO26M_SHA256'], 'model checksum mismatch: ' + digest"

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["sh", "-c", "alembic upgrade head && uvicorn chessfen.app:app --host 0.0.0.0 --port 8000"]
