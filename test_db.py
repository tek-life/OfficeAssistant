import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db import Database


class TodoArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(self.db_path)
        self.addCleanup(lambda: self.db.close())

    def stored_todo(self, todo_id):
        return dict(self.db.conn.execute(
            "SELECT * FROM todos WHERE id=?", (todo_id,)
        ).fetchone())

    def completed_todo(self, text="完成的任务", category=""):
        todo_id = self.db.add_todo(text, category=category)
        self.db.toggle_todo(todo_id)
        return todo_id

    def test_new_todo_starts_open_and_unarchived(self):
        todo_id = self.db.add_todo("新任务")
        row = self.stored_todo(todo_id)
        self.assertIn("archived_at", row)
        self.assertIsNone(row["archived_at"])
        self.assertEqual(row["done"], 0)
        self.assertIsNone(row["completed_at"])

    def test_archive_hides_todo_without_deleting_it(self):
        todo_id = self.completed_todo()
        completed_at = self.stored_todo(todo_id)["completed_at"]
        self.db.archive_todo(todo_id)
        self.assertEqual(self.db.get_todos(), [])
        archived = self.db.get_todos(archived=True)
        self.assertEqual([row["id"] for row in archived], [todo_id])
        self.assertTrue(archived[0]["archived_at"])
        self.assertEqual(archived[0]["done"], 1)
        self.assertEqual(archived[0]["completed_at"], completed_at)

    def test_open_todo_cannot_be_archived(self):
        todo_id = self.db.add_todo("仍需处理")
        self.db.archive_todo(todo_id)
        self.assertEqual(len(self.db.get_todos()), 1)
        self.assertEqual(self.db.get_todos(archived=True), [])
        self.assertEqual(self.stored_todo(todo_id)["done"], 0)

    def test_archived_todo_cannot_be_toggled(self):
        todo_id = self.completed_todo()
        self.db.archive_todo(todo_id)
        before = self.stored_todo(todo_id)
        self.db.toggle_todo(todo_id)
        self.assertEqual(self.stored_todo(todo_id), before)

    def test_restore_returns_to_done_then_can_reopen(self):
        todo_id = self.completed_todo()
        completed_at = self.stored_todo(todo_id)["completed_at"]
        self.db.archive_todo(todo_id)
        self.db.restore_todo(todo_id)
        row = self.db.get_todos()[0]
        self.assertEqual(row["done"], 1)
        self.assertEqual(row["completed_at"], completed_at)
        self.assertIsNone(row["archived_at"])
        self.assertEqual(self.db.get_todos(archived=True), [])
        self.db.toggle_todo(todo_id)
        self.assertEqual(self.stored_todo(todo_id)["done"], 0)
        self.assertIsNone(self.stored_todo(todo_id)["completed_at"])

    def test_archive_order_uses_time_descending_then_id(self):
        ids = [self.completed_todo(str(i)) for i in range(3)]
        for todo_id in ids:
            self.db.archive_todo(todo_id)
        for todo_id, timestamp in zip(ids, (
            "2026-10-08 12:00:00", "2026-10-07 12:00:00", "2026-10-08 12:00:00"
        )):
            self.db.conn.execute(
                "UPDATE todos SET archived_at=? WHERE id=?", (timestamp, todo_id)
            )
        self.db.conn.commit()
        self.assertEqual(
            [row["id"] for row in self.db.get_todos(archived=True)],
            [ids[2], ids[0], ids[1]],
        )

    def test_category_filter_applies_to_both_views(self):
        active = self.db.add_todo("未完成", category="工作")
        done = self.completed_todo("已完成", category="工作")
        archived = self.completed_todo("已归档", category="工作")
        other = self.completed_todo("其他分类", category="个人")
        self.db.archive_todo(archived)
        self.db.archive_todo(other)
        self.assertEqual(
            [r["id"] for r in self.db.get_todos("工作")], [active, done]
        )
        self.assertEqual(
            [r["id"] for r in self.db.get_todos("工作", archived=True)], [archived]
        )

    def test_archiving_and_restoring_are_idempotent(self):
        todo_id = self.completed_todo()
        self.db.archive_todo(todo_id)
        archived = self.stored_todo(todo_id)
        self.db.archive_todo(todo_id)
        self.assertEqual(self.stored_todo(todo_id), archived)
        self.db.restore_todo(todo_id)
        restored = self.stored_todo(todo_id)
        self.db.restore_todo(todo_id)
        self.assertEqual(self.stored_todo(todo_id), restored)

    def test_archive_survives_reopening_database(self):
        todo_id = self.completed_todo()
        self.db.archive_todo(todo_id)
        before = self.stored_todo(todo_id)
        self.db.close()
        self.db = Database(self.db_path)
        self.assertEqual(self.db.get_todos(), [])
        self.assertEqual(self.db.get_todos(archived=True), [before])

    def test_legacy_database_is_upgraded_without_changing_records(self):
        self.db.close()
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.executescript("""
                CREATE TABLE todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 0,
                    category TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    completed_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                INSERT INTO todos (text, done, completed_at, priority, category, tags)
                VALUES ('旧的已完成任务', 1, '2026-10-01 09:00:00', 2,
                        '工作', '["历史"]');
                INSERT INTO todos (text) VALUES ('旧的未完成任务');
            """)
            conn.row_factory = sqlite3.Row
            original = [dict(r) for r in conn.execute("SELECT * FROM todos")]
        self.db = Database(legacy_path)
        upgraded = self.db.get_todos()
        self.assertIn("archived_at", upgraded[0])
        for old, new in zip(original, upgraded):
            self.assertIsNone(new.pop("archived_at"))
            self.assertEqual(new, old)
        self.assertEqual(len(upgraded), len(original))
        self.db.close()
        self.db = Database(legacy_path)
        self.assertEqual(len(self.db.get_todos()), 2)
        self.assertEqual(self.db.get_todos(archived=True), [])


if __name__ == "__main__":
    unittest.main()
