# RAGOps Observatory — one image for the API, worker (DB or Arq/Redis), Streamlit dashboard and CLI.
#
#   docker build -t ragops-observatory:0.6.0 .
#   docker build --build-arg EXTRAS=server,dashboard,pgvector,redis,presidio,ragas,evidently -t ragops:full .
#
# Base images are build args so air-gapped / mirrored registries work:
#   --build-arg NODE_IMAGE=registry.acme.internal/node:22-slim --build-arg PYTHON_IMAGE=registry.acme.internal/python:3.12-slim
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim

# --- stage 1: build the React UI --------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS ui
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- stage 2: the Python service ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
ARG EXTRAS=server,dashboard,pgvector,redis,sso
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/ragops PATH=/opt/ragops/bin:$PATH
WORKDIR /app
RUN (id -u ragops >/dev/null 2>&1 || useradd --create-home --uid 10001 ragops) \
 && python3 -m venv /opt/ragops
COPY pyproject.toml README.md LICENSE ./
COPY ragops ./ragops
COPY --from=ui /web/dist ./ragops/server/ui
RUN pip install --upgrade pip >/dev/null && pip install ".[${EXTRAS}]" \
 && (case ",${EXTRAS}," in *,presidio,*) python -m spacy download en_core_web_sm || true;; esac) \
 && mkdir -p /data && chown ragops /data
USER ragops
ENV RAGOPS_DATABASE_URL=sqlite:////data/ragops.db
EXPOSE 8000 8501
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os,sys; sys.exit(0 if os.environ.get('RAGOPS_ROLE','api')!='api' else urllib.request.urlopen('http://localhost:8000/health').status!=200)"
# Override per service: `ragops worker`, `ragops dashboard`, `ragops demo`, `ragops db upgrade`
CMD ["ragops", "serve", "--host", "0.0.0.0", "--port", "8000"]
