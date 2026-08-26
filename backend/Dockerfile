FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng \
  && rm -rf /var/lib/apt/lists/*

ENV PIP_PREFER_BINARY=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .

# ultralytics pulls torch, and torch's default Linux wheels drag in the whole
# NVIDIA CUDA runtime — 16 packages, several GB — which is dead weight on any
# CPU-only host. Setting TORCH_INDEX_URL to the CPU index installs torch first
# so the requirements resolve against it.
#
# Empty by default, so an unparameterised build behaves exactly as before.
# docker-compose.yml passes the CPU index for local development.
ARG TORCH_INDEX_URL=""

RUN python -m pip install --upgrade pip setuptools wheel \
  && if [ -n "$TORCH_INDEX_URL" ]; then \
       pip install --default-timeout=1200 --retries=10 \
         --index-url "$TORCH_INDEX_URL" torch torchvision; \
     fi \
  && pip install --default-timeout=1200 --retries=10 -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
