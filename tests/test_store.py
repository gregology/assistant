import frontmatter

from assistant_sdk.store import NoteStore


class TestNoteStore:
    def test_save_creates_markdown_with_frontmatter(self, notes_dir):
        store = NoteStore(notes_dir)
        path = store.save("test.md", content="Hello world", title="Test", count=42)

        assert path.exists()
        post = frontmatter.load(path)
        assert post.metadata["title"] == "Test"
        assert post.metadata["count"] == 42
        assert post.content == "Hello world"

    def test_update_modifies_frontmatter(self, notes_dir):
        store = NoteStore(notes_dir)
        store.save("test.md", title="Original")
        store.update("test.md", title="Updated", new_field="added")

        post = frontmatter.load(notes_dir / "test.md")
        assert post.metadata["title"] == "Updated"
        assert post.metadata["new_field"] == "added"

    def test_archive_moves_to_subdirectory(self, notes_dir):
        store = NoteStore(notes_dir)
        store.save("test.md", title="Archivable")
        store.archive("test.md", archived=True)

        assert not (notes_dir / "test.md").exists()
        archived = notes_dir / "archive" / "test.md"
        assert archived.exists()
        post = frontmatter.load(archived)
        assert post.metadata["archived"] is True
        assert post.metadata["title"] == "Archivable"

    def test_find_returns_none_for_missing(self, notes_dir):
        store = NoteStore(notes_dir)
        assert store.find("nonexistent.md") is None

    def test_find_returns_path_when_exists(self, notes_dir):
        store = NoteStore(notes_dir)
        store.save("exists.md", title="Found")
        result = store.find("exists.md")
        assert result is not None
        assert result.name == "exists.md"

    def test_all_returns_metadata(self, notes_dir):
        store = NoteStore(notes_dir)
        store.save("a.md", name="Alice")
        store.save("b.md", name="Bob")
        store.save("c.md", name="Charlie")

        items = store.all()
        assert len(items) == 3
        names = {item["name"] for item in items}
        assert names == {"Alice", "Bob", "Charlie"}

    def test_all_returns_empty_for_nonexistent_dir(self):
        from pathlib import Path
        store = NoteStore(Path("/tmp/assistant_test_nonexistent_dir"))
        assert store.all() == []

    def test_save_creates_directory_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "dir"
        store = NoteStore(nested)
        path = store.save("test.md", title="Deep")

        assert path.exists()
        assert nested.is_dir()

    def test_update_returns_none_for_missing(self, notes_dir):
        store = NoteStore(notes_dir)
        assert store.update("missing.md", title="Nope") is None

    def test_archive_returns_none_for_missing(self, notes_dir):
        store = NoteStore(notes_dir)
        assert store.archive("missing.md") is None
