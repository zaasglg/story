#!/usr/bin/env bash
# Update the existing systemd-managed Docker backend; preserve its environment.
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $EUID -ne 0 ]]; then
  echo "Run as root on the backend server." >&2
  exit 1
fi

update_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_directory="/var/backups/m1917/update-${update_stamp}"
rollback_image="alash-backend:rollback-${update_stamp}"
host_python=/opt/m1917-backend/.venv/bin/python
service_stopped=false

for executable in docker systemctl curl /usr/bin/pg_dump /usr/bin/pg_restore "$host_python"; do
  command -v "$executable" >/dev/null
done
test -r /etc/m1917/backend.codex.env
systemctl is-active --quiet m1917-backend
docker image tag "$(docker inspect --format '{{.Image}}' m1917-backend-runtime)" "$rollback_image"

recover() {
  update_status=$?
  trap - ERR
  echo "Update failed. Restoring the previous runtime image; database is not restored." >&2
  if [[ $service_stopped == true ]]; then
    systemctl stop m1917-backend || true
  fi
  docker image tag "$rollback_image" alash-backend:current || true
  if [[ $service_stopped == true ]]; then
    systemctl start m1917-backend || true
  fi
  echo "Check: journalctl -u m1917-backend -n 100 --no-pager" >&2
  exit "$update_status"
}
trap recover ERR

docker build -t alash-backend:current backend
docker run --rm --network host --env-file /etc/m1917/backend.codex.env \
  alash-backend:current python -c '
from backend.main import create_app
paths = create_app().openapi()["paths"]
required = {
    "/api/v1/boss/notes",
    "/api/v1/boss/objects/{object_id}/files",
    "/api/v1/objects/{object_id}/work-progress",
    "/api/v1/admin/maps/config",
}
missing = required - paths.keys()
assert not missing, f"Missing API routes: {missing}"
print("Required API routes are present")
'

# The helper verifies an encrypted database dump and records financial hashes.
# Use a separate environment copy; never replace the service's working settings.
service_pid=$(docker inspect --format '{{.State.Pid}}' m1917-backend-runtime)
"$host_python" deploy/prepare_existing.py prepare --pid "$service_pid" \
  --directory "$backup_directory" --env-output "$backup_directory/backend.env"

service_stopped=true
systemctl stop m1917-backend
docker run --rm --network host --env-file /etc/m1917/backend.codex.env \
  alash-backend:current alembic upgrade head
"$host_python" deploy/prepare_existing.py verify \
  --directory "$backup_directory" --env-output "$backup_directory/backend.env"
systemctl start m1917-backend
curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 2 \
  http://127.0.0.1:8000/health/ready
echo
echo "Backend updated. Backup: $backup_directory; previous image: $rollback_image"
