# Versioned Contracts

`models.py` is the integration boundary for the initial Lou demo. Every model is
strict (`extra="forbid"`) and carries `schema_version: "1"`, so producers and
consumers fail visibly instead of silently accepting an incompatible payload.

The first contract set covers repository change/context, workload selection,
findings and evidence, verification results, agent output, patch metadata, and
the final Lou decision. It intentionally has no database or FastAPI dependency.

See the [field-level contract reference](REFERENCE.md) for required fields,
allowed values, ownership, and examples.

## Fixtures

The JSON files in `fixtures/` are the hand-off examples for parallel work.
There is one valid version-one fixture for each contract model.
Validate an incoming payload with the corresponding model before consuming it:

```python
from contracts import RepositoryContext

context = RepositoryContext.model_validate_json(payload)
```

When a field must change, add a new schema version and fixture; do not repurpose
an existing field with a new meaning.
