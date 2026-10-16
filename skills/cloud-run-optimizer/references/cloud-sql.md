# Cloud Run & Cloud SQL: High-Performance, Low-Latency Connection Architecture

Connecting an autoscaling serverless compute layer (**Cloud Run**) to a fixed-capacity relational database (**Cloud SQL** for PostgreSQL / MySQL / SQL Server) is one of the most common sources of production outages, connection exhaustion, and cold-start latency spikes.

This reference provides production-tested architectural patterns, sizing formulas, and connection strategies to guarantee high performance, security, and cost efficiency.

---

## 1. The Core Problem: Ephemeral Autoscaling vs. Fixed Database Capacity

* **Cloud Run** scales up horizontally in seconds. If a traffic burst causes your service to scale from 2 to 50 container instances, each instance boots independently.
* **Cloud SQL** has a hard limit on maximum concurrent connections dictated by its memory allocation (e.g., PostgreSQL defaults to `max_connections = 100` on smaller instances, up to a few thousands on high-memory tiers).
* **The Failure:** If each container runs an ORM or connection pool initialized with 10–20 connections:
  $$\text{Total Attempted Connections} = 50 \text{ instances} \times 15 \text{ pool} = 750 \text{ connections}$$
  This instantly exhausts Cloud SQL's connection limit, causing `FATAL: remaining connection slots are reserved for non-replication superuser connections`, cascading 500 errors, and total downtime.

---

## 2. Connection Pool Sizing Formula

To guarantee your Cloud Run service can never overwhelm your database, you must **couple your container pool size directly to Cloud Run's `--max-instances` ceiling**:

$$\text{pool\_size\_per\_instance} \le \left\lfloor \frac{\text{DB\_MAX\_CONNECTIONS} \times 0.8}{\text{max\_instances}} \right\rfloor$$

*(The $0.8$ factor reserves 20% of connection slots for migrations, background jobs, read replicas, and admin debugging).*

### Practical Sizing Matrix

| Cloud SQL Machine Tier | Approximate `max_connections` | If `--max-instances = 20` | If `--max-instances = 50` | If `--max-instances = 100+` |
| :--- | :--- | :--- | :--- | :--- |
| **db-custom-1-3840 (1 vCPU, 3.75 GB)** | ~100 | **Max 4** conns / container | **Max 1** conn / container | **Requires Connection Pooler (PgBouncer)** |
| **db-custom-2-7680 (2 vCPU, 7.5 GB)** | ~250–400 | **Max 10** conns / container | **Max 4** conns / container | **Max 2** conns or PgBouncer |
| **db-custom-4-15360 (4 vCPU, 15 GB)** | ~500–1,000 | **Max 20** conns / container | **Max 10** conns / container | **Max 5** conns / container |

