# Changelog

[简体中文](CHANGELOG.md) · English

Versions map to `v`-prefixed Git tags and follow [Semantic Versioning](https://semver.org/). During `0.x`, releases follow feature batches: `x` is the batch and `y` is a fix.

## v0.1.0 — 2026-10-09

First runnable release. The core customer-service flow is complete and usable.

### Added

**Platform capabilities**

- Agent configuration and execution: configure and publish agents, then run reasoning and tool calls through an asynchronous LangGraph ReAct loop.
- Knowledge base and retrieval: ingest documents, configure chunking, preview content, build vector indexes, and test retrieval quality.
- Unified model access: manage connections, catalogs, and versioned profiles for Chat, Embedding, Rerank, OCR, and speech models.
- Business tool integration: connect business APIs and manage call credentials, parameters, and execution permissions.
- Human collaboration and tickets: conversation takeover plus ticket drafting and confirmation.
- Channels and application access: connect through channel endpoints and application APIs, including the embedded chat widget and enterprise identity login (OIDC, WeCom, DingTalk, Feishu).
- Observability and evaluation: review execution records, tool calls, and evaluation results.

13 domain modules and 29 database migrations. The console ships in Simplified Chinese, Traditional Chinese, English, and Hindi.

**Deployment topologies**

- `quickstart`: two containers (app plus PostgreSQL), no model key required, ships with a mock model.
- `production`: four long-running services (web, API, runtime worker, knowledge worker) plus a one-shot migration task. Middleware is reused from an existing PostgreSQL, Milvus, and MinIO rather than started again.

**Seeded data**

- Sample support setup: knowledge base, model profile (published), agent (published v1), and a `sample-support` conversation, ready to chat with out of the box.
- Nine industry case sets: 9 knowledge bases, 18 agent drafts, and 36 documents, each marked as fictional and prefixed with `[案例]`.
- Case agent prompts are organised in three layers: conduct rules, writing style, and per-industry voice. Behaviour constraints stay separate from phrasing, so the model does not recite its own guardrails back to the user.

**HTTPS for the quickstart demo**

- The quickstart bundle ships its own private CA and server certificate, generated when the container starts. Reaching the demo over a LAN IP or an internal hostname is therefore still a secure context, so secure-only APIs such as `crypto.randomUUID` and `navigator.clipboard` behave normally.
- The leaf certificate is re-issued automatically when the host list changes or it nears expiry, while the CA stays the same, so an already imported `ca.crt` never needs to be imported again.

### Known limitations

- The demo uses a mock model and **does not represent real answer quality**. It does not connect to real order systems and provides no real OCR, vector search, or GPU inference.
- The demo injects an admin-role token for requests that carry no identity. **Do not expose it to the public internet**; if you must widen the bind address, restrict the source with a firewall.
- The production topology does not terminate TLS itself; the host or an ingress proxy is expected to.
- Real parser, Milvus, and S3 integrations have not been validated end to end. See [module delivery notes](docs/modules-delivery.md).
- The project is under active development and is not production ready.

### Upgrade notes

This is the first release, so there is no upgrade path. Database migrations run automatically when the container starts and **cannot be rolled back** — reverting the image does not revert the schema, so migrations must stay forward compatible (new columns nullable or defaulted, dropped columns removed over two releases). See [database migrations](docs/database-migrations.md).
