#!/usr/bin/env bash
# ==============================================================================
# Cloud Run Live Service Auditor
# Inspects a deployed Cloud Run service via `gcloud` and flags cost & latency
# optimization opportunities.
# Usage: bash audit-live-service.sh <SERVICE_NAME> <REGION> [PROJECT_ID]
# ==============================================================================

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <SERVICE_NAME> <REGION> [PROJECT_ID]" >&2
  exit 1
fi

SERVICE_NAME="$1"
REGION="$2"
PROJECT_FLAG=()
if [ $# -ge 3 ] && [ -n "$3" ]; then
  PROJECT_FLAG=(--project="$3")
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "Error: 'gcloud' CLI is not installed or not in PATH." >&2
  exit 1
fi

echo "=================================================================="
echo " Auditing Live Cloud Run Service: $SERVICE_NAME ($REGION)"
echo "=================================================================="

SERVICE_JSON=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" "${PROJECT_FLAG[@]}" --format=json)

python3 -c '
import json, sys

data = json.loads(sys.stdin.read())
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

print(f"\n[Current Configuration]")
print(f"  - Image:                 {c0.get(\"image\", \"unknown\")}")
print(f"  - CPU / Memory:          {cpu} vCPU / {memory}")
print(f"  - Concurrency:           {concurrency}")
print(f"  - Min / Max Instances:   {min_scale} / {max_scale}")
print(f"  - CPU Throttling:        {cpu_throttling} ({\"Request-based billing\" if cpu_throttling == \"true\" else \"Instance-based 24/7 billing\"})")
print(f"  - Startup CPU Boost:     {cpu_boost}")
print(f"  - Execution Environment: {exec_env}")
print(f"  - Request Timeout:       {timeout}s")
print(f"  - VPC Connector:         {vpc_connector or \"None\"}")
print(f"  - Direct VPC Network:    {\"Configured\" if network_interfaces else \"None\"} (egress: {vpc_egress or \"N/A\"})")

print(f"\n[Optimization Findings]")
findings = 0

if cpu_boost.lower() != "true":
    findings += 1
    print("  1. [PERFORMANCE] Startup CPU Boost is DISABLED.")
    print("     -> Action: Enable `--cpu-boost` to cut cold-start latency at $0 idle cost.")

if cpu_throttling.lower() == "false":
    findings += 1
    print("  2. [COST] Instance-based billing (`--no-cpu-throttling`) is ENABLED.")
    print("     -> Action: Unless this service has >40% steady 24/7 CPU usage or background threads, switch to `--cpu-throttling` to stop paying for idle CPU between requests.")

if int(min_scale) > 0:
    findings += 1
    print(f"  3. [COST] minScale is set to {min_scale} (warm instances kept alive 24/7).")
    print("     -> Action: Verify if `--cpu-boost` with `min-instances=0` meets your latency SLA, or ensure `--cpu-throttling` is enabled to pay the lower idle rate.")

if int(concurrency) == 1:
    findings += 1
    print("  4. [COST & SCALE] Concurrency is set to 1.")
    print("     -> Action: If this service handles I/O (DB/HTTP calls), raising `--concurrency` to 40-80 can reduce instance count and compute cost by up to 90%.")

if vpc_connector:
    findings += 1
    print(f"  5. [COST & LATENCY] Legacy Serverless VPC Access Connector ({vpc_connector}) is in use.")
    print("     -> Action: Migrate to Direct VPC Egress (`--network` / `--subnet`) to eliminate fixed 24/7 connector VM costs ($15-$35+/mo) and reduce hop latency.")

if vpc_egress == "all-traffic":
    findings += 1
    print("  6. [COST] VPC Egress is set to `all-traffic`.")
    print("     -> Action: Switch to `private-ranges-only` unless outbound static IPs via Cloud NAT are strictly required, avoiding Cloud NAT per-GB data processing fees.")

if findings == 0:
    print("  [OK] No obvious configuration inefficiencies found! Review Cloud Monitoring CPU/Memory p99 metrics for right-sizing.")
' <<< "$SERVICE_JSON"
