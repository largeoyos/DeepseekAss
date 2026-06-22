import os
import tempfile
import unittest

from core.snapshots import EncryptedSnapshotService, SnapshotError
from core.workspace import BookWorkspace


class PrefixCrypto:
    @staticmethod
    def encrypt_text(key, path, text):
        prefix = key.decode("ascii")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(prefix + ":" + text)

    @staticmethod
    def decrypt_text(key, path):
        prefix = key.decode("ascii") + ":"
        with open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
        if not data.startswith(prefix):
            raise ValueError("invalid key")
        return data[len(prefix):]


class SnapshotFailureModeTests(unittest.TestCase):
    def test_wrong_key_cannot_read_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            first = BookWorkspace(root, crypto=PrefixCrypto(), enc_key=b"one")
            first.ensure_manifest(book_id="book")
            first.storage.write_text("chapter.txt", "content")
            EncryptedSnapshotService(first).create("baseline")

            second = BookWorkspace(root, crypto=PrefixCrypto(), enc_key=b"two")
            with self.assertRaises(ValueError):
                second.ensure_manifest(book_id="book")

    def test_corrupt_block_does_not_modify_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = BookWorkspace(root)
            workspace.ensure_manifest(book_id="book")
            workspace.storage.write_text("chapter.txt", "original")
            service = EncryptedSnapshotService(workspace)
            snapshot = service.create("baseline")
            block = snapshot.files[0].sha256
            # Locate the chapter block rather than relying on manifest ordering.
            for entry in snapshot.files:
                if entry.path == "chapter.txt":
                    block = entry.sha256
            workspace.storage.write_text(
                f"{workspace.snapshot_root}/blocks/{block}.blob",
                "corrupt",
            )
            workspace.storage.write_text("chapter.txt", "current")
            with self.assertRaises(SnapshotError):
                service.restore(snapshot.snapshot_id)
            self.assertEqual("current", workspace.storage.read_text("chapter.txt"))


if __name__ == "__main__":
    unittest.main()
