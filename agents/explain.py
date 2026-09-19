"""
The `explain` agent: turns a student's proposal into a couple of sentences
they can weigh up before accepting. Architecture doc section 5.

One tool, `explain(student_id)`, returning the exact facts from facts.py as
plain sentences. The code calls the tool itself (recorded in the conversation
like any tool call), so the model only rewrites the facts; every reply then
goes through agents/guarded.py, and the templated sentence from the same facts
is used if it fails, errors or times out.

Measured with agents/eval.py on qwen2.5:3b, every reply read by hand:
  model calls the tool, JSON facts      4/8 correct
  code calls the tool, sentence facts   8/8 correct after the prompt and
                                        fact-wording fixes, median ~17s on CPU
That is too slow for the release path: keep the template there, and run the
agent on demand.

Reads through the Repo interface only (EXODUS_REPO=fake|dynamo), so it runs
against the JSON fake with nothing else up.

  python -m agents.explain imt2022101                  # against the fake repo
  EXODUS_REPO=dynamo python -m agents.explain imt2022101   # against LocalStack
"""

from __future__ import annotations

import os
import sys
import time

from agents import guarded
from agents.facts import explain_facts, fact_lines, template

TIMEOUT_SECONDS = float(os.environ.get("EXPLAIN_TIMEOUT_SECONDS", "60"))

# The examples' minutes aren't multiples of 5, and every real time is, so a
# reply that copies an example always fails the guard (a 3B model does copy them).
SYSTEM_PROMPT = """\
You explain a shared cab proposal to one student, speaking to them as "you".
The explain tool has already been called; its result is the list of facts
above. Rewrite those facts, and only those facts, as two or three friendly
sentences.

Keep every fact exactly: same numbers, same meaning. The other students in
the facts are the ones sharing this cab with you. Only say why their exact
time wasn't used if a fact says so. Do not add anything the facts don't say.
Never give clock times, names or student ids. No greeting, no question.

Example 1. Facts:
- You leave 17 minutes later than the time you asked for.
- That uses 43% of the flexibility you offered.
- You share the cab with 2 other students, 3 people in total, and split the fare 3 ways.
- One of them leaves 8 minutes earlier than they asked.
- One of them leaves exactly when they asked.
- Your exact time is outside another member's window.
Answer: Your cab leaves 17 minutes later than you asked, which uses 43% of the
flexibility you offered. You're sharing with 2 other students and splitting the
fare 3 ways, and one of them is leaving 8 minutes earlier than they wanted. Your
exact time couldn't work because it's outside another member's window.

Example 2. Facts:
- You leave exactly at the time you asked for.
- You share the cab with 1 other student, 2 people in total, and split the fare 2 ways.
- One of them leaves 33 minutes later than they asked.
Answer: Your cab leaves exactly when you asked. You're sharing with 1 other
student and splitting the fare 2 ways; they're the one moving, leaving 33
minutes later than they wanted."""


def load_facts(student_id: str, repo) -> dict | None:
    """The facts for this student's current proposal, or None if they have none."""
    request = repo.get_request(student_id)
    group = repo.group_for(student_id)
    if request is None or group is None or group.get("state") != "FORMED":
        return None
    others = [repo.get_request(m) for m in group["members"] if m != student_id]
    if any(o is None for o in others):
        return None
    return explain_facts(request, group, others, repo.config())


def _ask_model(student_id: str, facts: dict) -> tuple[str, list[str]]:
    """(reply, what the tool returned). The code calls the tool itself, recorded
    in the conversation like any tool call, so the model's only job is the
    wording: a 3B model plans tool calls badly but rewrites sentences well."""
    from strands import Agent, tool

    outputs: list[str] = []

    @tool
    def explain(student_id: str) -> str:
        """The exact facts of this student's cab proposal.

        Args:
            student_id: the student the proposal is for
        """
        outputs.append(fact_lines(facts))
        return outputs[-1]

    agent = Agent(model=guarded.model(), tools=[explain], system_prompt=SYSTEM_PROMPT, callback_handler=None)
    agent.tool.explain(student_id=student_id)
    return str(agent("Write the explanation for this student now.")), outputs


def explain_for(student_id: str, repo, timeout: float = TIMEOUT_SECONDS) -> dict:
    """{"text", "source", "facts", "rejected"}. source is "agent" or "template";
    rejected says why an agent reply was not used, when it wasn't."""
    facts = load_facts(student_id, repo)
    if facts is None:
        return {"text": None, "source": None, "facts": None, "rejected": "no open proposal"}
    out = guarded.run(lambda: _ask_model(student_id, facts), template(facts), timeout, group_exists=True)
    return {**out, "facts": facts}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    from repo import get_repo

    started = time.monotonic()
    out = explain_for(argv[0], get_repo())
    if out["facts"] is None:
        print(f"{argv[0]}: {out['rejected']}")
        return 1
    print(f"[{out['source']}, {time.monotonic() - started:.1f}s]")
    print(out["text"])
    if out["rejected"]:
        print(f"\n(agent reply not used: {out['rejected']})")
        if out.get("reply"):
            print(f"  it said: {out['reply']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
