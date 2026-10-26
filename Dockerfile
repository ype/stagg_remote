# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
ENV POETRY_VIRTUALENVS_IN_PROJECT=true POETRY_NO_INTERACTION=1 PIP_NO_CACHE_DIR=1
RUN pip install poetry==2.5.1
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY src ./src
RUN poetry build -f wheel && .venv/bin/pip install --no-deps dist/*.whl

FROM python:3.12-slim AS runtime
# tzdata: the kettle schedule runs on local wall-clock time, so TZ must resolve.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 STAGG_STATE_FILE=/data/state.json TZ=Europe/London
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "--factory", "stagg_remote.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
