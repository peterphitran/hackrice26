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
