#!/usr/bin/env python3
"""
Cloud Run Workload Evaluator & Config Generator
Inspects a local codebase, detects frameworks, libraries, Dockerfile settings,
and code patterns to classify the workload into one of 10 Cloud Run Profiles,
then outputs tailored CPU, memory, concurrency, and deployment settings.

Usage:
  python3 evaluate-workload.py [DIR] [options]
Options:
  --format {terminal,json,deploy-cmd,yaml,tf}  Output format (default: terminal)
  --service-name NAME                          Cloud Run service name (default: auto)
  --region REGION                              GCP region (default: us-central1)
  --project PROJECT_ID                         GCP project ID (default: PROJECT_ID)
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path

# ==============================================================================
# Workload Profiles Definition & Defaults
# ==============================================================================
PROFILES = {
    "A": {
        "name": "I/O-Bound Async API",
        "description": "Non-blocking event loop or lightweight green-threads handling network/DB I/O.",
        "cpu": "1",
        "memory": "512Mi",
        "concurrency": 80,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 60,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 30,
        "rationale": "High concurrency (80) utilizes idle CPU time during I/O waits, reducing instance count up to 80x."
    },
    "B": {
        "name": "Sync / CPU-Bound API & Processing",
        "description": "Thread-per-request or blocking WSGI/Rack runtimes, image/PDF processing, or CPU-heavy compute.",
        "cpu": "2",
        "memory": "1Gi",
        "concurrency": 8,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 120,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "rationale": "Concurrency matches worker thread limits (4-8) to avoid socket backlog stalls under blocking operations."
    },
    "C": {
        "name": "Full-Stack SSR Web App",
        "description": "Node/JS full-stack web framework rendering HTML/React Server Components dynamically.",
        "cpu": "2",
        "memory": "1Gi",
        "concurrency": 50,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 60,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 40,
        "rationale": "Requires CPU boost for React hydration bursts and 2 vCPUs to prevent event loop delay during rendering."
    },
    "D": {
        "name": "AI Agent, LLM Gateway & Streaming",
        "description": "Agentic backends, LLM token streaming via SSE, or long-lived AI pipelines.",
        "cpu": "2",
        "memory": "1Gi",
        "concurrency": 30,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 600,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 25,
        "rationale": "Higher timeout (600s) supports slow LLM completions; request-based billing keeps CPU allocated during active token streams."
    },
    "E": {
        "name": "Background Queue Worker & Scheduled Batch Job",
        "description": "Asynchronous job processing, queue consumption, DB migrations, or cron tasks.",
        "cpu": "2",
        "memory": "2Gi",
        "concurrency": 1,
        "cpu_throttling": False,
        "cpu_boost": False,
        "execution_env": "gen2",
        "timeout": 1800,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 10,
        "primitive": "Cloud Run Job / Worker Pool",
        "rationale": "Use Cloud Run Jobs for batch/cron tasks, or Cloud Tasks / Pub/Sub push to scale worker services to zero."
    },
    "F": {
        "name": "GPU & AI / ML Inference (NVIDIA L4)",
        "description": "Local model weights execution (vLLM, Ollama, PyTorch, Whisper, Hugging Face TGI, ComfyUI).",
        "cpu": "4",
        "memory": "16Gi",
        "concurrency": 8,
        "cpu_throttling": False,
        "cpu_boost": False,
        "execution_env": "gen2",
        "timeout": 600,
        "session_affinity": False,
        "gpu": {"count": 1, "type": "nvidia-l4"},
        "min_scale": 0,
        "max_scale": 5,
        "rationale": "Requires GPU accelerator (--gpu=1 --gpu-type=nvidia-l4), CPU always allocated, and GCS volume mount for model weights."
    },
    "G": {
        "name": "Enterprise JVM & Managed Runtimes",
        "description": "Java (Spring Boot, Quarkus, Micronaut), Kotlin, Scala, or ASP.NET Core.",
        "cpu": "2",
        "memory": "1Gi",
        "concurrency": 60,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 60,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 30,
        "rationale": "Needs JVM memory cgroup limit (-XX:MaxRAMPercentage=75.0) and Startup CPU Boost to smooth JIT warmup."
    },
    "H": {
        "name": "High-Burst Event & Webhook Receiver",
        "description": "Stripe, GitHub, Shopify webhooks, Eventarc triggers, or Pub/Sub push receivers.",
        "cpu": "1",
        "memory": "256Mi",
        "concurrency": 20,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 20,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 50,
        "rationale": "Bounded concurrency (20) and short timeouts protect internal databases from sudden burst avalanches."
    },
    "I": {
        "name": "Static SPA, Reverse Proxy & Edge Gateway",
        "description": "NGINX, Caddy, Envoy, lightweight Go reverse proxy, or static frontend container.",
        "cpu": "0.5",
        "memory": "256Mi",
        "concurrency": 200,
        "cpu_throttling": True,
        "cpu_boost": False,
        "execution_env": "gen1",
        "timeout": 60,
        "session_affinity": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "rationale": "Gen1 execution environment gives sub-100ms cold starts with fractional CPU (0.5) handling 200 concurrent proxy connections."
    },
    "J": {
        "name": "Stateful WebSockets & Interactive Sessions",
        "description": "Persistent WebSocket connections, collaborative real-time editing (Yjs), multiplayer backends, or interactive agents.",
        "cpu": "1",
        "memory": "512Mi",
        "concurrency": 80,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 3600,
        "session_affinity": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "rationale": "Requires --session-affinity to route sticky sessions to the same instance and --timeout=3600s (max allowed)."
    }
}

# ==============================================================================
# Analyzer Engine
# ==============================================================================
class CodebaseEvaluator:
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).resolve()
        self.files = []
        self.scores = {k: 0 for k in PROFILES.keys()}
        self.evidence = {k: [] for k in PROFILES.keys()}
        self.detected_languages = set()
        self.detected_frameworks = set()
        self.detected_databases = set()
        self.dockerfile_info = {}
        self.special_flags = {
            "has_webhooks": False,
            "has_websockets": False,
            "has_streaming": False,
            "has_gpu_model": False,
            "has_db_connection": False,
            "has_cron_timers": False,
            "has_queue_worker": False,
        }
        self._collect_files()

    def _collect_files(self):
        ignore_dirs = {
            "node_modules", ".git", ".next", "dist", "build",
            ".venv", "venv", "__pycache__", "target", "vendor",
            ".exodus", ".agents", ".claude", ".gemini"
        }
        for root, dirs, files in os.walk(self.target_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for f in files:
                rel_path = os.path.relpath(os.path.join(root, f), self.target_dir)
                if rel_path.endswith("evaluate-workload.py") or rel_path.endswith("audit-codebase.sh") or rel_path.endswith("audit-live-service.sh"):
                    continue
                self.files.append(rel_path)

    def _file_exists(self, filename: str) -> bool:
        return any(f == filename or f.endswith("/" + filename) for f in self.files)

    def _read_file_safe(self, filename: str) -> str:
        full_path = self.target_dir / filename
        if not full_path.is_file():
            # Search for relative match
            matches = [self.target_dir / f for f in self.files if f.endswith(filename)]
            if matches:
                full_path = matches[0]
            else:
                return ""
        try:
            return full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def evaluate(self):
        self._analyze_manifests()
        self._analyze_dockerfile()
        self._analyze_code_patterns()
        return self._synthesize_recommendations()

    def _add_score(self, profile: str, points: int, reason: str):
        self.scores[profile] += points
        self.evidence[profile].append(reason)

    def _analyze_manifests(self):
        # 1. Node.js / JavaScript / TypeScript
        pkg_content = self._read_file_safe("package.json")
        if pkg_content:
            self.detected_languages.add("Node.js/TypeScript")
            try:
                pkg_data = json.loads(pkg_content)
                deps = {
                    **pkg_data.get("dependencies", {}),
                    **pkg_data.get("devDependencies", {})
                }
                
                # Check Next.js / Nuxt / Remix / SvelteKit / Astro (Profile C)
                if any(k in deps for k in ["next", "nuxt", "@remix-run/node", "@sveltejs/kit", "astro"]):
                    self.detected_frameworks.add("Full-Stack SSR")
                    self._add_score("C", 40, "Detected SSR framework in package.json (Next/Nuxt/Remix/SvelteKit/Astro)")

                # Check Socket.io / ws / persistent WebSockets (Profile J)
                if any(k in deps for k in ["socket.io", "ws", "sockjs", "uWebSockets.js"]):
                    self.special_flags["has_websockets"] = True
                    self.detected_frameworks.add("WebSockets")
                    self._add_score("J", 35, "Detected WebSocket library in package.json (socket.io/ws)")

                # Check AI / LLM SDKs (Profile D)
                if any(k in deps for k in ["@ai-sdk", "ai", "langchain", "@langchain/core", "openai", "@anthropic-ai/sdk", "@google/genai", "@modelcontextprotocol/sdk"]):
                    self.detected_frameworks.add("AI/LLM SDK")
                    self.special_flags["has_streaming"] = True
                    self._add_score("D", 30, "Detected AI/LLM SDK in package.json (LangChain/OpenAI/Anthropic/MCP)")

                # Check Queue workers (Profile E)
                if any(k in deps for k in ["bullmq", "bull", "agenda", "pg-boss", "amqplib"]):
                    self.special_flags["has_queue_worker"] = True
                    self._add_score("E", 25, "Detected queue/broker library in package.json (BullMQ/Amqplib)")

                # Check Express / Fastify / Hono / NestJS / Koa (Profile A)
                if any(k in deps for k in ["express", "fastify", "hono", "@nestjs/core", "koa"]):
                    self.detected_frameworks.add("Async Node.js API")
                    self._add_score("A", 25, "Detected lightweight async HTTP framework (Express/Fastify/Hono/NestJS)")

                # Check Database clients (to compute max-instance connection pools)
                if any(k in deps for k in ["@prisma/client", "pg", "mysql2", "typeorm", "mongoose", "drizzle-orm", "knex"]):
                    self.special_flags["has_db_connection"] = True
                    self.detected_databases.add("Relational/Document DB (Node)")

            except Exception:
                pass

        # 2. Python
        pyproject = self._read_file_safe("pyproject.toml")
        reqs = self._read_file_safe("requirements.txt")
        pipfile = self._read_file_safe("Pipfile")
        python_text = f"{pyproject}\n{reqs}\n{pipfile}".lower()

        if python_text.strip():
            self.detected_languages.add("Python")
            
            # Check Heavy GPU / ML packages (Profile F)
            if any(k in python_text for k in ["vllm", "torch", "transformers", "diffusers", "accelerate", "onnxruntime-gpu", "deepspeed", "ollama", "whisper"]):
                self.special_flags["has_gpu_model"] = True
                self.detected_frameworks.add("Deep Learning / PyTorch")
                self._add_score("F", 50, "Detected GPU/Deep Learning dependencies (torch/vllm/transformers/diffusers)")

            # Check AI / LLM orchestration (Profile D)
            if any(k in python_text for k in ["langchain", "llama-index", "openai", "anthropic", "google-genai", "mcp", "instructor"]):
                self.detected_frameworks.add("AI/LLM Framework")
                self.special_flags["has_streaming"] = True
                self._add_score("D", 30, "Detected AI/LLM client libraries (LangChain/LlamaIndex/OpenAI/Anthropic/MCP)")

            # Check FastAPI / Starlette / ASGI (Profile A)
            if any(k in python_text for k in ["fastapi", "starlette", "uvicorn", "sanic", "aiohttp"]):
                self.detected_frameworks.add("Python ASGI (FastAPI)")
                self._add_score("A", 30, "Detected Python ASGI async web framework (FastAPI/Starlette/Uvicorn)")

            # Check Django / Flask / WSGI / Gunicorn (Profile B)
            if any(k in python_text for k in ["django", "flask", "gunicorn", "uwsgi", "falcon", "bottle"]):
                self.detected_frameworks.add("Python WSGI (Django/Flask)")
                self._add_score("B", 35, "Detected Python synchronous WSGI web framework (Django/Flask/Gunicorn)")

            # Check Celery / RQ / Arq (Profile E)
            if any(k in python_text for k in ["celery", "rq", "arq", "kombu"]):
                self.special_flags["has_queue_worker"] = True
                self._add_score("E", 30, "Detected background task queue worker (Celery/RQ/Arq)")

            # Check Databases
            if any(k in python_text for k in ["sqlalchemy", "psycopg", "asyncpg", "pymongo", "tortoise-orm", "peewee"]):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Relational/Document DB (Python)")

        # 3. Go
        go_mod = self._read_file_safe("go.mod")
        if go_mod:
            self.detected_languages.add("Go")
            self._add_score("A", 30, "Detected Go (compiled non-blocking goroutines)")
            if any(k in go_mod for k in ["gorilla/websocket", "nhooyr.io/websocket", "gobwas/ws"]):
                self.special_flags["has_websockets"] = True
                self._add_score("J", 30, "Detected Go WebSocket library")
            if any(k in go_mod for k in ["caddyserver", "traefik", "vulcand", "httputil"]):
                self._add_score("I", 30, "Detected Go reverse proxy / gateway")
            if any(k in go_mod for k in ["gorm.io", "database/sql", "jackc/pgx"]):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Relational DB (Go)")

        # 4. Rust
        cargo = self._read_file_safe("Cargo.toml")
        if cargo:
            self.detected_languages.add("Rust")
            self._add_score("A", 30, "Detected Rust (Tokio/Actix/Axum high-performance async)")
            if any(k in cargo for k in ["sqlx", "diesel", "sea-orm"]):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Relational DB (Rust)")

        # 5. Java / JVM / .NET (Profile G)
        has_pom = self._file_exists("pom.xml")
        has_gradle = self._file_exists("build.gradle") or self._file_exists("build.gradle.kts")
        has_dotnet = any(f.endswith(".csproj") or f.endswith(".fsproj") for f in self.files)
        if has_pom or has_gradle or has_dotnet:
            runtime_name = ".NET" if has_dotnet else "Java/JVM"
            self.detected_languages.add(runtime_name)
            self._add_score("G", 45, f"Detected managed runtime manifest ({runtime_name})")

        # 6. Reverse Proxy / Gateway / Static Configs (Profile I)
        if self._file_exists("nginx.conf") or self._file_exists("Caddyfile") or self._file_exists("traefik.yaml"):
            self._add_score("I", 45, "Detected reverse proxy configuration file (nginx/caddy/traefik)")

    def _analyze_dockerfile(self):
        dockerfile = self._read_file_safe("Dockerfile")
        if not dockerfile:
            return

        self.dockerfile_info["present"] = True
        
        # Check base images
        if re.search(r'FROM\s+.*(cuda|nvidia|vllm|pytorch)', dockerfile, re.IGNORECASE):
            self.special_flags["has_gpu_model"] = True
            self._add_score("F", 40, "Dockerfile uses NVIDIA/CUDA/PyTorch GPU base image")

        if re.search(r'FROM\s+.*(nginx|caddy|traefik)', dockerfile, re.IGNORECASE):
            self._add_score("I", 40, "Dockerfile uses NGINX/Caddy reverse proxy base image")

        if re.search(r'FROM\s+.*(openjdk|temurin|corretto|dotnet)', dockerfile, re.IGNORECASE):
            self._add_score("G", 35, "Dockerfile uses Java/JVM or .NET base image")

        # Check multi-stage
        stages = len(re.findall(r'^\s*FROM\s+', dockerfile, re.MULTILINE | re.IGNORECASE))
        self.dockerfile_info["stages"] = stages
        self.dockerfile_info["is_multistage"] = stages >= 2

        # Check non-root
        self.dockerfile_info["has_user"] = bool(re.search(r'^\s*USER\s+', dockerfile, re.MULTILINE | re.IGNORECASE))

        # Check exec-form CMD
        self.dockerfile_info["exec_form_cmd"] = bool(re.search(r'^\s*(CMD|ENTRYPOINT)\s*\[', dockerfile, re.MULTILINE))

        # Next.js standalone check
        if "next" in dockerfile and "standalone" in dockerfile:
            self._add_score("C", 15, "Dockerfile leverages Next.js standalone output")

    def _analyze_code_patterns(self):
        # Heuristic search across files (first 30 code files sampled)
        code_exts = {".js", ".ts", ".jsx", ".tsx", ".py", ".go", ".rs", ".java", ".cs"}
        code_files = [f for f in self.files if Path(f).suffix in code_exts][:40]

        webhook_hits = 0
        websocket_hits = 0
        stream_hits = 0
        timer_hits = 0

        for f in code_files:
            content = self._read_file_safe(f)
            if not content:
                continue

            # Webhooks
            if re.search(r'(stripe\.webhook|github-webhook|/webhook|eventarc|verifyWebhookSignature)', content, re.IGNORECASE):
                webhook_hits += 1

            # WebSockets
            if re.search(r'(\.upgrade\(|WebSocket\(|handle_websocket|on\(["\']connection["\'])', content):
                websocket_hits += 1

            # Streaming
            if re.search(r'(text/event-stream|StreamingResponse|ReadableStream|res\.write\(|flush_interval)', content):
                stream_hits += 1

            # In-process timers
            if re.search(r'(setInterval|node-cron|cron\.schedule|BackgroundScheduler|time\.NewTicker)', content):
                timer_hits += 1

        if webhook_hits >= 2:
            self.special_flags["has_webhooks"] = True
            self._add_score("H", 30, f"Found webhook handling endpoints in {webhook_hits} files")

        if websocket_hits >= 1 and not self.special_flags["has_websockets"]:
            self.special_flags["has_websockets"] = True
            self._add_score("J", 25, f"Found WebSocket handlers in source code")

        if stream_hits >= 1:
            self.special_flags["has_streaming"] = True
            self._add_score("D", 20, f"Found streaming/SSE response patterns in source code")

        if timer_hits >= 1:
            self.special_flags["has_cron_timers"] = True

    def _synthesize_recommendations(self):
        # Pick winner profile based on score
        sorted_profiles = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        best_code, best_score = sorted_profiles[0]

        # Default fallback to Profile A if no strong signals
        if best_score < 15:
            best_code = "A"
            self.evidence["A"].append("Defaulted to I/O-Bound Async API baseline (no specialized signals detected)")

        profile_meta = PROFILES[best_code].copy()

        # Dynamic adjustments based on detected patterns
        if self.special_flags["has_streaming"] and best_code != "F":
            # Ensure timeout is at least 300s
            profile_meta["timeout"] = max(profile_meta["timeout"], 300)
        
        if self.special_flags["has_websockets"]:
            profile_meta["session_affinity"] = True
            profile_meta["timeout"] = 3600

        # Name detection
        service_name = self.target_dir.name.lower().replace("_", "-")
        pkg_content = self._read_file_safe("package.json")
        if pkg_content:
            try:
                p_name = json.loads(pkg_content).get("name")
                if p_name:
                    service_name = re.sub(r'[^a-z0-9\-]', '', p_name.lower().replace("@", "").replace("/", "-"))
            except Exception:
                pass

        # Build gcloud deploy command
        flags = [
            f"gcloud run deploy {service_name}",
            f"--region=us-central1",
            f"--cpu={profile_meta['cpu']}",
            f"--memory={profile_meta['memory']}",
            f"--concurrency={profile_meta['concurrency']}",
            f"--min-instances={profile_meta['min_scale']}",
            f"--max-instances={profile_meta['max_scale']}",
            f"--timeout={profile_meta['timeout']}s",
        ]

        if profile_meta["cpu_throttling"]:
            flags.append("--cpu-throttling")
        else:
            flags.append("--no-cpu-throttling")

        if profile_meta["cpu_boost"]:
            flags.append("--cpu-boost")

        if profile_meta.get("execution_env"):
            flags.append(f"--execution-environment={profile_meta['execution_env']}")

        if profile_meta.get("session_affinity"):
            flags.append("--session-affinity")

        if profile_meta.get("gpu"):
            gpu = profile_meta["gpu"]
            flags.append(f"--gpu={gpu['count']} --gpu-type={gpu['type']}")

        deploy_command = " \\\n  ".join(flags)

        return {
            "service_name": service_name,
            "target_dir": str(self.target_dir),
            "profile_code": best_code,
            "profile_name": profile_meta["name"],
            "profile_desc": profile_meta["description"],
            "score": best_score,
            "evidence": self.evidence[best_code],
            "detected_languages": list(self.detected_languages),
            "detected_frameworks": list(self.detected_frameworks),
            "detected_databases": list(self.detected_databases),
            "dockerfile_info": self.dockerfile_info,
            "special_flags": self.special_flags,
            "recommended_config": profile_meta,
            "deploy_command": deploy_command,
            "all_scores": self.scores
        }


# ==============================================================================
# Formatters & Exporters
# ==============================================================================
def format_terminal(report: dict) -> str:
    cfg = report["recommended_config"]
    p_code = report["profile_code"]
    p_name = report["profile_name"]

    lines = []
    lines.append("==================================================================")
    lines.append(f" Cloud Run Workload Evaluation: {report['service_name']}")
    lines.append("==================================================================")
    
    lines.append("\n[1] Detected Tech Stack & Characteristics:")
    lines.append(f"  * Languages:     {', '.join(report['detected_languages']) or 'Generic / Container'}")
    lines.append(f"  * Frameworks:    {', '.join(report['detected_frameworks']) or 'Standard'}")
    if report["detected_databases"]:
        lines.append(f"  * Databases:     {', '.join(report['detected_databases'])}")
    if report["special_flags"]["has_gpu_model"]:
        lines.append("  * Hardware:      Requires GPU (NVIDIA L4) for ML/Inference")
    if report["special_flags"]["has_websockets"]:
        lines.append("  * Real-Time:     Persistent WebSockets detected")
    if report["special_flags"]["has_streaming"]:
        lines.append("  * Streaming:     SSE / LLM Streaming tokens detected")
    if report["special_flags"]["has_webhooks"]:
        lines.append("  * Events:        Inbound Webhook endpoints detected")

    lines.append(f"\n[2] Classified Workload Profile:")
    lines.append(f"  -> Profile {p_code}: {p_name}")
    lines.append(f"     \"{report['profile_desc']}\"")
    lines.append("     Primary Evidence:")
    for ev in report["evidence"][:3]:
        lines.append(f"       * {ev}")

    lines.append("\n[3] Tailored Cloud Run Sizing & Configuration:")
    lines.append(f"  * vCPU / Memory:         {cfg['cpu']} vCPU / {cfg['memory']}")
    lines.append(f"  * Concurrency:           {cfg['concurrency']} concurrent requests/instance")
    lines.append(f"  * CPU Allocation:        {'Request-based (--cpu-throttling) [Cost: $0 idle]' if cfg['cpu_throttling'] else 'Always-allocated (--no-cpu-throttling) [24/7]'}")
    lines.append(f"  * Startup CPU Boost:     {'Enabled (--cpu-boost)' if cfg['cpu_boost'] else 'Disabled'}")
    lines.append(f"  * Execution Environment: {cfg.get('execution_env', 'gen2')}")
    lines.append(f"  * Request Timeout:       {cfg['timeout']} seconds")
    lines.append(f"  * Session Affinity:      {'Enabled (--session-affinity)' if cfg.get('session_affinity') else 'Disabled (Stateless)'}")
    if cfg.get("gpu"):
        lines.append(f"  * GPU Accelerator:       {cfg['gpu']['count']}x {cfg['gpu']['type']}")
    lines.append(f"  * Autoscaling Limits:    min-instances={cfg['min_scale']}, max-instances={cfg['max_scale']}")
    lines.append(f"  * Sizing Justification:  {cfg['rationale']}")

    if report["special_flags"]["has_db_connection"]:
        max_inst = cfg['max_scale']
        lines.append("\n[!] Database Connection Pooling Alert:")
        lines.append(f"    Your service connects to a database and can autoscale to {max_inst} instances.")
        lines.append(f"    Cap your client connection pool per instance to prevent exhausting DB connections:")
        lines.append(f"      pool_size <= floor((DB_MAX_CONNECTIONS * 0.8) / {max_inst})")

    lines.append("\n[4] Tailored Deployment Command:")
    lines.append(f"```bash\n{report['deploy_command']}\n```")

    lines.append("==================================================================")
    return "\n".join(lines)


def format_yaml(report: dict, region="us-central1", project_id="PROJECT_ID") -> str:
    cfg = report["recommended_config"]
    name = report["service_name"]
    cpu = cfg["cpu"]
    memory = cfg["memory"]
    concurrency = cfg["concurrency"]
    timeout = cfg["timeout"]
    cpu_throttling = "true" if cfg["cpu_throttling"] else "false"
    cpu_boost = "true" if cfg["cpu_boost"] else "false"
    exec_env = cfg.get("execution_env", "gen2")
    min_scale = cfg["min_scale"]
    max_scale = cfg["max_scale"]

    annotations = [
        f'        run.googleapis.com/cpu-throttling: "{cpu_throttling}"',
        f'        run.googleapis.com/startup-cpu-boost: "{cpu_boost}"',
        f'        run.googleapis.com/execution-environment: {exec_env}',
        f'        autoscaling.knative.dev/minScale: "{min_scale}"',
        f'        autoscaling.knative.dev/maxScale: "{max_scale}"',
    ]

    if cfg.get("session_affinity"):
        annotations.append('        run.googleapis.com/sessionAffinity: "true"')

    gpu_limits = ""
    if cfg.get("gpu"):
        gpu = cfg["gpu"]
        annotations.append(f'        run.googleapis.com/gpu-type: {gpu["type"]}')
        gpu_limits = f'\n              nvidia.com/gpu: "{gpu["count"]}"'

    ann_str = "\n".join(annotations)

    return f"""apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: {name}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
{ann_str}
    spec:
      containerConcurrency: {concurrency}
      timeoutSeconds: {timeout}
      serviceAccountName: {name}-sa@{project_id}.iam.gserviceaccount.com
      containers:
        - image: {region}-docker.pkg.dev/{project_id}/apps/{name}:latest
          ports:
            - name: http1
              containerPort: 8080
          resources:
            limits:
              cpu: "{cpu}"
              memory: {memory}{gpu_limits}
