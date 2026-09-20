# Exodus

End-of-semester cab clustering for IIITB. Students say when they want to leave and how much earlier or later they can manage. At scheduled releases a solver groups the whole open pool for a route into cabs of up to three, instead of matching people one at a time in a chat thread.

Everything runs on one laptop with no AWS account: SAM CLI hosts the Lambda handlers, LocalStack provides DynamoDB, EventBridge and S3, Cedar answers every authorisation question, and a static page is the UI.

> **Status:** the backend is complete and tested end to end — the real grouping solver, the decline lifecycle, explanations, advice, and all four routes. What's left is the look of the UI.

## How it works

1. A student signs in with an `@iiitb.ac.in` roll-number address (`imt2022001@…`; `hello@iiitb.ac.in` is nobody), picks a route from fixed dropdowns, and gives a preferred departure time plus how much *earlier* and *later* they'll accept.
2. A timer fires a **release**. For each route the solver partitions the whole pending pool into groups. Each group becomes a `FORMED` proposal with a departure time and an accept deadline.
3. Every member accepts or declines. When all accept, the group is `CONFIRMED` and members see each other's contact.
4. **A decline in a group of three doesn't dissolve it.** The decliner goes back to the pool and is told how long until the next release. The other two keep the same departure time and are asked again: *stay together as a pair*, or *split* (and both wait for the next release). Their earlier accepts don't carry over, since they agreed to a trio. If both stay, the pair is confirmed and its time is locked; if either splits, the group dissolves, and the splitter's reason is recorded like any decline. A trio that can't continue as a pair (one of the two only takes full cabs, or they're blocked) dissolves at once, and a decline in a group of two always does.
5. **A confirmed pair can gain a third rider.** If it leaves late enough, a later release offers the seat to the pending student who fits it best. The departure time never changes, so a student fits only if that exact time is inside their own window; they must accept, and can decline like any proposal. The pair are told who joined. Two thresholds, both in `config.py`: the pair stays open only if it leaves at least `backfill_open_lead_minutes` (6 h) after the next release, and a release may offer the seat only at least `backfill_close_lead_minutes` (3 h) before departure. Whoever left the cab is never offered its seat back.
6. Silence past the deadline counts as a `TIMEOUT` decline: one silent member of a trio carries on as a pair exactly as a decline would, and an unanswered seat offer simply expires. A student who keeps declining without changing anything sits out one release, so the loop always terminates.

Before submitting, a student can ask `POST /advise` how their window will fare: it runs the real solver on the current pool plus their hypothetical request and answers concretely — *with ±15 you would travel alone; ±45 puts you in a cab leaving 35 minutes earlier, 78% of your flexibility*. Every proposal also carries an explanation of the trade it made. Both come from the solver's arithmetic; there is no language model anywhere in this codebase.

A proposal names the other members — name and roll number, from `roster.csv` — because you can't sensibly decline "not with this person" without knowing who they are. Cedar decides that only members of a group may see who else is in it. Names are display only: nothing matches, groups or authorises on them, and a student missing from the roster is shown by roll number alone.

## Prerequisites

| Need | Notes |
| --- | --- |
| Docker with Compose v2 | your user must be able to run `docker` without sudo |
| Python 3.12 | the handlers run on the 3.12 Lambda runtime |
| AWS SAM CLI | `pipx install aws-sam-cli` (make sure `~/.local/bin` is on your `PATH`) |
| Google Chrome | only for `scripts/ui_e2e.py` |

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d                    # LocalStack: DynamoDB, EventBridge, S3
.venv/bin/python create_tables.py       # tables + audit bucket; re-run any time to reset

scripts/start_api.sh                    # terminal 1: API on http://127.0.0.1:3000
                                        #   EXODUS_TRAVEL_DATE=YYYY-MM-DD scripts/start_api.sh  to say which day the trip is
python3 -m http.server 8080 --directory web   # terminal 2: UI on http://127.0.0.1:8080
scripts/scheduler.sh                    # terminal 3 (optional): release every 120s, sweep every 30s
```

LocalStack keeps state in memory, so **re-run `create_tables.py` whenever the container is recreated.**

## Try it

```bash
scripts/seed.py                         # three students on COLLEGE_AIRPORT
scripts/release.sh --force              # fire a release now instead of waiting
```

Ask for advice before a release, while the pool is still open:

```bash
curl -s -X POST http://127.0.0.1:3000/advise \
  -H 'Content-Type: application/json' -H 'X-Student-Email: imt2022999@iiitb.ac.in' \
  -d '{"route": "COLLEGE_AIRPORT", "p": 1025, "b": 15, "a": 15}'
```

Open http://127.0.0.1:8080 and sign in as `imt2022101@iiitb.ac.in`, then `imt2022102@…` and `imt2022103@…` in separate browser profiles or private windows (each keeps its own sign-in). You'll see a proposed group at 5:15 pm, with a line explaining the trade it made. Decline one of them with "not with one of these people" and watch the others get told their group dissolved. Fire another release and the block is respected.

`scripts/seed.py --count 40 --seed 3` seeds the demo pool: 40 students clustered around the evening flights, with a few outliers, generated by `poolgen.py`. That pool becomes **16 cabs instead of 40**. `scripts/sweep.py --compare 0.5 2.5` shows the same 40 students grouped at two values of `upsilon` — 20 cabs with 10 people alone, against 16 cabs with 4 — which is one knob trading convenience against how many people travel alone. `scripts/show_pool.py` prints each request exactly as the solver receives it.

## Tests

```bash
.venv/bin/python -m pytest tests        # 256 unit tests; no containers needed
scripts/gate2.py                        # the decline loop end to end over HTTP
scripts/e2e.py                          # every reason, the budget, the sweep, trio-to-pair, a third rider, 4 routes, 120 students
.venv/bin/python scripts/ui_e2e.py      # the board in real Chrome, several sessions
```

The three scripts need the stack running and **empty tables** (`create_tables.py` first), one at a time.

`e2e.py` is the broad one: each decline reason, the decline budget and the sit-out, the deadline sweep, a trio that loses a member and carries on as a pair, a confirmed pair gaining a third rider (offered, declined, expired, accepted), advice on an open pool, all four routes in one release, and a 120-student pool whose live result is compared field by field against the solver run offline on the same pool.

The third-rider scenario needs to know which day the trip is on, because a departure is only "far enough away" relative to a date: start the API with `EXODUS_TRAVEL_DATE=<tomorrow> scripts/start_api.sh` and export the same value when running `e2e.py`, or that scenario prints `SKIP`. See [Limitations](#limitations).

## Layout

| Path | What it is |
| --- | --- |
| `solver.py` | the grouping algorithm: normalised asymmetric penalties, an exact grid scan for the departure time, and a DP over contiguous runs under four orderings |
| `lifecycle.py` | what a decline changes, and when a student sits a release out |
| `explainer.py` | why a student got the group they got, from the solver's own numbers |
| `advisor.py` | what a window is likely to get you, by running the solver on the pool plus a hypothetical request |
| `schedule.py` | the wall clock: when the next release is, and whether a cab leaves late enough that a third rider is still worth finding |
| `poolgen.py` | seeded synthetic pools that look like an end-of-semester evening |
| `roster.py`, `roster.csv` | roll number to name, the mapping a college would own; override the path with `ROSTER_FILE` |
| `handlers/` | Lambda handlers: `submit_request`, `get_my_request`, `get_board`, `advise`, `respond`, `release_orchestrator`, `lifecycle_sweep`; `_decline.py` is the one path every decline takes, `_groups.py` what happens to a group when someone leaves or it is confirmed |
| `policies.cedar`, `cedar_authz.py` | the four policies, and `is_permitted()`, the only code that talks to Cedar (fails closed) |
| `repo.py`, `repo_dynamo.py` | the data layer: a JSON-file `FakeRepo` and the real `DynamoRepo`, one interface |
| `config.py` | every tuning constant and enum; nothing else hardcodes one |
| `template.yaml`, `docker-compose.yml`, `create_tables.py` | infrastructure |
| `web/index.html` | the whole UI: one static file, no build step |
| `scripts/` | `start_api.sh`, `scheduler.sh`, `seed.py`, `release.sh`, `show_pool.py`, `sweep.py`, `gate2.py`, `e2e.py`, `ui_e2e.py` |

## API

Every time in every body is minutes since midnight. Identity is the `X-Student-Email` header.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/requests` | `{route, p, b, a, min_group_size?}` | `201` the stored request |
| `GET` | `/requests/me` | — | `{me, request, proposal}`; the proposal carries its explanation and names the other members |
| `POST` | `/advise` | `{route, p, b, a}` | `{message, simulated_outcome, pool, better_window}` |
| `POST` | `/groups/{id}/respond` | `{action: "accept" or "decline", reason?, payload?}` | `200 {state, ...}`. A decline returns `REDUCED` (the group carries on as a pair), `DISSOLVED`, or `OFFER_DECLINED` (a third seat), plus `next_release_at`. Also how a student answers an offered third seat. |
| `GET` | `/board` | — | countdown, per-route pool size and last release |
| `POST` | `/internal/release` | — | fires a release (`?force=1` ignores the minimum gap) |
| `POST` | `/internal/sweep` | — | resolves groups past their accept deadline |

## The seam

The grouping logic and the platform meet only at [`contracts.md`](contracts.md), and the seam survives even though one person now owns both sides — it is what keeps the algorithm testable without any of the infrastructure:

- Every time crossing it is an **integer of minutes since midnight** (5:20 pm is `1040`). Conversion to clock time happens once, in `web/index.html`.
- `solve(requests, config)` is pure: plain dicts in, plain dicts out, no I/O, stdlib only. So is `on_decline(request, reason, payload)`, which returns a **state delta** the API persists key by key without interpreting the reason.
- `explainer.py` and `advisor.py` take the same shapes, so an explanation can be checked against the solver in a unit test.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `docker ps` errors about `~/.docker/desktop/docker.sock` | `docker context use default` |
| `docker-credential-desktop: executable file not found` | remove the `"credsStore"` line from `~/.docker/config.json` |
| `GET /board` returns 500 | LocalStack isn't up or the tables are missing: `docker compose up -d`, then `create_tables.py` |
| Lambda errors with `No module named 'handlers'` | Docker can't see the project directory (snap Docker can't read outside your home directory, for example). Clone under your home directory |
| Edited a handler and nothing changed | the API keeps containers warm; restart `scripts/start_api.sh` |
| `sam: command not found` | `pipx install aws-sam-cli`, and put `~/.local/bin` on your `PATH` |
| A test script fails oddly, or students get regrouped mid-test | `scripts/scheduler.sh` is running and firing releases underneath it. Stop it while running `gate2.py`, `e2e.py` or `ui_e2e.py` |
| Port 8080 is already taken | serve the page anywhere else (`python3 -m http.server 8090 --directory web`) and point the browser test at it: `EXODUS_WEB=http://127.0.0.1:8090/ scripts/ui_e2e.py` |
| Countdown looks wrong | `RELEASE_INTERVAL_SECONDS` in `template.yaml` and the interval passed to `scheduler.sh` must match (both default to 120) |

