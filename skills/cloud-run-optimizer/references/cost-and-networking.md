# Cloud Run Cost Billing Models & Networking Optimization

Use this reference to eliminate hidden networking charges, choose the lowest-cost billing mode, and optimize cold starts without paying for idle instances.

---

## 1. Request-Based vs. Instance-Based Billing (`--cpu-throttling`)

| Billing Mode | CLI Flag | Knative Annotation | When You Pay | Best For |
| :--- | :--- | :--- | :--- | :--- |
| **Request-Based (Default)** | `--cpu-throttling` | `run.googleapis.com/cpu-throttling: "true"` | Only during the milliseconds an HTTP request or active stream is being processed (+ startup/shutdown). | **95% of workloads:** APIs, web apps, webhooks, SSR, AI streaming, variable or spiky traffic. |
| **Instance-Based** | `--no-cpu-throttling` | `run.googleapis.com/cpu-throttling: "false"` | For the entire lifetime of the container instance (even while idle between requests). | High-throughput services with continuous >40% CPU utilization 24/7, or apps with unavoidable in-process async tasks. |

> **Cost Trap:** Combining `--min-instances >= 1` with `--no-cpu-throttling` turns Cloud Run into an always-on VM billed at serverless rates. If you must use `--min-instances=1` to eliminate cold starts, **keep `--cpu-throttling` enabled** so the warm instance bills at the much lower *idle minimum instance* rate when not serving requests.

---

## 2. Eliminating Cold Starts Without Paying for Idle Compute

Before setting `--min-instances=1` (which adds ~\$10–\$55+/month per instance depending on CPU/RAM and cpu-throttling settings), apply these zero-idle-cost optimizations in order:

1. **Enable Startup CPU Boost (`--cpu-boost`):**
   * Annotation: `run.googleapis.com/startup-cpu-boost: "true"`
   * Temporarily allocates extra vCPUs during container startup and for 10 seconds after the instance starts listening on `$PORT`.
   * Particularly impactful for Java/Spring Boot, Node.js/Next.js, and Python ML apps where module loading is CPU-bound.
   * **Cost:** Only bills for the extra CPU during the few seconds of startup—\$0 when idle.
2. **Optimize Startup Probes:**
   * Configure a fast HTTP/TCP startup probe with `periodSeconds: 1` or `2` instead of waiting on long default intervals so traffic routes the instant the server binds to `$PORT`.
3. **Defer Non-Critical Initialization:**
   * Connect to external APIs or warm secondary caches lazily on first use rather than blocking `listen($PORT)`.

---

## 3. Networking Cost Traps & Optimizations

### A. Replace Serverless VPC Access Connectors with Direct VPC Egress
* **The Trap:** Legacy **Serverless VPC Access Connectors** (`--vpc-connector`) provision permanent Compute Engine VM instances (`e2-micro` / `f1-micro`, minimum 2–3 instances) that run 24/7. Even if your Cloud Run service receives 0 requests all month, a VPC Connector costs **\$15 to \$40+ per month**.
* **The Fix:** Switch to **Direct VPC Egress** (`--network=NETWORK --subnet=SUBNET --vpc-egress=private-ranges-only`).
  * Scales to zero along with your Cloud Run service (\$0 idle cost).
  * Lower network latency and higher throughput (no connector VM hop).
  * Use `--vpc-egress=private-ranges-only` (instead of `all-traffic`) so public API calls and Google APIs do not needlessly route through Cloud NAT (which bills per GB processed).

### B. Avoid Cloud NAT Charges for Google APIs
* If using `--vpc-egress=all-traffic` with Cloud NAT, enable **Private Google Access** on the subnet so traffic to Cloud Storage, BigQuery, Vertex AI, and Secret Manager stays on Google's internal network at \$0 NAT processing cost.

### C. Region Co-Location (Zero Inter-Region Data Transfer)
* Deploy Cloud Run in the **exact same region** (e.g., `us-central1`) as your Cloud SQL instance, Memorystore, Firestore, and Cloud Storage buckets.
* Cross-region traffic between `us-central1` and `us-east1` bills egress on every database query and file read, AND adds 20–40ms of round-trip latency per query.
* Note: **Tier 1 pricing regions** (such as `us-central1`, `us-east1`, `us-west1`, `europe-west1`) have lower per-vCPU and per-GiB-second rates than Tier 2 regions.

### D. Secret Manager Caching
* Mounting secrets as **Environment Variables** (`--set-secrets=API_KEY=my-secret:latest`) resolves the secret **once at container startup**.
* Fetching secrets via the Secret Manager API on *every HTTP request* adds latency and incurs Secret Manager access operation charges at scale. Cache secrets in memory at startup (or with a 5–15 minute TTL if rotation without redeployment is required).

---

## 4. Committed Use Discounts (CUDs)
* For mature workloads with predictable baseline spend Across Cloud Run Services, Jobs, and Worker Pools, Google Cloud **Compute Flexible CUDs** (1-year or 3-year) apply automatically to Cloud Run vCPU and memory usage, reducing compute bills by **17% to 46%** without locking you into a single region or machine family.
