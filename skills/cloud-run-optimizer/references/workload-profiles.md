# Cloud Run Workload Profiles & Sizing Matrix

Use this reference to select the optimal `cpu`, `memory`, `concurrency`, `execution-environment`, and `timeout` based on your project's architecture and runtime.

> [!TIP]
> You don't have to classify workloads manually: run `python3 ./scripts/evaluate-workload.py .` to scan your repository manifests, dependencies, and Dockerfile and automatically generate a tailored configuration and deployment command.

---

## 1. Master Sizing Matrix (10 Workload Profiles)

| Profile | Typical Stacks | vCPU | Memory | `--concurrency` | `--cpu-boost` | `--cpu-throttling` | Execution Env | Timeout | Key Flags |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A. I/O-Bound Async API** | Go, Rust, Node.js (Express, Fastify, Hono), Python (FastAPI/Uvicorn) | `1` | `256Mi` – `512Mi` | `80` – `100` | `true` | `true` (Request-based) | `gen1` / `gen2` | `60s` | High concurrency |
| **B. Sync / CPU-Bound API** | Python WSGI (Django, Flask), Ruby on Rails, PHP, Image/PDF processing | `1` – `2` | `1Gi` – `2Gi` | `1` – `8` (match threads) | `true` | `true` | `gen2` | `60s` – `120s` | Match concurrency to threads |
| **C. Full-Stack SSR App** | Next.js (Standalone), Nuxt, Remix, Astro, SvelteKit, Angular SSR | `1` – `2` | `512Mi` – `1Gi` | `40` – `80` | `true` | `true` | `gen2` | `60s` | `output: 'standalone'` |
| **D. AI / LLM / Streaming** | LLM Gateways, LangChain, LlamaIndex, MCP Servers, SSE token streaming | `1` – `2` | `512Mi` – `1Gi` | `20` – `50` | `true` | `true` | `gen2` | `300s` – `1800s` | Request-based billing stays warm |
| **E. Batch / Cron / Queue** | DB migrations, scheduled tasks, asynchronous job queues | `1` – `4` | `512Mi` – `4Gi` | **Cloud Run Jobs** | N/A | N/A (Job billing) | `gen2` | Up to `86400s` | Cloud Run Jobs or Worker Pools |
| **F. GPU / ML Inference** | vLLM, Ollama, Hugging Face TGI, PyTorch, Whisper, ComfyUI | `4` – `8` | `16Gi` – `32Gi` | `1` – `16` | N/A | `false` (Always allocated) | `gen2` | `300s` – `1800s` | `--gpu=1 --gpu-type=nvidia-l4` + GCS volume mount |
| **G. Enterprise JVM & .NET** | Java (Spring Boot, Quarkus, Micronaut), Kotlin, ASP.NET Core | `1` – `2` | `1Gi` – `2Gi` | `40` – `80` | `true` (Mandatory) | `true` | `gen2` | `60s` | `-XX:MaxRAMPercentage=75.0` |
| **H. Event & Webhook Bursts** | Stripe/GitHub webhooks, Eventarc audit logs, Pub/Sub push | `1` | `256Mi` – `512Mi` | `10` – `30` | `true` | `true` | `gen1` / `gen2` | `15s` – `30s` | Bounded concurrency protects DB |
| **I. Reverse Proxy & Gateway** | NGINX, Caddy, Envoy, Traefik, lightweight Go reverse proxies | `0.25` – `0.5` | `128Mi` – `256Mi` | `200` – `250` | `false` | `true` | `gen1` | `60s` | Fractional CPU, sub-100ms cold start |
| **J. WebSockets & Real-Time** | Collaborative editing (Yjs), multiplayer backends, interactive sessions | `1` – `2` | `512Mi` – `1Gi` | `50` – `100` | `true` | `true` | `gen2` | `3600s` (Max) | `--session-affinity` enabled |

---

## 2. Profile Deep-Dives

