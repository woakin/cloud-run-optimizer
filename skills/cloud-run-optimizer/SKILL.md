---
name: cloud-run-optimizer
description: >-
  Audits, configures, and optimizes Google Cloud Run services, jobs, and worker pools
  for maximum performance, low cold-start latency, and minimal cost. Use whenever
  generating, migrating, reviewing, or deploying applications to Google Cloud Run,
  writing Dockerfiles for Cloud Run, tuning concurrency/CPU/memory/autoscaling flags,
  or troubleshooting high Cloud Run bills and latency.
license: Apache-2.0
metadata:
  category: cloud-infrastructure
  platform: google-cloud-run
  version: "1.0.0"
---

# Google Cloud Run Performance & Cost Optimizer

Follow this workflow whenever creating, migrating, or optimizing an application for **Google Cloud Run** to prevent serverless anti-patterns, minimize cold starts, and eliminate idle billing.

---

## Step 1: Audit Current State & Classify the Workload

1. **If auditing an existing local repository**, run the static scanner first:
   ```bash
   bash ./scripts/audit-codebase.sh .
   ```
2. **If auditing a live deployed Cloud Run service** (and `gcloud` is authenticated), run:
   ```bash
   bash ./scripts/audit-live-service.sh <SERVICE_NAME> <REGION> [PROJECT_ID]
   ```
3. **Select the right Cloud Run primitive** before configuring resources:
   * **Cloud Run Service:** HTTP/gRPC APIs, web apps, webhooks, WebSockets, or SSE streaming. Scales to zero.
   * **Cloud Run Job:** Scheduled tasks (cron), batch processing, DB migrations, or queue batches that run to completion and exit. Never run cron loops inside a Service.
   * **Cloud Run Worker Pool:** Continuous pull-based consumers (Kafka, RabbitMQ, NATS) with no HTTP endpoint.

4. **Classify the Service into one of 5 Workload Profiles** (see [references/workload-profiles.md](./references/workload-profiles.md) for full sizing matrices):
   * **Profile A — I/O-Bound Async API:** Go, Node.js (Express/Fastify/Nest), Python ASGI (FastAPI/Uvicorn), Rust.
   * **Profile B — Sync / CPU-Bound API:** Python WSGI (Django/Flask), Ruby/Rails, PHP, PDF/image/video processing.
   * **Profile C — Full-Stack SSR Web App:** Next.js, Nuxt, Remix, SvelteKit, Astro, Angular SSR.
   * **Profile D — AI / LLM / Streaming:** Agentic backends, LLM SSE streaming, WebSockets, MCP servers.
   * **Profile E — Background Queue / Cron:** Tasks triggered asynchronously via Cloud Tasks, Pub/Sub, or Cloud Scheduler.

---

## Step 2: Enforce Serverless Code Guardrails

Before deploying or generating infrastructure configs, verify the application code satisfies these rules (detailed patterns and fixes in [references/code-antipatterns.md](./references/code-antipatterns.md)):

1. **Bind to `0.0.0.0:$PORT`:** Read `PORT` from the environment (default `8080`) and bind to `0.0.0.0`, never `127.0.0.1` or `localhost`.
2. **No Work After HTTP Response:** Under request-based billing (`--cpu-throttling`), CPU is throttled to near-zero immediately after the HTTP response finishes. Any fire-and-forget promises, threads, or `BackgroundTasks` will freeze or fail. Offload async work to **Cloud Tasks** or **Pub/Sub** before returning the response.
3. **No In-Process Timers (`setInterval`, `node-cron`, `APScheduler`):** Instances scale to zero or multiply unpredictably. Extract scheduled jobs into **Cloud Scheduler** invoking a **Cloud Run Job** or an authenticated HTTP endpoint.
4. **Ephemeral `/tmp` Is In-Memory (`tmpfs`):** Writing files to `/tmp` consumes the container's memory limit and causes OOM crashes if not deleted. Always delete temporary files in a `finally` block, or stream uploads/downloads directly to **Cloud Storage (GCS)** using Signed URLs.
5. **Stateless Memory:** Never store user sessions, rate-limit counters, or upload state in process memory. Use **Firestore**, **Memorystore (Valkey/Redis)**, or signed cookies/JWTs.
6. **Graceful Shutdown (`SIGTERM`):** Cloud Run sends `SIGTERM` and gives **10 seconds** before `SIGKILL`. Register a `SIGTERM` handler to stop accepting new requests, finish in-flight requests, and close DB pools cleanly.
7. **Global Connection Pools & Concurrency Caps:** Initialize DB/HTTP clients at module scope (outside request handlers) so warm instances reuse connections. Bound per-instance pool size using:
   $$\text{pool\_size} \le \left\lfloor \frac{\text{DB\_MAX\_CONNECTIONS} \times 0.8}{\text{max\_instances}} \right\rfloor$$

