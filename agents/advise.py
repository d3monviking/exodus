"""
The `advise` agent: before a student submits, it runs the real solver on the
current pool plus their hypothetical request, and tells them the trade in
concrete terms. Architecture doc sections 5 and 7.6.

Two tools, as the architecture doc names them: `pool_density()` and
`simulate(minutes_earlier, minutes_later)`. The code decides which windows to
try, calls the tools itself, and decides whether widening helps; the model
only rewrites the results. Measured on qwen2.5:3b (agents/eval.py): left to
choose the what-ifs it skipped the student's own window and invented others;
left to compare two results it got the comparison wrong about half the time. Its reply goes through agents/guarded.py: every number in it must
appear in what the tools returned during that conversation, or the student
gets the templated advice from agents/whatif.py, computed from a fixed set of
what-ifs.

Reads the pool through the Repo interface only (EXODUS_REPO=fake|dynamo).

  python -m agents.advise COLLEGE_AIRPORT 17:05 15 15
  EXODUS_REPO=dynamo python -m agents.advise COLLEGE_AIRPORT 17:05 15 15 --student imt2022101
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from agents import guarded
from agents.whatif import WIDEN_TO, advise_facts, conclusion_line, advise_template, density, density_line, sim_line, simulate

TIMEOUT_SECONDS = float(os.environ.get("ADVISE_TIMEOUT_SECONDS", "120"))
MIN_FLEX, MAX_FLEX = 5, 240  # what the submission form accepts

# The examples' minutes aren't multiples of 5, and every real time is, so a
# reply that copies an example always fails the guard (a 3B model does copy them).
SYSTEM_PROMPT = """\
You help a student decide how flexible to be before they request a shared cab.
The tools have already been run; their results are above, followed by a
conclusion. Rewrite the results and the conclusion, and only those, as two or
three friendly sentences, speaking to the student as "you".

Keep every fact exactly: same numbers, same meaning, and keep straight which
result belongs to their own window and which to the wider one. The conclusion
is decided for you; state it, never argue with it. End by saying it's based on
who is waiting right now. Never give clock times, names or student ids. No
greeting, no question, no list.

Example 1. Results:
- With 12 minutes earlier and 22 minutes later allowed: you would travel alone.
- With 62 minutes earlier and 62 minutes later allowed: you would share a cab with 2 other students, leaving 41 minutes earlier than you want, which uses 66% of your flexibility.
Conclusion: Widening your window helps: the wider window gets you a better result than your own.
Answer: With your window of 12 minutes earlier and 22 minutes later you would
travel alone. Widening it to 62 minutes either way would put you in a cab with 2
other students, leaving 41 minutes earlier than you want, which uses 66% of your
flexibility. That's based on who is waiting right now.

Example 2. Results:
- With 27 minutes earlier and 27 minutes later allowed: you would share a cab with 2 other students, leaving 7 minutes later than you want, which uses 26% of your flexibility.
Conclusion: Your own window already gets you a full cab, so there is no need to widen it.
Answer: Your window of 27 minutes either way already gets you a full cab with 2
other students, leaving 7 minutes later than you want, which uses 26% of your
flexibility, so there's no need to widen it. That's based on who is waiting
right now."""


def _ask_model(pool, route, p, b, a, config, student_id) -> tuple[str, list[str]]:
    from strands import Agent, tool

    outputs: list[str] = []

    @tool
    def pool_density() -> str:
        """How many students are waiting on this route, and how many want to leave near this student's time."""
        outputs.append(density_line(density(pool, p, student_id)))
        return outputs[-1]

    @tool(name="simulate")  # the architecture doc's name; simulate() itself is the pure function
    def simulate_window(minutes_earlier: int, minutes_later: int) -> str:
        """Run the real grouping solver as if the student joined now with this window.

        Args:
            minutes_earlier: how many minutes earlier than their preferred time they would leave, 5 to 240
            minutes_later: how many minutes later than their preferred time they would leave, 5 to 240
        """
        if not (MIN_FLEX <= minutes_earlier <= MAX_FLEX and MIN_FLEX <= minutes_later <= MAX_FLEX):
            return f"Each side of the window must be between {MIN_FLEX} and {MAX_FLEX} minutes."
        outputs.append(sim_line(simulate(pool, route, p, minutes_earlier, minutes_later, config, student_id)))
        return outputs[-1]

    # The code picks the what-ifs (your window, and the wider one that helps
    # most) and runs them through the agent's own tools, recorded in the
    # conversation. The model only has to write up what they returned.
    facts = advise_facts(pool, route, p, b, a, config, student_id)
    wider = facts["better"] or simulate(pool, route, p, max(b, WIDEN_TO[-1]), max(a, WIDEN_TO[-1]), config, student_id)
    agent = Agent(model=guarded.model(), tools=[pool_density, simulate_window],
                  system_prompt=SYSTEM_PROMPT, callback_handler=None)
    agent.tool.pool_density()
    agent.tool.simulate(minutes_earlier=b, minutes_later=a)
    if facts["better"] or not (facts["current"]["outcome"] == "group" and facts["current"]["group_size"] == 3):
        agent.tool.simulate(minutes_earlier=wider["minutes_earlier_ok"], minutes_later=wider["minutes_later_ok"])
    conclusion = conclusion_line(facts)
    outputs.append(conclusion)  # the guard may accept what the conclusion says, like any tool output
    return str(agent(f"Conclusion: {conclusion}\nWrite the advice for this student now.")), outputs


def advise(route: str, p: int, b: int, a: int, repo, student_id: str | None = None,
           timeout: float = TIMEOUT_SECONDS, use_model: bool = True) -> dict:
    """{"text", "source", "rejected", "facts"} for a request that hasn't been submitted."""
    pool, config = repo.open_pool(route), repo.config()
    facts = advise_facts(pool, route, p, b, a, config, student_id)
    if not use_model:
        return {"text": advise_template(facts), "source": "template", "rejected": None, "facts": facts}
    out = guarded.run(lambda: _ask_model(pool, route, p, b, a, config, student_id), advise_template(facts), timeout)
    return {**out, "facts": facts}


def _minutes(text: str) -> int:
    """"17:05" or "1025" -> 1025. The command line is a human edge, like the frontend."""
    if ":" in text:
        h, m = text.split(":")
        return int(h) * 60 + int(m)
    return int(text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("route")
    ap.add_argument("time", help="preferred departure, HH:MM (24h) or minutes since midnight")
    ap.add_argument("earlier", type=int, help="minutes earlier you'd accept")
    ap.add_argument("later", type=int, help="minutes later you'd accept")
    ap.add_argument("--student", help="your id, if you're already in the pool (left out of the simulation)")
    ap.add_argument("--template", action="store_true", help="skip the model")
    args = ap.parse_args(argv)
    from repo import get_repo

    started = time.monotonic()
    out = advise(args.route, _minutes(args.time), args.earlier, args.later, get_repo(), args.student,
                 use_model=not args.template)
    print(f"[{out['source']}, {time.monotonic() - started:.1f}s]")
    print(out["text"])
    if out["rejected"]:
        print(f"\n(agent reply not used: {out['rejected']})")
        if out.get("reply"):
            print(f"  it said: {out['reply']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