"""


def format_terraform(report: dict, region="us-central1", project_id="PROJECT_ID") -> str:
    cfg = report["recommended_config"]
    name = report["service_name"]
    cpu = cfg["cpu"]
    memory = cfg["memory"]
    concurrency = cfg["concurrency"]
    timeout = f"{cfg['timeout']}s"
    cpu_idle = "true" if cfg["cpu_throttling"] else "false"
    cpu_boost = "true" if cfg["cpu_boost"] else "false"
    exec_env = "EXECUTION_ENVIRONMENT_GEN2" if cfg.get("execution_env") == "gen2" else "EXECUTION_ENVIRONMENT_GEN1"
    min_scale = cfg["min_scale"]
    max_scale = cfg["max_scale"]
    session_affinity = "true" if cfg.get("session_affinity") else "false"

    return f"""resource "google_cloud_run_v2_service" "{name.replace('-', '_')}" {{
  name     = "{name}"
  location = "{region}"
  project  = "{project_id}"
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {{
    execution_environment = "{exec_env}"
    session_affinity      = {session_affinity}
    timeout               = "{timeout}"
    max_instance_request_concurrency = {concurrency}

    scaling {{
      min_instance_count = {min_scale}
      max_instance_count = {max_scale}
    }}

    containers {{
      image = "{region}-docker.pkg.dev/{project_id}/apps/{name}:latest"

      resources {{
        limits = {{
          cpu    = "{cpu}"
          memory = "{memory}"
        }}
        cpu_idle = {cpu_idle}
        startup_cpu_boost = {cpu_boost}
      }}

      ports {{
        container_port = 8080
      }}
    }}
  }}
}}
"""


# ==============================================================================
# CLI Entrypoint
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Evaluate a codebase for optimal Cloud Run configuration.")
    parser.add_argument("target_dir", nargs="?", default=".", help="Root directory of the codebase to evaluate.")
    parser.add_argument("--format", choices=["terminal", "json", "deploy-cmd", "yaml", "tf"], default="terminal",
                        help="Output format (default: terminal)")
    parser.add_argument("--service-name", default=None, help="Cloud Run service name override")
    parser.add_argument("--region", default="us-central1", help="GCP region (default: us-central1)")
    parser.add_argument("--project", default="PROJECT_ID", help="GCP project ID (default: PROJECT_ID)")

    args = parser.parse_args()

    evaluator = CodebaseEvaluator(args.target_dir)
    report = evaluator.evaluate()

    if args.service_name:
        report["service_name"] = args.service_name

    if args.format == "terminal":
        print(format_terminal(report))
    elif args.format == "json":
        print(json.dumps(report, indent=2))
    elif args.format == "deploy-cmd":
        print(report["deploy_command"])
    elif args.format == "yaml":
        print(format_yaml(report, region=args.region, project_id=args.project))
    elif args.format == "tf":
        print(format_terraform(report, region=args.region, project_id=args.project))


if __name__ == "__main__":
    main()
