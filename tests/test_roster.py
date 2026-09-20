import roster


def test_the_shipped_roster_covers_the_demo_pool():
    names = roster.reload()
    assert len(names) > 500
    # the seeded demo pool and the ids the test scripts use
    for sid in ("imt2022101", "imt2022140", "imt2022301", "imt2022420", "imt2023001", "imt2024001"):
        assert names.get(sid), sid


def test_a_name_is_looked_up_however_the_id_is_cased():
    assert roster.name_for("imt2022101") == roster.name_for("IMT2022101")


def test_someone_not_on_the_roster_simply_has_no_name():
    assert roster.name_for("imt2099001") is None
    assert roster.name_for("") is None
    assert roster.name_for(None) is None


def test_a_missing_roster_is_not_an_error(tmp_path, monkeypatch):
    # a release must never fail because a CSV moved
    monkeypatch.setattr(roster, "ROSTER_FILE", tmp_path / "nothing.csv")
    assert roster.reload() == {}
    assert roster.name_for("imt2022101") is None


def test_blank_rows_and_stray_spacing_are_ignored(tmp_path, monkeypatch):
    csv = tmp_path / "roster.csv"
    csv.write_text("student_id,name\n IMT2022001 , Ananya Rao \nimt2022002,\n,Nobody\n")
    monkeypatch.setattr(roster, "ROSTER_FILE", csv)
    assert roster.reload() == {"imt2022001": "Ananya Rao"}


def test_the_roster_is_read_once(tmp_path, monkeypatch):
    csv = tmp_path / "roster.csv"
    csv.write_text("student_id,name\nimt2022001,First Name\n")
    monkeypatch.setattr(roster, "ROSTER_FILE", csv)
    assert roster.reload()["imt2022001"] == "First Name"
    csv.write_text("student_id,name\nimt2022001,Changed Name\n")
    assert roster.name_for("imt2022001") == "First Name"  # cached
    assert roster.reload()["imt2022001"] == "Changed Name"
