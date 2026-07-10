#!/usr/bin/env bash
#
# Build the lake-rise-model image locally and deploy it to the Synology NAS over
# password-based SSH, without going through a registry.
#
# Flow: build -> docker save | gzip -> scp to a staging dir on the NAS -> docker load
#       -> move compose + runtime .env into the deploy dir -> docker-compose up -d.
#
# Config comes from a local .env in this directory (copy .env.example). The NAS runs
# Docker 20.10.3 / docker-compose 1.28.5 and requires sudo for docker, so all remote
# docker calls are fed the SSH password via `sudo -S` on stdin.
#
# Requires (local): docker, sshpass, ssh, scp, gzip.
#   sshpass:  brew install hudochenkov/sshpass/sshpass
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Load config -------------------------------------------------------------
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source ./.env
set +a

: "${SSH_HOST:?Set SSH_HOST in .env}"
: "${SSH_USER:?Set SSH_USER in .env}"
: "${SSH_PASSWORD:?Set SSH_PASSWORD in .env}"
: "${HA_URL:?Set HA_URL in .env}"
: "${HA_TOKEN:?Set HA_TOKEN in .env}"
SSH_PORT="${SSH_PORT:-22}"
NAS_DEPLOY_DIR="${NAS_DEPLOY_DIR:-/volume2/docker/lake-rise}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LAKE_RISE_PORT="${LAKE_RISE_PORT:-8077}"
# The Synology NAS is x86_64; build for it regardless of the local (e.g. Apple
# Silicon) host. Override only if your NAS is arm64.
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
IMAGE="lake-rise-model:${IMAGE_TAG}"

# --- Preflight ---------------------------------------------------------------
command -v docker  >/dev/null 2>&1 || { echo "ERROR: docker not found locally." >&2; exit 1; }
docker buildx version >/dev/null 2>&1 || { echo "ERROR: docker buildx not available (needed for cross-platform build)." >&2; exit 1; }
command -v sshpass >/dev/null 2>&1 || {
  echo "ERROR: sshpass not found. Install: brew install hudochenkov/sshpass/sshpass" >&2
  exit 1
}

# --- Validate the HA token up front (catches mis-pastes before a slow deploy) -
# A JWT is header.payload.signature; HA signs long-lived tokens with HS256, so
# the signature is HMAC-SHA256 = 32 bytes = 43 base64url chars. A different length
# means the string is corrupted (a copy-paste slip in the opaque tail is invisible
# to the eye). Then confirm HA actually accepts it.
IFS='.' read -r _jh _jp _js <<< "$HA_TOKEN"
if [[ -z "${_jh:-}" || -z "${_jp:-}" || -z "${_js:-}" ]]; then
  echo "ERROR: HA_TOKEN is not a JWT (expected 3 dot-separated parts)." >&2
  exit 1
fi
if [[ "${#_js}" -ne 43 ]]; then
  echo "ERROR: HA_TOKEN signature is ${#_js} chars; a valid HS256 token signature is 43." >&2
  echo "       The token is malformed (likely a copy-paste slip). Re-copy it from Home Assistant." >&2
  exit 1
fi
echo "==> Checking token against $HA_URL"
_ha_code="$(curl -s -o /dev/null -w '%{http_code}' -m 8 -H "Authorization: Bearer $HA_TOKEN" "${HA_URL%/}/api/" 2>/dev/null || echo 000)"
case "$_ha_code" in
  200) echo "    HA auth OK (200)";;
  401) echo "ERROR: HA returned 401 at $HA_URL — token rejected (revoked, or wrong HA instance)." >&2; exit 1;;
  000) echo "    WARN: could not reach $HA_URL from here (fine if it's a NAS-only address); skipping live auth check.";;
  *)   echo "    WARN: HA returned HTTP $_ha_code at $HA_URL; continuing.";;
esac

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
SSHPASS_ENV=(env "SSHPASS=$SSH_PASSWORD")

