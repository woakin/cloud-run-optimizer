# Cloud Run Observability, Metrics & Cost Verification

To guarantee that a Cloud Run service is truly optimized—and to continuously verify that resource allocations are neither wasting money nor risking outages—you must monitor the **4 Golden Serverless Metrics** and configure targeted Cloud Logging filters.

---

## 1. The 4 Golden Metrics in Cloud Monitoring

All metrics are available in Google Cloud Monitoring under the `run.googleapis.com/` namespace.

```
Cloud Run Resource Right-Sizing Quadrant:

         High CPU (>80%)
               │
   [Risk: Latency Spikes / Throttling]  │  [Healthy / Well-Tuned]
   Action: Increase vCPU or reduce     │  High utilization under load;
   concurrency                         │  good cost efficiency
───────────────────────────────────────┼────────────────────────────────────────
   [Risk: Idle Waste]                  │  [Risk: Memory Exhaustion]
   Action: Downscale CPU / memory;     │  Action: Increase container RAM;
   raise concurrency                   │  audit in-memory leaks or /tmp
               │
         Low CPU (<20%)
               └─────────────────────── Low Memory (<30%) ─── High Memory (>85%)
```

### 1. `container/cpu/utilizations` (Percentile Distribution)
* **What it measures:** Fraction of allocated vCPU actively consumed by the container during request processing.
* **Tuning Thresholds:**
  * **p95 < 20%:** Overprovisioned. You are paying for unused CPU capacity. You can safely halve the CPU allocation (e.g. from 2 vCPU to 1 vCPU) or raise `--concurrency`.
  * **p95 > 85%:** Underprovisioned or CPU throttled. Incoming requests will queue or experience high P99 latency. Increase vCPU or decrease `--concurrency`.

### 2. `container/memory/utilizations` (Percentile Distribution)
* **What it measures:** Fraction of allocated RAM currently held by the container (including `/tmp` file writes and runtime caches).
* **Tuning Thresholds:**
  * **p99 < 30%:** Overprovisioned RAM. Reduce memory limit (e.g., from 2Gi to 512Mi or 1Gi).
  * **p99 > 85%:** **CRITICAL RISK OF OOM (Out Of Memory).** Linux kernel or Cloud Run hypervisor will send `SIGKILL` (exit code 137). Immediately increase container memory limit or audit `/tmp` files and heap sizes.

### 3. `container/instance_count` (Grouped by `state: active vs idle`)
* **What it measures:** Number of running container instances split between actively processing requests (`active`) vs waiting warm (`idle`).
* **Cost Insights:**
  * If `idle` count is high and you have `--no-cpu-throttling` enabled, you are paying full compute rates for idle instances.
  * If `active` instances spike drastically during small traffic increases, `--concurrency` is likely set too low.

### 4. `request_latencies` (Percentile Distribution: p50, p95, p99)
* **What it measures:** Total round-trip latency from when Cloud Run's proxy receives the request to when the response is sent back.
* **Cold-start signature:** A large delta between p50 (e.g. 45ms) and p99 (e.g. 2,800ms) indicates cold-start delays on newly scaled instances. Fix by enabling `--cpu-boost` or tuning base images.

---

## 2. Fast Diagnosis: Essential Cloud Logging Filters

Run these queries in **Cloud Logging Console** or via `gcloud logging read`:

### A. Detect Out of Memory (OOM) Container Kills
```text
resource.type="cloud_run_revision"
severity>=ERROR
"Memory limit of" OR "exceeded memory limit" OR "exit code 137"
```

### B. Detect Cold-Start / Port Binding Failures
```text
resource.type="cloud_run_revision"
severity>=ERROR
"Container failed to start and listen on the port"
```
*(If this occurs, check if startup probe timeout is too short, or if application blocks on external DB initialization before listening on `$PORT`).*

### C. Detect Cloud Run Gateway Timeouts (504)
```text
resource.type="cloud_run_revision"
httpRequest.status=504
```
*(Indicates requests exceeded the service's configured `--timeout` limit).*

### D. CLI Command for Quick Triage
```bash
# Read the last 20 warning or error events for a service
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="MY_SERVICE" AND severity>=WARNING' \
  --limit=20 \
  --format="table(timestamp, severity, textPayload)"
```

---

## 3. Recommended Production Alerting Policies

Configure these alerts in Cloud Monitoring to catch regressions early:

| Alert Name | Condition Metric | Threshold | Action |
| :--- | :--- | :--- | :--- |
| **High Memory Warning** | `run.googleapis.com/container/memory/utilizations` | p99 > 85% for 3 minutes | Prevent imminent OOM crashes; scale container memory. |
| **Elevated 5xx Error Rate** | `run.googleapis.com/request_count` (filter: `response_code_class="5xx"`) | > 2% of total traffic for 5 minutes | Inspect recent revision deployment or database pool starvation. |
| **Container Instance Cap Reached** | `run.googleapis.com/container/instance_count` | Instance count == `--max-instances` for 5 minutes | Requests are queuing or dropping; consider raising `--max-instances` or scaling database tier. |
