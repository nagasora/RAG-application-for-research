FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-jpn tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 paperpilot && useradd --uid 10001 --gid paperpilot --no-create-home --shell /usr/sbin/nologin paperpilot
COPY --chown=paperpilot:paperpilot app ./app
COPY --chown=paperpilot:paperpilot migrations ./migrations
COPY --chown=paperpilot:paperpilot alembic.ini .
# Local development bind-mounts these directories.  Create them in the image
# as well so a single Render API service can run inline ingestion before the
# application is moved to object storage and dedicated workers.
RUN mkdir -p /app/data/originals /app/data/assets && chown -R paperpilot:paperpilot /app/data
ENV PYTHONUNBUFFERED=1
USER 10001:10001
