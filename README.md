# ⚡ cloud-run-optimizer

[![Install via skills.sh](https://img.shields.io/badge/skills.sh-install-00c853?style=flat-square)](https://skills.sh/woakin/cloud-run-optimizer)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg?style=flat-square)](LICENSE)
[![Platform: Google Cloud Run](https://img.shields.io/badge/Google_Cloud-Cloud_Run_&_Cloud_SQL-4285F4?style=flat-square&logo=google-cloud&logoColor=white)](https://cloud.google.com/run)
[![Agents: Claude Code | Cursor | Antigravity | Gemini CLI](https://img.shields.io/badge/AI_Agents-Claude_%7C_Cursor_%7C_Antigravity_%7C_Windsurf-purple?style=flat-square)](https://agentskills.io)

> **Stop deploying to Google Cloud Run with generic AI defaults that 10x your bill, crash your Cloud SQL database, or silently wipe your local data.**

**`cloud-run-optimizer`** is an [Agent Skill](https://agentskills.io) that gives your AI coding assistant (Claude Code, Cursor, Google Antigravity, Gemini CLI, Windsurf, Codex) the instincts of a **Senior GCP DevOps & FinOps Engineer**—while remaining effortless for **indie builders and vibe-coders** deploying their very first app.

---

## 🚀 Quick Install

Install in one command with the [`skills`](https://skills.sh) CLI:

```bash
npx skills add woakin/cloud-run-optimizer
```

<details>
<summary><b>Manual Install (No npm required)</b></summary>

Clone or copy `skills/cloud-run-optimizer` directly into your agent's skill directory:
* **Project-level:** `.agents/skills/cloud-run-optimizer`
* **Global (Antigravity / Gemini CLI):** `~/.gemini/config/skills/cloud-run-optimizer`
* **Global (Claude Code):** `~/.claude/skills/cloud-run-optimizer`
</details>

---

## 🔥 Why You Need This Skill (Default AI vs. `cloud-run-optimizer`)

When you ask a standard LLM *"Deploy my app to Cloud Run"*, it guesses generic flags from outdated blog posts. Here is what happens before vs. after installing **`cloud-run-optimizer`**:

| Scenario | ❌ Standard AI / Default Deploy | ✅ With `cloud-run-optimizer` | 💰 Real-World Impact |
| :--- | :--- | :--- | :--- |
| **Vibe-Coded App with SQLite** (`better-sqlite3`, Prisma `dev.db`) | Deploys local `.db` file inside the container with zero warning | **Blocks silent data loss:** warns that Cloud Run wipes local disk on scale-to-zero & helps switch to Cloud SQL / Neon / Supabase / Turso | **Prevents 100% production data loss** |
| **Node.js / Go / FastAPI API** | Sets `--concurrency=1` or over-allocates `min-instances` | Sets `--concurrency=80`, `--cpu-throttling`, `--cpu-boost`, `min-instances=0` | **Up to 80x lower compute bill ($0/mo when idle)** |
| **Next.js / Nuxt / Remix SSR** | Ships `1.4 GB` container running `npm run dev` on port `3000` | Configures `output: 'standalone'`, `node:22-slim`, `0.0.0.0:$PORT`, `2 vCPU / 1Gi`, and `--max-old-space-size=768` | **70% smaller image & 3x faster cold starts** |
| **Cloud SQL (Postgres / MySQL)** | Default pool (`10` conns) × `100` max instances = **1,000 connections** | Inspects your live Cloud SQL tier & calculates exact safe `pool_size` + `--max-instances` cap | **Zero `FATAL: too many clients` outages** |
| **Private VPC Networking** | Creates a legacy Serverless VPC Access Connector | Configures **Direct VPC Egress** (`--network` & `--subnet`) | **Saves $15–$35+/mo in idle VM charges** |
| **Hidden GCP Storage & IP Taxes** | Leaves Cloud SQL Public IPv4 enabled + lets `cloud-run-source-deploy` accumulate 80+ GB of old build images | Disables unneeded Public IPv4 (`--no-assign-ip`), trims backups to the 7 free tier, and attaches an **Artifact Registry cleanup policy** | **Saves $10–$25+/mo in silent recurring fees** |
| **GPU Inference (vLLM / Ollama)** | Multi-zone GPU defaults + caches weights in `/tmp` (causes RAM OOM) | Enforces `nvidia-l4` (`4 vCPU / 16Gi`), `--no-gpu-zonal-redundancy`, and GCS FUSE volume mounts | **~35% cheaper GPU/hr & zero OOM crashes** |

---

## 💬 How to Use It (Just Ask Your AI Agent)

Once installed, your AI agent automatically activates the skill whenever you talk about Cloud Run, Dockerfiles, Cloud SQL, or GCP costs.

### For Builders & Vibe-Coders ("Do-It-For-Me" Mode)
> *"Make my app ready for Google Cloud Run, fix any port or Dockerfile issues for me, and give me the command to deploy it."*

> *"I want to host this project on Cloud Run as cheaply as possible ($0 when nobody is using it). Set everything up."*

### For DevOps & Backend Engineers
> *"Audit this repo with `cloud-run-optimizer` and generate a production Knative `service.yaml` and Terraform config."*

> *"Audit our live Cloud Run service `payments-api` in `us-central1` and check if our Cloud SQL connection pool is safe for traffic spikes."*

---

## 🖥️ What You Get (Live Terminal Preview)

Behind the scenes, the skill runs deterministic scanners (`audit-codebase.sh` and `evaluate-workload.py`) that inspect your actual dependencies, code patterns, and local `gcloud` CLI version:

```text
==================================================================
 Cloud Run Workload Evaluation: my-saas-app
 Platform Schema: v2026.10 | gcloud CLI: Google Cloud SDK 589.0.0
==================================================================

[1] Detected Tech Stack & Characteristics:
  * Languages:     Node.js/TypeScript
  * Frameworks:    Full-Stack SSR (Next.js)
  * Databases:     Prisma + PostgreSQL

[2] Classified Workload Profile:
  -> Profile C: Full-Stack SSR Web App
     "Node/JS full-stack web framework rendering HTML/React Server Components dynamically."

[3] Tailored Cloud Run Sizing & Configuration:
  * vCPU / Memory:         2 vCPU / 1Gi
  * Concurrency:           50 concurrent requests/instance
  * CPU Allocation:        Request-based (--cpu-throttling) [Cost: $0 idle]
  * Startup CPU Boost:     Enabled (--cpu-boost)
  * Execution Environment: gen2
  * Autoscaling Limits:    min-instances=0, max-instances=40
  * Runtime Env Tuning:    NODE_ENV=production, NODE_OPTIONS=--max-old-space-size=768

[4] Plain-English Summary (What This Means For Your App & Bill):
  * $0 Idle Cost:      Your app scales to zero when nobody is using it—you pay $0/month while idle.
  * Fast Cold Starts:  Extra CPU is boosted automatically during startup (--cpu-boost) at $0 idle cost.
  * Bill Protection:   Capped at 40 max instances so a traffic spike or bot attack cannot cause a runaway bill.
  * High Efficiency:   Each container handles up to 50 requests at once before spinning up another container.

[5] Ready-to-Run Commands (Copy & Paste):
# Build & deploy directly from your project folder:
gcloud run deploy my-saas-app \
  --source=. \
  --region=us-central1 \
  --cpu=2 \
  --memory=1Gi \
  --concurrency=50 \
  --min-instances=0 \
  --max-instances=40 \
  --timeout=60s \
  --cpu-throttling \
  --cpu-boost \
  --execution-environment=gen2 \
  --set-env-vars="NODE_ENV=production,NODE_OPTIONS=--max-old-space-size=768" \
  --allow-unauthenticated
```

---

## 🎯 10 Specialized Workload Profiles

Instead of one-size-fits-all flags, `cloud-run-optimizer` classifies your codebase into one of **10 architectural profiles** and enforces strict GCP hardware constraints (such as `gen2` memory floors, fractional CPU concurrency locks, and runtime heap limits):

| Profile | Workload Type | Common Stacks | vCPU / Memory | Concurrency | CPU Billing |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **A** | **I/O-Bound Async API** | Go, Node.js (Express/Fastify/Hono), FastAPI, Rust | `1 vCPU / 512Mi` | `80` | Request-based (`$0` idle) |
| **B** | **Sync / CPU-Bound API** | Django, Flask, Rails, Laravel, Sharp/FFmpeg/PDF | `2 vCPU / 1Gi` | `4` | Request-based (`$0` idle) |
| **C** | **Full-Stack SSR Web App** | Next.js, Nuxt, Remix, SvelteKit, Astro, Angular SSR | `2 vCPU / 1Gi` | `50` | Request-based (`$0` idle) |
| **D** | **AI Agent & LLM Streaming** | OpenAI/Anthropic/Gemini SDKs, LangChain, MCP, SSE | `1 vCPU / 512Mi` | `80` | Request-based (`600s` timeout) |
| **E** | **Background Queue / Cron** | Celery, BullMQ, Cloud Tasks, Pub/Sub Workers | `1 vCPU / 1Gi` | `1` | Cloud Run **Job** / Worker |
| **F** | **GPU & ML Inference** | PyTorch, vLLM, Ollama, Whisper, HuggingFace (`nvidia-l4`) | `4 vCPU / 16Gi` | `4` | Instance-based (`--no-gpu-zonal-redundancy`) |
| **G** | **Enterprise JVM & .NET** | Spring Boot, Quarkus, Micronaut, Kotlin, ASP.NET Core | `2 vCPU / 1Gi` | `80` | Request-based + Startup Probe |
| **H** | **High-Burst Webhooks** | Stripe, GitHub, Shopify, Eventarc, Pub/Sub Push | `1 vCPU / 512Mi` | `100` | Request-based (`60s` timeout) |
| **I** | **Static SPA & Edge Proxy** | NGINX, Caddy, Envoy, Vite/React Static Bundles | `1 vCPU / 512Mi` | `200` | Request-based (`gen1` fast boot) |
| **J** | **Stateful WebSockets** | Socket.io, `ws`, Yjs, LiveKit, Multiplayer Rooms | `1 vCPU / 1Gi` | `150` | Instance-based + Session Affinity |

---

## 🛡️ 8 Deadly Serverless Traps Caught Automatically

The static code scanner (`scripts/audit-codebase.sh`) inspects your repository before you deploy and flags:

1. **🗄️ Ephemeral SQLite Data Wipe:** Detects `better-sqlite3`, `sqlite3`, `aiosqlite`, and Prisma `file:./dev.db` before Cloud Run deletes your database on scale-to-zero.
2. **🔌 Hardcoded Dev Ports & Loopback Binding:** Catches `app.listen(3000)` or `127.0.0.1` / `localhost` that cause Cloud Run startup failures (`PORT=8080` required).
3. **🐢 Dev Servers in Production Dockerfiles:** Flags `npm run dev`, `next dev`, and `uvicorn --reload` that consume 4x RAM and fail under load.
4. **🧊 Silent Background Task Freeze:** Catches fire-and-forget promises, threads, and FastAPI `BackgroundTasks` that freeze the millisecond the HTTP response finishes under `--cpu-throttling`.
5. **💥 `/tmp` In-Memory RAM OOMs:** Flags uncleaned file writes to `/tmp` (which is an in-memory `tmpfs` backed directly by your container's RAM limit).
6. **⏰ Duplicate In-Process Cron Timers:** Detects `node-cron`, `setInterval`, and `APScheduler` that either stop firing at 0 instances or fire 20x in parallel during traffic bursts.
7. **🐘 Cloud SQL Connection Exhaustion:** Calculates per-container `pool_size` using $\lfloor (\text{max\_connections} \times 0.8) / \text{max\_instances} \rfloor$ and verifies live Cloud SQL tiers.
8. **🩺 Cascading Probe Outages:** Prevents `livenessProbe` / `startupProbe` endpoints from querying databases synchronously and triggering restart storms.

---

## 🛠️ Standalone CLI Usage (Works Without an AI Agent)

Every script in this repository has **zero external Python/Node dependencies** and can be run directly in your terminal or CI/CD pipeline:

```bash
# 1. Full static audit (Code anti-patterns + Dockerfile check + Workload sizing):
bash skills/cloud-run-optimizer/scripts/audit-codebase.sh /path/to/your/app

# 2. Generate tailored config in Terminal, JSON, gcloud command, Knative YAML, or Terraform:
python3 skills/cloud-run-optimizer/scripts/evaluate-workload.py /path/to/your/app --format terminal
python3 skills/cloud-run-optimizer/scripts/evaluate-workload.py /path/to/your/app --format deploy-cmd
python3 skills/cloud-run-optimizer/scripts/evaluate-workload.py /path/to/your/app --format yaml
python3 skills/cloud-run-optimizer/scripts/evaluate-workload.py /path/to/your/app --format tf

# 3. Audit a live deployed Cloud Run service + its attached Cloud SQL database:
bash skills/cloud-run-optimizer/scripts/audit-live-service.sh <SERVICE_NAME> <REGION> [PROJECT_ID]
```

### Adaptive to GCP & `gcloud` Updates
* **Live `gcloud` Probing:** `evaluate-workload.py` automatically inspects your locally installed `gcloud` CLI (`gcloud run deploy --help`) and switches between GA flags and `gcloud beta run deploy` if your local SDK is older.
* **Externalized Platform Schema (`scripts/platform-limits.json`):** GCP execution environment floors, fractional vCPU rules, GPU SKU requirements, and Cloud SQL tier defaults live in a clean JSON schema (`v2026.10`) that is validated automatically.

---

## 📂 Repository Structure

```text
cloud-run-optimizer/
├── README.md
├── LICENSE
└── skills/
    └── cloud-run-optimizer/
        ├── SKILL.md                              # Core instructions & "Do-It-For-Me" workflow
        ├── scripts/
        │   ├── evaluate-workload.py              # 10-profile classifier & config generator
        │   ├── platform-limits.json              # Externalized GCP platform & Cloud SQL limits
        │   ├── audit-codebase.sh                 # Static code, Dockerfile & SQLite/port scanner
        │   └── audit-live-service.sh             # Live Cloud Run + Cloud SQL instance auditor
        ├── references/
        │   ├── workload-profiles.md              # Deep-dive sizing matrices (Profiles A–J)
        │   ├── code-antipatterns.md              # 8 serverless code traps & remediations
        │   ├── dockerfile-runtime-tuning.md      # Multi-stage builds & Node/Python/Go/JVM tuning
        │   ├── cost-and-networking.md            # Billing math, CPU boost & Direct VPC Egress
        │   ├── cloud-sql.md                      # Connection pooling math, Private IP & IAM auth
        │   ├── health-probes.md                  # Startup & liveness probes without cascading failures
        │   └── observability-metrics.md          # Golden metrics, OOM triage & Cloud Logging queries
        └── examples/
            ├── service-io-bound.yaml             # Knative YAML for Go/Node/FastAPI APIs
            ├── service-cpu-or-ssr.yaml           # Knative YAML for Next.js SSR & CPU workloads
            ├── service-gpu-inference.yaml        # Knative YAML for NVIDIA L4 GPU + GCS FUSE
            ├── service-jvm-enterprise.yaml       # Knative YAML for Spring Boot / JVM with probes
            └── main.tf                           # Production Terraform (Cloud Run v2 + Direct VPC)
```

## 📄 License

[Apache-2.0](LICENSE)
