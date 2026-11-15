FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY presets ./presets
RUN pip install --no-cache-dir . && useradd -r -u 1000 app && mkdir /data && chown app /data

ENV PT_DATA_DIR=/data PT_PRESETS_DIR=/app/presets PT_PORT=8000
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["practice-tracker"]
