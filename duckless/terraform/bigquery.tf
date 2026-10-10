# Reading BigQuery from jobs: the Storage Read API (bigquery_scan, read_bigquery) needs a read
# session on the project and read access on the datasets; queries need jobUser on top.

locals {
  bigquery_reads = length(var.bigquery_datasets) > 0 || var.bigquery_jobs
  bigquery_services = local.bigquery_reads ? toset([
    "bigquery.googleapis.com",
    "bigquerystorage.googleapis.com",
  ]) : toset([])
  bigquery_runner_roles = toset(concat(
    local.bigquery_reads ? ["roles/bigquery.readSessionUser"] : [],
    var.bigquery_jobs ? ["roles/bigquery.jobUser"] : [],
  ))
}

resource "google_project_service" "bigquery" {
  for_each = local.bigquery_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_project_iam_member" "runner_bigquery" {
  for_each = local.bigquery_runner_roles

  project = var.project_id
  role    = each.value
  member  = google_service_account.runner.member

  depends_on = [google_project_service.bigquery]
}

locals {
  # `dataset` or `project.dataset` (`project:dataset` accepted) -> {project, dataset}
  bigquery_dataset_refs = {
    for d in var.bigquery_datasets : d => {
      project = length(split(".", replace(d, ":", "."))) == 2 ? split(".", replace(d, ":", "."))[0] : var.project_id
      dataset = reverse(split(".", replace(d, ":", ".")))[0]
    }
  }
}

resource "google_bigquery_dataset_iam_member" "runner_read" {
  for_each = local.bigquery_dataset_refs

  project    = each.value.project
  dataset_id = each.value.dataset
  role       = "roles/bigquery.dataViewer"
  member     = google_service_account.runner.member

  depends_on = [google_project_service.bigquery]
}
