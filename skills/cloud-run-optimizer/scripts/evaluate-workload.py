#!/usr/bin/env python3
"""
Cloud Run Workload Evaluator & Adaptive Config Generator
Inspects a local codebase, detects frameworks, libraries, Dockerfile settings,
and code patterns to classify the workload into one of 10 Cloud Run Profiles.
Dynamically loads platform rules from platform-limits.json and probes the local
`gcloud` CLI to adapt generated flags to the installed SDK version.

Usage:
  python3 evaluate-workload.py [DIR] [options]
Options:
  --format {terminal,json,deploy-cmd,yaml,tf}  Output format (default: terminal)
  --service-name NAME                          Cloud Run service name (default: auto)
  --region REGION                              GCP region (default: us-central1)
  --project PROJECT_ID                         GCP project ID (default: PROJECT_ID)
  --no-gcloud-probe                            Skip probing local gcloud CLI help
"""

import os
import sys
import json
import re
import shutil
import subprocess
import argparse
from pathlib import Path

# ==============================================================================
# Load Externalized Platform Limits (platform-limits.json)
# ==============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
LIMITS_FILE = SCRIPT_DIR / "platform-limits.json"

DEFAULT_PLATFORM_LIMITS = {
    "schema_version": "2026.10",
    "execution_environments": {
        "gen1": {"min_cpu": 0.08, "min_memory_mb": 128, "supports_fractional_cpu": True},
        "gen2": {"min_cpu": 1.0, "min_memory_mb": 512, "supports_fractional_cpu": False}
    },
    "concurrency_rules": {
        "fractional_cpu_max_concurrency": 1,
        "standard_max_concurrency": 1000
    },
    "gpu_skus": {
        "nvidia-l4": {"min_cpu": 4, "min_memory_gi": 16, "supports_no_zonal_redundancy": True}
    }
}


def load_platform_limits() -> dict:
    if LIMITS_FILE.is_file():
        try:
            return json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_PLATFORM_LIMITS


PLATFORM_LIMITS = load_platform_limits()


