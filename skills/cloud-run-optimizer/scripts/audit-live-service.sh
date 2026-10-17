#!/usr/bin/env bash
# ==============================================================================
# Cloud Run & Cloud SQL Live Service Auditor
# Inspects a deployed Cloud Run service (and any attached Cloud SQL instances)
# via `gcloud` and flags cost, latency, and connection-sizing optimizations.
# Usage: bash audit-live-service.sh <SERVICE_NAME> <REGION> [PROJECT_ID]
# ==============================================================================

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <SERVICE_NAME> <REGION> [PROJECT_ID]" >&2
  exit 1
fi

SERVICE_NAME="$1"
REGION="$2"
PROJECT_ID="${3:-}"
PROJECT_FLAG=()
if [ -n "$PROJECT_ID" ]; then
  PROJECT_FLAG=(--project="$PROJECT_ID")
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Error: 'gcloud' CLI is not installed or not in PATH." >&2
  exit 1
fi

echo "=================================================================="
echo " Auditing Live Cloud Run Service: $SERVICE_NAME ($REGION)"
echo "=================================================================="

SERVICE_JSON=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" "${PROJECT_FLAG[@]}" --format=json)

export SERVICE_JSON
python3 - "$REGION" "$PROJECT_ID" << 'EOF'
import json, os, sys, subprocess, re

run_region = sys.argv[1]
default_project = sys.argv[2]

data = json.loads(os.environ.get("SERVICE_JSON", "{}"))
spec = data.get("spec", {}).get("template", {})
meta_ann = spec.get("metadata", {}).get("annotations", {})
service_ann = data.get("metadata", {}).get("annotations", {})
template_spec = spec.get("spec", {})
containers = template_spec.get("containers", [{}])
c0 = containers[0] if containers else {}
limits = c0.get("resources", {}).get("limits", {})

cpu = limits.get("cpu", "1")
memory = limits.get("memory", "512Mi")
concurrency = template_spec.get("containerConcurrency", 80)
timeout = template_spec.get("timeoutSeconds", 300)

cpu_throttling = meta_ann.get("run.googleapis.com/cpu-throttling", "true")
cpu_boost = meta_ann.get("run.googleapis.com/startup-cpu-boost", "false")
min_scale = meta_ann.get("autoscaling.knative.dev/minScale", "0")
max_scale = meta_ann.get("autoscaling.knative.dev/maxScale", "100")
exec_env = meta_ann.get("run.googleapis.com/execution-environment", "default (gen1)")
vpc_connector = meta_ann.get("run.googleapis.com/vpc-access-connector", "")
vpc_egress = meta_ann.get("run.googleapis.com/vpc-access-egress", "")
network_interfaces = meta_ann.get("run.googleapis.com/network-interfaces", "")
cloud_sql_instances = meta_ann.get("run.googleapis.com/cloudsql-instances", "")
gpu_type = meta_ann.get("run.googleapis.com/gpu-type", "")
gpu_zonal_disabled = meta_ann.get("run.googleapis.com/gpu-zonal-redundancy-disabled", "false")
startup_probe = c0.get("startupProbe", None)
liveness_probe = c0.get("livenessProbe", None)

print(f"\n[Current Configuration]")
print(f"  - Image:                 {c0.get('image', 'unknown')}")
print(f"  - CPU / Memory:          {cpu} vCPU / {memory}")
print(f"  - Concurrency:           {concurrency}")
print(f"  - Min / Max Instances:   {min_scale} / {max_scale}")
print(f"  - CPU Throttling:        {cpu_throttling} ({'Request-based billing' if cpu_throttling == 'true' else 'Instance-based 24/7 billing'})")
print(f"  - Startup CPU Boost:     {cpu_boost}")
print(f"  - Startup Probe:         {'Configured' if startup_probe else 'None (default 4s HTTP wait)'}")
print(f"  - Liveness Probe:        {'Configured' if liveness_probe else 'None'}")
print(f"  - Execution Environment: {exec_env}")
print(f"  - Request Timeout:       {timeout}s")
print(f"  - Cloud SQL Instances:   {cloud_sql_instances or 'None'}")
print(f"  - VPC Connector:         {vpc_connector or 'None'}")
print(f"  - Direct VPC Network:    {'Configured' if network_interfaces else 'None'} (egress: {vpc_egress or 'N/A'})")
if gpu_type:
    print(f"  - GPU Accelerator:       {gpu_type} (zonal redundancy disabled: {gpu_zonal_disabled})")


def estimate_sql_max_connections(tier: str, db_flags: list) -> int:
    for flag in db_flags:
        if flag.get("name") == "max_connections":
            try:
                return int(flag.get("value"))
            except Exception:
                pass
    if tier == "db-f1-micro":
        return 25
    if tier == "db-g1-small":
        return 50
    m = re.search(r"db-custom-\d+-(\d+)", tier)
    if m:
        ram_mb = int(m.group(1))
        if ram_mb < 4096:
            return 100
        elif ram_mb < 8192:
            return 200
        elif ram_mb < 16384:
            return 400
        elif ram_mb < 32768:
            return 800
        return 1000
    return 100


