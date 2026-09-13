# Execution, Testing & Verification

## Sandbox

For the MVP, use Docker.

```text
Clone repository
      ↓
Create isolated workspace
      ↓
Build
      ↓
Run tests
      ↓
Start application
      ↓
Run load test
      ↓
Collect evidence
      ↓
Destroy environment
```

Minimum controls:

```text
CPU limits
memory limits
PID limits
disk limits
timeouts
non-root user
restricted network
ephemeral filesystem
```

Future hardening:

```text
Docker
   ↓
gVisor
   ↓
Firecracker microVMs
```

## Verification Principle

> Never trust an AI-generated patch because the AI says it works.

Verification can include:

```text
build
unit tests
integration tests
static analysis
security analysis
coverage
load tests
performance regression checks
generated regression tests
```

## Differential Verification

Compare:

```text
BASELINE
vs
PR
vs
AI FIX
```

Example:

```text
                BASE       PR       FIX

unit tests      818        818      821
coverage        81%        81%      84%
p95             182ms      420ms    191ms
error rate      0.1%       4.2%     0.2%
CPU             42%        67%      45%
complexity      41         41       37
```

Every comparison must pin the commit, dependency lockfiles, tool versions, environment image, resource limits, dataset, workload, and configuration. Store raw samples and logs in addition to aggregates so a result can be reproduced and audited.

### Experimental rigor

A single before/after run is not sufficient for a performance claim. Performance verification should use:

```text
warm-up
baseline × N
PR × N
fix × N
```

Run candidates under equivalent isolation and resource limits. Gates should combine absolute SLO thresholds, relative regression limits, sample count, variance, and confidence. Mark an experiment inconclusive when noise is too high instead of forcing pass/fail.

## Load Testing

Use k6 for:

```text
performance testing
load testing
stress testing
regression testing
```

Metrics:

```text
p50
p95
p99
throughput
error rate
CPU
memory
database latency
```

### MVP

Use fixed scenarios.

### Later

Use graph-guided adaptive tests:

```text
PR changes symbols
        ↓
Repository graph
        ↓
Affected endpoints/services
        ↓
Select relevant scenario
        ↓
Run k6
```

Workloads should represent user behavior rather than only virtual-user counts. Scenario weights, arrival rates, ramp, burst, soak, and breakpoint profiles can come from checked-in tests, OpenAPI descriptions, recorded traffic, or production traces. The graph selects relevant existing scenarios first; an LLM may fill bounded gaps but must not invent an unconstrained workload and execute it without validation.

Validation intensity should scale with blast radius, performance sensitivity, criticality, historical regressions, and available compute budget.

## Independent Validation Channels

An agent can overfit the exact failing test. A remediation therefore needs multiple channels where appropriate:

```text
original failure-inducing test
existing test suite
new regression test
static and security analysis
load or performance test
fuzzing or fault injection for applicable changes
```

Always classify failures differentially: baseline-only, candidate-only, shared pre-existing failure, or fix-only. Never attribute pre-existing debt to the current pull request.

## Observability

Use OpenTelemetry for:

```text
metrics
traces
logs
runtime metadata
CI pipeline spans
```

Long-term correlation:

```text
slow trace
    ↓
service
    ↓
endpoint
    ↓
function
    ↓
repository graph node
```

Continuous profiling is a later complement to traces for CPU, allocation, and lock hotspots. Runtime signals must carry repository, commit, build, service, and environment identity so a span or profile can be mapped back to the correct graph node.
