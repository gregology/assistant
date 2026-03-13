"""Tests for IssueStore — issue-specific save and field mappings."""

import frontmatter

from assistant_github.platforms.issues.store import IssueStore


def _make_store(tmp_path) -> IssueStore:
    return IssueStore(path=tmp_path)


class TestIssueStoreSave:
    def test_creates_file_with_correct_name(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({
            "org": "myorg", "repo": "myrepo", "number": 10,
            "title": "Bug report", "author": "bob",
            "state": "open", "labels": ["bug"], "comment_count": 3,
        })
        assert path.name == "myorg__myrepo__10.md"
        assert path.exists()

    def test_stores_all_fields(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({
            "org": "myorg", "repo": "myrepo", "number": 10,
            "title": "Bug report", "author": "bob",
            "state": "open", "labels": ["bug", "urgent"], "comment_count": 3,
        })
        post = frontmatter.load(path)
        meta = post.metadata
        assert meta["org"] == "myorg"
        assert meta["repo"] == "myrepo"
        assert meta["number"] == 10
        assert meta["title"] == "Bug report"
        assert meta["author"] == "bob"
        assert meta["state"] == "open"
        assert meta["labels"] == ["bug", "urgent"]
        assert meta["comment_count"] == 3

    def test_generates_github_url(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({
            "org": "myorg", "repo": "myrepo", "number": 10,
            "title": "T",
        })
        post = frontmatter.load(path)
        assert post.metadata["url"] == "https://github.com/myorg/myrepo/issues/10"

    def test_defaults_for_optional_fields(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save({
            "org": "o", "repo": "r", "number": 1,
            "title": "T",
        })
        post = frontmatter.load(path)
        assert post.metadata["author"] == ""
        assert post.metadata["state"] == "open"
        assert post.metadata["labels"] == []
        assert post.metadata["comment_count"] == 0

    def test_findable_after_save(self, tmp_path):
        store = _make_store(tmp_path)
        store.save({
            "org": "o", "repo": "r", "number": 1,
            "title": "T",
        })
        assert store.find("o", "r", 1) is not None
