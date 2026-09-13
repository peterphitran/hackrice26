package lou

import rego.v1

# Example organization policy for POST /v1/data/lou/decision.
# The Python safety gates still apply after this response is received.
default decision := {
    "revision": "1",
    "max_autonomy": 0,
    "deny_reasons": ["policy_revision_mismatch"],
    "denied": true,
}

decision := {
    "revision": "1",
    "max_autonomy": min([3, input.evidence_level]),
    "deny_reasons": [],
    "denied": false,
} if {
    input.requested_revision == "1"
}
