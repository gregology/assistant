from assistant_email.platforms.inbox.store import EmailStore, _sanitize_message_id
from assistant_sdk.store import NoteStore


def _make_store(tmp_path) -> EmailStore:
    return EmailStore(path=tmp_path)


def _seed(store: EmailStore, message_id: str, subdir: str | None = None) -> None:
    """Write a minimal note file using message_id as the identity key."""
    dest = store._path / subdir if subdir else store._path
    sanitized = _sanitize_message_id(message_id)
    NoteStore(dest).save(f"2026_01_01_00_00_00__{sanitized}.md", message_id=message_id, uid="99")


# ---------------------------------------------------------------------------
# _sanitize_message_id
# ---------------------------------------------------------------------------


class TestSanitizeMessageId:
    def test_strips_angle_brackets(self):
        assert _sanitize_message_id("<abc@example.com>") == "abc_example.com"

    def test_replaces_at_sign(self):
        assert _sanitize_message_id("abc@example.com") == "abc_example.com"

    def test_replaces_plus(self):
        assert _sanitize_message_id("<abc+tag@example.com>") == "abc_tag_example.com"

    def test_preserves_safe_chars(self):
        result = _sanitize_message_id("<CABcd.123-xyz@mail.gmail.com>")
        assert result == "CABcd.123-xyz_mail.gmail.com"

    def test_empty_string(self):
        assert _sanitize_message_id("") == ""

    def test_whitespace_stripped(self):
        assert _sanitize_message_id("  <abc@example.com>  ") == "abc_example.com"


# ---------------------------------------------------------------------------
# known_message_ids
# ---------------------------------------------------------------------------


class TestKnownMessageIds:
    def test_finds_message_id_in_root(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg1@example.com>")
        assert "<msg1@example.com>" in store.known_message_ids()

    def test_finds_message_id_in_archive_subdir(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg2@example.com>", subdir="archive")
        assert "<msg2@example.com>" in store.known_message_ids()

    def test_finds_message_id_in_spam_subdir(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg3@example.com>", subdir="spam")
        assert "<msg3@example.com>" in store.known_message_ids()

    def test_finds_message_id_in_trash_subdir(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg4@example.com>", subdir="trash")
        assert "<msg4@example.com>" in store.known_message_ids()

    def test_aggregates_across_all_subdirs(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<a@example.com>")
        _seed(store, "<b@example.com>", subdir="archive")
        _seed(store, "<c@example.com>", subdir="spam")
        _seed(store, "<d@example.com>", subdir="trash")
        assert store.known_message_ids() == {
            "<a@example.com>",
            "<b@example.com>",
            "<c@example.com>",
            "<d@example.com>",
        }

    def test_returns_empty_for_nonexistent_dir(self, tmp_path):
        store = _make_store(tmp_path / "nonexistent")
        assert store.known_message_ids() == set()

    def test_fallback_synthetic_key_for_missing_message_id(self, tmp_path):
        """Notes without message_id yield a synthetic imap_{uid} key."""
        NoteStore(tmp_path).save("2026_01_01_00_00_00__imap_37001.md", uid="37001", message_id="")
        store = _make_store(tmp_path)
        assert "imap_37001" in store.known_message_ids()


# ---------------------------------------------------------------------------
# find_by_message_id
# ---------------------------------------------------------------------------


class TestFindByMessageId:
    def test_finds_note_in_root(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        result = store.find_by_message_id("<msg@example.com>")
        assert result is not None
        assert result.parent == tmp_path

    def test_finds_note_in_archive(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>", subdir="archive")
        result = store.find_by_message_id("<msg@example.com>")
        assert result is not None
        assert result.parent.name == "archive"

    def test_finds_note_in_trash(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>", subdir="trash")
        result = store.find_by_message_id("<msg@example.com>")
        assert result is not None
        assert result.parent.name == "trash"

    def test_returns_none_for_missing(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.find_by_message_id("<nobody@example.com>") is None

    def test_returns_none_for_empty_message_id(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.find_by_message_id("") is None

    def test_finds_synthetic_key(self, tmp_path):
        """Synthetic imap_{uid} keys are findable via find_by_message_id."""
        NoteStore(tmp_path).save("2026_01_01_00_00_00__imap_37001.md", uid="37001")
        store = _make_store(tmp_path)
        assert store.find_by_message_id("imap_37001") is not None


# ---------------------------------------------------------------------------
# move_to_subdir
# ---------------------------------------------------------------------------


class TestMoveToSubdir:
    def test_moves_note_from_root_to_archive(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "archive")
        assert not list(tmp_path.glob("*__msg_example.com.md"))
        assert (tmp_path / "archive").is_dir()
        assert list((tmp_path / "archive").glob("*__msg_example.com.md"))

    def test_moves_to_trash(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "trash")
        assert list((tmp_path / "trash").glob("*__msg_example.com.md"))

    def test_moves_to_spam(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "spam")
        assert list((tmp_path / "spam").glob("*__msg_example.com.md"))

    def test_creates_subdir_if_missing(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        assert not (tmp_path / "trash").exists()
        store.move_to_subdir("<msg@example.com>", "trash")
        assert (tmp_path / "trash").is_dir()

    def test_missing_message_id_logs_warning_no_crash(self, tmp_path):
        store = _make_store(tmp_path)
        store.move_to_subdir("<nobody@example.com>", "trash")  # should not raise

    def test_note_still_findable_after_move(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "archive")
        assert store.find_by_message_id("<msg@example.com>") is not None
        assert "<msg@example.com>" in store.known_message_ids()

    def test_moves_to_arbitrary_named_folder(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "Newsletters")
        assert list((tmp_path / "Newsletters").glob("*__msg_example.com.md"))

    def test_moves_to_nested_folder(self, tmp_path):
        store = _make_store(tmp_path)
        _seed(store, "<msg@example.com>")
        store.move_to_subdir("<msg@example.com>", "Work/Stripe")
        assert list((tmp_path / "Work" / "Stripe").glob("*__msg_example.com.md"))
        assert store.find_by_message_id("<msg@example.com>") is not None
