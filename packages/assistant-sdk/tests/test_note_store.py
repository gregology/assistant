"""Tests for assistant_sdk.store — NoteStore CRUD and archive operations.

These test the SDK's NoteStore directly, without the app config singleton.
"""

import frontmatter

from assistant_sdk.store import NoteStore


class TestNoteStoreSave:
    def test_creates_file(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.save("test.md", content="Hello", title="Test")
        assert path.exists()
        assert path.name == "test.md"

    def test_frontmatter_fields(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.save("test.md", title="Test", count=42)
        post = frontmatter.load(path)
        assert post.metadata["title"] == "Test"
        assert post.metadata["count"] == 42

    def test_content_preserved(self, tmp_path):
        store = NoteStore(tmp_path)
        path = store.save("test.md", content="Body text", title="T")
        post = frontmatter.load(path)
        assert post.content == "Body text"

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        store = NoteStore(nested)
        path = store.save("test.md", title="Deep")
        assert path.exists()
        assert nested.is_dir()


class TestNoteStoreFind:
    def test_returns_path_when_exists(self, tmp_path):
        store = NoteStore(tmp_path)
        store.save("exists.md", title="Found")
        assert store.find("exists.md") is not None

    def test_returns_none_when_missing(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.find("nope.md") is None


class TestNoteStoreUpdate:
    def test_modifies_fields(self, tmp_path):
        store = NoteStore(tmp_path)
        store.save("test.md", title="Original")
        store.update("test.md", title="Updated", extra="new")
        post = frontmatter.load(tmp_path / "test.md")
        assert post.metadata["title"] == "Updated"
        assert post.metadata["extra"] == "new"

    def test_returns_none_for_missing(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.update("missing.md", title="Nope") is None


class TestNoteStoreArchive:
    def test_moves_to_archive_subdir(self, tmp_path):
        store = NoteStore(tmp_path)
        store.save("test.md", title="Archivable")
        store.archive("test.md", archived=True)

        assert not (tmp_path / "test.md").exists()
        archived = tmp_path / "archive" / "test.md"
        assert archived.exists()
        post = frontmatter.load(archived)
        assert post.metadata["archived"] is True
        assert post.metadata["title"] == "Archivable"

    def test_returns_none_for_missing(self, tmp_path):
        store = NoteStore(tmp_path)
        assert store.archive("missing.md") is None


class TestNoteStoreAll:
    def test_returns_metadata(self, tmp_path):
        store = NoteStore(tmp_path)
        store.save("a.md", name="Alice")
        store.save("b.md", name="Bob")
        items = store.all()
        assert len(items) == 2
        names = {item["name"] for item in items}
        assert names == {"Alice", "Bob"}

    def test_returns_empty_for_nonexistent_dir(self, tmp_path):
        store = NoteStore(tmp_path / "nonexistent")
        assert store.all() == []

    def test_skips_malformed_files(self, tmp_path):
        store = NoteStore(tmp_path)
        store.save("good.md", name="Alice")
        # Write a file that will cause frontmatter parse issues
        (tmp_path / "bad.md").write_text("not valid frontmatter\n---\n---\n")
        items = store.all()
        # Should get at least the good one without crashing
        assert len(items) >= 1
