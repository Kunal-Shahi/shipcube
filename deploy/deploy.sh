#!/usr/bin/env bash
# Build ShipCube's images on Cloud Build and deploy the four Cloud Run services.
#
#   DATA_WORKSPACE=<workspace uuid> deploy/deploy.sh             build + deploy everything
#                                       (without DATA_WORKSPACE the data_* MCP tools aren't mounted)
#   DATA_WORKSPACE=... deploy/deploy.sh --no-build                redeploy the images for $TAG
#   deploy/deploy.sh --only gateway [--no-build]                  one service (console|mcp|worker|gateway)
#
# Prerequisites (deploy/README.md): Terraform applied (service accounts, secrets, build bucket);
# migrations applied with infra/db/migrate.sh (the services refuse to start on a pending one; this
# script never migrates).
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/env.sh

BUILD=1
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --only) ONLY="$2"; shift ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done
want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

if [ -n "$(git status --porcelain)" ]; then
  echo "warning: uncommitted changes; images are tagged $TAG but include them" >&2
fi

if [ "$BUILD" = 1 ]; then
  gcloud builds submit . --project "$PROJECT" --region "$REGION" --config deploy/cloudbuild.yaml \
    --ignore-file deploy/.gcloudignore --service-account "projects/$PROJECT/serviceAccounts/$SA_BUILD" \
    --gcs-source-staging-dir "gs://$BUILD_BUCKET/source" --substitutions "_IMAGES=$IMAGES,_TAG=$TAG"
fi

render() {  # render <template> -> stdout; fails on any unset ${VAR}
  python3 - "$1" <<'PY'
import os, re, string, sys
text = open(sys.argv[1]).read()
missing = sorted({m for m in re.findall(r"\$\{([A-Z_]+)\}", text) if m not in os.environ})
if missing:
    sys.exit(f"{sys.argv[1]}: unset variables: {', '.join(missing)}")
print(string.Template(text).substitute(os.environ))
PY
}
apply() {  # apply <name>: render deploy/services/<name>.yaml and replace the Cloud Run service
  local out
  out="$(mktemp "${TMPDIR:-/tmp}/shipcube-$1.XXXXXX")"
  render "deploy/services/$1.yaml" > "$out"
  gcloud run services replace "$out" --project "$PROJECT" --region "$REGION" --quiet
  rm -f "$out"
}
public() {  # public <service>: allow unauthenticated invocations (the service authenticates callers itself)
  gcloud run services add-iam-policy-binding "$1" --project "$PROJECT" --region "$REGION" \
    --member allUsers --role roles/run.invoker --quiet >/dev/null
}

export PROJECT APPS_PROJECT REGION TAG SQL_INSTANCE CONSOLE_URL MCP_URL GATEWAY_URL META_KEY_VERSION PEPPER_VERSION
export SA_API SA_WORKER SA_GATEWAY SA_WEB SA_HOSTED_DEPLOYER
export IMAGE_API="$IMAGES/api:$TAG" IMAGE_GATEWAY="$IMAGES/gateway:$TAG" IMAGE_CONSOLE="$IMAGES/console:$TAG"
export MCP_HOST="${MCP_URL#https://}"

if want mcp; then
  if [ -z "${DATA_WORKSPACE:-}" ]; then
    echo "warning: DATA_WORKSPACE is unset, so the data_* tools are not mounted (11 of 16 MCP tools)" >&2
  fi
  export DATA_WORKSPACE="${DATA_WORKSPACE:-}"
  apply mcp
  public shipcube-mcp
fi

if want worker; then
  # The profiler sandbox settings come from Terraform (module.profiler.env), or from
  # PROFILER_ENV_JSON where Terraform state isn't readable (the GitHub deploy workflow).
  if [ -z "${PROFILER_ENV_JSON:-}" ]; then
    PROFILER_ENV_JSON="$(terraform -chdir=infra/terraform output -json profiler)"
  fi
  PROFILER_ENV="$(echo "$PROFILER_ENV_JSON" | python3 -c '
import json, sys
for k, v in sorted(json.load(sys.stdin).items()):
    print(f"            - {{name: {k}, value: \"{v}\"}}")')"
  export PROFILER_ENV
  apply worker
  # The uptime check calls /healthz with an OIDC token minted by the Monitoring service agent,
  # which Google creates on first use; make sure it exists before granting it.
  curl -fsS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "x-goog-user-project: $PROJECT" \
    "https://serviceusage.googleapis.com/v1beta1/projects/$PROJECT/services/monitoring.googleapis.com:generateServiceIdentity" >/dev/null
  gcloud run services add-iam-policy-binding shipcube-worker --project "$PROJECT" --region "$REGION" \
    --member "serviceAccount:service-$PROJECT_NUMBER@gcp-sa-monitoring-notification.iam.gserviceaccount.com" \
    --role roles/run.invoker --quiet >/dev/null
fi

if want gateway; then
  python3 deploy/gateway_registry.py --project "$PROJECT" --apps-project "$APPS_PROJECT" --region "$REGION"
  apply gateway
  public shipcube-gateway
fi

if want console; then
  # The Identity Platform web API key is public by design (it identifies the project to the browser).
  KEY_ID="$(gcloud services api-keys list --project "$PROJECT" --filter 'displayName~"Browser key"' --format 'value(name)' | head -1)"
  FIREBASE_WEB_API_KEY="$(gcloud services api-keys get-key-string "$KEY_ID" --format 'value(keyString)')"
  export FIREBASE_WEB_API_KEY
  apply console
  public shipcube-console
  python3 deploy/identity_platform.py --project "$PROJECT" --add-domain "${CONSOLE_URL#https://}"
fi

echo
echo "Health:"
for svc in console mcp gateway worker; do
  want "$svc" || continue
  url="$(run_url "shipcube-$svc")"
  path=/health; [ "$svc" = console ] && path=/sign-in  # /healthz is reserved on run.app URLs
  auth=()
  [ "$svc" = worker ] && auth=(-H "Authorization: Bearer $(gcloud auth print-identity-token)")
  code="$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$url$path" || true)"
  printf '  %-16s %s  %s%s\n' "shipcube-$svc" "$code" "$url" "$path"
done
cat <<URLS

Console:  $CONSOLE_URL
MCP:      $MCP_URL/mcp
API:      $MCP_URL/v1
Gateway:  $GATEWAY_URL/v1
Worker:   $WORKER_URL (private)
URLS
