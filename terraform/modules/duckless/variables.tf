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

variable "data_buckets" {
  description = "Buckets the runner may read and write (Parquet data). The work bucket is always included."
  type        = list(string)
  default     = []
}

variable "runner_image_repository" {
  description = "Artifact Registry repository the runner pulls from, as projects/<p>/locations/<l>/repositories/<r>. Null when the image is public."
  type        = string
  default     = null
}

variable "labels" {
  description = "Labels added to every resource (and usable to track job costs in billing exports)."
  type        = map(string)
  default     = {}
}
