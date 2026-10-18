# OHO — Orchestration Hub

Early-stage, security-first proof of concept for coordinating coding tasks across desktop nodes.

## Current scope
- FastAPI + SQLAlchemy control plane
- PostgreSQL via Docker Compose
- Project, DAG run, node enrollment, task approval and lease-based claim APIs
- Isolation by design: controller never merges or deploys automatically

**Status: prototype. NOT production ready.** Worker, dashboard, retry/recovery policies, audit trails, migrations, continuous integration, API hardening, and operational deployment remain to be completed. Do not expose to the public internet or enroll production machines yet.

## Local development
```sh
cp .env.example .env
# Generate unique strong secrets, set POSTGRES_PASSWORD, and update OHO_DATABASE_URL
docker compose up --build
```

The web service defined in Compose remains a placeholder pending dashboard implementation. Do not run production deployment from this branch until it is built and tested.

## Security
Enrollment and admin secrets must be distinct, random, and at least 24 characters. Keep API bound to private network and reverse proxy behind TLS and access controls. Do not give command execution to untrusted nodes.

The default GitHub branch is not changed; development stays in a feature branch pending review.
