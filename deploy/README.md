# Lucera NCloud deployment

The application is a standard-library Python HTTP service. The canonical
SQLite database is copied into the image or uploaded to `/opt/lucera/data/db`
on a Compute Server. Runtime secrets are supplied through an environment file;
they are never copied into the image.

## Container

Run from the repository root:

```powershell
docker build -f deploy/Dockerfile -t lucera:latest .
docker run --rm --env-file .env -p 8000:8000 lucera:latest
```

## Ubuntu Compute Server

Copy the project to `/opt/lucera`, create `/opt/lucera/deploy/lucera.env`
from `lucera.env.example`, then run `deploy/bootstrap.sh` as root. The script
installs Python, Nginx, a locked-down `lucera` system user, and the systemd
unit. Nginx listens on port 80 and proxies to the local application on 8000.

The NCloud API credentials in `.env.ncloud` are control-plane credentials and
must not be used as application environment variables. The application API
keys belong in `CLIK_API_KEY` and `ROAD_ADDRESS_API_KEY`.
