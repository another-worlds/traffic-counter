#!/usr/bin/env bash
# One-shot deploy script for traffic-counter on GCP.
#
# Prerequisites:
#   - gcloud CLI authenticated and set to your project (gcloud auth login)
#   - Billing enabled
#   - APIs enabled (the script enables them too): run, sqladmin, storage,
#     artifactregistry, cloudbuild, secretmanager
#
# Edit the variables in .env, then: ./infra/deploy.sh
#
# What it builds:
#   - Artifact Registry repo
#   - GCS bucket
#   - Cloud SQL Postgres instance + db + user
#   - Cloud Run service (api, public, CPU)
#   - Cloud Run service (frontend, public, CPU)
#   - Cloud Run JOB (worker, GPU L4)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] && set -a && source "$ROOT/.env" && set +a

: "${GCP_PROJECT:?GCP_PROJECT must be set in .env}"
: "${GCP_REGION:=europe-west4}"   # L4 supported region
: "${GCS_BUCKET:?GCS_BUCKET must be set in .env}"
: "${CLOUDSQL_INSTANCE:=traffic-counter-pg}"
: "${DB_PASSWORD:?DB_PASSWORD must be set in .env}"
: "${API_SERVICE:=traffic-counter-api}"
: "${FRONTEND_SERVICE:=traffic-counter-frontend}"
: "${WORKER_JOB:=traffic-counter-worker}"
: "${AR_REPO:=traffic-counter}"

echo "▶ project=$GCP_PROJECT region=$GCP_REGION"

gcloud config set project "$GCP_PROJECT" >/dev/null

echo "▶ enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com

echo "▶ artifact registry"
gcloud artifacts repositories describe "$AR_REPO" --location="$GCP_REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker --location="$GCP_REGION" \
    --description="traffic-counter images"
AR_HOST="${GCP_REGION}-docker.pkg.dev"
AR_PATH="$AR_HOST/$GCP_PROJECT/$AR_REPO"

echo "▶ GCS bucket"
gcloud storage buckets describe "gs://$GCS_BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$GCS_BUCKET" --location="$GCP_REGION"

echo "▶ Cloud SQL Postgres"
gcloud sql instances describe "$CLOUDSQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql instances create "$CLOUDSQL_INSTANCE" \
    --database-version=POSTGRES_16 \
    --tier=db-f1-micro --region="$GCP_REGION" \
    --storage-size=10GB --storage-type=SSD

gcloud sql databases describe traffic --instance="$CLOUDSQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create traffic --instance="$CLOUDSQL_INSTANCE"

gcloud sql users list --instance="$CLOUDSQL_INSTANCE" --format='value(name)' | grep -qx traffic || \
  gcloud sql users create traffic --instance="$CLOUDSQL_INSTANCE" --password="$DB_PASSWORD"

CLOUDSQL_CONN=$(gcloud sql instances describe "$CLOUDSQL_INSTANCE" --format='value(connectionName)')
DB_URL="postgresql+psycopg://traffic:${DB_PASSWORD}@/traffic?host=/cloudsql/${CLOUDSQL_CONN}"

echo "▶ building images with Cloud Build"
gcloud builds submit "$ROOT/api"      --tag "$AR_PATH/api:latest"
gcloud builds submit "$ROOT/worker"   --tag "$AR_PATH/worker:latest"
gcloud builds submit "$ROOT/frontend" --tag "$AR_PATH/frontend:latest"

echo "▶ deploying API"
gcloud run deploy "$API_SERVICE" \
  --image "$AR_PATH/api:latest" \
  --region "$GCP_REGION" \
  --platform managed --allow-unauthenticated \
  --add-cloudsql-instances "$CLOUDSQL_CONN" \
  --cpu 2 --memory 2Gi \
  --min-instances 0 --max-instances 5 \
  --set-env-vars "ENV=prod,STORAGE_BACKEND=gcs,GCS_BUCKET=$GCS_BUCKET,JOB_RUNNER=cloudrun,GCP_PROJECT=$GCP_PROJECT,GCP_REGION=$GCP_REGION,WORKER_JOB_NAME=$WORKER_JOB" \
  --set-env-vars "^|^DATABASE_URL=$DB_URL"

API_URL=$(gcloud run services describe "$API_SERVICE" --region "$GCP_REGION" --format='value(status.url)')
echo "  API_URL=$API_URL"

echo "▶ deploying worker job (L4 GPU, scale-to-zero)"
# Use --no-gpu-zonal-redundancy so quota grant is automatic and per-second billing is best
gcloud run jobs describe "$WORKER_JOB" --region "$GCP_REGION" >/dev/null 2>&1 && OP=update || OP=create
gcloud run jobs "$OP" "$WORKER_JOB" \
  --image "$AR_PATH/worker:latest" \
  --region "$GCP_REGION" \
  --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu 4 --memory 16Gi \
  --max-retries 1 --parallelism 1 --tasks 1 \
  --add-cloudsql-instances "$CLOUDSQL_CONN" \
  --set-env-vars "STORAGE_BACKEND=gcs,GCS_BUCKET=$GCS_BUCKET,WORKER_MODE=single,DEVICE=cuda:0,HALF=true,MODEL_NAME=yolov8m.pt" \
  --set-env-vars "^|^DATABASE_URL=$DB_URL"

echo "▶ deploying frontend"
gcloud run deploy "$FRONTEND_SERVICE" \
  --image "$AR_PATH/frontend:latest" \
  --region "$GCP_REGION" \
  --platform managed --allow-unauthenticated \
  --cpu 1 --memory 1Gi \
  --min-instances 0 --max-instances 3 \
  --set-env-vars "API_URL=$API_URL"

FRONTEND_URL=$(gcloud run services describe "$FRONTEND_SERVICE" --region "$GCP_REGION" --format='value(status.url)')

cat <<EOF

✅ Done.

  Frontend:  $FRONTEND_URL
  API:       $API_URL
  Bucket:    gs://$GCS_BUCKET
  Cloud SQL: $CLOUDSQL_CONN
  Worker:    Cloud Run Job '$WORKER_JOB' (L4 GPU)

To trigger a worker run manually:
  gcloud run jobs execute $WORKER_JOB --region $GCP_REGION \\
      --update-env-vars VIDEO_ID=<uuid>
EOF
