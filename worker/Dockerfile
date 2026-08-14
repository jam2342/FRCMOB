FROM python:3.11-slim

WORKDIR /app

# deno: yt-dlp needs a JS runtime to solve YouTube's player challenges;
# without one YouTube hides every DASH format and only the 360p premuxed
# stream is visible, which starves tracking and bumper OCR.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-eng curl unzip ca-certificates \
  && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- --yes \
  && rm -rf /var/lib/apt/lists/*

ENV PIP_PREFER_BINARY=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .

RUN python -m pip install --upgrade pip setuptools wheel \
  && pip install --default-timeout=1200 --retries=10 -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
