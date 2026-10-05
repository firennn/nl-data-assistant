# Dashboard image. The Olist demo database is built into the image, so the app starts fast.
#
#   docker build -t nl-data-assistant .
#   docker run -p 8501:8501 -e LLM_API_KEY=... nl-data-assistant
#
# Build with --build-arg BUILD_DEMO_DB=0 to skip the demo database (uploads still work).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ARG BUILD_DEMO_DB=1
RUN if [ "$BUILD_DEMO_DB" = "1" ]; then \
        python -m data.build_db && rm -rf data/raw /root/.cache/kagglehub; \
    fi

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["sh", "-c", "streamlit run dashboard/app.py --server.headless=true --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
