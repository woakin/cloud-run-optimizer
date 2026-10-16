#!/usr/bin/env bash
# ==============================================================================
# Cloud Run Codebase & Dockerfile Static Auditor
# Scans a local project directory for Cloud Run performance & cost anti-patterns.
# Usage: bash audit-codebase.sh [TARGET_DIR]
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-.}"
if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory '$TARGET_DIR' does not exist." >&2
  exit 1
fi

cd "$TARGET_DIR"

echo "=================================================================="
echo " Cloud Run Static Optimization Audit: $(pwd)"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 1. Detect Stack & Manifests
# ------------------------------------------------------------------------------
echo ""
echo "[1/5] Detected Project Stack & Manifests:"
STACKS=()
[ -f "package.json" ] && STACKS+=("Node.js/TypeScript (package.json)")
[ -f "next.config.js" ] || [ -f "next.config.mjs" ] || [ -f "next.config.ts" ] && STACKS+=("Next.js")
[ -f "requirements.txt" ] || [ -f "pyproject.toml" ] || [ -f "Pipfile" ] && STACKS+=("Python")
[ -f "go.mod" ] && STACKS+=("Go (go.mod)")
[ -f "Cargo.toml" ] && STACKS+=("Rust (Cargo.toml)")
[ -f "pom.xml" ] || [ -f "build.gradle" ] || [ -f "build.gradle.kts" ] && STACKS+=("Java/JVM")
[ -f "Gemfile" ] && STACKS+=("Ruby (Gemfile)")
[ -f "composer.json" ] && STACKS+=("PHP (composer.json)")

