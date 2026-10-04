FROM python:3.11-slim

WORKDIR /app

ENV PIP_PREFER_BINARY=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .

# torch's default Linux wheels drag in the whole NVIDIA CUDA runtime — several
# GB — which is dead weight on any CPU-only host. Setting TORCH_INDEX_URL to the
# CPU index installs torch first so the requirements resolve against it.
# docker-compose.yml passes the CPU index for local development.
ARG TORCH_INDEX_URL=""

RUN python -m pip install --upgrade pip setuptools wheel \
  && if [ -n "$TORCH_INDEX_URL" ]; then \
       pip install --default-timeout=1200 --retries=10 \
         --index-url "$TORCH_INDEX_URL" "$(grep -E '^torch==' requirements.txt)"; \
     fi \
  && pip install --default-timeout=1200 --retries=10 -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
