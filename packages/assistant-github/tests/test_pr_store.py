"""Tests for PullRequestStore — PR-specific save and field mappings."""

import frontmatter

from assistant_github.platforms.pull_requests.store import PullRequestStore


def _make_store(tmp_path) -> PullRequestStore:
    return PullRequestStore(path=tmp_path)


class TestPullRequestStoreSave:
    def test_creates_file_with_correct_name(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save(
            {
                "org": "myorg",
                "repo": "myrepo",
                "number": 42,
                "title": "Add feature",
                "author": "alice",
                "additions": 50,
                "deletions": 10,
                "changed_files": 3,
            }
        )
        assert path.name == "myorg__myrepo__42.md"
        assert path.exists()

    def test_stores_all_fields(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save(
            {
                "org": "myorg",
                "repo": "myrepo",
                "number": 42,
                "title": "Add feature",
                "author": "alice",
                "additions": 50,
                "deletions": 10,
                "changed_files": 3,
            }
        )
        post = frontmatter.load(path)
        meta = post.metadata
        assert meta["org"] == "myorg"
        assert meta["repo"] == "myrepo"
        assert meta["number"] == 42
        assert meta["title"] == "Add feature"
        assert meta["author"] == "alice"
        assert meta["status"] == "open"
        assert meta["additions"] == 50
        assert meta["deletions"] == 10
        assert meta["changed_files"] == 3

    def test_generates_github_url(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save(
            {
                "org": "myorg",
                "repo": "myrepo",
                "number": 42,
                "title": "T",
            }
        )
        post = frontmatter.load(path)
        assert post.metadata["url"] == "https://github.com/myorg/myrepo/pull/42"

    def test_defaults_for_optional_fields(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save(
            {
                "org": "o",
                "repo": "r",
                "number": 1,
                "title": "T",
            }
        )
        post = frontmatter.load(path)
        assert post.metadata["author"] == ""
        assert post.metadata["additions"] == 0
        assert post.metadata["deletions"] == 0
        assert post.metadata["changed_files"] == 0

    def test_findable_after_save(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(
            {
                "org": "o",
                "repo": "r",
                "number": 1,
                "title": "T",
            }
        )
        assert store.find("o", "r", 1) is not None
