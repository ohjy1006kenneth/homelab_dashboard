# Production storage layout and migration runbook

The dashboard is deployed from `/opt/lab-dashboard`. Its mutable state and
managed Compose definitions are outside the Git checkout:

| Contract | Path |
| --- | --- |
| source and runtime venv | `/opt/lab-dashboard` |
| SQLite, cache, agent logs, compose backups | `/var/lib/lab-dashboard` |
| dashboard-managed Compose definitions | `/srv/docker/stacks/<app>/compose.yaml` |
| internal app data | `/srv/appdata/<app>` |
| external NAS filesystem | `/srv/storage` (must be a mount with the provisioned UUID) |
| external NAS data | `/srv/storage/nas` |
| University Syncthing folder | `/srv/storage/nas/University` |
| Projects (disabled; retained in Syncthing config) | no production destination |
| Immich library | `/srv/storage/nas/Photos` |
| Syncthing config | `/srv/appdata/big-bear-syncthing` (preserve identity/config) |

The application reads these paths from `DASHBOARD_*` environment variables;
the production systemd templates provide the approved values. Secrets remain
in protected, untracked environment files consumed by Compose; this repository
contains no secret values.

## Safe migration

### Reproducible test setup

From a clean checkout, install `uv` in a temporary bootstrap environment, then
create the project environment and install production plus test dependencies:

```sh
python3 -m venv /tmp/dashboard-uv-bootstrap
/tmp/dashboard-uv-bootstrap/bin/pip install uv
/tmp/dashboard-uv-bootstrap/bin/uv venv .venv
/tmp/dashboard-uv-bootstrap/bin/uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_storage_layout.py tests/test_storage_contract_revision.py -q
```

The migration tests use temporary fixture paths only; they do not mount storage
or invoke live services.

1. Provision the protected `DASHBOARD_STORAGE_UUID` environment contract (for
   example in `/etc/lab-dashboard/storage.env`, mode `0640`, readable by the
   service account) with the UUID obtained from `blkid`. Do not guess a UUID.
   Mount the ext4 filesystem at `/srv/storage` and record its UUID. Do not run
   the migration before the mount is active.
2. Review the plan without changing the host:

   ```sh
   sudo DASHBOARD_STORAGE_UUID=<NAS_DATA_UUID> \
     /opt/lab-dashboard/.venv/bin/python \
     /opt/lab-dashboard/scripts/migrate_storage_layout.py --dry-run
   ```

3. Stop affected dashboard-managed services for the final rsync and create a
   timestamped backup. The script preserves ownership, timestamps, ACLs and
   extended attributes (`rsync -aHAX --numeric-ids`), never deletes a source,
   and refuses to continue when `/srv/storage` is not a mountpoint or its UUID
   does not match:

   ```sh
   sudo DASHBOARD_STORAGE_UUID=<NAS_DATA_UUID> \
     /opt/lab-dashboard/.venv/bin/python \
     /opt/lab-dashboard/scripts/migrate_storage_layout.py \
     --service-unit lab-dashboard.service
   ```

   The script only creates `/srv/storage/nas`; bulk NAS data is not moved by
   this dashboard migration. Copy Photos and other NAS data separately with a
   reviewed rsync plan. Storage-dependent Compose projects must be started only
   after validating the mount (`mountpoint -q /srv/storage` and matching
   `findmnt -n -o UUID --target /srv/storage`).

4. Syncthing is migrated with a config backup and stable folder-path matching:
   remap the existing University folder to `/srv/storage/nas/University` and
   disable (do not delete) the Projects folder. The operation refuses duplicate
   or unknown mappings, preserves identity/devices/keys, and is repeatable.
   Never sync the legacy `/DATA/nas/Projects` mapping; the retained Projects
   folder remains disabled and has no `/srv/storage` destination.

4. Verify the sibling backup directory `/var/lib/lab-dashboard-migration-backups/<UTC timestamp>/`, inspect Compose bind paths, and run the focused/full tests before enabling the production service.

Rollback is non-destructive: stop the service, restore the desired destination
from the timestamped backup with rsync, point the service variables back to the
previous paths, and start it. Do not delete the old source until an independent
backup and restoration test have succeeded.

`migrate_from_casaos.py` remains a legacy CasaOS import compatibility tool and
is not the production storage migration entrypoint.
