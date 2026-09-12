# Security & Policy

Ledger executes untrusted repository code and therefore needs a strict security boundary.

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

Move from Docker to gVisor or Firecracker if Ledger becomes a multi-tenant product.

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

## Agent Security

Repository content must be treated as untrusted input.

The agent should not receive privileged infrastructure credentials.

Preferred pattern:

```text
Agent generates patch
      ↓
Trusted verifier validates
      ↓
Trusted control plane creates branch / PR
```

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

Ledger predicts risk.

OPA determines whether policy permits the action.
