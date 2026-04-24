FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-noto-core \
    libcairo2 \
    libffi8 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

RUN useradd --create-home --uid 10001 renderer \
  && mkdir -p /app/doc_renderer_service/themes

COPY --chmod=0444 __init__.py app.py main.py rendering.py /app/doc_renderer_service/
COPY --chmod=0444 themes/*.css /app/doc_renderer_service/themes/

USER renderer

EXPOSE 8080

CMD ["uvicorn", "doc_renderer_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
