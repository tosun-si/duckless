#!/usr/bin/env bash
# Spike infra (will become the Terraform module behind `duckless init`).
# Idempotent: re-running only creates what is missing.
set -euo pipefail

: "${DUCKLESS_PROJECT:?}" "${DUCKLESS_REGION:?}" "${DUCKLESS_BUCKET:?}"
P=$DUCKLESS_PROJECT R=$DUCKLESS_REGION B=$DUCKLESS_BUCKET
SA_NAME=duckless-runner
SA="${SA_NAME}@${P}.iam.gserviceaccount.com"
REPO=duckless

gcloud services enable batch.googleapis.com compute.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com logging.googleapis.com --project "$P"

gcloud storage buckets describe "gs://$B" --project "$P" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$B" --project "$P" --location "$R" \
       --uniform-bucket-level-access --public-access-prevention

gcloud artifacts repositories describe "$REPO" --project "$P" --location "$R" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --project "$P" --location "$R" --repository-format docker

gcloud iam service-accounts describe "$SA" --project "$P" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_NAME" --project "$P" --display-name "DuckLess runner (spike)"

for role in roles/batch.agentReporter roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$P" --member "serviceAccount:$SA" --role "$role" --condition None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$B" --member "serviceAccount:$SA" --role roles/storage.objectUser >/dev/null
gcloud artifacts repositories add-iam-policy-binding "$REPO" --project "$P" --location "$R" \
  --member "serviceAccount:$SA" --role roles/artifactregistry.reader >/dev/null

echo "runner SA : $SA"
echo "bucket    : gs://$B"
echo "image     : ${R}-docker.pkg.dev/${P}/${REPO}/runner"
