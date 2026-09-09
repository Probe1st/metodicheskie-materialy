from tools.course_manifest import SESSIONS


def test_manifest_contains_the_course_load():
    assert len(SESSIONS) == 49
    assert [s.number for s in SESSIONS] == list(range(1, 50))
    assert sum(s.kind == "theory" for s in SESSIONS) == 24
    assert sum(s.kind == "practice" for s in SESSIONS) == 25
