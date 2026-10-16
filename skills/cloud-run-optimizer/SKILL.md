---
name: cloud-run-optimizer
description: >-
  Audits, configures, deploys, and optimizes applications on Google Cloud (GCP),
  Cloud Run (services, jobs, worker pools), and Cloud SQL for maximum performance,
  low cold-start latency, and minimal cost ($0 idle). Use whenever a user asks to
  deploy an app to Google Cloud / GCP, configure or optimize Cloud Run or Cloud SQL,
  write Dockerfiles for GCP, tune concurrency/CPU/memory/autoscaling/GPU flags,
  fix database connection exhaustion, or lower their Google Cloud bill.
license: Apache-2.0
metadata:
  category: cloud-infrastructure
  platform: google-cloud-run
  version: "1.1.0"
---

# Google Cloud Run Performance & Cost Optimizer

Follow this workflow whenever creating, migrating, or optimizing an application for **Google Cloud Run** to prevent serverless anti-patterns, minimize cold starts, and eliminate idle billing.

## How to Interact with the User ("Do-It-For-Me" Mode)

Many users are builders or "vibe-coders" who want their app running fast and cheap without learning GCP jargon. Unless the user is explicitly asking for enterprise Terraform/Knative YAML:
1. **Fix the files directly — don't just lecture:** If the repo is missing a `Dockerfile` or `.dockerignore`, has `output: 'standalone'` missing in `next.config.*`, or hardcodes `app.listen(3000)` / `127.0.0.1`, **edit or create those files for them**.
2. **Stop SQLite data loss before it happens:** If the app uses local SQLite (`better-sqlite3`, `sqlite3`, Prisma `provider = "sqlite"`, `*.db` files), warn them in plain English that **Cloud Run wipes local files every time the server sleeps**, and offer to wire up a free cloud Postgres (Neon/Supabase), Cloud SQL, or Turso (libSQL).
3. **Explain the bill in plain English:** Always translate flags into real-world dollar impact (e.g., *"Your app will cost **$0/month when nobody is visiting**, can handle **80 users per container at once**, and has a **safety cap (`--max-instances=10`)** so a traffic spike or bot attack won't give you a surprise bill"*).
4. **Default to a 1-command deploy:** Give beginners a single copy-pasteable `gcloud run deploy --source=.` command (plus the 1-time `gcloud services enable` command if it's a fresh project). Only output Knative YAML or Terraform if they ask for Infrastructure-as-Code or CI/CD templates.

---

## Step 1: Audit Current State & Classify the Workload

1. **If auditing an existing local repository**, run the automated scanner and classifier:
   ```bash
   # Runs code anti-pattern scan AND automated workload profile classification:
   bash ./scripts/audit-codebase.sh .
   
   # Or run the evaluator directly to output JSON, YAML, or deploy commands:
   python3 ./scripts/evaluate-workload.py . --format terminal
   ```
2. **If auditing a live deployed Cloud Run service** (and `gcloud` is authenticated), run:
   ```bash
   bash ./scripts/audit-live-service.sh <SERVICE_NAME> <REGION> [PROJECT_ID]
   ```
3. **Select the right Cloud Run primitive** before configuring resources:
   * **Cloud Run Service:** HTTP/gRPC APIs, web apps, webhooks, WebSockets, or SSE streaming. Scales to zero.
   * **Cloud Run Job:** Scheduled tasks (cron), batch processing, DB migrations, or queue batches that run to completion and exit. Never run cron loops inside a Service.
   * **Cloud Run Worker Pool:** Continuous pull-based consumers (Kafka, RabbitMQ, NATS) with no HTTP endpoint.

4. **Classify the Service into one of 10 Workload Profiles** (see [references/workload-profiles.md](./references/workload-profiles.md) for full sizing matrices):
   * **Profile A — I/O-Bound Async API:** Go, Node.js (Express/Fastify/Hono), Python ASGI (FastAPI/Uvicorn), Rust.
   * **Profile B — Sync / CPU-Bound API:** Python WSGI (Django/Flask), Ruby/Rails, PHP, PDF/image/video processing.
   * **Profile C — Full-Stack SSR Web App:** Next.js, Nuxt, Remix, SvelteKit, Astro, Angular SSR.
   * **Profile D — AI / LLM / Streaming:** Agentic backends, LLM SSE streaming, WebSockets, MCP servers.
   * **Profile E — Background Queue / Cron:** Tasks triggered asynchronously via Cloud Tasks, Pub/Sub, or Cloud Scheduler.
   * **Profile F — GPU & AI / ML Inference:** vLLM, Ollama, PyTorch, Whisper, Hugging Face TGI with NVIDIA L4 GPU.
   * **Profile G — Enterprise JVM & Managed Runtimes:** Java (Spring Boot, Quarkus, Micronaut), Kotlin, ASP.NET Core.
   * **Profile H — High-Burst Event & Webhook Receivers:** Stripe, GitHub, Eventarc, Pub/Sub push.
   * **Profile I — Static SPA, Reverse Proxy & Edge Gateway:** NGINX, Caddy, Envoy, lightweight Go forwarders.
   * **Profile J — Stateful WebSockets & Interactive Sessions:** Real-time multiplayer, Yjs collaborative editing, terminal sessions.

---

## Step 2: Enforce Serverless Code Guardrails

Before deploying or generating infrastructure configs, verify the application code satisfies these rules (detailed patterns and fixes in [references/code-antipatterns.md](./references/code-antipatterns.md)):

1. **Bind to `0.0.0.0:$PORT`:** Read `PORT` from the environment (default `8080`) and bind to `0.0.0.0`, never `127.0.0.1` or `localhost`, and never hardcode port `3000`/`5000`.
2. **No SQLite or Unbacked Local Disk:** Container filesystems are ephemeral and wiped on scale-to-zero. Never store SQLite databases (`dev.db`, `.sqlite`) or user uploads on local disk without an external database (Cloud SQL, Firestore, Supabase/Neon/Turso) or Cloud Storage (GCS) volume.
3. **No Work After HTTP Response:** Under request-based billing (`--cpu-throttling`), CPU is throttled to near-zero immediately after the HTTP response finishes. Any fire-and-forget promises, threads, or `BackgroundTasks` will freeze or fail. Offload async work to **Cloud Tasks** or **Pub/Sub** before returning the response.
4. **No In-Process Timers (`setInterval`, `node-cron`, `APScheduler`):** Instances scale to zero or multiply unpredictably. Extract scheduled jobs into **Cloud Scheduler** invoking a **Cloud Run Job** or an authenticated HTTP endpoint.
5. **Ephemeral `/tmp` Is In-Memory (`tmpfs`):** Writing files to `/tmp` consumes the container's memory limit and causes OOM crashes if not deleted. Always delete temporary files in a `finally` block, or stream uploads/downloads directly to **Cloud Storage (GCS)** using Signed URLs.
6. **Stateless Memory:** Never store user sessions, rate-limit counters, or upload state in process memory. Use **Firestore**, **Memorystore (Valkey/Redis)**, or signed cookies/JWTs.
7. **Graceful Shutdown (`SIGTERM`):** Cloud Run sends `SIGTERM` and gives **10 seconds** before `SIGKILL`. Register a `SIGTERM` handler to stop accepting new requests, finish in-flight requests, and close DB pools cleanly.
8. **Global Connection Pools & Cloud SQL Limits:** Initialize DB/HTTP clients at module scope (outside request handlers) so warm instances reuse connections. Enforce Cloud SQL connection limits and sizing formulas (see [references/cloud-sql.md](./references/cloud-sql.md)) using:
   $$\text{pool\_size} \le \left\lfloor \frac{\text{DB\_MAX\_CONNECTIONS} \times 0.8}{\text{max\_instances}} \right\rfloor$$
   Never run synchronous database queries in health/startup probes or block container boot on DB connection establishment.

---

## Step 3: Optimize the Container & Runtime (`Dockerfile`)

Cold start duration is directly proportional to image pull size and runtime initialization time. Apply the rules in [references/dockerfile-runtime-tuning.md](./references/dockerfile-runtime-tuning.md):

* **Multi-stage builds & minimal base images:** Use `gcr.io/distroless/static-debian12:nonroot` (Go/Rust), `node:22-slim` (Node.js), or `python:3.12-slim` (Python). Avoid full OS images (`ubuntu`, default `node:22`, `python:3.12`). Always create a `.dockerignore` excluding `node_modules`, `.git`, `.env`, and `.next`.
* **Never run dev servers in production:** Replace `npm run dev`, `next dev`, or `uvicorn --reload` with production builds (`node server.js`, `uvicorn main:app`), which use 3–5x less RAM and start 4x faster.
* **Match server workers to vCPUs:**
  * **Node.js / Go / Rust:** Single process per container (let Cloud Run scale horizontally across instances). Set `--max-old-space-size` in Node.js to ~75–80% of container memory.
  * **Python (FastAPI/Uvicorn):** `1 worker` per vCPU (`--workers 1` for 1 vCPU) with high `--concurrency` (40–80) if 100% async I/O.
  * **Python (Django/Flask WSGI with Gunicorn):** Use `--workers 1 --threads 4` or `2` workers on `1–2 vCPU` with `--concurrency` matching total threads (`4–8`).
  * **Next.js:** Always set `output: 'standalone'` in `next.config.js` to cut image size by ~70% and reduce cold-start time.

---

## Step 4: Tune Cloud Run Configuration for Performance & Cost

Select the exact flags for `gcloud run deploy`, Knative YAML (`examples/`), or Terraform (`examples/main.tf`). For deep-dive networking and billing rules, read [references/cost-and-networking.md](./references/cost-and-networking.md).

### Quick Configuration Matrix

| Parameter | Flag / YAML Field | Rule for Cost & Performance |
| :--- | :--- | :--- |
| **CPU Billing** | `--cpu-throttling` (default) | **Always prefer Request-based billing** (`--cpu-throttling`) so you pay \$0 for CPU between requests. Only use `--no-cpu-throttling` (Instance-based) if the service has sustained >40% utilization 24/7, uses GPUs, or maintains stateful WebSockets. |
| **Startup CPU Boost** | `--cpu-boost` | **Enable on every production service.** Temporarily boosts CPU during instance startup for faster cold starts at **zero idle cost** (far cheaper than `min-instances`). |
| **Concurrency** | `--concurrency` | **80–100** for async I/O (Go, Node, FastAPI). **40–80** for SSR (Next.js). **1–8** for synchronous/CPU-bound workloads. Note: GCP forces `concurrency=1` whenever `cpu < 1`. |
| **Min Instances** | `--min-instances` | **0** for dev/staging and most prod services with `--cpu-boost`. Set to **1** only for strict P99 latency SLAs on heavy runtimes (Java/JVM, large ML models). |
| **Max Instances** | `--max-instances` | Always set a sensible ceiling (e.g., `5–30`) to prevent runaway cost spikes and protect downstream databases from connection exhaustion. |
| **Execution Env** | `--execution-environment=gen2` | Use `gen2` for CPU-intensive workloads, network file systems (GCS FUSE / Filestore), or faster Linux syscall performance (requires `>= 1 vCPU` and `>= 512Mi` memory). Use `gen1` for sub-1-vCPU microservices. |
| **VPC Egress** | `--network` & `--subnet` | **Use Direct VPC Egress** instead of Serverless VPC Access Connectors. Connectors run always-on e2/f1 VMs that cost \$15–\$35+/month even when your service scales to zero. |
| **Startup Probes** | `startupProbe` / `livenessProbe` | Configure for runtimes taking >2s to boot (see [references/health-probes.md](./references/health-probes.md)). Never query external databases in probes. |

---

## Step 5: Validate, Adapt to `gcloud` CLI, and Deliver

Platform limits and `gcloud` CLI flags evolve over time. To ensure recommendations never break due to SDK or GCP updates:
1. **Externalized Platform Limits ([scripts/platform-limits.json](./scripts/platform-limits.json)):** `evaluate-workload.py` loads execution environment floors (`gen2 >= 512Mi` & `>= 1 vCPU`), fractional CPU concurrency rules (`cpu < 1` forces `concurrency=1`), and GPU SKU minimums dynamically from `scripts/platform-limits.json` and automatically probes the user's installed `gcloud` version.
2. **Verify Advanced CLI Flags Locally:** Before running or recommending newly introduced flags (e.g., GPU types, `--no-gpu-zonal-redundancy`, `worker-pools`, multi-container sidecars), verify whether the user's installed SDK supports them in GA (`gcloud run deploy --help`) or requires `gcloud beta run deploy`. When configuring bleeding-edge GCP features, query live Google Cloud Run documentation rather than relying solely on pre-trained defaults.
3. **Report detected bottlenecks, Workload Profile & Plain-English Cost:** Summarize any code anti-patterns, Dockerfile issues, or suboptimal Cloud Run flags found, explain *why* the chosen `cpu`, `memory`, `concurrency`, and billing model fit their stack, and include the plain-English cost/scaling summary.
4. **Provide ready-to-apply artifacts:**
   * **Default (Beginners & Fast Deploys):** Output the 1-command `gcloud run deploy <SERVICE> --source=. --allow-unauthenticated ...` command along with the 1-time API enablement command (`gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com`).
   * **Advanced / IaC Users:** Output the Knative `service.yaml` (see templates in [examples/](./examples/): [service-io-bound.yaml](./examples/service-io-bound.yaml), [service-cpu-or-ssr.yaml](./examples/service-cpu-or-ssr.yaml), [service-gpu-inference.yaml](./examples/service-gpu-inference.yaml), or [service-jvm-enterprise.yaml](./examples/service-jvm-enterprise.yaml)) or Terraform block ([examples/main.tf](./examples/main.tf)).
5. **Verify after deployment (if applicable):** Follow [references/observability-metrics.md](./references/observability-metrics.md) to check CPU p95, memory p99, and triage logs:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND severity>=WARNING" --limit=20
   ```
