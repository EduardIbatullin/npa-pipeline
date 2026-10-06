FROM python:3.12-slim

# Системные зависимости OCR: Tesseract с русским языком; libgomp/libgl — для PaddlePaddle и OpenCV
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-rus \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    FLAGS_use_mkldnn=false \
    HF_HOME=/app/cache/huggingface \
    PADDLE_PDX_CACHE_HOME=/app/cache/paddlex

WORKDIR /app

# PyTorch CPU-сборка (иначе pip тянет CUDA-колёса в несколько гигабайт)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Зависимости отдельно — слой кэшируется при неизменном pyproject.toml
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[web,ocr]"

# Каталоги рантайма (БД, PDF, кэш моделей); при volume mount перекроются
RUN mkdir -p data downloads cache

EXPOSE 8765

# host 0.0.0.0 — иначе извне контейнера UI недоступен
# (npa-web по умолчанию слушает только 127.0.0.1)
CMD ["uvicorn", "npa_pipeline.webapp:app", "--host", "0.0.0.0", "--port", "8765"]
