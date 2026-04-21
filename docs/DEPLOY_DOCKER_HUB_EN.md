# Deploying Prebuilt Docker Images

This workflow is for production setups where the server must not build images locally. The flow is:

1. Build and push images to Docker Hub from your local machine.
2. Install Docker on a clean Linux node.
3. Copy `docker-compose.yml` and `.env` to the node.
4. Run `docker compose pull && docker compose up -d` on the server.

## Which images are published

- `awg-jump`: backend + frontend + runtime

## 1. Publish images to Docker Hub

Log in first:

```bash
docker login
```

Then run the publish script from the repository root:

```bash
./scripts/publish_dockerhub.sh <dockerhub-namespace> <tag> --latest
```

Example:

```bash
./scripts/publish_dockerhub.sh myteam 2026-04-08 --latest
```

The script pushes:

- `docker.io/myteam/awg-jump:2026-04-08`
- plus `latest` tags if `--latest` is passed

If you also want to publish the upstream node image, add `--with-node`:

```bash
./scripts/publish_dockerhub.sh myteam 2026-04-08 --latest --with-node
```

## 2. Bootstrap the first clean node

The bootstrap script installs Docker on the remote machine, asks for the deploy directory, and places these files there:

- `docker-compose.yml`
- `.env`
- `.env.ru.example`
- `.env.en.example`

This bootstrap is intended for the main node running `awg-jump`.
It does not deploy upstream nodes.

Run on Linux/macOS:

```bash
./scripts/bootstrap_first_node.sh
```

Run on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_first_node.ps1
```

The script will ask for:

- node IP/hostname
- SSH user
- SSH port
- deploy directory
- Docker Hub namespace
  This is usually your Docker Hub username or Docker Hub organization name.
- image tag

After that, the node will contain a ready-to-start directory.

Windows requirements:

- OpenSSH Client installed (`ssh`, `scp`)
- PowerShell 5.1+ or PowerShell 7+

Windows note:

- If you bootstrap through Windows PowerShell with password-based SSH authentication, use a server password without special characters.
  In practice, Windows `ssh`/`scp` password prompts may behave inconsistently with special characters.
  SSH key authentication is the preferred option.

## 3. First start on the server

Connect to the server and edit at least:

- `ADMIN_PASSWORD`
- `SECRET_KEY`
- `AWG_JUMP_IMAGE`
- optionally `TLS_COMMON_NAME`
- optionally `SERVER_HOST`

Then start the stack:

```bash
cd /opt/awg-jump
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```

Check status:

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100
```

## 4. Update without rebuilding on the server

After publishing a new tag:

1. update image tags in `.env`
2. run:

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```