### Profile A: I/O-Bound Async APIs (Go, Node.js, FastAPI, Rust)
* **Why high concurrency saves money:** When an instance waits on PostgreSQL, Firestore, or external REST APIs, CPU utilization is negligible. Handling 80 concurrent requests per instance costs up to **80x less** than spinning up 80 separate instances with `--concurrency=1`.
* **Memory sizing:** Go/Rust run reliably on `256Mi` (or `128Mi` on Gen1). Node.js and FastAPI should start at `512Mi`.

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
* **Avoid continuous polling loops in HTTP Services:** Never run a `while True` queue poller (Celery, BullMQ) with `min-instances=1` and `--no-cpu-throttling` inside an HTTP Service.
* **Cloud Run native primitives:**
  1. **Cloud Run Jobs (`gcloud run jobs`):** For batch workloads, database migrations, and cron tasks triggered by Cloud Scheduler. Pay only for execution duration.
  2. **Push Queues:** Use Cloud Tasks or Pub/Sub push to invoke a standard Cloud Run Service endpoint, preserving scale-to-zero.
  3. **Cloud Run Worker Pools:** For continuous Kafka/RabbitMQ consumers that don't expose HTTP endpoints.

### Profile F: GPU & AI / ML Inference (NVIDIA L4)
* **Hardware:** Cloud Run supports NVIDIA L4 GPUs (`--gpu=1 --gpu-type=nvidia-l4`).
* **Billing requirement:** GPU instances **require** CPU always allocated (`--no-cpu-throttling`) and the `gen2` execution environment.
* **Model weight caching via Volume Mounts:**
  * Do NOT download 10GB+ Hugging Face weights on container startup over HTTP.
  * Use Cloud Storage FUSE volume mounts (`--add-volume=name=model-bucket,type=cloud-storage,bucket=MY_MODELS --add-volume-mount=volume=model-bucket,mount-path=/models`) or persistent SSD volumes to cache model weights across revision startups.
* **Concurrency:** Keep low (`1` to `8`) to avoid exhausting GPU VRAM (24GB on L4).

### Profile G: Enterprise JVM & Managed Runtimes (.NET, Spring Boot, Quarkus)
* **Container cgroup memory limit:** Set `-XX:MaxRAMPercentage=75.0` in `JAVA_TOOL_OPTIONS` (or `.NET` garbage collection limits) to prevent JVM heap allocations from triggering container cgroup OOM kills.
* **Cold start optimization:** Mandatory Startup CPU Boost (`--cpu-boost`) temporarily doubles CPU allocations during JIT compilation and class loading at \$0 idle cost.
* **Memory baseline:** Allocate at least `1Gi` – `2Gi`. Consider GraalVM Native Image or Quarkus for sub-second startup.

### Profile H: High-Burst Event & Webhook Receivers (Stripe, GitHub, Eventarc)
* **Downstream protection:** High-volume webhooks (e.g., flash sales, bulk Stripe events) can spawn hundreds of container instances within seconds.
* **Capping concurrency:** Set `--concurrency=10-30` and `--max-instances=30-50` to throttle ingress pressure into your transactional database.
* **Idempotency:** Webhook deliverers retry aggressively on timeouts. Keep request timeout low (`15s` – `30s`) and record message IDs in Memorystore/Firestore.

### Profile I: Static SPA, Reverse Proxy & Edge Gateway (NGINX, Caddy, Envoy)
* **Ultra-low footprint:** Reverse proxies and static servers use very little memory and non-blocking epoll sockets.
* **Configuration:** `0.25` – `0.5 vCPU`, `128Mi` – `256Mi`, `--concurrency=200-250`, execution environment `gen1` for sub-100ms cold starts.

### Profile J: Stateful WebSockets & Interactive Sessions
* **Sticky routing:** Enable `--session-affinity` (`run.googleapis.com/sessionAffinity: "true"`) so requests from the same client session are routed to the same container instance.
* **Max timeout:** Set `--timeout=3600s` (60 minutes is the maximum timeout on Cloud Run).
* **Keepalives & Heartbeats:** Implement client/server WebSocket ping-pongs every 30–45 seconds to keep intermediary proxies from closing idle TCP sockets.
