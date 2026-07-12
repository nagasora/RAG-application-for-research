FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-jpn tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 paperpilot && useradd --uid 10001 --gid paperpilot --no-create-home --shell /usr/sbin/nologin paperpilot
COPY --chown=paperpilot:paperpilot app ./app
COPY --chown=paperpilot:paperpilot migrations ./migrations
COPY --chown=paperpilot:paperpilot alembic.ini .
ENV PYTHONUNBUFFERED=1
USER 10001:10001
