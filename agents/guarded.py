"""
Run a model call so that a student never sees a number the tools didn't give.

Both agents go through `run()`. The model call returns its reply plus
everything its tools returned during the conversation; the reply is used only
if at least one tool ran and every number in it appears in those tool
outputs. Otherwise, and on any error or timeout, the caller's template is
used. The template is computed from the same facts, so it is always correct.

On top of that, `claim_errors` checks what the numbers are attached to: a
reply's "N minutes later", "N%" and "N other students" must each match a
statement a tool made, since our tools phrase every fact in a fixed way. That
catches a real number given the wrong meaning ("three others" in a group of
three), which the number check alone lets through. Negations about the cab
itself ("they don't join your ride") are refused outright where a group exists.
"""

from __future__ import annotations

import concurrent.futures
import os
import re

MODEL_ID = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b-instruct")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

CLOCK_TIME = re.compile(r"\b\d{1,2}(?::\d{2}|\s?[ap]\.?m\b)", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def model():
    """The local model both agents use. Imported lazily, so the templated
    path works where the Strands SDK isn't installed."""
    from strands.models.ollama import OllamaModel

    return OllamaModel(host=OLLAMA_HOST, model_id=MODEL_ID, temperature=0.1, max_tokens=250)


def allowed_numbers(facts) -> set[float]:
    """Every number a reply may mention: those in the facts (dicts, lists and
    strings all searched), plus the small counts that come with talking about
    a cab ("one of the others", "all three")."""
    found: set[float] = {0, 1, 2, 3, 100}
    stack = [facts]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
        elif isinstance(x, str):
            found.update(float(n) for n in NUMBER.findall(CLOCK_TIME.sub(" ", x)))
        elif isinstance(x, bool) or x is None:
            continue
        elif isinstance(x, (int, float)):
            found.add(float(x))
    return found


def unsupported_numbers(text: str, facts) -> list[str]:
    """Numbers in `text` the facts don't back. Empty means the text is safe to show.

    A clock time ("5:10", "5pm") is always unsupported: everything the agents
    see is relative minutes, so a clock time can only have been made up.
    """
    ok = allowed_numbers(facts)
    rest = CLOCK_TIME.sub(" ", text)
    return CLOCK_TIME.findall(text) + [n for n in NUMBER.findall(rest) if float(n) not in ok]


WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
         "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
SHIFT = re.compile(r"(\d+) (?:more )?minutes? (earlier|later|before|after|sooner)", re.IGNORECASE)
PERCENT = re.compile(r"(\d+)\s?(?:%|per ?cent)", re.IGNORECASE)
OTHERS = re.compile(r"(\d+) other(?:s| students?| people| passengers?| riders?)?\b"
                    r"|\bwith (\d+) (?:people|students|passengers)\b(?! in total)", re.IGNORECASE)
TOTAL = re.compile(r"(\d+) (?:people|students|of you)(?: in total| altogether)?\b(?! (?:are|who) waiting)", re.IGNORECASE)
NEGATED_CAB = re.compile(r"\b(?:not|n't|never)\b[^.]{0,25}\b(?:join|joining|share|sharing|ride|riding|in your cab|with you)\b",
                         re.IGNORECASE)
DIRECTION = {"before": "earlier", "sooner": "earlier", "after": "later"}


def _digits(text: str) -> str:
    """"two other students, forty-five minutes" -> "2 other students, 45 minutes"."""
    for word in sorted(WORDS, key=len, reverse=True):
        text = re.sub(rf"\b{word}\b", str(WORDS[word]), text, flags=re.IGNORECASE)
    return text


def _claims(text: str) -> dict[str, set]:
    t = _digits(text)
    return {
        "shift": {(int(n), DIRECTION.get(d.lower(), d.lower())) for n, d in SHIFT.findall(t)},
        "percent": {int(n) for n in PERCENT.findall(t)},
        "others": {int(a or b) for a, b in OTHERS.findall(t)},
        "total": {int(n) for n in TOTAL.findall(t)},
    }


def claim_errors(text: str, outputs, group_exists: bool) -> list[str]:
    """Claims in `text` that no tool output makes. Empty means every claim is backed."""
    said = _claims(text)
    backed = _claims(" ".join(o for o in outputs if isinstance(o, str)))
    errors = [f"{kind} {sorted(said[kind] - backed[kind])}" for kind in said if said[kind] - backed[kind]]
    if group_exists and NEGATED_CAB.search(text):
        errors.append(f"negates the cab: {NEGATED_CAB.search(text).group(0)!r}")
    return errors


def run(ask, fallback: str, timeout: float, group_exists: bool = False) -> dict:
    """`ask()` returns (reply, tool_outputs). Returns {"text", "source", "rejected", "reply"?}."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        reply, outputs = pool.submit(ask).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return {"text": fallback, "source": "template", "rejected": f"timed out after {timeout:g}s"}
    except Exception as exc:  # model down, SDK missing, anything: the template always works
        return {"text": fallback, "source": "template", "rejected": f"{type(exc).__name__}: {exc}"}
    finally:
        pool.shutdown(wait=False)

    reply = (reply or "").strip()
    if not outputs:
        return {"text": fallback, "source": "template", "rejected": "tool not called", "reply": reply}
    if not reply:
        return {"text": fallback, "source": "template", "rejected": "empty reply"}
    bad = unsupported_numbers(reply, outputs)
    if bad:
        return {"text": fallback, "source": "template", "rejected": f"unsupported numbers {bad}", "reply": reply}
    wrong = claim_errors(reply, outputs, group_exists)
    if wrong:
        return {"text": fallback, "source": "template", "rejected": f"wrong claims {wrong}", "reply": reply}
    return {"text": reply, "source": "agent", "rejected": None}