# ==============================================================================
# Live `gcloud` CLI Capability Prober
# ==============================================================================
class GcloudCapabilityProber:
    """
    Probes the locally installed `gcloud` CLI to verify SDK version and whether
    advanced flags (like --no-gpu-zonal-redundancy, --gpu, worker-pools) are
    available in GA `gcloud run` or require `gcloud beta run` on this machine.
    """
    @staticmethod
    def probe(enabled: bool = True) -> dict:
        info = {
            "installed": False,
            "version": None,
            "ga_deploy_track": "gcloud run deploy",
            "supports_gpu_ga": True,
            "supports_no_gpu_zonal_ga": True,
            "notes": []
        }
        if not enabled:
            return info

        gcloud_bin = shutil.which("gcloud")
        if not gcloud_bin:
            info["notes"].append("gcloud CLI not found in PATH; using standard GA flag syntax from platform-limits.json.")
            return info

        info["installed"] = True
        try:
            ver_proc = subprocess.run(
                [gcloud_bin, "--version"],
                capture_output=True, text=True, timeout=2.5
            )
            if ver_proc.returncode == 0 and ver_proc.stdout:
                first_line = ver_proc.stdout.splitlines()[0].strip()
                info["version"] = first_line
        except Exception:
            pass

        try:
            help_proc = subprocess.run(
                [gcloud_bin, "run", "deploy", "--help"],
                capture_output=True, text=True, timeout=3.0
            )
            if help_proc.returncode == 0 and help_proc.stdout:
                help_text = help_proc.stdout
                if "--gpu" not in help_text:
                    info["supports_gpu_ga"] = False
                if "--no-gpu-zonal-redundancy" not in help_text and "gpu-zonal-redundancy" not in help_text:
                    info["supports_no_gpu_zonal_ga"] = False
        except Exception:
            pass

        return info


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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 30,
        "startup_probe_failures": 10,
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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "startup_probe_failures": 15,
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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 40,
        "startup_probe_failures": 15,
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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 25,
        "startup_probe_failures": 15,
        "rationale": "Higher timeout (600s) supports slow LLM completions; request-based billing keeps CPU allocated during active token streams."
    },
    "E": {
        "name": "Background Queue Worker & Scheduled Batch Job",
        "description": "Asynchronous job processing, queue consumption, DB migrations, or cron tasks.",
        "cpu": "2",
        "memory": "2Gi",
        "concurrency": 1,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 1800,
        "session_affinity": False,
        "allow_unauthenticated": False,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 10,
        "startup_probe_failures": 15,
        "primitive": "Cloud Run Job / Push Worker",
        "rationale": "Use Cloud Run Jobs (gcloud run jobs deploy) for batch/cron tasks, or Cloud Tasks/PubSub HTTP push with --cpu-throttling to scale worker services to zero."
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
        "allow_unauthenticated": True,
        "gpu": {"count": 1, "type": "nvidia-l4", "no_zonal_redundancy": True},
        "min_scale": 0,
        "max_scale": 5,
        "startup_probe_failures": 30,
        "rationale": "Requires GPU accelerator (--gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy), >=4 vCPU / >=16Gi RAM, CPU always allocated, and GCS volume mount for model weights."
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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 30,
        "startup_probe_failures": 25,
        "rationale": "Needs JVM memory cgroup limit (-XX:MaxRAMPercentage=75.0) and Startup CPU Boost to smooth JIT warmup."
    },
    "H": {
        "name": "High-Burst Event & Webhook Receiver",
        "description": "Stripe, GitHub, Shopify webhooks, Eventarc triggers, or Pub/Sub push receivers.",
        "cpu": "1",
        "memory": "512Mi",
        "concurrency": 20,
        "cpu_throttling": True,
        "cpu_boost": True,
        "execution_env": "gen2",
        "timeout": 20,
        "session_affinity": False,
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 50,
        "startup_probe_failures": 10,
        "rationale": "Bounded concurrency (20) and short timeouts protect internal databases from sudden burst avalanches (uses 512Mi minimum required for gen2)."
    },
    "I": {
        "name": "Static SPA, Reverse Proxy & Edge Gateway",
        "description": "NGINX, Caddy, Envoy, lightweight Go reverse proxy, or static frontend container.",
        "cpu": "1",
        "memory": "256Mi",
        "concurrency": 200,
        "cpu_throttling": True,
        "cpu_boost": False,
        "execution_env": "gen1",
        "timeout": 60,
        "session_affinity": False,
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "startup_probe_failures": 5,
        "rationale": "Requires >= 1 vCPU because Cloud Run forces concurrency=1 on fractional (<1) CPUs. Gen1 + 256Mi gives sub-100ms cold starts handling 200 concurrent connections."
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
        "allow_unauthenticated": True,
        "gpu": None,
        "min_scale": 0,
        "max_scale": 20,
        "startup_probe_failures": 10,
        "rationale": "Requires --session-affinity to route sticky sessions to the same instance and --timeout=3600s (max allowed)."
    }
}