ssh_run() { "${SSHPASS_ENV[@]}" sshpass -e ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "$@"; }
# Stream a local file to a remote path over the interactive shell (not SFTP/scp).
# Synology's SFTP subsystem is chrooted to the user's home, so scp can't see the
# absolute deploy dir; the shell can. The deploy dir is user-writable (0777).
ssh_put() { "${SSHPASS_ENV[@]}" sshpass -e ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" "cat > '$2'" < "$1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
IMAGE_TAR="$TMP/lake-rise-model.tar"
TARBALL="$TMP/lake-rise-model.tar.gz"
RUNTIME_ENV="$TMP/.env"
# /volume2/docker/lake-rise is 0777 and writable by the SSH user, so we scp files
# straight into the deploy dir (the SSH user's home has a space in its path and an
# unreliable SFTP root, so we avoid staging there).
REMOTE_STAGE="$NAS_DEPLOY_DIR"

# --- 1. Build + save (single-platform Docker-format tar for old `docker load`) -
# `--platform linux/amd64` targets the x86_64 NAS; `--provenance=false` +
# `type=docker` avoid the OCI manifest-list/attestation format that Docker 20.10.3
# cannot load.
echo "==> Building $IMAGE for $TARGET_PLATFORM"
docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --provenance=false \
  -t "$IMAGE" \
  --output "type=docker,dest=$IMAGE_TAR" \
  .

echo "==> Compressing image to $TARBALL"
gzip -c "$IMAGE_TAR" > "$TARBALL"

# --- 3. Generate the NAS runtime .env (app keys only; no SSH/deploy creds) ----
# Forward every app-relevant key set in the local .env: HA_URL/HA_TOKEN plus any
# LAKE_RISE_*, ALERT_*, SMTP_*, TWILIO_*, CALIB_* setting — so alerting, calibration, and the
# sensor / freshness overrides actually reach the NAS, not just the HA connection. Deploy-only
# keys (SSH_*, NAS_*, TARGET_PLATFORM) are deliberately NOT copied. Values come from the
# already-sourced environment (so quotes / inline comments the shell stripped don't leak)
# and are written unquoted, which is what docker-compose's env_file expects.
echo "==> Generating runtime .env"
{
  printf 'LAKE_RISE_PORT=%s\n' "$LAKE_RISE_PORT"
  printf 'IMAGE_TAG=%s\n'      "$IMAGE_TAG"
  printf 'HA_URL=%s\n'         "$HA_URL"
  printf 'HA_TOKEN=%s\n'       "$HA_TOKEN"
  # Names of all uncommented LAKE_RISE_/ALERT_/SMTP_/TWILIO_/CALIB_ assignments in .env
  # (`|| true` so a .env with none doesn't trip `set -e` via grep's exit 1).
  { grep -E '^[[:space:]]*(export[[:space:]]+)?(LAKE_RISE_|ALERT_|SMTP_|TWILIO_|CALIB_)[A-Za-z0-9_]*=' .env || true; } \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=.*//' \
    | sort -u \
    | while IFS= read -r k; do
        case "$k" in LAKE_RISE_PORT|IMAGE_TAG|HA_URL|HA_TOKEN) continue;; esac  # already emitted
        v="${!k:-}"
        [[ -n "$v" ]] && printf '%s=%s\n' "$k" "$v"
      done
} > "$RUNTIME_ENV"

# --- 4. Transfer straight into the deploy dir --------------------------------
echo "==> Transferring to ${SSH_USER}@${SSH_HOST}:${REMOTE_STAGE}"
ssh_put "$TARBALL"        "$REMOTE_STAGE/lake-rise-model.tar.gz"
ssh_put docker-compose.yml "$REMOTE_STAGE/docker-compose.yml"
ssh_put "$RUNTIME_ENV"    "$REMOTE_STAGE/.env"

# --- 5. Remote install (sudo via password on stdin) --------------------------
echo "==> Loading image and starting container on the NAS"
ssh_run "SUDO_PW='$SSH_PASSWORD' DEPLOY_DIR='$NAS_DEPLOY_DIR' IMAGE='$IMAGE' bash -s" <<'REMOTE'
set -euo pipefail
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"
COMPOSE="$(command -v docker-compose || echo /usr/local/bin/docker-compose)"
sudo_do() { printf '%s\n' "$SUDO_PW" | sudo -S "$@"; }

cd "$DEPLOY_DIR"

echo "--> docker load"
sudo_do "$DOCKER" load -i "$DEPLOY_DIR/lake-rise-model.tar.gz"

# Seed the host-mounted artifacts/ from the image so the bind mount doesn't shadow the baked
# baseline model. `docker cp` overwrites the baseline model + registry (so they track the image)
# but never deletes host files, so any calibration versions/ and calibration_state.json persist.
# data/ is created empty (the archive fills it at runtime).
echo "--> seeding host artifacts/ from image (keeps calibration versions/state)"
mkdir -p "$DEPLOY_DIR/artifacts" "$DEPLOY_DIR/data"
_cid="$(sudo_do "$DOCKER" create "$IMAGE")"
sudo_do "$DOCKER" cp "$_cid:/app/artifacts/." "$DEPLOY_DIR/artifacts/"
sudo_do "$DOCKER" rm -f "$_cid" >/dev/null

echo "--> docker-compose up -d"
sudo_do "$COMPOSE" up -d

echo "--> status"
sudo_do "$COMPOSE" ps

rm -f "$DEPLOY_DIR/lake-rise-model.tar.gz"
REMOTE

echo
echo "==> Done. Service should be at http://${SSH_HOST}:${LAKE_RISE_PORT}/  (point the reverse proxy here)"
echo "    Health: http://${SSH_HOST}:${LAKE_RISE_PORT}/health"