---

## Step 3: Optimize the Container & Runtime (`Dockerfile`)

Cold start duration is directly proportional to image pull size and runtime initialization time. Apply the rules in [references/dockerfile-runtime-tuning.md](./references/dockerfile-runtime-tuning.md):

* **Multi-stage builds & minimal base images:** Use `gcr.io/distroless/static-debian12:nonroot` (Go/Rust), `node:22-slim` (Node.js), or `python:3.12-slim` (Python). Avoid full OS images (`ubuntu`, default `node:22`, `python:3.12`).
* **Match server workers to vCPUs:**
  * **Node.js / Go / Rust:** Single process per container (let Cloud Run scale horizontally across instances). Set `--max-old-space-size` in Node.js to ~80% of container memory.
  * **Python (FastAPI/Uvicorn):** `1 worker` per vCPU (`--workers 1` for 1 vCPU) with high `--concurrency` (40–80) if 100% async I/O.
  * **Python (Django/Flask WSGI with Gunicorn):** Use `--workers 1 --threads 4` or `2` workers on `1–2 vCPU` with `--concurrency` matching total threads (`4–8`).
  * **Next.js:** Always set `output: 'standalone'` in `next.config.js` to cut image size by ~70% and reduce cold-start time.

---

## Step 4: Tune Cloud Run Configuration for Performance & Cost

Select the exact flags for `gcloud run deploy`, Knative YAML (`examples/`), or Terraform (`examples/main.tf`). For deep-dive networking and billing rules, read [references/cost-and-networking.md](./references/cost-and-networking.md).

### Quick Configuration Matrix

| Parameter | Flag / YAML Field | Rule for Cost & Performance |
| :--- | :--- | :--- |
| **CPU Billing** | `--cpu-throttling` (default) | **Always prefer Request-based billing** (`--cpu-throttling`) so you pay \$0 for CPU between requests. Only use `--no-cpu-throttling` (Instance-based) if the service has sustained >40% utilization 24/7 or requires background threads. |
| **Startup CPU Boost** | `--cpu-boost` | **Enable on every production service.** Temporarily boosts CPU during instance startup for faster cold starts at **zero idle cost** (far cheaper than `min-instances`). |
| **Concurrency** | `--concurrency` | **80–100** for async I/O (Go, Node, FastAPI). **40–80** for SSR (Next.js). **1–8** for synchronous/CPU-bound workloads. Never set `--concurrency=1` on an async I/O server (multiplies bill by up to 80x). |
| **Min Instances** | `--min-instances` | **0** for dev/staging and most prod services with `--cpu-boost`. Set to **1** only for strict P99 latency SLAs on heavy runtimes (Java/JVM, large ML models). |
| **Max Instances** | `--max-instances` | Always set a sensible ceiling (e.g., `10–50`) to prevent runaway cost spikes and protect downstream databases from connection exhaustion. |
| **Execution Env** | `--execution-environment=gen2` | Use `gen2` for CPU-intensive workloads, network file systems (GCS FUSE / Filestore), or faster Linux syscall performance. Default or `gen1` is fine for lightweight I/O APIs with ultra-fast sub-second cold starts. |
| **VPC Egress** | `--network` & `--subnet` | **Use Direct VPC Egress** instead of Serverless VPC Access Connectors. Connectors run always-on e2/f1 VMs that cost \$15–\$35+/month even when your service scales to zero. |

---

## Step 5: Validate and Deliver

When presenting your optimization or deployment configuration to the user:
1. **Report detected bottlenecks:** Summarize any code anti-patterns, Dockerfile issues, or suboptimal Cloud Run flags found.
2. **State the selected Workload Profile:** Explain *why* the chosen `cpu`, `memory`, `concurrency`, and billing model fit their stack.
3. **Provide ready-to-apply artifacts:** Output the exact `gcloud run deploy` command, Knative `service.yaml` (based on [examples/service-io-bound.yaml](./examples/service-io-bound.yaml) or [examples/service-cpu-or-ssr.yaml](./examples/service-cpu-or-ssr.yaml)), or Terraform block ([examples/main.tf](./examples/main.tf)).
4. **Verify after deployment (if applicable):** Check container startup latency and memory p95 in Cloud Logging / Monitoring:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=<SERVICE_NAME> AND severity>=WARNING" --limit=20
   ```
