from cedar_authz import is_permitted

STUDENT = {"id": "imt2022001", "email": "imt2022001@iiitb.ac.in"}
OUTSIDER = {"id": "bob", "email": "bob@gmail.com"}
REQ = {"type": "Request", "id": "r1"}


def group(state="FORMED", members=("imt2022001", "imt2022002"), blocked=False):
    return {
        "type": "Group",
        "id": "g1",
        "members": list(members),
        "state": state,
        "contains_blocked_pair": blocked,
    }


# policy 1
def test_iiitb_email_may_create_request():
    assert is_permitted(STUDENT, "CreateRequest", REQ)


def test_other_domain_may_not_create_request():
    assert not is_permitted(OUTSIDER, "CreateRequest", REQ)


def test_lookalike_domain_may_not_create_request():
    p = {"id": "x", "email": "x@iiitb.ac.in.evil.com"}
    assert not is_permitted(p, "CreateRequest", REQ)


# policy 2
def test_member_may_respond():
    assert is_permitted(STUDENT, "RespondToProposal", group())


def test_non_member_may_not_respond():
    stranger = {"id": "imt2022099", "email": "imt2022099@iiitb.ac.in"}
    assert not is_permitted(stranger, "RespondToProposal", group())


# policy 3
def test_members_see_each_other_as_soon_as_a_group_is_proposed():
    assert is_permitted(STUDENT, "ViewContactDetails", group(state="FORMED"))


def test_members_still_see_each_other_once_confirmed():
    assert is_permitted(STUDENT, "ViewContactDetails", group(state="CONFIRMED"))


def test_a_non_member_never_sees_who_is_in_a_group():
    stranger = {"id": "imt2022099", "email": "imt2022099@iiitb.ac.in"}
    assert not is_permitted(stranger, "ViewContactDetails", group(state="FORMED"))
    assert not is_permitted(stranger, "ViewContactDetails", group(state="CONFIRMED"))


# policy 4
ORCH = {"type": "Service", "id": "orchestrator"}


def test_clean_group_may_form():
    assert is_permitted(ORCH, "FormGroup", group(blocked=False))


def test_group_with_blocked_pair_may_not_form():
    assert not is_permitted(ORCH, "FormGroup", group(blocked=True))


# fail closed
def test_unknown_action_is_denied():
    assert not is_permitted(STUDENT, "DeleteEverything", REQ)


def test_evaluation_error_is_denied():
    # missing the 'members' attribute makes policy 2 error; must deny, not skip
    assert not is_permitted(STUDENT, "RespondToProposal", {"type": "Group", "id": "g"})
