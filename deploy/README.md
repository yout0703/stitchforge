# JP2C4G production deployment

Public URL: https://stichforge.0703.pro (the hostname intentionally follows the requested spelling).

The image contains the frozen Python dependencies, Chinese chart fonts and the prebuilt web
application. One API process supervises one image worker. It listens only on
`127.0.0.1:18765` using Docker host networking; Caddy terminates HTTPS and proxies requests.
Existing Caddy loopback sites must remain unchanged.

The subdomain has a proxied Cloudflare A record and a host-specific Page Rule enabling
strict SSL for `https://stichforge.0703.pro/*`; other hosts keep their existing settings.
UFW permits TCP 80/443 on the public interface only from Cloudflare's IPv4 ranges.
The Origin CA certificate covers only this hostname and expires on 2041-10-23.
Its private key is generated on the VPS and retained at
`/etc/caddy/stitchforge-tls/origin.key` (`root:caddy`, `0640`); the public certificate is
`origin.pem` beside it. Keep Cloudflare proxying enabled when using this certificate.
Review Cloudflare's published IP ranges when maintaining the firewall and Caddy matcher.

## Persistent files

- Releases: `/opt/stitchforge/releases/<release>`; active symlink: `/opt/stitchforge/current`.
- Environment/secrets: `/opt/stitchforge/shared/production.env`, permission `0600`.
- SQLite, artifacts and work directory: `/opt/stitchforge/shared/data`, owned by UID/GID 10001.
- Backups: `/opt/stitchforge/backups`, readable by root only.
- Caddy site: `/etc/caddy/stitchforge.site`, imported by `/etc/caddy/Caddyfile`.

The first deployment starts a new production database; local user data is not copied.
Payment providers and credit packs remain disabled. New accounts therefore have zero
generation credits; free OXS/XSD import, editing and downloads remain available.
AI settings are transferred over SSH from the project's existing environment, never
included in the image or release archive. Secrets are not written into source control.

## Build and start

Run the frontend checks/build with pnpm locally, then transfer an allowlisted release
containing `pyproject.toml`, `uv.lock`, `README.md`, `packages`, `schemas`, `catalogs`,
`deploy`, `.dockerignore`, and `apps/web/dist`. Exclude caches and all local user data.
On the server, from that release directory:

```sh
docker compose --env-file deploy/release.env -p stitchforge -f deploy/compose.yaml build
docker compose --env-file deploy/release.env -p stitchforge -f deploy/compose.yaml up -d
curl --fail http://127.0.0.1:18765/api/v1/ready
```

Keep `STITCHFORGE_RELEASE=<release>` in `deploy/release.env` and explicitly pass it so
subsequent Compose commands use the correct image. Update `current` after readiness passes.
Validate the complete Caddy configuration before reloading it.

## Operations and rollback

```sh
cd /opt/stitchforge/current
docker compose --env-file deploy/release.env -p stitchforge -f deploy/compose.yaml ps
docker compose --env-file deploy/release.env -p stitchforge -f deploy/compose.yaml logs --tail=100 app
sudo systemctl start stitchforge-backup.service
sudo systemctl status stitchforge-backup.timer
```

Rollback by running `docker compose ... up -d` from the previous release and repointing
`current`. Persistent data is shared: inspect database schema compatibility before rolling
back across future migrations. Retain previous images and releases until rollback is no
longer needed; do not prune unrelated Docker images or volumes.

To restore, stop the app, move the existing data directory aside for recovery, create a new
data directory with the correct ownership, restore `metadata.sqlite3` and extract
`artifacts.tar.gz` there, then start the compatible application release. Test restoration
against a separate temporary directory before replacing production data.

Daily backups are local snapshots, not disaster recovery. Copy them off-server and monitor
disk usage; this initial deployment does not automatically delete backup history.

## Initial release verification (2026-10-28, Asia/Shanghai)

- Release: `20261027T155017Z` (UTC identifier).
- Running image: `sha256:702ce7763f64fbe1683b834ef9125d6afa5070bf2cbd1d1fca867423e5300c06`.
- Backend: 394 tests passed; frontend: 48 tests plus lint/typecheck/build passed.
- Account browser regression passed: mobile registration, pinch zoom, account isolation,
  generation charging and download/save-conflict recovery.
- The actual Linux image passed isolated API/session, CSRF, OXS import, PNG/OXS/XSD
  downloads, persistent queue and exactly-once generation-credit checks using local AI.
- A 600x600 Chinese PNG export completed in 11.66 seconds; that export process reached
  336.9 MiB peak RSS. This is one export measurement, not a concurrent-load capacity claim.
- Restored an isolated backup containing two valid projects and four hash-verified artifacts
  through the real application storage classes. Production starts with a separate empty DB.
- Public HTML, assets, API readiness, robots/sitemap, redirects and private-route protection
  passed; browser checks passed at the desktop viewport and 390x844 without horizontal overflow.
- Existing DeepSeek credentials returned HTTP 200 for model discovery and the configured
  model is available. No paid image-analysis request was made during deployment checks.
- The app is healthy with `unless-stopped`; the existing code-server and collector remain up.
- Daily backups run around 04:15-04:25 Asia/Shanghai. The first production snapshot is
  `/opt/stitchforge/backups/snapshot-BS9ai4oG`; an initial copy is also saved locally under
  `output/deployment/first-backup`. Automated off-server replication is not configured.
- Screenshots are under `output/deployment/`; the isolated preflight container was removed.
