variable "project_id" {
  description = "Project where DuckLess jobs run (data never leaves it)."
  type        = string
}

variable "region" {
  description = "Region of the work bucket and of the job VMs."
  type        = string
  default     = "europe-west1"
}

variable "name" {
  description = "Prefix for the resources DuckLess creates."
  type        = string
  default     = "duckless"
}

variable "work_bucket_name" {
  description = "Work bucket (job sources, runner metrics). Defaults to <project_id>-<name>-work."
  type        = string
  default     = null
}

variable "runs_retention_days" {
  description = "Days after which runs/<job-id>/ objects (sources, metrics) are deleted."
  type        = number
  default     = 30
}

variable "force_destroy" {
  description = "Let `duckless destroy` delete the work bucket even when it still holds objects."
  type        = bool
  default     = false
}

variable "data_buckets" {
  description = "Buckets the runner may read and write (Parquet data). The work bucket is always included."
  type        = list(string)
  default     = []
}

variable "runner_image_registry" {
  description = "Upstream registry of the runner image, proxied by an Artifact Registry remote repository."
  type        = string
  default     = "https://ghcr.io"
}

variable "runner_image_path" {
  description = "Runner image path in the upstream registry."
  type        = string
  default     = "tosun-si/duckless-runner"
}

variable "runner_image_tag" {
  description = "Runner image tag; pin it to the duckless CLI version."
  type        = string
  default     = "edge"
}

variable "labels" {
  description = "Labels added to every resource (and usable to track job costs in billing exports)."
  type        = map(string)
  default     = {}
}

variable "ducklake" {
  description = "Create a DuckLake catalog: a Cloud SQL Postgres instance on a private IP, used by every job."
  type        = bool
  default     = false
}

variable "network" {
  description = "VPC network of the job VMs and of the catalog's private IP."
  type        = string
  default     = "default"
}

variable "catalog_tier" {
  description = "Cloud SQL machine tier of the catalog. It holds metadata only; db-g1-small fits most lakes."
  type        = string
  default     = "db-g1-small"
}

variable "bigquery_datasets" {
  description = "BigQuery datasets of this project the jobs may read (dataset ids): the runner gets roles/bigquery.dataViewer on each, and the Storage Read API on the project."
  type        = list(string)
  default     = []
}

variable "bigquery_jobs" {
  description = "Let jobs run BigQuery queries (the extension's ATTACH, bigquery_query): roles/bigquery.jobUser on the project."
  type        = bool
  default     = false
}
