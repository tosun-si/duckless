#!/usr/bin/env bash
# One-off: the project, its billing link and the Terraform state bucket.
# Everything else is in main.tf.
#
#   ACCOUNT=me@gmail.com BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX ./bootstrap.sh
set -euo pipefail

: "${ACCOUNT:?personal Google account}" "${BILLING_ACCOUNT:?billing account id}"
PROJECT=${PROJECT:-duckless-public}
REGION=${REGION:-europe-west1}
STATE_BUCKET=${STATE_BUCKET:-${PROJECT}-tfstate}
G=(gcloud --account "$ACCOUNT")

"${G[@]}" projects describe "$PROJECT" >/dev/null 2>&1 \
  || "${G[@]}" projects create "$PROJECT" --name "DuckLess public" --labels app=duckless

"${G[@]}" billing projects link "$PROJECT" --billing-account "$BILLING_ACCOUNT" >/dev/null
"${G[@]}" services enable storage.googleapis.com cloudresourcemanager.googleapis.com --project "$PROJECT"

"${G[@]}" storage buckets describe "gs://$STATE_BUCKET" --project "$PROJECT" >/dev/null 2>&1 \
  || "${G[@]}" storage buckets create "gs://$STATE_BUCKET" --project "$PROJECT" --location "$REGION" \
       --uniform-bucket-level-access --public-access-prevention
"${G[@]}" storage buckets update "gs://$STATE_BUCKET" --versioning >/dev/null

echo "project $PROJECT ready, state in gs://$STATE_BUCKET"