if [ ${#STACKS[@]} -eq 0 ]; then
  echo "  - No standard language manifest found in root."
else
  for s in "${STACKS[@]}"; do
    echo "  - $s"
  done
fi

# ------------------------------------------------------------------------------
# 2. Dockerfile Audit
# ------------------------------------------------------------------------------
echo ""
echo "[2/5] Dockerfile & Container Build Audit:"
if [ -f "Dockerfile" ]; then
  echo "  - Dockerfile: FOUND"

  STAGE_COUNT=$(grep -iE '^[[:space:]]*FROM[[:space:]]+' Dockerfile | wc -l | tr -d ' ')
  if [ "$STAGE_COUNT" -lt 2 ]; then
    echo "  [WARN] Single-stage Dockerfile detected ($STAGE_COUNT FROM statement). Use multi-stage builds to shrink image size and speed up cold starts."
  else
    echo "  [OK]   Multi-stage build detected ($STAGE_COUNT stages)."
  fi

  if grep -iE '^[[:space:]]*FROM[[:space:]]+(node|python|ubuntu|debian):[^-[:space:]]+$' Dockerfile >/dev/null 2>&1; then
    echo "  [WARN] Heavy full OS base image detected. Switch to '-slim', '-alpine', or 'gcr.io/distroless/*' to cut cold start time."
  fi

  if ! grep -iE '^[[:space:]]*USER[[:space:]]+' Dockerfile >/dev/null 2>&1; then
    echo "  [INFO] No 'USER' instruction in Dockerfile. Consider running as non-root (e.g., USER node / nonroot)."
  fi

  if grep -E '^[[:space:]]*(CMD|ENTRYPOINT)[[:space:]]+[^[]' Dockerfile >/dev/null 2>&1; then
    echo "  [WARN] Shell-form CMD/ENTRYPOINT detected in Dockerfile. Use exec form 'CMD [\"executable\", \"arg\"]' so SIGTERM reaches your process during Cloud Run scale-down."
  fi

  if [ -f "package.json" ] && ! grep -q "max-old-space-size" Dockerfile 2>/dev/null; then
    echo "  [INFO] Node.js Dockerfile missing 'NODE_OPTIONS=--max-old-space-size=...'. Cap V8 heap at ~75% of container memory to prevent silent OOM kills."
  fi

  if ( [ -f "pom.xml" ] || [ -f "build.gradle" ] || [ -f "build.gradle.kts" ] ) && ! grep -q "MaxRAMPercentage" Dockerfile 2>/dev/null; then
    echo "  [WARN] Java/JVM Dockerfile missing '-XX:MaxRAMPercentage=75.0'. JVM may exceed Cloud Run cgroup memory limit and crash with OOM (exit code 137)."
  fi

  if grep -iE '(npm[[:space:]]+run[[:space:]]+dev|yarn[[:space:]]+dev|pnpm[[:space:]]+dev|next[[:space:]]+dev|--reload)' Dockerfile >/dev/null 2>&1; then
    echo "  [WARN] Development server command ('npm run dev' / '--reload') detected in Dockerfile! Always run a production build ('npm run build' + 'npm start' / 'node server.js') on Cloud Run."
  fi
else
  echo "  - Dockerfile: NOT FOUND (Will rely on Cloud Buildpacks if deploying from source)."
fi

if [ ! -f ".dockerignore" ]; then
  echo "  [WARN] Missing .dockerignore file. Build context uploads to Cloud Build may include node_modules/.git/.venv and slow down deployments."
else
  echo "  [OK]   .dockerignore present."
fi

# Next.js standalone check
for next_cfg in next.config.js next.config.mjs next.config.ts; do
  if [ -f "$next_cfg" ]; then
    if ! grep -E "output[[:space:]]*:[[:space:]]*['\"]standalone['\"]" "$next_cfg" >/dev/null 2>&1; then
      echo "  [WARN] Next.js project detected ($next_cfg) without \"output: 'standalone'\". Enabling standalone mode reduces Cloud Run image size by ~70%."
    else
      echo "  [OK]   Next.js standalone output enabled in $next_cfg."
    fi
  fi
done

# ------------------------------------------------------------------------------
# 3. Serverless Code Anti-Patterns Scan
# ------------------------------------------------------------------------------
echo ""
echo "[3/5] Serverless Code Anti-Pattern Scan:"

EXCLUDE_DIRS=(
  --exclude-dir=node_modules
  --exclude-dir=.git
  --exclude-dir=.next
  --exclude-dir=dist
  --exclude-dir=build
  --exclude-dir=.venv
  --exclude-dir=venv
  --exclude-dir=__pycache__
  --exclude-dir=target
  --exclude-dir=vendor
  --exclude-dir=.exodus
  --exclude-dir=.agents
  --exclude-dir=.claude
  --exclude="*.md"
  --exclude="audit-codebase.sh"
  --exclude="audit-live-service.sh"
  --exclude="evaluate-workload.py"
  --exclude="platform-limits.json"
)

scan_pattern() {
  local label="$1"
  local pattern="$2"
  local advice="$3"
  local hits
  hits=$(grep -rnI "${EXCLUDE_DIRS[@]}" -E "$pattern" . 2>/dev/null | head -n 5 || true)
  if [ -n "$hits" ]; then
    echo "  [WARN] $label"
    echo "         Advice: $advice"
    echo "$hits" | sed 's/^/           -> /'
  fi
}

scan_pattern \
  "SQLite / Local File Database Detected (CRITICAL DATA LOSS RISK)" \
  "(better-sqlite3|sqlite3\.connect|sqlite:///|provider[[:space:]]*=[[:space:]]*['\"]sqlite['\"]|file:\./.*\.db)" \
  "Cloud Run containers are stateless—local SQLite files are permanently wiped every time the container restarts or scales to zero! Switch to Cloud SQL, Firestore, Supabase, or Neon."

scan_pattern \
  "Hardcoded Dev Port (3000 / 5000 / 8000) Without \$PORT" \
  "\.(listen|run)\([[:space:]]*(3000|5000|8000)[[:space:],)]" \
  "Cloud Run injects PORT=8080 dynamically. Always read process.env.PORT || 8080 (Node) or os.environ.get('PORT', 8080) (Python) and bind to 0.0.0.0."

scan_pattern \
  "In-Process Timers / Cron Detected" \
  "(node-cron|cron\.schedule|APScheduler|BackgroundScheduler|setInterval\(|time\.NewTicker)" \
  "In-process timers freeze when CPU is throttled and duplicate across instances. Use Cloud Scheduler + Cloud Run Jobs."

scan_pattern \
  "In-Memory Session Store Detected" \
  "(express-session|MemoryStore|CookieSession|session\.MemoryStore)" \
  "In-memory sessions break when Cloud Run autoscales past 1 instance. Use signed JWTs, Firestore, or Memorystore."

scan_pattern \
  "Background Work After HTTP Response Detected" \
  "(BackgroundTasks|background_tasks\.add_task)" \
  "FastAPI BackgroundTasks / post-response work freezes when request-based CPU throttling activates. Use Cloud Tasks or Pub/Sub."

scan_pattern \
  "Hardcoded Loopback Binding (127.0.0.1 / localhost)" \
  "\.(listen|run)\([^)]*['\"](127\.0\.0\.1|localhost)['\"]" \
  "Cloud Run containers must bind to '0.0.0.0' and listen on \$PORT, never 127.0.0.1 or localhost."

scan_pattern \
  "Continuous Queue Consumer Loop Detected" \
  "(new Worker\(|celery\.worker|Sidekiq|BasicConsume)" \
  "Pull-based queue workers inside HTTP Services prevent scale-to-zero. Use Cloud Tasks HTTP push, Pub/Sub push, or Cloud Run Worker Pools."

scan_pattern \
  "Unbounded or Large Database Pool Detected" \
  "(pool_size[[:space:]]*=[[:space:]]*[2-9][0-9]|connection_limit[[:space:]]*=[[:space:]]*[2-9][0-9]|max:[[:space:]]*[2-9][0-9])" \
  "Large database pool size detected. Ensure pool size per instance is bounded and coupled to --max-instances to prevent Cloud SQL connection starvation (see references/cloud-sql.md)."

# Check SIGTERM handling
SIGTERM_HITS=$(grep -rnI "${EXCLUDE_DIRS[@]}" -E "SIGTERM" . 2>/dev/null | head -n 3 || true)
if [ -z "$SIGTERM_HITS" ]; then
  echo "  [INFO] No explicit 'SIGTERM' handler found in codebase. Add graceful shutdown handling to drain connections within Cloud Run's 10s termination window."
else
  echo "  [OK]   SIGTERM graceful shutdown reference found."
fi

# ------------------------------------------------------------------------------
# 4. Existing Cloud Run / IaC Config Scan
# ------------------------------------------------------------------------------
echo ""
echo "[4/5] Infrastructure Configs (YAML / Terraform / scripts) Scan:"
IAC_FILES=$(find . -maxdepth 3 \( -name "*.yaml" -o -name "*.yml" -o -name "*.tf" -o -name "cloudbuild*.yaml" \) \
  -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/.venv/*" 2>/dev/null || true)

if [ -n "$IAC_FILES" ]; then
  VPC_CONN=$(grep -rnI "${EXCLUDE_DIRS[@]}" -E "(vpc-connector|vpc_access_connector|run\.googleapis\.com/vpc-access-connector)" . 2>/dev/null | head -n 5 || true)
  if [ -n "$VPC_CONN" ]; then
    echo "  [WARN] Legacy Serverless VPC Access Connector found. Migrate to Direct VPC Egress to eliminate fixed \$15-\$35+/mo VM connector charges."
    echo "$VPC_CONN" | sed 's/^/           -> /'
  fi

  NO_THROTTLE=$(grep -rnI "${EXCLUDE_DIRS[@]}" -E "(no-cpu-throttling|cpu-throttling:[[:space:]]*['\"]?false|cpu_idle[[:space:]]*=[[:space:]]*false)" . 2>/dev/null | head -n 5 || true)
  if [ -n "$NO_THROTTLE" ]; then
    echo "  [WARN] Instance-based billing (CPU always allocated / no-cpu-throttling) detected. Verify if request-based billing (--cpu-throttling) can be used to save idle CPU costs."
    echo "$NO_THROTTLE" | sed 's/^/           -> /'
  fi

  CONC_ONE=$(grep -rnI "${EXCLUDE_DIRS[@]}" -E "(containerConcurrency:[[:space:]]*1|--concurrency[=[:space:]]+1|max_instance_request_concurrency[[:space:]]*=[[:space:]]*1)" . 2>/dev/null | head -n 5 || true)
  if [ -n "$CONC_ONE" ]; then
    echo "  [WARN] Concurrency set to 1. Only use concurrency=1 for single-threaded CPU-bound tasks; async I/O services should use 40-100."
    echo "$CONC_ONE" | sed 's/^/           -> /'
  fi
else
  echo "  - No YAML or Terraform IaC files found in repository."
fi

# ------------------------------------------------------------------------------
# 5. Workload Evaluation & Tailored Sizing
# ------------------------------------------------------------------------------
echo ""
echo "[5/5] Workload Classification & Tailored Sizing Evaluation:"
if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/evaluate-workload.py" ]; then
  python3 "$SCRIPT_DIR/evaluate-workload.py" "$TARGET_DIR"
else
  echo "  - python3 or evaluate-workload.py not available. Run evaluate-workload.py manually."
fi

echo ""
echo "=================================================================="
echo " Static Audit & Evaluation Complete."
echo "=================================================================="
