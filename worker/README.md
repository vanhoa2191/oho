# OHO worker

Supported: macOS and Linux Python 3.12+, outbound HTTPS.

## Enrollment
Create a local checked-out Git worktree or repository for each project, then:
```bash
cd worker
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# Edit config.json with your server URL and local project roots.
python agent.py enroll --config config.json --enrollment-token 'YOUR_ENROLLMENT_SECRET'
python agent.py run --config config.json
```

Do not check `config.json` into Git if it reveals local paths or internal hostnames.

The node credential is saved to `~/.config/oho/node.json` (chmod 600).
The controller only needs outbound HTTPS; do not open inbound worker ports.

## Cloudflare Access (when the OHO hostname is protected)
Create a Cloudflare Access **Service Token** and add a `Service Auth` policy to the OHO Access application that permits that token. Keep the existing email-based `Allow` policy for human dashboard access; do not add a broad `Bypass` policy.

Supply the service token to the worker process through its environment, not through Git or task prompts:

```sh
export CF_ACCESS_CLIENT_ID='your-service-token-client-id'
export CF_ACCESS_CLIENT_SECRET='your-service-token-client-secret'
python agent.py enroll --config config.json --enrollment-token "$OHO_ENROLLMENT_TOKEN"
python agent.py run --config config.json
```

For a long-running systemd/launchd worker, store the values in a local environment file or secret store with restrictive permissions (Linux: mode `0600` and owned by the worker account). Do not paste these values into chat or commit them. The worker adds `CF-Access-Client-Id` and `CF-Access-Client-Secret` to API calls only when both variables are set. If only one is set, it stops with a configuration error.

## Execution boundaries
- Only `pytest`, `echo`, and read-only `git` commands are currently supported.
- Any agent execution requires approval on the controller. The Codex CLI adapter is the only implementation currently wired.
- A coding agent may modify workspace files, but the worker **never runs shell**, git push, git merge, deploys or privileged commands itself. Open a PR through a separate reviewed pipeline.
- Avoid sharing your everyday working directory with the worker; use a dedicated worktree.
- A worker isn't a hardened sandbox; run it under a low-privilege account with no production secrets.
- Risky operations need capabilities and a robust policy engine before they can be enabled.
