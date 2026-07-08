# Conflict Resolution Policies

Conflicts must be returned as structured data, not applied silently.

Minimum policy set:

- duplicate-title conflict
- assertion mismatch
- priority mismatch
- precondition mismatch
- step-overlap requiring review

Until real samples arrive, the runtime should preserve conflicting candidates and let `R1` gate review them.
