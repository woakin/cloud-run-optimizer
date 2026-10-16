# cloud-run-optimizer

[![Install via skills.sh](https://img.shields.io/badge/skills.sh-install-00c853)](https://skills.sh/woakin/cloud-run-optimizer)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

An [Agent Skill](https://agentskills.io) that equips AI coding assistants (Claude Code, Cursor, Gemini CLI, Google Antigravity, Windsurf, Codex) to **audit, configure, and optimize Google Cloud Run workloads for maximum performance and minimal cost**.

Unlike basic deployment guides that only teach an agent how to run `gcloud run deploy`, **`cloud-run-optimizer`** analyzes your application's architecture and runtime to prevent serverless anti-patterns, shrink cold starts, and eliminate idle compute & networking charges.

---

## Install

Using the [`skills`](https://skills.sh) CLI:

```bash
npx skills add woakin/cloud-run-optimizer
```

Or copy `skills/cloud-run-optimizer` directly into your project's `.agents/skills/` (or global `~/.gemini/config/skills/` / `~/.claude/skills/`) directory.

---

## Prerequisites

* **Local Codebase Audits & Architecture Sizing:** No cloud tools required (runs using static inspection and standard shell tools).
* **Live Service Audits & Direct Deployment:** Requires the **Google Cloud CLI (`gcloud`)** installed and authenticated:
  * [Install Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
  * Authenticate and set your active project:
    ```bash
    gcloud auth login
    gcloud config set project <PROJECT_ID>
    ```

---

## What This Skill Optimizes

### 1. Automated Workload Evaluation & Sizing (`references/workload-profiles.md`)
Run the automated evaluator to inspect your repo and output a tailored sizing profile, deployment flags, or Knative YAML:
```bash
python3 skills/cloud-run-optimizer/scripts/evaluate-workload.py . --format terminal
```

Classifies your project into one of **10 specialized workload profiles**:
* **Profile A — I/O-Bound Async APIs** (Go, Node.js, FastAPI/Uvicorn, Rust)
* **Profile B — Sync / CPU-Bound APIs** (Django/Flask WSGI, Rails, PHP, PDF/Image processing)
* **Profile C — Full-Stack SSR Web Apps** (Next.js Standalone, Nuxt, Remix, SvelteKit, Astro)
* **Profile D — AI Agent, LLM Gateway & Streaming** (LangChain, LlamaIndex, MCP servers, SSE token streaming)
* **Profile E — Background Queues & Cron** (Migrating in-process pollers to Cloud Run Jobs, Cloud Tasks, or Worker Pools)
* **Profile F — GPU & AI / ML Inference** (vLLM, Ollama, PyTorch, Whisper with NVIDIA L4 GPU & GCS FUSE model mounts)
* **Profile G — Enterprise JVM & .NET** (Spring Boot, Quarkus, Micronaut, ASP.NET Core with cgroup memory limits)
* **Profile H — High-Burst Event & Webhook Receivers** (Stripe, GitHub, Eventarc, Pub/Sub push with bounded concurrency)
* **Profile I — Static SPA, Reverse Proxy & Edge Gateway** (NGINX, Caddy, Envoy, lightweight Go proxies)
* **Profile J — Stateful WebSockets & Interactive Sessions** (Real-time collaboration, Yjs, terminal sessions with `--session-affinity`)

### 2. Serverless Code Anti-Pattern Detection (`references/code-antipatterns.md`)
Detects and remediates code patterns that break or overbill on Cloud Run:
* **Work-after-response bugs:** Fire-and-forget promises or `BackgroundTasks` that freeze when CPU throttling activates after the HTTP response.
* **In-process timers:** `node-cron`, `setInterval`, or `APScheduler` inside ephemeral containers.
* **Ephemeral `/tmp` RAM exhaustion:** Uncleaned writes to `/tmp` (which is an in-memory `tmpfs` backed by container RAM).
* **In-memory session stores:** State that breaks on horizontal autoscaling.
* **DB connection exhaustion:** Per-instance connection pool sizing paired with `--max-instances`.
* **Missing `SIGTERM` graceful shutdown:** Handling Cloud Run's 10-second termination window cleanly.

### 3. Container & Runtime Tuning (`references/dockerfile-runtime-tuning.md`)
* Multi-stage Dockerfiles using `distroless`, `slim`, or `alpine` base images.
* Exec-form `CMD` to preserve PID 1 signal forwarding.
* Runtime flags tuned to container limits (`--max-old-space-size` for Node.js, `output: 'standalone'` for Next.js, `-XX:MaxRAMPercentage=75.0` for JVM, CUDA 12 runtime recipes for GPUs, `GOMEMLIMIT` for Go).

### 4. Billing & Networking Cost Traps (`references/cost-and-networking.md`)
* **Request-based (`--cpu-throttling`) vs. Instance-based billing.**
* **Startup CPU Boost (`--cpu-boost`)** to cut cold starts at \$0 idle cost instead of overpaying for `min-instances`.
* **Direct VPC Egress** vs. legacy Serverless VPC Access Connectors (saves \$15–\$35+/month in fixed VM charges).
* **Cloud NAT & Tier-1 Region Co-location.**

### 5. Cloud SQL & Database Connection Resilience (`references/cloud-sql.md`)
* Mathematical connection pool sizing formula coupled to `--max-instances` to prevent database crashes during autoscaling bursts.
* Direct VPC Egress (Private IP) vs. Cloud SQL Auth Proxy (`--add-cloudsql-instances`) architecture.
* Preventing cold-start container timeouts caused by blocking synchronous database checks before binding `$PORT`.
* Safe pool configurations for Prisma, SQLAlchemy, and Go `pgxpool`.

### 6. Health & Startup Probes Configuration (`references/health-probes.md`)
* Eliminating false-death cold start restarts with correctly sized `startupProbe` grace windows.
* Avoiding cascading outage storms: why external dependencies (Cloud SQL, Redis, APIs) must **never** be checked in container runtime probes.
* TCP Socket vs. HTTP GET probes for minimal startup latency overhead.

### 7. Observability, Metrics & Right-Sizing (`references/observability-metrics.md`)
* The 4 Golden Metrics in Google Cloud Monitoring: `container/cpu/utilizations` (p95), `container/memory/utilizations` (p99), `container/instance_count`, and `request_latencies`.
* Detecting and triaging OOM (Out Of Memory) container crashes, 504 timeouts, and port-binding failures with targeted Cloud Logging queries.
* Essential production alerting thresholds.

---

## Included Deterministic Scripts

* `scripts/evaluate-workload.py`: Evaluates the local repository's dependencies, Dockerfile, and code patterns to classify the workload into one of 10 profiles and output tailored configurations (formats: `terminal`, `json`, `deploy-cmd`, `yaml`, `tf`).
* `scripts/audit-codebase.sh`: Scans a local repository for Dockerfile inefficiencies, missing `.dockerignore`, in-process timers, memory sessions, IaC misconfigurations, and automatically runs the workload evaluator.
* `scripts/audit-live-service.sh`: Inspects a live deployed Cloud Run service via `gcloud run services describe` and outputs prioritized cost and latency recommendations.

---

## Repository Structure

```text
cloud-run-optimizer/
├── README.md
├── LICENSE
└── skills/
    └── cloud-run-optimizer/
        ├── SKILL.md
        ├── scripts/
        │   ├── evaluate-workload.py
        │   ├── audit-codebase.sh
        │   └── audit-live-service.sh
        ├── references/
        │   ├── workload-profiles.md
        │   ├── code-antipatterns.md
        │   ├── dockerfile-runtime-tuning.md
        │   ├── cost-and-networking.md
        │   ├── cloud-sql.md
        │   ├── health-probes.md
        │   └── observability-metrics.md
        └── examples/
            ├── service-io-bound.yaml
            ├── service-cpu-or-ssr.yaml
            ├── service-gpu-inference.yaml
            ├── service-jvm-enterprise.yaml
            └── main.tf
```

## License

Apache-2.0
