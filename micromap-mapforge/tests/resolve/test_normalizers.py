from micromap_mapforge.resolve.normalizers import get_normalizer
from micromap_mapforge.normalize import normalize_disease_name


def test_known_name_returns_callable():
    assert get_normalizer("normalize_disease_name") is normalize_disease_name
    assert get_normalizer("disease") is normalize_disease_name


def test_none_returns_none():
    assert get_normalizer(None) is None


def test_unknown_name_returns_none(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        assert get_normalizer("no_such_normalizer") is None
    assert "no_such_normalizer" in caplog.text
