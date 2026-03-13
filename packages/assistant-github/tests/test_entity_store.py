"""Tests for assistant_github.entity_store — GitHubEntityStore base class.

Tests the core store operations: find, find_anywhere, active_keys,
move_to_synced, restore_to_active. These are all read-only or soft
reversible (filesystem moves), so standard unit tests per the
testing philosophy.
"""

import frontmatter

from assistant_github.entity_store import GitHubEntityStore


class ConcreteStore(GitHubEntityStore):
    """Minimal subclass for testing the base class."""
    _entity_type = "test"
    _url_path = "test"

    def save(self, entity: dict):
        org, repo, number = entity["org"], entity["repo"], entity["number"]
        return self._store.save(
            self._filename(org, repo, number),
            org=org, repo=repo, number=number,
            title=entity.get("title", ""),
        )


def _make_store(tmp_path) -> ConcreteStore:
    return ConcreteStore(path=tmp_path)


# ---------------------------------------------------------------------------
# _filename
# ---------------------------------------------------------------------------


class TestFilename:
    def test_format(self):
        assert GitHubEntityStore._filename("myorg", "myrepo", 42) == "myorg__myrepo__42.md"

    def test_hyphenated_names(self):
        assert GitHubEntityStore._filename("my-org", "my-repo", 1) == "my-org__my-repo__1.md"


# ---------------------------------------------------------------------------
# save + find
# ---------------------------------------------------------------------------


class TestSaveAndFind:
    def test_save_creates_file(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        assert path.exists()
        assert path.name == "o__r__1.md"

    def test_find_returns_path(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        assert store.find("o", "r", 1) is not None

    def test_find_returns_none_when_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.find("o", "r", 999) is None

    def test_frontmatter_fields(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({"org": "o", "repo": "r", "number": 1, "title": "Feature"})
        post = frontmatter.load(path)
        assert post.metadata["org"] == "o"
        assert post.metadata["repo"] == "r"
        assert post.metadata["number"] == 1
        assert post.metadata["title"] == "Feature"


# ---------------------------------------------------------------------------
# find_anywhere
# ---------------------------------------------------------------------------


class TestFindAnywhere:
    def test_finds_in_root(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        assert store.find_anywhere("o", "r", 1) is not None

    def test_finds_in_synced(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.move_to_synced("o", "r", 1)
        result = store.find_anywhere("o", "r", 1)
        assert result is not None
        assert "synced" in str(result)

    def test_returns_none_when_nowhere(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.find_anywhere("o", "r", 999) is None


# ---------------------------------------------------------------------------
# active_keys
# ---------------------------------------------------------------------------


class TestActiveKeys:
    def test_returns_active_entities(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T1"})
        store.save({"org": "o", "repo": "r", "number": 2, "title": "T2"})
        keys = store.active_keys()
        assert keys == {("o", "r", 1), ("o", "r", 2)}

    def test_excludes_synced(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.save({"org": "o", "repo": "r", "number": 2, "title": "T"})
        store.move_to_synced("o", "r", 1)
        keys = store.active_keys()
        assert keys == {("o", "r", 2)}

    def test_empty_dir(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.active_keys() == set()

    def test_nonexistent_dir(self, tmp_path):
        store = _make_store(tmp_path / "nonexistent")
        assert store.active_keys() == set()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_updates_fields(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "Old"})
        store.update("o", "r", 1, title="New", status="merged")
        post = frontmatter.load(tmp_path / "o__r__1.md")
        assert post.metadata["title"] == "New"
        assert post.metadata["status"] == "merged"

    def test_returns_none_for_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.update("o", "r", 999, title="X") is None


# ---------------------------------------------------------------------------
# move_to_synced
# ---------------------------------------------------------------------------


class TestMoveToSynced:
    def test_moves_file(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        result = store.move_to_synced("o", "r", 1)
        assert result is not None
        assert not (tmp_path / "o__r__1.md").exists()
        assert (tmp_path / "synced" / "o__r__1.md").exists()

    def test_updates_fields_on_move(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.move_to_synced("o", "r", 1, status="synced")
        post = frontmatter.load(tmp_path / "synced" / "o__r__1.md")
        assert post.metadata["status"] == "synced"

    def test_creates_synced_dir(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        assert not (tmp_path / "synced").exists()
        store.move_to_synced("o", "r", 1)
        assert (tmp_path / "synced").is_dir()

    def test_returns_none_for_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.move_to_synced("o", "r", 999) is None

    def test_no_longer_in_active_keys(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.move_to_synced("o", "r", 1)
        assert ("o", "r", 1) not in store.active_keys()


# ---------------------------------------------------------------------------
# restore_to_active
# ---------------------------------------------------------------------------


class TestRestoreToActive:
    def test_restores_from_synced(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.move_to_synced("o", "r", 1)
        result = store.restore_to_active("o", "r", 1)
        assert result is not None
        assert (tmp_path / "o__r__1.md").exists()
        assert not (tmp_path / "synced" / "o__r__1.md").exists()

    def test_returns_none_when_not_in_synced(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.restore_to_active("o", "r", 999) is None

    def test_back_in_active_keys(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T"})
        store.move_to_synced("o", "r", 1)
        store.restore_to_active("o", "r", 1)
        assert ("o", "r", 1) in store.active_keys()

    def test_round_trip_preserves_data(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "Feature"})
        store.move_to_synced("o", "r", 1, status="synced")
        store.restore_to_active("o", "r", 1)
        post = frontmatter.load(tmp_path / "o__r__1.md")
        assert post.metadata["title"] == "Feature"
        assert post.metadata["status"] == "synced"


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


class TestAll:
    def test_returns_metadata(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({"org": "o", "repo": "r", "number": 1, "title": "T1"})
        store.save({"org": "o", "repo": "r", "number": 2, "title": "T2"})
        items = store.all()
        assert len(items) == 2
        titles = {i["title"] for i in items}
        assert titles == {"T1", "T2"}
