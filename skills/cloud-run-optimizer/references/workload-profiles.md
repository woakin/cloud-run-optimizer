# Cloud Run Workload Profiles & Sizing Matrix

Use this reference to select the optimal `cpu`, `memory`, `concurrency`, `execution-environment`, and `timeout` based on your project's architecture and runtime.

> [!TIP]
> You don't have to classify workloads manually: run `python3 ./scripts/evaluate-workload.py .` to scan your repository manifests, dependencies, and Dockerfile and automatically generate a tailored configuration and deployment command.

---

## 1. Master Sizing Matrix (10 Workload Profiles)

| Profile | Typical Stacks | vCPU | Memory | `--concurrency` | `--cpu-boost` | `--cpu-throttling` | Execution Env | Timeout | Key Flags & Constraints |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A. I/O-Bound Async API** | Go, Rust, Node.js (Express, Fastify, Hono), Python (FastAPI/Uvicorn) | `1` | `512Mi` (`256Mi` on `gen1`) | `80` – `100` | `true` | `true` (Request-based) | `gen2` (`gen1` if `<512Mi`) | `60s` | High concurrency |
| **B. Sync / CPU-Bound API** | Python WSGI (Django, Flask), Ruby on Rails, PHP, Image/PDF processing | `1` – `2` | `1Gi` – `2Gi` | `1` – `8` (match threads) | `true` | `true` | `gen2` | `60s` – `120s` | Match concurrency to threads |
| **C. Full-Stack SSR App** | Next.js (Standalone), Nuxt, Remix, Astro, SvelteKit, Angular SSR | `1` – `2` | `512Mi` – `1Gi` | `40` – `80` | `true` | `true` | `gen2` | `60s` | `output: 'standalone'` |
| **D. AI / LLM / Streaming** | LLM Gateways, LangChain, LlamaIndex, MCP Servers, SSE token streaming | `1` – `2` | `512Mi` – `1Gi` | `20` – `50` | `true` | `true` | `gen2` | `300s` – `1800s` | Request-based billing stays warm |
| **E. Batch / Cron / Queue** | DB migrations, scheduled tasks, asynchronous job queues | `1` – `4` | `512Mi` – `4Gi` | **Cloud Run Jobs** (`--tasks`) | N/A | N/A (Job billing) | `gen2` | Up to `86400s` | `gcloud run jobs deploy` or Push Service |
| **F. GPU / ML Inference** | vLLM, Ollama, Hugging Face TGI, PyTorch, Whisper, ComfyUI | `4` – `8` | `16Gi` – `32Gi` | `1` – `8` | N/A | `false` (Always allocated) | `gen2` | `300s` – `1800s` | `--gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy` |
| **G. Enterprise JVM & .NET** | Java (Spring Boot, Quarkus, Micronaut), Kotlin, ASP.NET Core | `1` – `2` | `1Gi` – `2Gi` | `40` – `80` | `true` (Mandatory) | `true` | `gen2` | `60s` | `-XX:MaxRAMPercentage=75.0` |
| **H. Event & Webhook Bursts** | Stripe/GitHub webhooks, Eventarc audit logs, Pub/Sub push | `1` | `512Mi` | `10` – `30` | `true` | `true` | `gen2` | `15s` – `30s` | Bounded concurrency protects DB |
| **I. Reverse Proxy & Gateway** | NGINX, Caddy, Envoy, Traefik, lightweight Go reverse proxies | `1` | `256Mi` – `512Mi` | `200` – `250` | `false` | `true` | `gen1` (`256Mi`) or `gen2` (`512Mi`) | `60s` | **Must use `>= 1 vCPU`** for `concurrency > 1` |
| **J. WebSockets & Real-Time** | Collaborative editing (Yjs), multiplayer backends, interactive sessions | `1` – `2` | `512Mi` – `1Gi` | `50` – `100` | `true` | `true` | `gen2` | `3600s` (Max) | `--session-affinity` enabled |