# ==============================================================================
# Analyzer Engine
# ==============================================================================
class CodebaseEvaluator:
    def __init__(self, target_dir: str, probe_gcloud: bool = True):
        self.target_dir = Path(target_dir).resolve()
        self.probe_gcloud = probe_gcloud
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
            "has_sqlite": False,
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
        excluded_files = {
            "evaluate-workload.py", "audit-codebase.sh",
            "audit-live-service.sh", "platform-limits.json"
        }
        for root, dirs, files in os.walk(self.target_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for f in files:
                if f in excluded_files:
                    continue
                rel_path = os.path.relpath(os.path.join(root, f), self.target_dir)
                self.files.append(rel_path)
                if f.endswith(".sqlite") or f.endswith(".sqlite3") or f == "dev.db":
                    self.special_flags["has_sqlite"] = True
                    self.detected_databases.add("SQLite (Local File DB - Ephemeral Risk!)")

    def _file_exists(self, filename: str) -> bool:
        return any(f == filename or f.endswith("/" + filename) for f in self.files)

    def _read_file_safe(self, filename: str) -> str:
        full_path = self.target_dir / filename
        if not full_path.is_file():
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
        # Check Prisma schema for SQLite or Postgres/MySQL
        prisma_schema = self._read_file_safe("schema.prisma")
        if prisma_schema:
            if re.search(r'provider\s*=\s*["\']sqlite["\']', prisma_schema, re.IGNORECASE):
                self.special_flags["has_sqlite"] = True
                self.detected_databases.add("Prisma SQLite (Ephemeral Risk!)")
            elif re.search(r'provider\s*=\s*["\'](postgresql|postgres|mysql|cockroachdb|sqlserver)["\']', prisma_schema, re.IGNORECASE):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Prisma Relational DB")

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
                
                if any(k in deps for k in ["next", "nuxt", "@remix-run/node", "@sveltejs/kit", "astro"]):
                    self.detected_frameworks.add("Full-Stack SSR")
                    self._add_score("C", 40, "Detected SSR framework in package.json (Next/Nuxt/Remix/SvelteKit/Astro)")

                if any(k in deps for k in ["socket.io", "ws", "sockjs", "uWebSockets.js"]):
                    self.special_flags["has_websockets"] = True
                    self.detected_frameworks.add("WebSockets")
                    self._add_score("J", 35, "Detected WebSocket library in package.json (socket.io/ws)")

                if any(k in deps for k in ["ai", "@ai-sdk/openai", "@ai-sdk/anthropic", "langchain", "@langchain/core", "openai", "@anthropic-ai/sdk", "@google/genai", "@modelcontextprotocol/sdk"]):
                    self.detected_frameworks.add("AI/LLM SDK")
                    self.special_flags["has_streaming"] = True
                    self._add_score("D", 30, "Detected AI/LLM SDK in package.json (LangChain/OpenAI/Anthropic/MCP)")

                if any(k in deps for k in ["bullmq", "bull", "agenda", "pg-boss", "amqplib"]):
                    self.special_flags["has_queue_worker"] = True
                    self._add_score("E", 25, "Detected queue/broker library in package.json (BullMQ/Amqplib)")

                if any(k in deps for k in ["express", "fastify", "hono", "@nestjs/core", "koa"]):
                    self.detected_frameworks.add("Async Node.js API")
                    self._add_score("A", 25, "Detected lightweight async HTTP framework (Express/Fastify/Hono/NestJS)")

                if any(k in deps for k in ["@prisma/client", "prisma", "pg", "mysql2", "typeorm", "mongoose", "drizzle-orm", "knex", "sequelize"]):
                    self.special_flags["has_db_connection"] = True
                    self.detected_databases.add("Relational/Document DB (Node)")

                if any(k in deps for k in ["better-sqlite3", "sqlite3", "sql.js", "@libsql/client"]):
                    self.special_flags["has_sqlite"] = True
                    self.detected_databases.add("SQLite (Node - Ephemeral Risk!)")

            except Exception:
                pass

        # 2. Python
        pyproject = self._read_file_safe("pyproject.toml")
        reqs = self._read_file_safe("requirements.txt")
        pipfile = self._read_file_safe("Pipfile")
        python_text = f"{pyproject}\n{reqs}\n{pipfile}".lower()

        if python_text.strip():
            self.detected_languages.add("Python")
            
            if any(k in python_text for k in ["vllm", "torch", "transformers", "diffusers", "accelerate", "onnxruntime-gpu", "deepspeed", "ollama", "whisper"]):
                self.special_flags["has_gpu_model"] = True
                self.detected_frameworks.add("Deep Learning / PyTorch")
                self._add_score("F", 50, "Detected GPU/Deep Learning dependencies (torch/vllm/transformers/diffusers)")

            if any(k in python_text for k in ["langchain", "llama-index", "openai", "anthropic", "google-genai", "mcp", "instructor"]):
                self.detected_frameworks.add("AI/LLM Framework")
                self.special_flags["has_streaming"] = True
                self._add_score("D", 30, "Detected AI/LLM client libraries (LangChain/LlamaIndex/OpenAI/Anthropic/MCP)")

            if any(k in python_text for k in ["fastapi", "starlette", "uvicorn", "sanic", "aiohttp"]):
                self.detected_frameworks.add("Python ASGI (FastAPI)")
                self._add_score("A", 30, "Detected Python ASGI async web framework (FastAPI/Starlette/Uvicorn)")

            if any(k in python_text for k in ["django", "flask", "gunicorn", "uwsgi", "falcon", "bottle"]):
                self.detected_frameworks.add("Python WSGI (Django/Flask)")
                self._add_score("B", 35, "Detected Python synchronous WSGI web framework (Django/Flask/Gunicorn)")

            if any(k in python_text for k in ["celery", "rq", "arq", "kombu"]):
                self.special_flags["has_queue_worker"] = True
                self._add_score("E", 30, "Detected background task queue worker (Celery/RQ/Arq)")

            if any(k in python_text for k in ["sqlalchemy", "psycopg", "asyncpg", "pymongo", "tortoise-orm", "peewee", "sqlmodel"]):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Relational/Document DB (Python)")

            if any(k in python_text for k in ["aiosqlite", "sqlite"]):
                self.special_flags["has_sqlite"] = True
                self.detected_databases.add("SQLite (Python - Ephemeral Risk!)")

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
            if any(k in go_mod for k in ["go-sqlite3", "modernc.org/sqlite", "gorm.io/driver/sqlite"]):
                self.special_flags["has_sqlite"] = True
                self.detected_databases.add("SQLite (Go - Ephemeral Risk!)")

        # 4. Rust
        cargo = self._read_file_safe("Cargo.toml")
        if cargo:
            self.detected_languages.add("Rust")
            self._add_score("A", 30, "Detected Rust (Tokio/Actix/Axum high-performance async)")
            if any(k in cargo for k in ["sqlx", "diesel", "sea-orm"]):
                self.special_flags["has_db_connection"] = True
                self.detected_databases.add("Relational DB (Rust)")
            if "rusqlite" in cargo:
                self.special_flags["has_sqlite"] = True
                self.detected_databases.add("SQLite (Rust - Ephemeral Risk!)")

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
        
        if re.search(r'FROM\s+.*(cuda|nvidia|vllm|pytorch)', dockerfile, re.IGNORECASE):
            self.special_flags["has_gpu_model"] = True
            self._add_score("F", 40, "Dockerfile uses NVIDIA/CUDA/PyTorch GPU base image")

        if re.search(r'FROM\s+.*(nginx|caddy|traefik)', dockerfile, re.IGNORECASE):
            self._add_score("I", 40, "Dockerfile uses NGINX/Caddy reverse proxy base image")

        if re.search(r'FROM\s+.*(openjdk|temurin|corretto|dotnet)', dockerfile, re.IGNORECASE):
            self._add_score("G", 35, "Dockerfile uses Java/JVM or .NET base image")

        stages = len(re.findall(r'^\s*FROM\s+', dockerfile, re.MULTILINE | re.IGNORECASE))
        self.dockerfile_info["stages"] = stages
        self.dockerfile_info["is_multistage"] = stages >= 2
        self.dockerfile_info["has_user"] = bool(re.search(r'^\s*USER\s+', dockerfile, re.MULTILINE | re.IGNORECASE))
        self.dockerfile_info["exec_form_cmd"] = bool(re.search(r'^\s*(CMD|ENTRYPOINT)\s*\[', dockerfile, re.MULTILINE))

        if "next" in dockerfile and "standalone" in dockerfile:
            self._add_score("C", 15, "Dockerfile leverages Next.js standalone output")

    def _analyze_code_patterns(self):
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

            if re.search(r'(stripe\.webhook|github-webhook|/webhook|eventarc|verifyWebhookSignature)', content, re.IGNORECASE):
                webhook_hits += 1

            if re.search(r'(\.upgrade\(|WebSocket\(|handle_websocket|on\(["\']connection["\'])', content):
                websocket_hits += 1

            if re.search(r'(text/event-stream|StreamingResponse|ReadableStream|res\.write\(|flush_interval)', content):
                stream_hits += 1

            if re.search(r'(setInterval|node-cron|cron\.schedule|BackgroundScheduler|time\.NewTicker)', content):
                timer_hits += 1

            if re.search(r'(sqlite3\.connect|sqlite:///|better-sqlite3|file:\./.*\.db)', content):
                self.special_flags["has_sqlite"] = True

        if webhook_hits >= 2:
            self.special_flags["has_webhooks"] = True
            self._add_score("H", 30, f"Found webhook handling endpoints in {webhook_hits} files")

        if websocket_hits >= 1 and not self.special_flags["has_websockets"]:
            self.special_flags["has_websockets"] = True
            self._add_score("J", 25, "Found WebSocket handlers in source code")

        if stream_hits >= 1:
            self.special_flags["has_streaming"] = True
            self._add_score("D", 20, "Found streaming/SSE response patterns in source code")

        if timer_hits >= 1:
            self.special_flags["has_cron_timers"] = True

    @staticmethod
    def _parse_memory_mb(mem_str: str) -> int:
        if mem_str.endswith("Gi"):
            return int(float(mem_str[:-2]) * 1024)
        if mem_str.endswith("Mi"):
            return int(float(mem_str[:-2]))
        return 512

    def _compute_runtime_env_vars(self, profile_meta: dict) -> dict:
        """Compute runtime-specific memory & logging environment variables."""
        env_vars = {}
        mem_mb = self._parse_memory_mb(profile_meta.get("memory", "512Mi"))

        if "Node.js/TypeScript" in self.detected_languages:
            heap_mb = int(mem_mb * 0.75)
            env_vars["NODE_ENV"] = "production"
            env_vars["NODE_OPTIONS"] = f"--max-old-space-size={heap_mb}"
        if "Java/JVM" in self.detected_languages:
            env_vars["JAVA_TOOL_OPTIONS"] = "-XX:MaxRAMPercentage=75.0 -XX:+ExitOnOutOfMemoryError"
        if "Python" in self.detected_languages:
            env_vars["PYTHONUNBUFFERED"] = "1"
        if "Go" in self.detected_languages:
            gomem_mb = int(mem_mb * 0.85)
            env_vars["GOMEMLIMIT"] = f"{gomem_mb}MiB"
        return env_vars

    def _enforce_platform_constraints(self, profile_meta: dict):
        """Enforce dynamic GCP constraints from platform-limits.json."""
        exec_envs = PLATFORM_LIMITS.get("execution_environments", {})
        conc_rules = PLATFORM_LIMITS.get("concurrency_rules", {})
        env_name = profile_meta.get("execution_env", "gen2")
        env_limits = exec_envs.get(env_name, {"min_cpu": 1.0, "min_memory_mb": 512})

        min_mem_mb = env_limits.get("min_memory_mb", 512)
        if self._parse_memory_mb(profile_meta["memory"]) < min_mem_mb:
            profile_meta["memory"] = f"{min_mem_mb}Mi"

        min_cpu = float(env_limits.get("min_cpu", 1.0))
        if float(profile_meta["cpu"]) < min_cpu:
            profile_meta["cpu"] = str(int(min_cpu) if min_cpu.is_integer() else min_cpu)

        frac_max_conc = conc_rules.get("fractional_cpu_max_concurrency", 1)
        if float(profile_meta["cpu"]) < 1.0 and int(profile_meta["concurrency"]) > frac_max_conc:
            profile_meta["cpu"] = "1"

        if profile_meta.get("gpu"):
            gpu_type = profile_meta["gpu"]["type"]
            gpu_sku = PLATFORM_LIMITS.get("gpu_skus", {}).get(gpu_type, {})
            sku_min_cpu = gpu_sku.get("min_cpu", 4)
            sku_min_mem_gi = gpu_sku.get("min_memory_gi", 16)
            if float(profile_meta["cpu"]) < sku_min_cpu:
                profile_meta["cpu"] = str(sku_min_cpu)
            if self._parse_memory_mb(profile_meta["memory"]) < sku_min_mem_gi * 1024:
                profile_meta["memory"] = f"{sku_min_mem_gi}Gi"

    def _synthesize_recommendations(self):
        sorted_profiles = sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
        best_code, best_score = sorted_profiles[0]

        if best_score < 15:
            best_code = "A"
            self.evidence["A"].append("Defaulted to I/O-Bound Async API baseline (no specialized signals detected)")

        profile_meta = PROFILES[best_code].copy()

        if self.special_flags["has_streaming"] and best_code != "F":
            profile_meta["timeout"] = max(profile_meta["timeout"], 300)
        
        if self.special_flags["has_websockets"]:
            profile_meta["session_affinity"] = True
            profile_meta["timeout"] = 3600

        # Dynamically enforce platform limits from platform-limits.json
        self._enforce_platform_constraints(profile_meta)

        # Probe local gcloud CLI capabilities
        gcloud_info = GcloudCapabilityProber.probe(enabled=self.probe_gcloud)

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

        runtime_env = self._compute_runtime_env_vars(profile_meta)
        profile_meta["runtime_env"] = runtime_env

        # Determine gcloud track (GA vs beta if local CLI is older)
        deploy_cmd_prefix = "gcloud run deploy"
        if profile_meta.get("gpu") and gcloud_info["installed"]:
            if not gcloud_info["supports_gpu_ga"] or not gcloud_info["supports_no_gpu_zonal_ga"]:
                deploy_cmd_prefix = "gcloud beta run deploy"
                gcloud_info["notes"].append(
                    "Local gcloud SDK requires 'gcloud beta run deploy' for GPU / zonal-redundancy flags."
                )

        # Build gcloud deploy command (includes --source=. so beginners can deploy directly from folder)
        if best_code == "E":
            job_flags = [
                f"# Option 1: Batch / Scheduled Job (Cloud Run Jobs)",
                f"gcloud run jobs deploy {service_name}-job \\",
                f"  --source=. \\",
                f"  --region=us-central1 \\",
                f"  --cpu={profile_meta['cpu']} \\",
                f"  --memory={profile_meta['memory']} \\",
                f"  --tasks=1 \\",
                f"  --max-retries=3 \\",
                f"  --task-timeout={profile_meta['timeout']}s \\",
                f"  --execution-environment=gen2",
                f"",
                f"# Option 2: HTTP Push Queue Consumer (Cloud Tasks / PubSub Push -> Cloud Run Service)",
                f"{deploy_cmd_prefix} {service_name}-worker \\",
                f"  --source=. \\",
                f"  --region=us-central1 \\",
                f"  --cpu={profile_meta['cpu']} \\",
                f"  --memory={profile_meta['memory']} \\",
                f"  --concurrency=4 \\",
                f"  --min-instances=0 \\",
                f"  --max-instances={profile_meta['max_scale']} \\",
                f"  --timeout=600s \\",
                f"  --cpu-throttling \\",
                f"  --cpu-boost \\",
                f"  --execution-environment=gen2 \\",
                f"  --no-allow-unauthenticated"
            ]
            deploy_command = "\n".join(job_flags)
        else:
            flags = [
                f"{deploy_cmd_prefix} {service_name}",
                f"--source=.",
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

            if profile_meta.get("allow_unauthenticated", True):
                flags.append("--allow-unauthenticated")

            if profile_meta.get("gpu"):
                gpu = profile_meta["gpu"]
                flags.append(f"--gpu={gpu['count']} --gpu-type={gpu['type']}")
                if gpu.get("no_zonal_redundancy"):
                    flags.append("--no-gpu-zonal-redundancy")

            deploy_command = " \\\n  ".join(flags)

        return {
            "service_name": service_name,
            "target_dir": str(self.target_dir),
            "platform_limits_version": PLATFORM_LIMITS.get("schema_version", "unknown"),
            "gcloud_cli": gcloud_info,
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
    g_info = report.get("gcloud_cli", {})

    lines = []
    lines.append("==================================================================")
    lines.append(f" Cloud Run Workload Evaluation: {report['service_name']}")
    lines.append(f" Platform Schema: v{report.get('platform_limits_version', '2026.10')} | gcloud CLI: {g_info.get('version') or 'Not detected (offline mode)'}")
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
        zonal_note = " (--no-gpu-zonal-redundancy enabled to save ~35% cost)" if cfg["gpu"].get("no_zonal_redundancy") else ""
        lines.append(f"  * GPU Accelerator:       {cfg['gpu']['count']}x {cfg['gpu']['type']}{zonal_note}")
    lines.append(f"  * Autoscaling Limits:    min-instances={cfg['min_scale']}, max-instances={cfg['max_scale']}")
    if cfg.get("runtime_env"):
        env_summary = ", ".join(f"{k}={v}" for k, v in cfg["runtime_env"].items())
        lines.append(f"  * Runtime Env Tuning:    {env_summary}")
    lines.append(f"  * Sizing Justification:  {cfg['rationale']}")

    for note in g_info.get("notes", []):
        lines.append(f"  * CLI Adaptation Note:   {note}")

    lines.append("\n[4] Plain-English Summary (What This Means For Your App & Bill):")
    if cfg["min_scale"] == 0 and cfg["cpu_throttling"]:
        lines.append("  * $0 Idle Cost:      Your app scales to zero when nobody is using it—you pay $0/month while idle.")
    else:
        lines.append("  * Billing Mode:      Scales to zero when idle; bills for active instance uptime while running.")
    if cfg["cpu_boost"]:
        lines.append("  * Fast Cold Starts:  Extra CPU is boosted automatically during startup (--cpu-boost) at $0 idle cost.")
    lines.append(f"  * Bill Protection:   Capped at {cfg['max_scale']} max instances so a traffic spike or bot attack cannot cause a runaway bill.")
    lines.append(f"  * High Efficiency:   Each container handles up to {cfg['concurrency']} requests at once before spinning up another container.")

    if report["special_flags"]["has_sqlite"]:
        lines.append("\n[!!!] CRITICAL DATA LOSS WARNING (SQLite Detected):")
        lines.append("    Your project appears to use SQLite (local .db/.sqlite file).")
        lines.append("    Cloud Run containers are stateless—every time your app scales to zero or restarts,")
        lines.append("    ALL LOCAL SQLITE DATA WILL BE PERMANENTLY WIPED!")
        lines.append("    -> Fix: Switch to a cloud database (Cloud SQL Postgres, Firestore, Supabase, or Neon)")
        lines.append("       before deploying to production.")

    if report["special_flags"]["has_db_connection"]:
        max_inst = cfg['max_scale']
        lines.append("\n[!] Database Connection Pooling Alert:")
        lines.append(f"    Your service connects to a database and can autoscale to {max_inst} instances.")
        lines.append(f"    Cap your client connection pool per instance to prevent exhausting DB connections:")
        lines.append(f"      pool_size <= floor((DB_MAX_CONNECTIONS * 0.8) / {max_inst})")

    lines.append("\n[5] Ready-to-Run Commands (Copy & Paste):")
    lines.append("# (Optional) Run once if this is a brand-new Google Cloud project:")
    lines.append("gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com\n")
    lines.append("# Build & deploy directly from your project folder:")
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
    probe_failures = cfg.get("startup_probe_failures", 15)

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
        if gpu.get("no_zonal_redundancy"):
            annotations.append('        run.googleapis.com/gpu-zonal-redundancy-disabled: "true"')
        gpu_limits = f'\n              nvidia.com/gpu: "{gpu["count"]}"'

    ann_str = "\n".join(annotations)

    env_block = ""
    if cfg.get("runtime_env"):
        env_lines = ["          env:"]
        for k, v in cfg["runtime_env"].items():
            env_lines.append(f'            - name: {k}\n              value: "{v}"')
        env_block = "\n" + "\n".join(env_lines)

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
              containerPort: 8080{env_block}
          resources:
            limits:
              cpu: "{cpu}"
              memory: {memory}{gpu_limits}
          startupProbe:
            tcpSocket:
              port: 8080
            initialDelaySeconds: 0
            periodSeconds: 2
            timeoutSeconds: 1
            failureThreshold: {probe_failures}
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
    probe_failures = cfg.get("startup_probe_failures", 15)

    env_blocks = ""
    if cfg.get("runtime_env"):
        eb = []
        for k, v in cfg["runtime_env"].items():
            eb.append(f"""
      env {{
        name  = "{k}"
        value = "{v}"
      }}""")
        env_blocks = "".join(eb)

    return f"""resource "google_cloud_run_v2_service" "{name.replace('-', '_')}" {{
  name     = "{name}"
  location = "{region}"
  project  = "{project_id}"
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {{
    execution_environment            = "{exec_env}"
    session_affinity                 = {session_affinity}
    timeout                          = "{timeout}"
    max_instance_request_concurrency = {concurrency}

    scaling {{
      min_instance_count = {min_scale}
      max_instance_count = {max_scale}
    }}

    containers {{
      image = "{region}-docker.pkg.dev/{project_id}/apps/{name}:latest"

      ports {{
        name           = "http1"
        container_port = 8080
      }}{env_blocks}

      resources {{
        limits = {{
          cpu    = "{cpu}"
          memory = "{memory}"
        }}
        cpu_idle          = {cpu_idle}
        startup_cpu_boost = {cpu_boost}
      }}

      startup_probe {{
        initial_delay_seconds = 0
        period_seconds        = 2
        timeout_seconds       = 1
        failure_threshold     = {probe_failures}
        tcp_socket {{
          port = 8080
        }}
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
    parser.add_argument("--no-gcloud-probe", action="store_true", help="Skip probing local gcloud CLI help")

    args = parser.parse_args()

    evaluator = CodebaseEvaluator(args.target_dir, probe_gcloud=not args.no_gcloud_probe)
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
