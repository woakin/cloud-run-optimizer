# Cloud Run Health, Startup & Liveness Probes

Properly configuring health probes on Cloud Run is critical to avoid two opposite failure modes:
1. **False-death restart loops:** Containers getting killed by Cloud Run during cold start because they took a few seconds to load dependencies.
2. **Zombie instances:** Containers that have locked their event loop or run out of memory but continue receiving user traffic.

---

## 1. The Two Probe Types in Cloud Run

Cloud Run supports **Startup Probes** and **Liveness Probes** (via HTTP GET, TCP Socket, or gRPC).

| Probe Type | Purpose | What Happens on Failure | When to Use |
| :--- | :--- | :--- | :--- |
| **Startup Probe** | Gives the container a dedicated initialization window before routing traffic or running liveness checks. | If it doesn't succeed within `failureThreshold × periodSeconds`, Cloud Run terminates and discards the container. | **Mandatory for slow-starting runtimes:** Java/Spring Boot, Python ML/FastAPI, Ruby on Rails, Next.js cold starts. |
| **Liveness Probe** | Continuously checks if a running container's event loop is still responsive. | Cloud Run stops routing requests to that instance, sends `SIGTERM`, and restarts the container. | Long-running services that could encounter memory deadlocks or frozen event loops. |

```
Container Lifecycle on Cloud Run with Probes:

┌───────────────────────┐
│ Container starts boot │
└──────────┬────────────┘
           │
           ▼
┌───────────────────────────────────────────────┐
│              Startup Probe Checks             │
│ (Liveness checks paused; no user traffic yet) │
└──────────┬─────────────────────────────┬──────┘
           │ Success                     │ Exceeds (failureThreshold × periodSeconds)
           ▼                             ▼
┌──────────────────────────────┐   ┌───────────────────────────┐
│ Routes user traffic &        │   │ Container killed          │
│ begins periodic Liveness     │   │ (Cold start failed)       │
│ Probes                       │   └───────────────────────────┘
└──────────┬───────────────────┘
           │ Liveness probe fails
           ▼
┌──────────────────────────────┐
│ Container restarted (SIGTERM)│
└──────────────────────────────┘
```

---

## 2. The 3 Golden Rules of Serverless Health Checks

### Rule 1: NEVER Check External Dependencies (DB, Redis, Third-Party APIs)
* **The Anti-Pattern:** A `/health` or `/live` endpoint that executes `SELECT 1` on Cloud SQL or pings Stripe/Twilio.
* **The Disaster (Cascading Restart Storm):** If Cloud SQL hits a brief connection spike or latency hiccup, all Cloud Run instances fail their health check simultaneously. Cloud Run kills every container at once and starts fresh cold instances. The dozens of new cold instances immediately pound the struggling database with fresh connection handshakes, causing a total infrastructure collapse.
* **The Fix:**
  * **Liveness / Startup Probes:** Must check **internal process health only** (is the HTTP server listening? is the event loop responsive?). Return `HTTP 200 OK` with a static body (`{"status":"ok"}`).
  * **Deep Dependency Checks:** Reserve deep checks for dedicated, non-probe endpoints (e.g. `/health/deep` or `/ready`) called by external monitoring systems (Cloud Monitoring uptime checks, Datadog), never by the container runtime probe.

### Rule 2: Prefer TCP Probes for Lightweight Startup Verification
If your service simply needs to signal when it has bound to `$PORT`:
* A **TCP socket probe** tests port binding directly without invoking application routing logic, framework middleware, or authentication layers.
* Fastest response time and minimal CPU overhead during startup.

### Rule 3: Size the Startup Grace Window Correctly
$$\text{Max Allowed Startup Time} = \text{failureThreshold} \times \text{periodSeconds}$$
* For fast Go/Rust containers: `failureThreshold: 3`, `periodSeconds: 1` (3s window).
* For Node.js / Python: `failureThreshold: 10`, `periodSeconds: 2` (20s window).
* For heavy Java / Spring Boot: `failureThreshold: 30`, `periodSeconds: 2` (60s window).

---

## 3. Recommended Knative YAML Configurations

### A. Lightweight HTTP Startup & Liveness Probe
```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-service
spec:
  template:
    spec:
      containers:
      - image: gcr.io/my-project/my-service:v1
        ports:
        - containerPort: 8080
        startupProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 0
          periodSeconds: 2
          failureThreshold: 15     # Gives up to 30s to boot
          timeoutSeconds: 1
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 0
          periodSeconds: 10
          failureThreshold: 3
          timeoutSeconds: 2
```

### B. TCP Socket Startup Probe (Ultra-Fast)
```yaml
startupProbe:
  tcpSocket:
    port: 8080
  initialDelaySeconds: 0
  periodSeconds: 1
  failureThreshold: 10
  timeoutSeconds: 1
```

---

## 4. Terraform Example (`google_cloud_run_v2_service`)

```hcl
resource "google_cloud_run_v2_service" "api" {
  name     = "optimized-api"
  location = "us-central1"

  template {
    containers {
      image = "gcr.io/my-project/api:v1"

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 1
        period_seconds        = 2
        failure_threshold     = 15

        http_get {
          path = "/healthz"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 2
        period_seconds        = 10
        failure_threshold     = 3

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }
}
```
