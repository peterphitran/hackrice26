# Security & Policy

Lou executes untrusted repository code and therefore needs a strict security boundary.

## Sandbox Isolation

Never expose the execution environment to:

```text
GitHub App private keys
production credentials
other repositories
cloud admin credentials
host Docker socket
```

Use:

```text
ephemeral filesystem
CPU limits
memory limits
PID limits
timeouts
non-root users
restricted network
```

Move from Docker to gVisor or Firecracker if Lou becomes a multi-tenant product.

## GitHub Security

Use a GitHub App instead of personal access tokens.

Responsibilities:

```text
receive webhooks
verify webhook signatures
read repositories
read PRs
create checks
create remediation branches
open pull requests
```

Follow least privilege.

Suggested starting permissions:

```text
Metadata        read
Contents        read
Checks          write
Pull requests   write
Contents        write only in the trusted PR-publishing service
```

Verify `X-Hub-Signature-256` before accepting a webhook. Installation tokens must be short-lived and scoped to the selected repository. Do not execute untrusted pull-request code in a workflow context that also exposes privileged secrets.

## Agent Security

Repository content must be treated as untrusted input.

This includes comments, documentation, tests, build output, issue text, dependency metadata, and source-code instructions that attempt prompt injection.

The agent should not receive privileged infrastructure credentials.

Preferred pattern:

```text
Agent generates patch
      ↓
Trusted verifier validates
      ↓
Trusted control plane creates branch / PR
```

Agent capabilities should be deny-by-default:

```text
READ          selected repository worktree and evidence only
WRITE         temporary worktree only
NETWORK       denied except explicit allowlisted package access
CREDENTIALS   none
GIT           create a local diff; never push
TOOLS         allowlisted commands with time and resource budgets
```

Dependency installation is a network and code-execution boundary. Prefer locked dependencies, an internal or allowlisted proxy, checksum verification, and separate caches that cannot be poisoned across tenants.

## Open Policy Agent

OPA should eventually enforce organizational rules separately from model reasoning.

Example:

```text
Payment Service

Allowed:
✓ Analyze
✓ Recommend
✓ Generate patch
✓ Open PR

Not allowed:
✗ Auto-merge
✗ Auto-deploy
```

Lou predicts risk.

OPA determines whether policy permits the action.

Policy inputs, decision ID, policy revision, selected autonomy, and final privileged action should be retained in an audit log. Emergency cancellation and repository-level disable controls must remain available outside the agent workflow.