> [!IMPORTANT]
> **Hard GCP Cloud Run Platform Constraints:**
> 1. **Fractional vCPU (`< 1 vCPU`) Forces `concurrency=1`:** Setting `--cpu=0.08` through `--cpu=0.5` is only supported on `gen1` and **forces `--concurrency=1`**. Never use fractional CPUs for web APIs or proxies—`1 vCPU` handling 80–200 concurrent requests is up to **100x cheaper** under load than `0.5 vCPU` forced to 1 request per instance.
> 2. **Gen2 Minimum Resource Floor:** `--execution-environment=gen2` requires **at least `1 vCPU` and `512Mi` memory**. If you want a `256Mi` or `128Mi` micro-container (e.g. lightweight Go/Rust binary), you must use `gen1`.
> 3. **NVIDIA L4 GPU Minimums:** `--gpu=1 --gpu-type=nvidia-l4` requires **at least `4 vCPU` and `16Gi` memory**, `gen2`, and `--no-cpu-throttling`.

---

## 2. Profile Deep-Dives

### Profile A: I/O-Bound Async APIs (Go, Node.js, FastAPI, Rust)
* **Why high concurrency saves money:** When an instance waits on PostgreSQL, Firestore, or external REST APIs, CPU utilization is negligible. Handling 80 concurrent requests per instance costs up to **80x less** than spinning up 80 separate instances with `--concurrency=1`.
* **Memory sizing:** Go/Rust run reliably on `256Mi` (with `gen1`) or `512Mi` (with `gen2`). Node.js and FastAPI should start at `512Mi`.

### Profile B: Synchronous or CPU-Bound Workloads (Django, Flask, Rails, PHP)
* **The GIL / Thread-per-Request bottleneck:** Synchronous frameworks can only process as many concurrent requests per container as `workers * threads`.
* **Do not leave `--concurrency=80`:** If Gunicorn has 4 threads and concurrency is 80, requests 5–80 queue in the container backlog, causing severe latency spikes while Cloud Run's autoscaler fails to spin up additional instances.
* **Formula:** Set `--concurrency = workers * threads` (usually `4` to `8`). Use `gen2` for full Linux kernel compatibility and faster multi-core syscalls.

### Profile C: Full-Stack SSR Web Apps (Next.js, Nuxt, Remix)
* **Static asset offloading is mandatory:** Never let dynamic Cloud Run compute serve `/_next/static/*` or hashed assets. Route static assets through Cloud CDN / Firebase Hosting or Cloud Storage.
* **Image optimization & Standalone mode:** Enable `output: 'standalone'` in `next.config.mjs` to shrink container size by ~70%. Set `--cpu-boost` to accelerate server-side React hydration on cold start.

### Profile D: AI, LLM Orchestration & Streaming (SSE / LangChain / MCP)
* **Token streaming keeps CPU active:** Under request-based billing (`--cpu-throttling`), an open HTTP connection streaming SSE tokens is recognized by Cloud Run as active processing. The container receives full CPU during generation and scales to zero when idle.
* **Timeouts:** Increase `--timeout` to `300s` – `1800s` to prevent Cloud Run from dropping long agentic chains or reasoning runs.

### Profile E: Background Workers, Queues & Scheduled Jobs
* **Avoid continuous polling loops in HTTP Services:** Never run a `while True` queue poller (Celery, BullMQ) with `min-instances=1` and `--no-cpu-throttling` inside an HTTP Service. And never set `min-instances=0` on a pull worker inside an HTTP Service, as it will immediately scale to zero and stop pulling jobs.
* **Cloud Run native primitives:**
  1. **Cloud Run Jobs (`gcloud run jobs deploy`):** For batch workloads, database migrations, and cron tasks triggered by Cloud Scheduler. Supports up to `86400s` (24h) timeout and pays only for execution duration.
  2. **Push Queues (Best for Scale-to-Zero Workers):** Use Cloud Tasks or Pub/Sub HTTP push to invoke a standard Cloud Run Service endpoint with `--cpu-throttling`, preserving scale-to-zero.
  3. **Cloud Run Worker Pools (`gcloud run worker-pools deploy`):** For continuous Kafka/RabbitMQ pull consumers that don't expose HTTP endpoints.

