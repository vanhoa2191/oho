# OHO architecture / design boundaries

```text
                   Cloudflare Access / TLS Caddy
                                 |
                            OHO Dashboard
                            React / Nginx
                                 |
                           FastAPI control
                                 |
                             PostgreSQL
                                 |
                        outbound HTTPS polling
                     /        |        |        \
                macOS 1    macOS 2   Linux 1   Linux 2
                  |           |         |         |
          dedicated worktree + approved CLI adapter
```

## Task lifecycle

A run contains a directed acyclic graph of task keys; Pydantic validates dependencies and cycles. New tasks are either pending or awaiting approval. The control plane takes PostgreSQL row locks when claiming tasks to avoid duplicate assignment. Nodes have renewable 90-second leases and idempotent worker identity. Leases use random unguessable tokens, and their ownership is revalidated for every start, renewal and completion call.

The worker advertises operating system, tags and available models. Task placement matches tags and exact advertised model names. No inbound ports are required on worker machines.

## Privilege boundary

UI operator approval is necessary for elevated/agent/Git write tasks but is NOT permission to execute unsafe shell commands. Workers use explicit command allowlists and refuse remote Git modifications, deploy operations and privileged actions even if the control plane has approved the task. Expansion requires a separately reviewed capability and sandboxing design.

## Reliability

If a task lease expires it may be retried, subject to max_attempts. A run cannot be considered exactly-once: an interrupted worker might have already performed external side effects. Never place non-idempotent operations directly into retryable task definitions. In this MVP, the worker does not execute external side-effectful commands.

## Data/security assumptions

Do not register untrusted workers. Do not reuse Cloud Harness secrets, model API keys or VPS SSH keys as enrollment credentials. Ensure TLS for every remote worker. Isolate workers with dedicated OS users or VMs and restrict filesystem mounts. Event logs can include task metadata, so avoid sending confidential information in task prompts.

## Later milestones

CLI adapters for Claude, Gemini, OpenCode, Orca IDE; policy engine for signed approvals; durable model registry and token budgets; streamed logs; Alembic migrations; workflow templates; role-based UI; CI-native PR preparation; telemetry and metrics.
