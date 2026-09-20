# Exodus — Contracts

*The seam between the solver side (brain) and the platform side (B). Changed only by deliberate, simultaneous edit on both sides — never silently.*

## Time

Every time crossing the seam is an integer: minutes since midnight, local, on the travel date. 5:20pm is `1040`. Never a `datetime`, never a string, never a timezone. The platform converts at the frontend edge on the way in and out — nowhere else does a conversion happen. Departure times are multiples of `grid_minutes` (5).

## Enums

See `config.py` — `ROUTES`, `STATUSES`, `STATES`, `REASONS`. These are the only valid values; nothing else is accepted at the boundary.

## The request

```json
{
  "student_id": "imt2022001",
  "route": "COLLEGE_AIRPORT",
  "p": 1040, "b": 30, "a": 45,
  "min_group_size": 2,
  "blocked_with": ["imt2022017"],
  "declined_anchors": [{"T": 1020, "size": 2}]
}
```

`b` and `a` are always at least 5, and `min_group_size` is 2 unless the student asked for a full cab (`POST /requests` accepts it, and a `TOO_FEW` decline sets it). `blocked_with` is denormalised onto the request by `repo.py` before the pool reaches the solver — the solver never queries anything.

## The solver — the whole seam

```python
def solve(requests: list[dict], config: dict) -> dict:
    """Pure. Deterministic. stdlib only. No I/O."""
```

Returns:

```python
{
  "groups": [
    {"members": [...], "departure_time": 1040, "penalties": {...}, "cost": 0.66}
  ],
  "ungrouped": ["imt2022099"],
  "stats": {
    "pool_size": 40, "groups_of_3": 11, "groups_of_2": 2,
    "ungrouped": 3, "cabs_saved": 24, "total_cost": 12.4
  }
}
```

Invariants the platform relies on: every input `student_id` appears exactly once across `groups[].members` and `ungrouped`; every `departure_time` is a multiple of `grid_minutes`; no group contains a blocked pair; same input + config -> same output.

## The repository — platform writes it, solver-side agents call it

```python
class Repo:
    def open_pool(self, route) -> list[dict]: ...
    def get_request(self, student_id) -> dict: ...
    def latest_release(self, route) -> dict: ...
    def group_for(self, student_id) -> dict | None: ...
    def config(self) -> dict: ...
```

Two implementations, same interface, selected by one env var: `DynamoRepo` (real, boto3 against LocalStack) and `FakeRepo` (JSON-file-backed). Both ship in the same commit so agent/algorithm work never blocks on LocalStack being up.

`DynamoRepo.open_pool()` must coerce every numeric field (`p`, `b`, `a`, `min_group_size`) to plain Python `int` — boto3 returns `Decimal`, and the solver assumes real ints.

## Lifecycle — solver side writes, platform calls

```python
def on_decline(request: dict, reason: str, payload: dict) -> dict:
    """Pure. Returns a state delta, does not touch storage."""

def should_sit_out(request: dict, config: dict) -> bool:
    """Pure. Implements the decline budget."""
```

`on_decline` returns a delta with zero or more of these keys, which the platform persists verbatim through a dispatcher — no branching on the reason string anywhere in platform code:

| Delta key | What the platform writes |
| --- | --- |
| `block_pair` | New `Blocks` item, `pair_key` = the two ids sorted and joined |
| `update_request` | Overwrite `b` / `a` on the request |
| `set_min_group_size` | Field on the request |
| `withdraw` | Delete the request |
| `increment_decline_count` | Counter on the request |
| `add_anchor` | Append to `declined_anchors` |

`declined_anchors` is recorded but nothing reads it yet: the soft time anchor of architecture 7.5 is not implemented, and the decline budget is what guarantees the release loop terminates.

### `on_decline` payload — agreed

The architecture doc never defined `payload`. The API passes exactly these keys, and drops anything else a client sends:

| Key | Set by | Meaning |
| --- | --- | --- |
| `named_student_id` | client, validated | The student being blocked. Must be another member of the group being declined: blocking is pair-scoped, so nobody can block a stranger they never travelled with. |
| `b`, `a` | client, validated (int, 5-240) | A proposed new window. `on_decline` decides whether it counts as a widening. |
| `departure_time`, `group_size` | **platform only** | The declined group's facts, always overwriting anything the client sent, so a client can't forge an anchor. |

`reason` is one of `REASONS`. `TIMEOUT` is only ever sent by the sweep, never accepted from a client. An unchanged window must not count as a state change, or a student could dodge the decline budget and re-loop the release forever.

## Who never touches what

- The solver side (`solver.py`, `lifecycle.py`, `poolgen.py`, `explainer.py`, `advisor.py`) never imports an AWS SDK, never does HTTP, never touches DynamoDB, and never interprets a decline reason as a storage write. `advisor.advise()` is the one exception to "no I/O", and it only reads, through `Repo`.
- The platform side never reasons about grouping or declines — it calls `solve()`, `on_decline()`, `explain()` and `advise()`, and persists exactly what comes back.

## Explanations and advice

Both are computed from the solver's own numbers, so they are exact and instant. No language model is involved anywhere in this codebase.

```python
explain(request, group, others, config) -> str   # explainer.py, called by the release
advise(route, p, b, a, repo, student_id) -> dict # advisor.py, called by POST /advise
```

Neither ever names another student, and neither ever produces a clock time: minutes-since-midnight becomes a clock time once, in `web/index.html`.