## Limitations

- **Sign-in is not real.** Identity is the unverified `X-Student-Email` header: the address must be an IIITB roll number (`imt|mt|ms|phd` + 7 digits) at `iiitb.ac.in`, but nothing proves it is yours, so anyone who can reach the API can act as any student. `start_api.sh` binds `127.0.0.1` only for this reason. Don't expose it.
- **Releases come from a timer loop**, not EventBridge. LocalStack can fire schedules, but it can't invoke a function hosted by `sam local start-api`. The handlers accept both event shapes, so nothing changes if they're deployed to AWS later.
- **The board's "last release" figure** is the most recent release on the route, so it can drop after a later, smaller one.
- **The system has no travel date, so one is configured.** A departure is minutes since midnight and a release is a wall-clock time; deciding whether a pair leaves "6 hours after the next release" needs the day they're on. `schedule.py` measures against the day of the release unless `EXODUS_TRAVEL_DATE` (or `config["travel_date"]`) says otherwise, in the college's timezone (`tz_offset_minutes`, IST by default). Run after the pool's departure times without setting it and no pair has room for a third rider, which is correct but looks like the feature doing nothing.
- **Filling a seat is a heuristic.** Seats are offered before the pool is solved, to the student with the smallest penalty at the pair's locked time, so a student who takes a seat is unavailable to the solver even if a better group existed for them. The pair have no veto over a third rider beyond their blocklists.
- **Soft time anchors are not implemented.** A time-based decline records `(departure_time, group_size)` in `declined_anchors`, and the solver ignores it. Termination is guaranteed by the decline budget instead: two declines that change nothing and the student sits out a release.
- **Advice reflects the pool as it is now.** After a release most requests are `GROUPED`, so `POST /advise` is only informative while a pool is still open, and it says so in its own answer.
- **Contiguity is a heuristic.** The departure time is exactly optimal on the 5-minute grid, and the partition is exactly optimal over runs contiguous in each ordering the solver tries; with widely varying window shapes that can still miss the true optimum, measured at about 2% of random small pools (`solver.py` documents the numbers).
- **No language model.** Explanations and advice are computed from the solver. A Strands-plus-Ollama version of both was built and measured, and cut: it added 15-25s per answer, needed a guard against invented numbers, and contributed no facts.
