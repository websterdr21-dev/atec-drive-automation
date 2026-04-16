"""Tests for utils/site_detection — FMAS membership check."""

import pytest
from utils.site_detection import is_fmas_site, reload


@pytest.fixture(autouse=True)
def _reload_default():
    """Ensure each test starts with the real site list."""
    reload()
    yield


class TestIsFmasSite:
    def test_exact_match(self):
        assert is_fmas_site("The Topaz") is True

    def test_lowercase_match(self):
        assert is_fmas_site("the topaz") is True

    def test_uppercase_match(self):
        assert is_fmas_site("THE TOPAZ") is True

    def test_whitespace_stripped(self):
        assert is_fmas_site("  The Topaz  ") is True

    def test_not_in_list(self):
        assert is_fmas_site("Sunset Heights") is False

    def test_empty_string(self):
        assert is_fmas_site("") is False

    def test_whitespace_only(self):
        assert is_fmas_site("   ") is False

    def test_partial_does_not_match(self):
        assert is_fmas_site("Topaz") is False

    def test_all_known_sites_match(self):
        known = [
            "The Topaz", "Emerald Place", "Garnet Place", "Sapphire Mews",
            "Amstel Terrace", "Square on 10th", "First on Forest",
            "Alphine Mews", "Southwark Mews", "Riverside Mews",
            "Meadow Ridge Mews", "The Residence", "The Eden",
            "Greencourt", "Stepney Green", "The Diplomat",
            "Helderberg Manor Estate",
        ]
        for site in known:
            assert is_fmas_site(site) is True, f"{site} should be FMAS"


class TestReload:
    def test_reload_custom_file(self, tmp_path):
        custom = tmp_path / "custom_sites.txt"
        custom.write_text("Alpha Site\nBeta Site\n")
        count = reload(str(custom))
        assert count == 2
        assert is_fmas_site("Alpha Site") is True
        assert is_fmas_site("Beta Site") is True
        assert is_fmas_site("The Topaz") is False

    def test_reload_missing_file_empties_set(self, tmp_path):
        count = reload(str(tmp_path / "no_such_file.txt"))
        assert count == 0
        assert is_fmas_site("The Topaz") is False

    def test_reload_blank_lines_skipped(self, tmp_path):
        custom = tmp_path / "sparse.txt"
        custom.write_text("\n\nOnly Site\n\n\n")
        count = reload(str(custom))
        assert count == 1
        assert is_fmas_site("Only Site") is True
