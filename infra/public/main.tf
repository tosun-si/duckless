# Public artifacts of the DuckLess open-source project (not something users deploy):
# the runner image registry, its private build cache, and keyless GitHub Actions access.
# Project, billing and state bucket are created once by bootstrap.sh.

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }

  backend "gcs" {
    bucket = "duckless-public-tfstate"
    prefix = "public"
  }
}

provider "google" {
  project = var.project_id
}

locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])
}

resource "google_project_service" "this" {
  for_each = local.services

  service            = each.value
  disable_on_destroy = false
}

# ---------- registries ----------

resource "google_artifact_registry_repository" "runner" {
  location      = var.registry_location
  repository_id = "duckless"
  description   = "DuckLess runner images (public)"
  format        = "DOCKER"
  labels        = { app = "duckless" }

  depends_on = [google_project_service.this]
}

resource "google_artifact_registry_repository_iam_member" "runner_public" {
  location   = google_artifact_registry_repository.runner.location
  repository = google_artifact_registry_repository.runner.name
  role       = "roles/artifactregistry.reader"
  member     = "allUsers"
}

resource "google_artifact_registry_repository" "ci_cache" {
  location      = var.registry_location
  repository_id = "ci-cache"
  description   = "Docker build cache for CI (private)"
  format        = "DOCKER"
  labels        = { app = "duckless" }

  cleanup_policies {
    id     = "drop-old-cache"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }

  depends_on = [google_project_service.this]
}

# ---------- GitHub Actions, keyless (WIF) ----------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only this repository can exchange tokens, whatever the binding below says.
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  account_id   = "github-ci"
  display_name = "GitHub Actions CI (${var.github_repository})"
}

resource "google_service_account_iam_member" "ci_wif" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "ci_push" {
  for_each = {
    runner   = google_artifact_registry_repository.runner.name
    ci_cache = google_artifact_registry_repository.ci_cache.name
  }

  location   = var.registry_location
  repository = each.value
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.ci.member
}