### Profile F: GPU & AI / ML Inference (NVIDIA L4)
* **Hardware & Minimum Specs:** Cloud Run supports NVIDIA L4 GPUs (`--gpu=1 --gpu-type=nvidia-l4`, 24GB VRAM). Enforces a minimum of **`4 vCPU` and `16Gi` memory** (8 vCPU / 32Gi recommended for fast weight loading).
* **Billing & Zonal Redundancy Cost Optimization:**
  * GPU instances **require** CPU always allocated (`--no-cpu-throttling`) and `gen2`.
  * **Always pass `--no-gpu-zonal-redundancy`** (`run.googleapis.com/gpu-zonal-redundancy-disabled: "true"`) unless multi-zone failover is strictly mandated—this reduces hourly GPU cost by **~30–38%** and halves regional GPU quota consumption.
* **Model weight caching via Volume Mounts (Beware `/tmp` RAM Cache):**
  * Do NOT download 10GB+ Hugging Face weights over HTTP on startup.
  * Mount weights via Cloud Storage FUSE (`--add-volume=name=models,type=cloud-storage,bucket=MY_MODELS --add-volume-mount=volume=models,mount-path=/mnt/models`).
  * **Trap:** Do *not* enable large GCSFuse `file-cache:max-size-mb` options unless you increase container RAM accordingly, because local caching writes to in-memory `tmpfs` and will OOM-kill a 16Gi container.

### Profile G: Enterprise JVM & Managed Runtimes (.NET, Spring Boot, Quarkus)
* **Container cgroup memory limit:** Set `-XX:MaxRAMPercentage=75.0` in `JAVA_TOOL_OPTIONS` (or `.NET` garbage collection limits) to prevent JVM heap allocations from triggering container cgroup OOM kills.
* **Cold start optimization:** Mandatory Startup CPU Boost (`--cpu-boost`) temporarily doubles CPU allocations during JIT compilation and class loading at \$0 idle cost. Configure a `startupProbe` with a 30–60s grace window.
* **Memory baseline:** Allocate at least `1Gi` – `2Gi`. Consider GraalVM Native Image, Spring AOT, or Quarkus for sub-second startup.

### Profile H: High-Burst Event & Webhook Receivers (Stripe, GitHub, Eventarc)
* **Downstream protection:** High-volume webhooks (e.g., flash sales, bulk Stripe events) can spawn hundreds of container instances within seconds.
* **Capping concurrency:** Set `--concurrency=10-30` and `--max-instances=30-50` to throttle ingress pressure into your transactional database.
* **Idempotency:** Webhook deliverers retry aggressively on timeouts. Keep request timeout low (`15s` – `30s`) and record message IDs in Memorystore/Firestore.

### Profile I: Static SPA, Reverse Proxy & Edge Gateway (NGINX, Caddy, Envoy)
* **Why `1 vCPU` is mandatory:** While reverse proxies use minimal CPU per request, Cloud Run **forces `concurrency=1` if `cpu < 1`**. Always allocate `1 vCPU` so you can set `--concurrency=200-250`.
* **Configuration:** `1 vCPU`, `256Mi` (with `gen1` for sub-100ms cold starts) or `512Mi` (with `gen2`), `--concurrency=200-250`.

### Profile J: Stateful WebSockets & Interactive Sessions
* **Sticky routing:** Enable `--session-affinity` (`run.googleapis.com/sessionAffinity: "true"`) so requests from the same client session are routed to the same container instance.
* **Max timeout:** Set `--timeout=3600s` (60 minutes is the maximum timeout on Cloud Run).
* **Keepalives & Heartbeats:** Implement client/server WebSocket ping-pongs every 30–45 seconds to keep intermediary proxies from closing idle TCP sockets.
