from stalled_news.models import ProjectInput
from stalled_news.query_pack import build_query_pack


def test_build_query_pack_without_rera():
    p = ProjectInput(project_name="Foo Heights", city="Mumbai", rera_id=None)
    qs = build_query_pack(p)
    assert len(qs) >= 6
    assert any("Foo Heights" in q for q in qs)
    assert not any("None" in q for q in qs)


def test_build_query_pack_with_rera():
    p = ProjectInput(project_name="Foo Heights", city="Mumbai", rera_id="P12345")
    qs = build_query_pack(p)
    assert any("P12345" in q for q in qs)
