# DuckLess in your own Terraform, applied by your CI/CD with your deployment identity.
# `duckless init` does the same through Infrastructure Manager; this is the way for teams that
# keep their infrastructure as code.

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.18" # your lock file pins the exact version
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "Project where the DuckLess jobs run."
  type        = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

module "duckless" {
  source = "git::https://github.com/tosun-si/duckless.git//duckless/terraform?ref=v0.4.1"

  project_id       = var.project_id
  region           = var.region
  runner_image_tag = "0.4.1" # the CLI version your jobs use

  # Buckets the jobs read and write (in any project).
  data_buckets = ["${var.project_id}-raw", "central-lake-bucket"]

  # BigQuery datasets the jobs read: `dataset` (this project) or `project.dataset`.
  bigquery_datasets = ["sales", "ref-project.reference_data"]

  # DuckLake tables: a Cloud SQL catalog with a private IP on the jobs' network. The network needs
  # private services access (often already there on a Shared VPC); see the DuckLake guide.
  ducklake   = true
  network    = "projects/host-project/global/networks/shared-vpc"
  subnetwork = "projects/host-project/regions/europe-west1/subnetworks/data-europe-west1"

  labels = { team = "data-platform" }
}

# The settings DuckLess users need: put them in the project's .envrc.
output "envrc" {
  value = module.duckless.envrc
}
