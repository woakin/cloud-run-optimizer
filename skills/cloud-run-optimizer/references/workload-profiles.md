# Cloud Run Workload Profiles & Sizing Matrix

Use this reference to select the optimal `cpu`, `memory`, `concurrency`, `execution-environment`, and `timeout` based on the project's architecture and runtime.

---

## 1. Master Sizing Matrix

| Profile | Typical Stacks | vCPU | Memory | `--concurrency` | `--cpu-boost` | `--cpu-throttling` | Execution Env | Timeout |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A. I/O-Bound Async API** | Go, Rust, Node.js (Express, Fastify, Hono), Python (FastAPI/Uvicorn async) | `1` | `256Mi` – `512Mi` | `80` – `100` | `true` | `true` (Request-based) | `gen1` or `gen2` | `60s` |
| **B. Sync / CPU-Bound API** | Python WSGI (Django, Flask), Ruby on Rails, PHP, Image/PDF/Crypto processing | `2` | `1Gi` – `2Gi` | `1` – `8` (match worker threads) | `true` | `true` | `gen2` | `60s` – `300s` |
| **C. Full-Stack SSR App** | Next.js (Standalone), Nuxt, Remix, Astro, SvelteKit, Angular SSR | `1` – `2` | `512Mi` – `1Gi` | `40` – `80` | `true` | `true` | `gen2` | `60s` |
| **D. AI / LLM / Streaming** | LLM Gateways, Agentic Workflows, SSE, WebSockets, MCP Servers | `1` – `2` | `512Mi` – `1Gi` | `15` – `50` | `true` | `true` | `gen2` | `300s` – `3600s` |
| **E. Batch / Cron / Queue** | DB migrations, ETL, report generation, scheduled jobs, heavy workers | `1` – `4` | `512Mi` – `4Gi` | **Use Cloud Run Jobs** (`--parallelism`) | N/A | N/A (Job billing) | `gen2` | `600s` – `86400s` |

---

## 2. Profile Deep-Dives

### Profile A: I/O-Bound Async APIs
* **Why high concurrency saves money:** When an instance waits on PostgreSQL, Firestore, or an external REST API, the CPU is mostly idle. Handling 80 concurrent requests on 1 instance costs **80x less** than spinning up 80 instances with `--concurrency=1`.
* **Memory sizing:**
  * **Go / Rust:** `256Mi` (or `128Mi` on Gen1) is usually sufficient unless buffering large payloads in memory.
  * **Node.js / Python FastAPI:** Start at `512Mi`. Monitor `run.googleapis.com/container/memory/utilizations` p99; downsize to `256Mi` if peak usage stays below 140 MB.

### Profile B: Synchronous or CPU-Bound Workloads
* **The GIL / Thread-per-Request bottleneck:** Synchronous frameworks (Django WSGI, Flask WSGI, Rails) can only process as many concurrent requests per container as `workers * threads`.
* **Never leave `--concurrency=80` on a synchronous server with 4 threads:** Requests 5 through 80 will queue inside the container's socket backlog, causing massive tail latency spikes while preventing Cloud Run's autoscaler from spinning up new instances.
* **Rule of thumb:** Set `--concurrency` equal to `workers * threads` (typically `4` to `8` for web apps, or `1` for pure CPU tasks like video encoding, FFmpeg, or Sharp/Pillow batch image resizing).
* **Always use `gen2`:** Gen2 provides full Linux compatibility, faster CPU performance without virtualization overhead on syscalls, and better multi-core scaling.

### Profile C: Full-Stack SSR Web Apps (Next.js, Nuxt, Remix)
* **Static asset offloading is mandatory:** Never let Cloud Run compute bill for serving `/_next/static/*`, `/assets/*`, images, or fonts. Either:
  1. Put **Cloud Load Balancing + Cloud CDN** in front of the Cloud Run service and send `Cache-Control: public, max-age=31536000, immutable` on hashed assets, OR
  2. Host static assets on Firebase Hosting / Cloud Storage CDN.
* **SSR CPU spikes:** React Server Components and HTML hydration are CPU-heavy during bursts. `1 vCPU` with `--cpu-boost` and `--concurrency=40` prevents event-loop lag. Upgrade to `2 vCPU` if SSR render times exceed 150ms.

### Profile D: AI, LLM Orchestration & Streaming (SSE / WebSockets)
* **Long-lived connections:** LLM token streaming and WebSockets hold connections open for 10–120+ seconds while consuming very little CPU.
* **Keep `--cpu-throttling` enabled:** Because Cloud Run considers a connection with an open HTTP/SSE/WebSocket request as "processing a request", CPU remains allocated while the stream is active, and scales to zero when disconnected.
* **Session Affinity:** Enable `--session-affinity` only if stateful in-memory caching per client improves token cache hit rates; otherwise keep stateless so any instance can serve any turn.
* **Timeouts:** Default Cloud Run timeout is `300s` (5 min). For deep research agents or long WebSocket sessions, increase `--timeout` up to `3600s` (60 min).

### Profile E: Background Workers, Queues & Scheduled Jobs
* **Anti-pattern:** Running a `while True` queue poller (BullMQ, Celery, Sidekiq) inside a Cloud Run Service with `--min-instances=1` and `--no-cpu-throttling`. You pay for 24/7 idle compute.
* **Cloud Run Native Alternatives:**
  1. **Push-based Queues (Best for Scale-to-Zero):** Push jobs to **Cloud Tasks** or **Pub/Sub** configured with an HTTP push subscription targeting a Cloud Run Service endpoint. Scales to zero when the queue is empty and scales up automatically with queue depth.
  2. **Scheduled / Batch Work:** Use **Cloud Scheduler** to trigger a **Cloud Run Job** (`gcloud run jobs`). You pay only for the exact seconds the job runs.
  3. **Pull-based Legacy Brokers (Kafka / RabbitMQ):** If you cannot switch to push, deploy as a **Cloud Run Worker Pool** (`gcloud run worker-pools`) rather than a dummy HTTP Service.
