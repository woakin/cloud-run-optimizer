# Cloud Run Code Anti-Patterns & Serverless Remediations

When generating or reviewing application code for Cloud Run, eliminate these 7 anti-patterns that break horizontal scaling, cause silent failures, or inflate monthly bills.

---

## 1. Work After Response (Silent Freezing Bug)

### Why it fails on Cloud Run
With request-based billing (`--cpu-throttling`, the default and most cost-effective mode), Cloud Run throttles container CPU to near zero **the millisecond the HTTP response is sent**. Any background thread, unawaited Promise, `setImmediate`, or FastAPI `BackgroundTasks` running after the response will freeze indefinitely until the next incoming request wakes the container—or be killed on scale-down.

### Bad Pattern
```javascript
// Node.js / Express - BROKEN on Cloud Run with CPU throttling
app.post('/orders', async (req, res) => {
  const order = await db.saveOrder(req.body);
  res.status(202).json({ id: order.id });

  // CPU is throttled right here! This email/webhook may take 10 minutes or never finish.
  sendConfirmationEmail(order).catch(console.error);
});
```

### Optimized Pattern
* **Option A (Fast < 500ms tasks):** `await` the task *before* sending the HTTP response (use `Promise.all` for parallel I/O).
* **Option B (Slow / Retryable tasks):** Enqueue a **Cloud Tasks** HTTP task or publish a **Pub/Sub** message *before* returning the response. Cloud Run will invoke a dedicated worker endpoint with its own request lifecycle and automatic retries.

---

## 2. In-Process Timers & Cron Libraries

### Why it fails on Cloud Run
Libraries like `node-cron`, `setInterval`, Python `APScheduler`/`schedule`, Go `time.Ticker`, or Ruby `whenever`:
1. Stop firing when the service scales to zero (`min-instances=0`) or when CPU is throttled between requests.
2. Fire **N times in parallel** (causing duplicate charges/emails) when traffic scales the service to N instances.

### Optimized Pattern
Remove in-process timers completely. Expose an authenticated HTTP POST endpoint (e.g., `POST /internal/cron/cleanup`) or build a **Cloud Run Job**, and trigger it with **Cloud Scheduler** using OIDC authentication.

---

## 3. Unbounded Writes to `/tmp` or Local Disk

### Why it fails on Cloud Run
Cloud Run's writable filesystem (`/tmp`) is an **in-memory `tmpfs` ramdisk**. Every megabyte written to `/tmp` is subtracted directly from your container's memory limit (`--memory`). Writing a 300 MB file on a `512Mi` instance immediately triggers an Out-Of-Memory (`Memory limit exceeded`) crash and kills all concurrent requests on that instance.

### Optimized Pattern
1. **Direct-to-GCS Uploads:** For user file uploads/downloads, generate a **Cloud Storage V4 Signed URL** and let the client upload/download directly to GCS, bypassing Cloud Run memory and egress altogether.
2. **Stream Processing:** Stream data directly from GCS/HTTP readers to writers without buffering the full file on disk.
3. **Mandatory Cleanup:** If a library requires a local file path in `/tmp`, always delete it inside a `try / finally` block before the request completes:
   ```python
   import os, tempfile

   tmp_path = tempfile.mktemp(dir="/tmp")
   try:
       process_file(tmp_path)
   finally:
       if os.path.exists(tmp_path):
           os.remove(tmp_path)
   ```

---

## 4. In-Memory Sessions, Caches & Rate Limiters

### Why it fails on Cloud Run
Instances are stateless and ephemeral. Storing sessions (`express-session` MemoryStore), WebSocket rooms, or rate-limit counters (`express-rate-limit` default memory store) in process RAM breaks as soon as Cloud Run scales beyond 1 instance or recycles a revision.

### Optimized Pattern
* **Authentication/Sessions:** Use stateless **HTTP-only signed JWT cookies**, Firebase Auth, or store session state in **Firestore** / **Memorystore**.
* **Caching:** Bounded in-memory LRU cache (`max` items/MB) is acceptable *only* for read-only, immutable reference data (keeping in mind it eats container RAM). Never use an unbounded `Map` or `dict`.

---

## 5. Per-Request Client Instantiation & Database Connection Exhaustion

### Why it fails on Cloud Run
Creating a new database pool, `fetch` agent, or Google Cloud SDK client inside the HTTP request handler adds 50–300ms of TLS/TCP handshake latency to every request and leaks sockets. Furthermore, if 50 Cloud Run instances each open a default pool of 10 PostgreSQL connections, 500 connections will overwhelm Cloud SQL.

### Optimized Pattern
1. **Global Scope Initialization:** Instantiate database pools, Redis clients, and Google Cloud SDK clients (`Storage()`, `Firestore()`, `PubSub()`) in global/module scope so warm instances reuse keep-alive connections across thousands of requests.
2. **Right-size DB Pool per Container:**
   * Set small per-instance pool limits (e.g., `max: 5` in Node `pg` or `pool_size=5` in SQLAlchemy) paired with a strict `--max-instances` cap on the Cloud Run service.
   * Enable HTTP keep-alive on outbound HTTP clients (`keepAlive: true`).

---

## 6. Missing `SIGTERM` Graceful Shutdown Handler

### Why it matters on Cloud Run
When Cloud Run scales down an instance or deploys a new revision, it sends `SIGTERM` and waits up to **10 seconds** before sending `SIGKILL`. If your app ignores `SIGTERM` (or if your Dockerfile uses shell form `CMD npm start` which traps signals in `/bin/sh` instead of Node/Python), in-flight requests are abruptly dropped with `503` errors and database transactions are left dangling.

### Optimized Pattern
1. Use `exec` form in `Dockerfile` (`CMD ["node", "dist/server.js"]`) so your process runs as PID 1 (or use `tini` / `dumb-init`).
2. Handle `SIGTERM` in code:
   ```javascript
   // Node.js graceful shutdown for Cloud Run (10s window)
   process.on('SIGTERM', () => {
     server.close(async () => {
       await dbPool.end();
       process.exit(0);
     });
     setTimeout(() => process.exit(1), 9000).unref();
   });
   ```

---

## 7. Hardcoded Host (`127.0.0.1`) or Port

### Why it fails on Cloud Run
Cloud Run's ingress proxy routes traffic into the container over a virtual network interface on the port injected via `$PORT` (default `8080`). Binding to `localhost` or `127.0.0.1` causes the startup probe to fail with `The user-provided container failed to start and listen on the port defined provided by the PORT=8080 environment variable`.

### Optimized Pattern
Always bind to `0.0.0.0` and read `process.env.PORT || 8080` (or `os.environ.get("PORT", "8080")`).
