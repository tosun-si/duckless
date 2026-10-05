locals {
  work_bucket = coalesce(var.work_bucket_name, "${var.project_id}-${var.name}-work")
  labels      = merge({ app = "duckless" }, var.labels)
  services = toset([
    "artifactregistry.googleapis.com",
    "batch.googleapis.com",
    "compute.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "storage.googleapis.com",
  ])
  # Minimum the job VM needs: report task status to Batch, write logs and metrics.
  runner_project_roles = toset([
    "roles/batch.agentReporter",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  data_buckets = toset(concat([local.work_bucket], var.data_buckets))
}

resource "google_project_service" "this" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "work" {
  project                     = var.project_id
  name                        = local.work_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy
  labels                      = local.labels

  lifecycle_rule {
    condition {
      age            = var.runs_retention_days
      matches_prefix = ["runs/"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.this]
}

resource "google_service_account" "runner" {
  project      = var.project_id
  account_id   = "${var.name}-runner"
  display_name = "DuckLess runner (job VMs)"
}

resource "google_project_iam_member" "runner" {
  for_each = local.runner_project_roles

  project = var.project_id
  role    = each.value
  member  = google_service_account.runner.member
}

# objectUser: read, create, overwrite and delete objects; no bucket admin.
resource "google_storage_bucket_iam_member" "runner_data" {
  for_each = local.data_buckets

  bucket = each.value
  role   = "roles/storage.objectUser"
  member = google_service_account.runner.member

  depends_on = [google_storage_bucket.work]
}

locals {
  runner_image = "${google_artifact_registry_repository.runner.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.runner.repository_id}/${var.runner_image_path}:${var.runner_image_tag}"
}

# Pull-through cache of the public runner image: job VMs have no external IP and reach
# Artifact Registry through Private Google Access; the image is cached in the region.
resource "google_artifact_registry_repository" "runner" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name}-runner"
  description   = "DuckLess runner image, proxied from ${var.runner_image_registry}"
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"
  labels        = local.labels

  remote_repository_config {
    description = var.runner_image_registry
    docker_repository {
      custom_repository {
        uri = var.runner_image_registry
      }
    }
  }

  depends_on = [google_project_service.this]
}

resource "google_artifact_registry_repository_iam_member" "runner_pull" {
  project    = var.project_id
  location   = google_artifact_registry_repository.runner.location
  repository = google_artifact_registry_repository.runner.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.runner.member
}
