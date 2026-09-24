# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, using GitHub Security Advisories:

<https://github.com/GroundedCore/nexusdesk/security/advisories/new>

Do not open a public issue for a suspected vulnerability.

A useful report includes:

- the affected commit or version, and the deployment mode (quickstart or production);
- reproduction steps, with the smallest configuration that shows the problem;
- the expected impact, and any preconditions or required privileges.

Please **never** include real API keys, access tokens, customer data, or a working
exploit against a live deployment. Sanitize logs before attaching them.

## Supported versions

The project is under active development and has not cut a release. Only the `main`
branch receives security fixes.

## Operator responsibilities

NexusDesk is software you run and expose. The following are yours to configure and
protect:

- **Credential master key.** It encrypts model API keys and business tool credentials.
  The path comes from `AGENT_MODEL_CREDENTIAL_KEY_FILE` (default
  `data/credentials/master.key`, `/data/credentials/master.key` in containers). Back it
  up separately from the database, and give every backend process the same key. When
  encrypted records already exist and the key is missing, saving and invoking
  credentials fails **by design** — the platform refuses to generate a replacement key,
  because the existing records could not be decrypted with it.
- **Role tokens.** `AGENT_API_TOKEN`, `AGENT_OPERATOR_API_TOKEN`, and
  `AGENT_VIEWER_API_TOKEN` are required outside development, must be distinct, and
  provide coarse role separation only. They are not per-user identity, and audit records
  attribute actions to the token role rather than to a person.
- **Host allowlists.** `AGENT_TOOL_ALLOWED_HOSTS` and
  `AGENT_MODEL_GATEWAY_ALLOWED_HOSTS` decide which endpoints tools and models may reach.
  Keep them as narrow as the integration allows.
- **Network exposure.** The production stack binds the web entry point to `127.0.0.1`
  by default and does not publish the API port. Terminate HTTPS in front of it, and set
  `NEXUSDESK_BIND` only behind your own access control. The quickstart stack injects a
  local administrator identity and must not be exposed to other people.
- **Default administrator.** Fresh deployments initialize `admin` / `nexusdesk` and
  require a password change on first login. Change it before the service is reachable
  by anyone else.

## Security-relevant design limits

These are known boundaries of the current implementation, not undiscovered bugs. Review
them against your threat model before relying on the platform in production.

- **HTTP tools can perform writes, and writes are not gated by an approval step.**
  A tool registered with `POST` is executed directly when the model calls it, and is
  recorded with `effect=write`. Only the built-in `propose_ticket` produces a
  `requires_confirmation` action for a human to approve. The system prompt instructs the
  model not to perform external writes, but that is a prompt-level instruction rather
  than an enforced control. **Register only endpoints whose invocation by the model you
  accept**, and enforce idempotency and authorization on the business service itself.
- **Tool host allowlisting matches hostnames only.** There is no secondary validation
  for DNS rebinding or private address ranges. Prefer fixed endpoints on hosts you
  control.
- **Human handoff isolation is enforced at the entry point.** Automatic replies stop
  while a conversation is in `human` or `waiting` mode because new runs are not
  submitted. A run that is already in flight is stopped through a cancellation flag; the
  runtime engine does not itself re-check conversation mode, and an external service may
  already have received a request before cancellation took effect.
- **Run state is not checkpointed.** A worker that loses its lease marks the run failed.
  Runs are not resumed from an intermediate LangGraph node.
- **The SSE stream carries phase events, not tokens.** Tool arguments, raw tool results,
  and credentials are not exposed in it. Final replies and conversation history are
  customer data and need a retention policy and access control of their own.
- **Evaluation is process-local.** Evaluation runs execute in the API process and are
  rate-limited in-process, not through a distributed queue.

## Reporting a non-security bug

Use the regular issue tracker for functional bugs and documentation problems.
