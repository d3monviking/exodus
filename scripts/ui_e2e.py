#!/usr/bin/env python3
"""
Browser end-to-end for the board: real Chrome, one isolated session per student,
each polling the real API like a real user.

  A. three students grouped; one declines "not with this person" by name; the
     other two are told and asked to stay as a pair or split; one splits, so the
     cab dissolves; the next release pairs two of them; both accept and see the
     confirmed cab and each other's contact.
  B. widen-my-window decline updates the request; a plans-changed decline
     withdraws it, and "check my chances" answers from the real solver.

Needs the full stack (LocalStack + sam local on :3000 + `python3 -m http.server
8080 --directory web`), empty tables, and dev deps (playwright; uses system Chrome).
Set EXODUS_WEB / EXODUS_API if those ports are taken, e.g.
EXODUS_WEB=http://127.0.0.1:8090/ when something else already has 8080.
Also asserts the picker's payload values are the REASONS enum strings.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright  # noqa: E402

from config import REASONS  # noqa: E402
from roster import name_for  # noqa: E402
from seed import make_requests, post  # noqa: E402

API = os.environ.get("EXODUS_API", "http://127.0.0.1:3000")
WEB = os.environ.get("EXODUS_WEB", "http://127.0.0.1:8080/")
SHOTS = os.environ.get("SHOT_DIR")
failures = 0


def check(label, ok, detail=""):
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail and not ok else ""))


def api(method, path, email, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Student-Email": email})
    return json.load(urllib.request.urlopen(req, timeout=60))


def release():
    req = urllib.request.Request(f"{API}/internal/release?force=1", method="POST")
    return json.load(urllib.request.urlopen(req, timeout=60))


def seed(route, first_id):
    for email, body in make_requests(3, route, 0, first_id):
        post(API, email, body)


def wait_for_notice(page, text, timeout=15000):
    """Wait for the banner to say something, and on failure say what it said instead."""
    try:
        page.wait_for_function(f"document.getElementById('notice').textContent.includes({text!r})", timeout=timeout)
        return True
    except Exception:
        print(f"       notice was: {page.eval_on_selector('#notice', 'e => e.textContent')!r}; "
              f"status={page.inner_text('#status')[-40:]!r}; "
              f"proposal_shown={page.locator('#proposal .proposal').count()}; "
              f"hidden={page.is_hidden('#status-card')}")
        return False


def shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name), full_page=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        errors, bad_http = [], []

        def session(n):
            ctx = browser.new_context(viewport={"width": 420, "height": 900})
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("response", lambda r: bad_http.append((r.status, r.url)) if r.status >= 400 and "3000" in r.url else None)
            page.goto(WEB)
            page.fill("#email", f"imt2022{n}@iiitb.ac.in")
            page.click("#signin-btn")
            return page

        def notice(page):
            # textContent, not inner_text: the banner hides itself after 20s and
            # inner_text reports "" for anything hidden
            return page.eval_on_selector("#notice", "e => e.textContent")

        print("A. decline -> dissolve -> re-release -> confirm")
        seed("COLLEGE_AIRPORT", 101)
        release()
        p1, p2, p3 = session(101), session(102), session(103)
        for pg in (p1, p2, p3):
            pg.wait_for_selector("#proposal .proposal h2", timeout=15000)
        check("all three see a proposed group", all("Proposed group" in pg.inner_text("#proposal") for pg in (p1, p2, p3)))
        check("the cab lists who you're travelling with, by name",
              all(name_for(f"imt20221{n:02d}") in p1.inner_text("#proposal") for n in (2, 3)), p1.inner_text("#proposal"))
        check("the header greets you by name", name_for("imt2022101") in p1.inner_text("#who-line"),
              p1.inner_text("#who-line"))
        # the time itself is the solver's business, so match its shape, not a value
        text = p1.inner_text("#proposal")
        check("proposal shows departure, size, explanation",
              bool(re.search(r"\d{1,2}:\d{2} [ap]m", text)) and "Why" in text and "fare" in text, text[:200])
        check("a response deadline is counting down", "Respond within" in p1.inner_text("#deadline"))
        p1.wait_for_function(r"/^\d+:\d\d$|due any moment/.test(document.getElementById('countdown').textContent.trim())", timeout=5000)
        check("board shows a live countdown to the next release",
              bool(re.fullmatch(r"\d+:\d\d|due any moment", p1.inner_text("#countdown").strip())), p1.inner_text("#countdown"))
        check("board shows the last release on this route", "1 cab of three" in p1.inner_text("#last") and "2 cabs saved" in p1.inner_text("#last"),
              p1.inner_text("#last"))

        p1.click("#decline")
        p1.wait_for_selector(".picker .opt")
        offered = p1.eval_on_selector_all(".picker .opt", "els => els.map(e => e.dataset.reason)")
        check("picker sends exactly the REASONS enum strings", offered == [r for r in REASONS if r != "TIMEOUT"], str(offered))
        shot(p1, "picker.png")
        p1.click('[data-reason="PERSON"]')
        p1.wait_for_selector("[data-student]")
        offered_people = p1.eval_on_selector_all("[data-student]", "e => e.map(x => x.dataset.student)")
        check("the picker names the other two members", offered_people == ["imt2022102", "imt2022103"], str(offered_people))
        labels = p1.eval_on_selector_all("[data-student]", "e => e.map(x => x.textContent)")
        check("and shows their roster names", all(name_for(s) in l for s, l in zip(offered_people, labels)), str(labels))
        p1.click('[data-student="imt2022103"]')
        p1.wait_for_function("document.getElementById('notice').textContent.includes('declined') && "
                             "document.getElementById('status').textContent.includes('PENDING')", timeout=15000)
        check("decliner sees a confirmation and is PENDING again",
              "PENDING" in p1.inner_text("#status") and not p1.is_hidden("#form-card"))
        check("the decliner is told when the next release is, and that they wait for it",
              "the next release is" in notice(p1) and "wait" in notice(p1), notice(p1))
        told = [wait_for_notice(pg, "now a pair") for pg in (p2, p3)]
        check("the other two are told who backed out and asked to stay or split",
              all(told) and all(name_for("imt2022101") in notice(pg) and "backed out" in notice(pg)
                                and "split" in notice(pg) for pg in (p2, p3)), notice(p2))
        actions = [pg.eval_on_selector("#actions", "e => e.textContent") for pg in (p2, p3)]
        check("...with both choices on screen, and the cost of splitting spelled out",
              all("Stay as a pair" in a and "Split" in a and "wait for the next release" in a for a in actions),
              str(actions))

        # 103 would rather split, so the pair dissolves and everyone goes back to the pool
        p3.click("#decline"); p3.wait_for_selector(".picker .opt")
        p3.click('[data-reason="TIME"]'); p3.wait_for_selector("#keep"); p3.click("#keep")
        check("the one who wanted to stay is told who backed out, and when the next release is",
              wait_for_notice(p2, "called off") and name_for("imt2022103") in notice(p2)
              and "next release is" in notice(p2), notice(p2))

        release()
        for pg in (p1, p2):
            pg.wait_for_function("document.querySelector('#proposal .proposal') !== null && document.getElementById('proposal').textContent.includes('Group size2')", timeout=15000)
        p3.wait_for_timeout(3500)
        check("101 and 102 are paired; 103 (blocked) gets nothing",
              "Group size2" in p1.evaluate("document.getElementById('proposal').textContent")
              and p3.locator("#proposal .proposal").count() == 0 and "PENDING" in p3.inner_text("#status"))

        p1.click("#accept")
        p1.wait_for_function("document.getElementById('actions').textContent.includes('You accepted')", timeout=15000)
        check("after accepting, waits on the others with a count", "1 of 2" in p1.inner_text("#actions"))
        p2.click("#accept")
        for pg in (p1, p2):
            pg.wait_for_function("document.querySelector('#proposal h2').textContent.includes('confirmed') && "
                                 "document.getElementById('status').textContent.includes('CONFIRMED')", timeout=15000)
        check("both see the confirmed cab", all("CONFIRMED" in pg.inner_text("#status") for pg in (p1, p2)))
        check("each sees the other's contact", "imt2022102@iiitb.ac.in" in p1.inner_text("#proposal")
              and "imt2022101@iiitb.ac.in" in p2.inner_text("#proposal"))
        check("the form is gone once confirmed", p1.is_hidden("#form-card"))
        shot(p1, "confirmed.png")

        print("B. widen window, then withdraw")
        seed("AIRPORT_COLLEGE", 201)
        release()
        q = session(201)
        q.wait_for_selector("#proposal .proposal h2", timeout=15000)
        q.click("#decline"); q.wait_for_selector(".picker .opt")
        q.click('[data-reason="TIME"]'); q.wait_for_selector("#w-early")
        check("widen panel can only widen (min is the current window)",
              q.eval_on_selector("#w-early", "s => s.options[0].value") == "30")
        shot(q, "widen.png")
        q.click("#widen")
        q.wait_for_function("document.getElementById('status').textContent.includes('45 min earlier / 45 min later')", timeout=15000)
        check("window widened to 45/45 and no budget wasted", "45 min earlier / 45 min later" in q.inner_text("#status"))

        # the other two were asked to carry on as a pair; one splits, so everyone can be regrouped
        other = "imt2022202@iiitb.ac.in"
        gid = api("GET", "/requests/me", other)["proposal"]["group_id"]
        api("POST", f"/groups/{gid}/respond", other, {"action": "decline", "reason": "TIME"})
        release()
        q.wait_for_selector("#proposal .proposal h2", timeout=15000)
        q.click("#decline"); q.wait_for_selector(".picker .opt")
        q.click('[data-reason="PLANS_CHANGED"]'); q.click("#confirm-plans")
        q.wait_for_function("document.getElementById('notice').textContent.includes('removed') && "
                            "document.getElementById('status-card').classList.contains('hidden')", timeout=15000)
        check("plans-changed removes the request and offers the form again",
              q.is_hidden("#status-card") and not q.is_hidden("#form-card") and q.inner_text("#submit") == "Join the pool")

        # the form is back, so ask what a window would get you before submitting again
        q.click("#advise")
        q.wait_for_function("document.getElementById('advice').textContent.length > 0 && "
                            "!document.getElementById('advice').textContent.includes('Running the solver')", timeout=30000)
        advice = q.inner_text("#advice")
        check("check-my-chances answers from the current pool", "students are waiting on this route" in advice, advice)
        check("...and says what this window would get you", "minutes earlier and" in advice, advice)
        shot(q, "advice.png")

        check("no JavaScript errors anywhere", not errors, str(errors))
        check("no failed API calls (4xx/5xx) during the whole run", not bad_http, str(bad_http))
        browser.close()

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
