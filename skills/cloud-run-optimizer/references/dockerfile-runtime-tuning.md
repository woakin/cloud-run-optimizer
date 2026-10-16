# Dockerfile & Runtime Tuning for Cloud Run

Optimize container build structure, image size, and language runtime flags to minimize Cloud Run cold starts and memory footprint.

---

## 1. Universal Dockerfile Rules for Cloud Run

1. **Multi-Stage Builds:** Always separate build dependencies (`gcc`, `typescript`, `cargo`, `maven`, devDependencies) from the final runtime image.
2. **Layer Caching Order:** Copy lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `uv.lock`, `go.sum`) and install dependencies *before* copying application source code (`COPY . .`).
3. **Exec Form `CMD` / `ENTRYPOINT`:** Always use JSON array syntax (`CMD ["node", "server.js"]`), never shell form (`CMD node server.js` or `CMD npm start`). Shell form prevents `SIGTERM` from reaching your app during Cloud Run's 10-second shutdown window and adds unnecessary `npm`/`sh` startup overhead (~300–600ms slower cold start).
4. **Run as Non-Root:** Use `USER nonroot` or `USER node` for security hardening.
5. **Include `.dockerignore`:** Always exclude `node_modules`, `.git`, `.env*`, `dist`, `.next/cache`, `__pycache__`, `.venv`, and test fixtures so Cloud Build context uploads stay under a few megabytes.

---

## 2. Runtime-Specific Optimization Recipes

### A. Node.js / TypeScript (APIs & Microservices)

* **Memory Tuning:** Node's V8 garbage collector defaults to ~1.5–4 GB heap ceilings, which causes Cloud Run `512Mi` containers to crash with OOM before GC triggers. Set `--max-old-space-size` to **75%–80%** of container memory (e.g., `384` for `512Mi`, `768` for `1Gi`).
* **Never run `ts-node` or `tsx` in production:** Pre-compile TypeScript to JS during the build stage. `ts-node` adds 1–3 seconds to every cold start and 100+ MB of RAM.

```dockerfile
# Optimized Node.js Dockerfile for Cloud Run
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build && npm prune --omit=dev

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
# For a 512Mi Cloud Run container, cap V8 old space at 384MB
ENV NODE_OPTIONS="--max-old-space-size=384"
USER node
COPY --from=build --chown=node:node /app/package.json ./
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/dist ./dist
EXPOSE 8080
CMD ["node", "dist/index.js"]
```

---

### B. Next.js (Full-Stack SSR on Cloud Run)

* **Mandatory `output: 'standalone'`:** In `next.config.mjs`, set `output: 'standalone'`. This traces only the required files into `.next/standalone`, shrinking the image from >1.2 GB to ~150 MB and cutting cold starts by more than half.

```dockerfile
# Optimized Next.js Standalone Dockerfile for Cloud Run
FROM node:22-slim AS base
WORKDIR /app

FROM base AS deps
COPY package*.json ./
RUN npm ci

FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM base AS runner
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME="0.0.0.0"
ENV PORT=8080
USER node
COPY --from=builder /app/public ./public
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
EXPOSE 8080
CMD ["node", "server.js"]
```

---

### C. Python (FastAPI / Django / Flask)

* **Always set `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`:** Ensures logs stream immediately to Cloud Logging without buffering.
* **Worker & Thread Formula:**
  * **FastAPI / Async (ASGI):** Run **1 worker per vCPU** (`uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1`) and let the async event loop handle `--concurrency=80`. Multiple Uvicorn workers on 1 vCPU waste RAM and cause CPU context-switching overhead.
  * **Django / Flask (WSGI):** Use `gunicorn` with `gthread` worker class: `--workers 1 --threads 8 --timeout 0` on `1 vCPU` (and set Cloud Run `--concurrency=8`).
* **Lazy-load heavy imports:** Defer importing massive libraries (`pandas`, `torch`, `matplotlib`) until the specific endpoint that needs them is called, or load ML models globally during startup with `--cpu-boost` enabled.

```dockerfile
# Optimized Python (FastAPI + uv) Dockerfile for Cloud Run
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home appuser
USER appuser
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app /app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
```

---

### D. Go (Ultra-Low Cold Start & Memory)

* **Compile static binary + Distroless:** Produces a 10–25 MB container image that cold-starts on Cloud Run in **< 100ms** and runs comfortably on `256Mi` (or `128Mi`) of memory.
* **Set `GOMEMLIMIT`:** In Go 1.19+, set `GOMEMLIMIT=220MiB` (for a `256Mi` instance) to prevent the Go garbage collector from exceeding the container cgroup limit during traffic spikes.

```dockerfile
# Optimized Go Dockerfile for Cloud Run
FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /bin/server ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
ENV GOMEMLIMIT=420MiB
COPY --from=build /bin/server /server
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/server"]
```

---

### E. Java / JVM (Spring Boot / Quarkus on Cloud Run)

* **cgroup Memory Ceilings:** The JVM may not detect container memory limits in older JDKs. Always specify `-XX:MaxRAMPercentage=75.0` to leave headroom for OS and non-heap memory.
* **Startup JIT Optimization:** Enable CDS (Class Data Sharing) or spring AOT compilation (`native-image`) to slash startup time from 12s down to <1s.

```dockerfile
# Optimized Spring Boot Dockerfile for Cloud Run
FROM eclipse-temurin:21-jdk-jammy AS build
WORKDIR /workspace
COPY mvnw pom.xml ./
COPY .mvn .mvn
RUN ./mvnw dependency:go-offline
COPY src src
RUN ./mvnw clean package -DskipTests

FROM eclipse-temurin:21-jre-jammy AS runtime
WORKDIR /app
# Cap heap at 75% of container RAM and optimize for single-tenant container
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError"
RUN useradd -m appuser
USER appuser
COPY --from=build /workspace/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

---

### F. GPU & AI / ML Inference (PyTorch / vLLM / CUDA)

* **Model weights out of the container:** Never bake 10GB+ Hugging Face weights into the Docker image. Keep the image under 3GB with PyTorch and dependencies; mount weights from Cloud Storage FUSE at runtime (`HF_HOME=/mnt/models`).
* **Pin CUDA version:** Use NVIDIA CUDA runtime base images compatible with Cloud Run NVIDIA L4 drivers (CUDA 12.1+).

```dockerfile
# Optimized GPU Inference Dockerfile for Cloud Run (NVIDIA L4)
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/mnt/models \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app app/
EXPOSE 8080
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

### G. Static Assets & Reverse Proxy (Caddy / NGINX)

* **Listen on non-privileged port:** Cloud Run routes to `$PORT` (default 8080). Do not run as root on port 80.
* **Enable Gzip & Keepalives:** Use non-blocking event loops for high concurrency (`--concurrency=250`).

```dockerfile
# Minimal Caddy Reverse Proxy for Cloud Run
FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
```

