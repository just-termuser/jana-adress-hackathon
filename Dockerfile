# syntax=docker/dockerfile:1
# Jana Extract: офлайн-сервис. Справочник уже собран и лежит в data/,
# PBF в образ не попадает. Сборка справочника — отдельная стадия ниже.

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY jana_extract ./jana_extract
COPY static ./static
COPY data/reference.json.gz data/kato.json.gz ./data/
RUN useradd --system --uid 10001 jana && python -c "from jana_extract.reference import get_reference; print(get_reference().meta['counts'])"
USER jana
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "jana_extract.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# Пересборка справочника из PBF: docker compose --profile build-reference run --rm build-reference
FROM runtime AS reference-builder
USER root
# pyosmium линкуется с expat, которого нет в slim-образе.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1     && rm -rf /var/lib/apt/lists/*
COPY requirements-build.txt .
RUN pip install -r requirements-build.txt
COPY tools ./tools
ENTRYPOINT ["python", "tools/build_reference.py"]