def inspect_cloud_sql_instance(conn_name: str):
    parts = conn_name.strip().split(":")
    if len(parts) == 3:
        sql_proj, sql_reg, sql_inst = parts
    else:
        sql_proj, sql_reg, sql_inst = default_project, run_region, conn_name.strip()

    info = {
        "conn_name": conn_name,
        "project": sql_proj,
        "region": sql_reg,
        "instance": sql_inst,
        "live_fetched": False,
        "tier": None,
        "max_connections": None,
        "private_ip": None,
        "public_ipv4_enabled": False,
        "retained_backups": 7,
        "availability_type": "ZONAL"
    }
    if not sql_inst:
        return info

    cmd = ["gcloud", "sql", "instances", "describe", sql_inst, "--format=json"]
    if sql_proj:
        cmd.append(f"--project={sql_proj}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
        if proc.returncode == 0 and proc.stdout:
            sql_data = json.loads(proc.stdout)
            info["live_fetched"] = True
            info["region"] = sql_data.get("region", sql_reg)
            settings = sql_data.get("settings", {})
            tier = settings.get("tier", "unknown")
            db_flags = settings.get("databaseFlags", [])
            info["tier"] = tier
            info["max_connections"] = estimate_sql_max_connections(tier, db_flags)
            info["availability_type"] = settings.get("availabilityType", "ZONAL")
            ip_cfg = settings.get("ipConfiguration", {})
            info["public_ipv4_enabled"] = bool(ip_cfg.get("ipv4Enabled", False))
            backup_cfg = settings.get("backupConfiguration", {})
            ret_settings = backup_cfg.get("backupRetentionSettings", {})
            if ret_settings.get("retainedBackups"):
                info["retained_backups"] = int(ret_settings.get("retainedBackups", 7))
            for ip_entry in sql_data.get("ipAddresses", []):
                if ip_entry.get("type") == "PRIVATE":
                    info["private_ip"] = ip_entry.get("ipAddress")
    except Exception:
        pass
    return info


def discover_project_sql_instances():
    if cloud_sql_instances:
        return [c.strip() for c in cloud_sql_instances.split(",") if c.strip()]
    cmd = ["gcloud", "sql", "instances", "list", "--format=json"]
    if default_project:
        cmd.append(f"--project={default_project}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
        if proc.returncode == 0 and proc.stdout:
            items = json.loads(proc.stdout)
            return [item.get("connectionName") for item in items if item.get("connectionName")]
    except Exception:
        pass
    return []


def inspect_artifact_registry():
    cmd = ["gcloud", "artifacts", "repositories", "list", f"--location={run_region}", "--format=json"]
    if default_project:
        cmd.append(f"--project={default_project}")
    bloated = []
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
        if proc.returncode == 0 and proc.stdout:
            repos = json.loads(proc.stdout)
            for r in repos:
                size_bytes = int(r.get("sizeBytes", 0) or 0)
                size_gb = size_bytes / (1024 ** 3)
                if size_gb < 2.0:
                    continue
                name = r.get("name", "").split("/")[-1]
                policies = r.get("cleanupPolicies")
                dry_run = r.get("cleanupPolicyDryRun", False)
                if policies is None and name:
                    desc_cmd = ["gcloud", "artifacts", "repositories", "describe", name, f"--location={run_region}", "--format=json"]
                    if default_project:
                        desc_cmd.append(f"--project={default_project}")
                    desc_proc = subprocess.run(desc_cmd, capture_output=True, text=True, timeout=5.0)
                    if desc_proc.returncode == 0 and desc_proc.stdout:
                        desc_data = json.loads(desc_proc.stdout)
                        policies = desc_data.get("cleanupPolicies", {})
                        dry_run = bool(desc_data.get("cleanupPolicyDryRun", False))
                if not policies or dry_run:
                    bloated.append((name, round(size_gb, 1), round(max(0.0, size_gb - 0.5) * 0.10, 2)))
    except Exception:
        pass
    return bloated


print(f"\n[Optimization Findings]")
findings = 0

if cpu_boost.lower() != "true" and not gpu_type:
    findings += 1
    print(f"  {findings}. [PERFORMANCE] Startup CPU Boost is DISABLED.")
    print("     -> Action: Enable `--cpu-boost` to cut cold-start latency at $0 idle cost.")

if not startup_probe:
    findings += 1
    print(f"  {findings}. [RELIABILITY] No explicit Startup Probe configured.")
    print("     -> Action: Configure a TCP or HTTP `startupProbe` with an explicit failureThreshold to prevent cold-start false-death restarts (see references/health-probes.md).")

if cpu_throttling.lower() == "false" and not gpu_type:
    findings += 1
    print(f"  {findings}. [COST] Instance-based billing (`--no-cpu-throttling`) is ENABLED.")
    print("     -> Action: Unless this service has >40% steady 24/7 CPU usage or background threads, switch to `--cpu-throttling` to stop paying for idle CPU between requests.")

if int(min_scale) > 0:
    findings += 1
    print(f"  {findings}. [COST] minScale is set to {min_scale} (warm instances kept alive 24/7).")
    print("     -> Action: Verify if `--cpu-boost` with `min-instances=0` meets your latency SLA, or ensure `--cpu-throttling` is enabled to pay the lower idle rate.")

if int(concurrency) == 1 and not gpu_type:
    findings += 1
    print(f"  {findings}. [COST & SCALE] Concurrency is set to 1.")
    print("     -> Action: If this service handles I/O (DB/HTTP calls), raising `--concurrency` to 40-80 (requires >= 1 vCPU) can reduce instance count and compute cost by up to 90%.")

sql_targets = discover_project_sql_instances()
for raw_conn in sql_targets:
    sql_info = inspect_cloud_sql_instance(raw_conn)
    sql_issues = []
    if sql_info["region"] and sql_info["region"] != run_region:
        sql_issues.append(f"[CRITICAL CROSS-REGION TRAP] Cloud Run is in '{run_region}' while Cloud SQL is in '{sql_info['region']}'! Co-locate both in the same region.")
    if sql_info["live_fetched"]:
        max_c = sql_info["max_connections"]
        safe_pool = int((max_c * 0.8) // max(1, int(max_scale)))
        if safe_pool < 1:
            sql_issues.append(f"[CONNECTION EXHAUSTION DANGER] maxScale={max_scale} exceeds 80% of DB capacity ({int(max_c * 0.8)} conns on {sql_info['tier']}). Lower `--max-instances` to <= {int(max_c * 0.4)} or use PgBouncer.")
        if cloud_sql_instances and sql_info["private_ip"]:
            sql_issues.append(f"[COLD-START LATENCY] Private IP ({sql_info['private_ip']}) is available on {sql_info['instance']}, but `--add-cloudsql-instances` Auth Proxy is still attached. Connect directly over Direct VPC to {sql_info['private_ip']}:5432 and remove `--add-cloudsql-instances`.")
        if sql_info["public_ipv4_enabled"] and sql_info["private_ip"] and network_interfaces:
            sql_issues.append(f"[HIDDEN COST: ~$7.30/mo] Public IPv4 is enabled on `{sql_info['instance']}` even though Private IP ({sql_info['private_ip']}) + Direct VPC are configured. Run `gcloud sql instances patch {sql_info['instance']} --no-assign-ip` to save $0.01/hr (~$7.30/mo).")
        if sql_info["retained_backups"] > 7:
            sql_issues.append(f"[BACKUP STORAGE COST] `{sql_info['instance']}` retains {sql_info['retained_backups']} backups (only 7 are free). Run `gcloud sql instances patch {sql_info['instance']} --retained-backups-count=7` to eliminate extra backup fees.")

    if sql_issues or cloud_sql_instances:
        findings += 1
        print(f"  {findings}. [DATABASE & CLOUD SQL INTROSPECTION] Instance: {sql_info['conn_name']} (Tier: {sql_info['tier'] or 'unknown'}, Max Conns: ~{sql_info['max_connections'] or 'unknown'})")
        if sql_info["live_fetched"]:
            safe_pool = max(1, int((sql_info["max_connections"] * 0.8) // max(1, int(max_scale))))
            print(f"     -> Safe Pool Sizing: With maxScale={max_scale}, cap your container DB pool at `pool_size <= {safe_pool}` per instance.")
        for issue in sql_issues:
            print(f"     -> {issue}")

bloated_repos = inspect_artifact_registry()
for repo_name, size_gb, est_cost in bloated_repos:
    findings += 1
    print(f"  {findings}. [ARTIFACT REGISTRY BLOAT] Repository `{repo_name}` is storing {size_gb} GB (~${est_cost}/mo) with NO cleanup policy.")
    print(f"     -> Action: Attach a cleanup policy (`gcloud artifacts repositories set-cleanup-policies {repo_name} --location={run_region} ...`) to keep only the 3 most recent images and purge old builds.")

if vpc_connector:
    findings += 1
    print(f"  {findings}. [COST & LATENCY] Legacy Serverless VPC Access Connector ({vpc_connector}) is in use.")
    print("     -> Action: Migrate to Direct VPC Egress (`--network` / `--subnet`) to eliminate fixed 24/7 connector VM costs ($15-$35+/mo) and reduce hop latency.")

if vpc_egress == "all-traffic":
    findings += 1
    print(f"  {findings}. [COST] VPC Egress is set to `all-traffic`.")
    print("     -> Action: Switch to `private-ranges-only` unless outbound static IPs via Cloud NAT are strictly required, avoiding Cloud NAT per-GB data processing fees.")

if gpu_type and gpu_zonal_disabled.lower() != "true":
    findings += 1
    print(f"  {findings}. [GPU COST] GPU Zonal Redundancy is ENABLED on {gpu_type}.")
    print("     -> Action: Pass `--no-gpu-zonal-redundancy` to reduce hourly GPU cost by ~30-38% and halve regional GPU quota consumption.")

if findings == 0:
    print("  [OK] No obvious configuration inefficiencies found! Review Cloud Monitoring CPU/Memory p99 metrics for right-sizing.")
EOF
