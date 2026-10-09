"""
办公助手 数据库模块 — SQLite 存储层。

三张表：
  - todos:        待办事项（替代 todos.txt）
  - daily_notes:  工作日志（每天一条，Markdown）
  - app_settings: 应用设置（替代 config.json，key-value）

首次启动自动迁移旧数据（todos.txt / config.json → SQLite），
旧文件重命名为 .bak 保留备份。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class Database:
    """SQLite 数据库封装，线程安全（check_same_thread=False）。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS todos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                text         TEXT    NOT NULL,
                done         INTEGER NOT NULL DEFAULT 0,
                priority     INTEGER NOT NULL DEFAULT 0,
                category     TEXT    DEFAULT '',
                tags         TEXT    DEFAULT '[]',
                sort_order   INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                completed_at TEXT,
                archived_at  TEXT,
                updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_todos_sort
                ON todos(category, sort_order);
            CREATE INDEX IF NOT EXISTS idx_todos_done
                ON todos(done);

            CREATE TABLE IF NOT EXISTS daily_notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT    NOT NULL UNIQUE,
                content    TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(todos)")}
        if "archived_at" not in columns:
            self.conn.execute("ALTER TABLE todos ADD COLUMN archived_at TEXT")
        self.conn.commit()

    # ── Todos CRUD ──────────────────────────────────────────

    def get_todos(self, category: str = "", *, archived: bool = False) -> list:
        archive_filter = "IS NOT NULL" if archived else "IS NULL"
        order = "archived_at DESC, id DESC" if archived else "sort_order, id"
        sql = f"SELECT * FROM todos WHERE archived_at {archive_filter}"
        params = []
        if category:
            sql += " AND category=?"
            params.append(category)
        rows = self.conn.execute(f"{sql} ORDER BY {order}", params).fetchall()
        return [dict(r) for r in rows]

    def add_todo(self, text: str, category: str = "", priority: int = 0,
                 tags: list = None) -> int:
        """添加待办，返回新记录 id。"""
        max_order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM todos"
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO todos (text, category, priority, tags, sort_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (text, category, priority, json.dumps(tags or [], ensure_ascii=False),
             max_order + 1),
        )
        self.conn.commit()
        return cur.lastrowid

    def toggle_todo(self, todo_id: int):
        """切换完成状态，自动记录/清除 completed_at。"""
        row = self.conn.execute(
            "SELECT done FROM todos WHERE id=? AND archived_at IS NULL", (todo_id,)
        ).fetchone()
        if row is None:
            return
        new_done = 0 if row["done"] else 1
        completed = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if new_done else None
        self.conn.execute(
            "UPDATE todos SET done=?, completed_at=?, "
            "updated_at=datetime('now','localtime') "
            "WHERE id=? AND archived_at IS NULL",
            (new_done, completed, todo_id),
        )
        self.conn.commit()

    def archive_todo(self, todo_id: int):
        self.conn.execute(
            "UPDATE todos SET archived_at=?, updated_at=datetime('now','localtime') "
            "WHERE id=? AND done=1 AND archived_at IS NULL",
            (datetime.now().isoformat(sep=" ", timespec="microseconds"), todo_id),
        )
        self.conn.commit()

    def restore_todo(self, todo_id: int):
        self.conn.execute(
            "UPDATE todos SET archived_at=NULL, "
            "updated_at=datetime('now','localtime') "
            "WHERE id=? AND archived_at IS NOT NULL", (todo_id,),
        )
        self.conn.commit()

    def update_todo(self, todo_id: int, **kwargs):
        """更新待办字段（text / priority / category / tags / sort_order）。"""
        allowed = {"text", "priority", "category", "tags", "sort_order"}
        fields, values = [], []
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k == "tags" and isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k}=?")
            values.append(v)
        if not fields:
            return
        fields.append("updated_at=datetime('now','localtime')")
        values.append(todo_id)
        self.conn.execute(
            f"UPDATE todos SET {', '.join(fields)} WHERE id=?", values
        )
        self.conn.commit()

    def delete_todo(self, todo_id: int):
        self.conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        self.conn.commit()

    def reorder_todos(self, id_order: list):
        """按给定 id 列表重新排序。"""
        for i, tid in enumerate(id_order):
            self.conn.execute(
                "UPDATE todos SET sort_order=? WHERE id=?", (i, tid)
            )
        self.conn.commit()

    # ── Daily Notes CRUD ────────────────────────────────────

    def get_daily_note(self, date_str: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM daily_notes WHERE date=?", (date_str,)
        ).fetchone()
        return dict(row) if row else {"date": date_str, "content": ""}

    def save_daily_note(self, date_str: str, content: str):
        """保存日志内容（upsert）。"""
        self.conn.execute(
            "INSERT INTO daily_notes (date, content) VALUES (?, ?) "
            "ON CONFLICT(date) DO UPDATE SET content=excluded.content, "
            "updated_at=datetime('now','localtime')",
            (date_str, content),
        )
        self.conn.commit()

    def get_all_note_dates(self) -> list:
        rows = self.conn.execute(
            "SELECT date, content FROM daily_notes ORDER BY date DESC"
        )
        return [r["date"] for r in rows if r["content"].strip()]

    # ── Settings ────────────────────────────────────────────

    def get_setting(self, key: str, default=None):
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value):
        self.conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, str(value)),
        )
        self.conn.commit()

    def get_all_settings(self) -> dict:
        rows = self.conn.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── 迁移 ────────────────────────────────────────────────

    def migrate_from_files(self, todos_path: Path, config_path: Path):
        """首次启动：从旧文件导入数据，然后重命名为 .bak。"""
        self._migrate_todos(todos_path)
        self._migrate_config(config_path)

    def _migrate_todos(self, path: Path):
        if not path.exists():
            return
        # 已有数据则跳过
        count = self.conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        if count > 0:
            return

        print("[迁移] 正在导入 todos.txt ...")
        in_section = False
        order = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    heading = line.lstrip("#").strip()
                    if heading.upper() == "TODO":
                        in_section = True
                    elif in_section:
                        break
                    continue
                if not in_section:
                    continue

                done = False
                for prefix in ("- [x] ", "- [X] ", "* [x] ", "* [X] "):
                    if line.startswith(prefix):
                        done = True
                        line = line[len(prefix):]
                        break
                else:
                    for prefix in ("- [ ] ", "* [ ] "):
                        if line.startswith(prefix):
                            line = line[len(prefix):]
                            break

                self.conn.execute(
                    "INSERT INTO todos (text, done, sort_order, created_at, "
                    "completed_at) VALUES (?, ?, ?, ?, ?)",
                    (line, 1 if done else 0, order, now,
                     now if done else None),
                )
                order += 1

        self.conn.commit()
        # 重命名旧文件为 .bak
        bak = path.with_suffix(".txt.bak")
        path.rename(bak)
        print(f"[迁移] todos.txt → SQLite 完成，旧文件已备份为 {bak.name}")

    def _migrate_config(self, path: Path):
        if not path.exists():
            return
        # 已有设置则跳过
        count = self.conn.execute(
            "SELECT COUNT(*) FROM app_settings"
        ).fetchone()[0]
        if count > 0:
            return

        print("[迁移] 正在导入 config.json ...")
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            for k, v in cfg.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                    (k, str(v)),
                )
            self.conn.commit()
        except Exception as e:
            print(f"[迁移] config.json 导入失败: {e}")
            return

        bak = path.with_suffix(".json.bak")
        path.rename(bak)
        print(f"[迁移] config.json → SQLite 完成，旧文件已备份为 {bak.name}")

    def close(self):
        self.conn.close()
