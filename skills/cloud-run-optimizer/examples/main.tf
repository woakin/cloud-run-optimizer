# Optimized Terraform Configuration (google_cloud_run_v2_service)
# Includes: Request-based billing, Startup CPU Boost, Direct VPC Egress, and Least-Privilege SA

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1" # Tier-1 pricing region
}

resource "google_service_account" "cloud_run_sa" {
  account_id   = "app-cloud-run-sa"
  display_name = "Cloud Run Service Runtime Identity"
  project      = var.project_id
}

resource "google_cloud_run_v2_service" "optimized_service" {
  name                = "optimized-api"
  location            = var.region
  project             = var.project_id
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.cloud_run_sa.email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 80
    timeout                          = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = 25
    }

    # Direct VPC Egress (Scales to zero — $0 idle cost vs VPC Access Connectors)
    vpc_access {
      network_interfaces {
        network    = "default"
        subnetwork = "default"
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/containers/api:latest"

      ports {
        name           = "http1"
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        # cpu_idle = true enables Request-Based Billing (--cpu-throttling)
        cpu_idle = true
        # startup_cpu_boost = true cuts cold-start times at $0 idle cost
        startup_cpu_boost = true
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 0
        period_seconds        = 1
        timeout_seconds       = 1
        failure_threshold     = 10
      }
    }
  }
}
