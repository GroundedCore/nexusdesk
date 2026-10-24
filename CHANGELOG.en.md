# Changelog

[简体中文](CHANGELOG.md) · English

Versions map to `v`-prefixed Git tags and follow [Semantic Versioning](https://semver.org/). During `0.x`, releases follow feature batches: `x` is the batch and `y` is a fix.

## v0.2.0 — 2026-10-24

Second feature batch: streamed answers, the agent memory system (phases 1–6), native channel integrations, member and access management, an evaluation workbench, and a release-gated deployment pipeline.

### Added

**Streamed answers**

- Model deltas stream in real time to the console over LISTEN/NOTIFY, and to the public API and embedded chat widget as well.
- Run traces are organised as rounds rather than raw events and can be opened directly from an answer; time-to-first-character is recorded.

**Agent memory system (phases 1–6)**

- Rolling summaries: conversation summary storage plus a memory task queue, with summaries updated as the conversation progresses.
- Customer profiles: customer identity resolution and profile/preference memory; repeated memory-item decisions return 409.
- Event memory: a MemoryStore (pgvector / TSVECTOR implementations), event writing and routing, a built-in `memory_search` tool, and idle-cycle maintenance.
- Memory governance: PII masking, protection flags, delete/merge, rule scanning and confidence decay, plus a governance console UI.
- Switches and visualisation: memory feature toggles, injection explainability (injection logs), and optional demo datasets seeded at server startup.
- The memory worker is split into a standalone container topology; the console surfaces event memory and commitments.

**Channels and access**

- Native channel integrations with channel workers; the channel tab is named for what it carries and its translations are complete.

**Members and permissions (P0)**

- Member management and session management; audit records capture the real client IP (including behind proxies), with paginated tables.

**Evaluation workbench**

- Agent evaluation panel, case workbook import, and a cross-agent quality board, rendered with antd Table.

**Conversation workbench**

- An agent entry page with per-agent stats and a scoped workbench; the conversation list can be filtered by agent.

**Engineering and release**

- Branch governance: `integration` is the default branch, rulesets protect `main`/`integration`, and CODEOWNERS enforces review of sensitive paths.
- Release-gated deployment: CD only runs on a published GitHub Release (`v*` tag) or a manual dispatch with an explicit version, gated by the `production` Environment; quickstart images are pushed to GHCR and mirrored to Docker Hub.
- The quickstart defaults to the pgvector image; new AGENTS.md entry point and team-standard skills (commit standard, dev workflow, and more).
- New brand mark (orbit logo with gradient wordmark); the quickstart pre-fills the demo sign-in form.

### Changed

- **The quickstart no longer injects an admin token for identity-less requests**: unauthenticated API calls are rejected. The demo account password is still the public default, so put real authentication (basic auth or an IP allowlist) in front when binding beyond the loopback address.

### Known limitations

- The demo uses a mock model and **does not represent real answer quality**; it does not connect to real order systems and provides no real OCR or GPU inference.
- The production topology does not terminate TLS itself; the host or an ingress proxy is expected to.
- Real parser, Milvus, and S3 integrations have not been validated end to end. See [module delivery notes](docs/modules-delivery.md).
- The project is under active development and is not production ready.

### Upgrade notes

Database migrations run automatically when the container starts (41 migrations cumulative in this release) and **cannot be rolled back** — reverting the image does not revert the schema. Upgrading from v0.1.0 is a straight image pull and restart; on a self-hosted PostgreSQL, vector retrieval uses pgvector by default with no extra middleware. See [database migrations](docs/database-migrations.md).

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