> [!IMPORTANT]
> If you cannot restrict `--max-instances` because you need massive horizontal throughput, **you must use an intermediate connection pooler** (such as Cloud SQL's built-in Managed Connection Pooler or a dedicated PgBouncer instance) rather than connecting directly from application containers.

---

## 3. Connection Method Comparison: Which Should You Use?

There are two primary ways to connect Cloud Run to Cloud SQL:

```
Option A: Direct VPC Egress (Private IP) [RECOMMENDED FOR PERFORMANCE]
┌─────────────────────────────────┐        Direct VPC Egress        ┌───────────────────────────────────┐
│     Cloud Run Container         │────────────────────────────────>│     Cloud SQL (Private IP)        │
│ (Connects to 10.x.x.x:5432/3306)│     (Private, sub-ms latency)   │ (No public IP, no proxy overhead) │
└─────────────────────────────────┘                                 └───────────────────────────────────┘

Option B: Built-in Cloud SQL Auth Proxy (Unix Domain Socket / Cloud SQL Connector)
┌─────────────────────────────────┐      mTLS over Cloud API        ┌───────────────────────────────────┐
│     Cloud Run Container         │────────────────────────────────>│     Cloud SQL (via IAM Proxy)     │
│   (/cloudsql/PROJECT:REGION:ID) │   (Native Cloud Run integration)│ (Automatic certificate rotation)  │
└─────────────────────────────────┘                                 └───────────────────────────────────┘
```

| Factor | Option A: Direct VPC Egress (Private IP) | Option B: Cloud SQL Admin Integration (`--add-cloudsql-instances`) |
| :--- | :--- | :--- |
| **How it connects** | TCP to internal VPC IP (`10.x.x.x:5432`) | Built-in proxy client writing to `/cloudsql/<INSTANCE_CONNECTION_NAME>` |
| **Latency** | **Lowest** (direct network route, sub-millisecond) | **Slight overhead** (extra local Unix socket / mTLS proxy hop) |
| **Cost** | \$0 idle cost (scales to zero with Direct VPC Egress) | \$0 idle cost |
| **Public IP Exposure** | Zero (Cloud SQL can disable public IP completely) | Can work with or without public IP |
| **Network Configuration** | Requires `--network`, `--subnet`, and `--vpc-egress=private-ranges-only` | No VPC configuration strictly required |
| **Best For** | Production architectures requiring maximum throughput and strict network isolation | Fast setups, microservices across different VPCs, or IAM-based authentication |

### Option A Deployment (Direct VPC Egress to Private IP)
```bash
gcloud run deploy my-api \
  --image=gcr.io/my-project/my-api:v1 \
  --network=default \
  --subnet=default \
  --vpc-egress=private-ranges-only \
  --set-env-vars=DB_HOST=10.128.0.5,DB_PORT=5432 \
  --set-secrets=DB_USER=db-user:latest,DB_PASS=db-pass:latest,DB_NAME=db-name:latest \
  --max-instances=30
```

### Option B Deployment (Cloud SQL Native Integration)
```bash
gcloud run deploy my-api \
  --image=gcr.io/my-project/my-api:v1 \
  --add-cloudsql-instances=my-project:us-central1:my-database \
  --set-env-vars=INSTANCE_UNIX_SOCKET=/cloudsql/my-project:us-central1:my-database \
  --set-secrets=DB_USER=db-user:latest,DB_PASS=db-pass:latest,DB_NAME=db-name:latest \
  --max-instances=30
```

---

## 4. Cold-Start & Health Check Traps

### A. Never Block Server Startup on DB Verification
* **Anti-pattern:** Running `await pool.query('SELECT 1')` or blocking migrations before calling `app.listen(PORT)`.
* **The Failure:** If the database is busy, cold-starting, or under load, the container will take >10–15 seconds to listen on `$PORT`. Cloud Run's startup probe will fail and kill the revision with:
  `Container failed to start and listen on the port.`
* **Solution:** Bind to `$PORT` immediately. Establish database connections asynchronously, or rely on pool lazy initialization on the first incoming query.

### B. Do NOT Perform Deep Database Checks in Liveness / Startup Probes
* **Anti-pattern:** Pointing Cloud Run's `/health` probe to an endpoint that runs `SELECT 1` on Cloud SQL.
* **The Failure:** Under high load, if database response times spike to 2 seconds, Cloud Run's health probe times out, marks the healthy container dead, terminates it, and spins up a new cold container—worsening the thundering herd on Cloud SQL.
* **Solution:** Health probes must only verify that the HTTP process itself is alive and able to accept connections. Monitor database health externally via Cloud Monitoring.

---

## 5. Idiomatic Pool Configurations by Runtime

### Node.js (Prisma ORM)
Prisma creates a connection pool per Node.js process. In Cloud Run, define `connection_limit` explicitly in the connection string:
```env
DATABASE_URL="postgresql://user:password@10.128.0.5:5432/mydb?connection_limit=5&pool_timeout=10"
```

### Python (SQLAlchemy)
```python
import os
from sqlalchemy import create_engine

# When using Direct VPC Egress (Private IP)
db_url = f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASS']}@{os.environ['DB_HOST']}:5432/{os.environ['DB_NAME']}"

engine = create_engine(
    db_url,
    pool_size=5,          # Keep pool small per container
    max_overflow=2,       # Bounded surge limit
    pool_timeout=10,      # Fail fast if pool is exhausted instead of hanging
    pool_recycle=1800,    # Recycle connections every 30m before Cloud SQL drops idle TCP
    pool_pre_ping=True,   # Verify connection liveness to gracefully handle recycled instances
)
```

### Go (`pgxpool`)
```go
package main

import (
	"context"
	"os"
	"time"
	"github.com/jackc/pgx/v5/pgxpool"
)

func initDB() (*pgxpool.Pool, error) {
	config, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		return nil, err
	}

	config.MaxConns = 5
	config.MinConns = 1
	config.MaxConnLifetime = 30 * time.Minute
	config.MaxConnIdleTime = 5 * time.Minute
	config.HealthCheckPeriod = 1 * time.Minute

	return pgxpool.NewWithConfig(context.Background(), config)
}
```

---

## 6. Secrets & IAM Database Authentication

* **Always use Secret Manager:** Never pass cleartext database passwords in `--set-env-vars`. Use Cloud Run's native Secret Manager integration (`--set-secrets`).
* **IAM Database Authentication:**
  * Cloud SQL supports authenticating using Google Cloud IAM Service Account tokens instead of static passwords.
  * Benefits: No password rotation needed, short-lived ephemeral tokens, zero risk of leaked credentials in environment dumps.
  * Use the official Cloud SQL Go, Python, or Node.js **IAM Connector libraries** if you want automatic IAM token exchange.
