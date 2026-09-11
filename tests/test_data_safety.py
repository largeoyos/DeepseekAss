import json
import os
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from core.auth_manager import AuthError, AuthManager
from core.novel_manager import NovelManager


class BookPathSafetyTests(unittest.TestCase):
    def test_special_and_unknown_titles_never_delete_directories(self):
        with tempfile.TemporaryDirectory() as root:
            for encrypted in (False, True):
                with self.subTest(encrypted=encrypted):
                    manager = NovelManager(os.path.join(root, str(encrypted)),
                                           crypto=AuthManager,
                                           enc_key=Fernet.generate_key() if encrypted else None)
                    manager.create_book("keep")
                    with patch("core.novel_manager.shutil.rmtree") as delete:
                        for title in ("", ".", "..", "...", " . ", "missing"):
                            self.assertFalse(manager.delete_book(title))
                        delete.assert_not_called()
                    self.assertEqual(["keep"], manager.list_books())
                    self.assertTrue(manager.delete_book("keep"))

    def test_encrypted_unknown_title_does_not_fall_back_to_directory(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root, crypto=AuthManager, enc_key=Fernet.generate_key())
            os.makedirs(os.path.join(root, "unknown"))
            with self.assertRaises(FileNotFoundError):
                manager.get_workspace("unknown")

    def test_resolved_target_cannot_leave_shelf(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(os.path.join(root, "bookshelf"))
            with self.assertRaises(ValueError):
                manager._resolve_book_directory("..")
            with patch("core.novel_manager.os.path.realpath", side_effect=[manager._bookshelf_root, root]):
                with self.assertRaises(ValueError):
                    manager._resolve_book_directory("linked-book")


class PasswordTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("USERS_DIR", self.temp.name),
                            ("USERS_DB", os.path.join(self.temp.name, "users.json"))):
            patcher = patch("core.auth_manager." + name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.old_key = AuthManager.register("reader", "old123")
        directory = AuthManager.get_user_dir("reader")
        self.files = [os.path.join(directory, name + ".enc") for name in ("a", "b")]
        for path, text in zip(self.files, ("first", "second")):
            AuthManager.encrypt_text(self.old_key, path, text)

    def assert_original_readable(self):
        valid, key = AuthManager.authenticate("reader", "old123")
        self.assertTrue(valid)
        self.assertFalse(AuthManager.authenticate("reader", "new123")[0])
        self.assertEqual(["first", "second"], [AuthManager.decrypt_text(key, p) for p in self.files])

    def fail_second_replace(self, exception):
        replace = os.replace
        def injected(source, destination):
            if source.endswith(".new") and destination == self.files[1]:
                raise exception
            return replace(source, destination)
        return patch("core.auth_manager.os.replace", side_effect=injected)

    def test_file_replacement_failure_rolls_back_all_files(self):
        with self.fail_second_replace(OSError("disk failure")):
            with self.assertRaises(AuthError):
                AuthManager.change_password("reader", "old123", "new123")
        self.assert_original_readable()

    def test_account_commit_failure_rolls_back_all_files(self):
        with patch.object(AuthManager, "_save_users", side_effect=OSError("database failure")):
            with self.assertRaises(AuthError):
                AuthManager.change_password("reader", "old123", "new123")
        self.assert_original_readable()

    def test_interrupted_change_is_recovered_before_next_login(self):
        with self.fail_second_replace(KeyboardInterrupt("process interrupted")):
            with self.assertRaises(KeyboardInterrupt):
                AuthManager.change_password("reader", "old123", "new123")
        self.assert_original_readable()

    def test_recovery_failure_keeps_backups_for_next_attempt(self):
        with self.fail_second_replace(KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                AuthManager.change_password("reader", "old123", "new123")
        with patch.object(AuthManager, "_atomic_write_bytes", side_effect=OSError("disk still unavailable")):
            with self.assertRaises(AuthError):
                AuthManager.authenticate("reader", "old123")
        self.assert_original_readable()

    def test_success_changes_password_and_all_ciphertexts(self):
        key = AuthManager.change_password("reader", "old123", "new123")
        self.assertFalse(AuthManager.authenticate("reader", "old123")[0])
        self.assertEqual((True, key), AuthManager.authenticate("reader", "new123"))
        self.assertEqual(["first", "second"], [AuthManager.decrypt_text(key, p) for p in self.files])
        self.assertFalse(any(name.startswith(".password-change-") for name in os.listdir(self.temp.name)))

    def test_interruption_after_commit_does_not_roll_back_new_password(self):
        save = AuthManager._save_users
        def commit_then_interrupt(users):
            save(users)
            raise KeyboardInterrupt()
        with patch.object(AuthManager, "_save_users", side_effect=commit_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                AuthManager.change_password("reader", "old123", "new123")
        valid, key = AuthManager.authenticate("reader", "new123")
        self.assertTrue(valid)
        self.assertEqual(["first", "second"], [AuthManager.decrypt_text(key, p) for p in self.files])


class MetadataProtectionTests(unittest.TestCase):
    def test_corruption_blocks_metadata_chapter_writes_and_deletes(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), tempfile.TemporaryDirectory() as root:
                key = Fernet.generate_key() if encrypted else None
                manager = NovelManager(root, crypto=AuthManager, enc_key=key)
                manager.create_book("book")
                path, _ = manager.save_chapter_version("book", 1, "first", "original", version=1)
                original_chapter = open_bytes(path)
                meta_path = manager._meta_path("book")
                original_meta = manager._read_encrypted_text(meta_path)
                for damaged in ("{broken", "null", "[]", '{"title":"book","chapter_versions":[]}'):
                    manager._write_encrypted_text(meta_path, damaged)
                    for action in (
                        lambda: manager.load_meta("book"),
                        lambda: manager.save_meta("book", genre="changed"),
                        lambda: manager.save_chapter_version("book", 1, "first", "replacement", version=1),
                        lambda: manager.delete_chapter("book", 1),
                        lambda: manager.delete_chapter_version("book", 1, 1),
                    ):
                        with self.assertRaises(RuntimeError):
                            action()
                    self.assertEqual(damaged, manager._read_encrypted_text(meta_path))
                    self.assertEqual(original_chapter, open_bytes(path))
                manager._write_encrypted_text(meta_path, original_meta)
                manager.save_meta("book", genre="recovered")
                self.assertEqual("original", manager.read_active_chapter("book", 1))
                self.assertEqual("recovered", manager.load_meta("book").genre)


def open_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()



if __name__ == "__main__":
    unittest.main()
