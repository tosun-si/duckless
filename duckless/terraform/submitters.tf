# Who runs jobs: the people or CI identities that call `duckless run` / `exec`. The CLI submits
# jobs as them, asking Batch or Cloud Run to run as the runner account.

locals {
  submitter_project_roles = toset([
    "roles/batch.jobsEditor", # create, follow and cancel Batch jobs
    "roles/run.developer",    # create, run and cancel Cloud Run jobs
    "roles/logging.viewer",   # duckless logs
    "roles/compute.viewer",   # duckless preflight: regional quotas
  ])
  submitter_grants = {
    for pair in setproduct(var.job_submitters, local.submitter_project_roles) : "${pair[0]} ${pair[1]}" => {
      member = pair[0]
      role   = pair[1]
    }
  }
}

resource "google_project_iam_member" "submitters" {
  for_each = local.submitter_grants

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}

# Jobs run as the runner account: submitting one needs actAs on it.
resource "google_service_account_iam_member" "submitters_act_as" {
  for_each = toset(var.job_submitters)

  service_account_id = google_service_account.runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

# Job sources go up to the work bucket; metrics and results come back from it.
resource "google_storage_bucket_iam_member" "submitters_work" {
  for_each = toset(var.job_submitters)

  bucket = google_storage_bucket.work.name
  role   = "roles/storage.objectUser"
  member = each.value
}
