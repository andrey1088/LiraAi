import json
import os
import re
import sqlite3
import struct
from urllib.parse import urlparse, urlunparse

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None


from core.scripts.chat.domain.message import Message
from core.scripts.chat.domain.session import ChatSession
from infrastructure.external_events.perception_rules import is_telegram_channel_user_content
from infrastructure.locale.variables import var_get
from infrastructure.model_tasks.gallery.quality import (
    is_bad_gallery_description,
    sanitize_gallery_description,
)

_PDF_USER_HINT_RES: list[re.Pattern[str]] = []


def _pdf_user_hint_regexes() -> list[re.Pattern[str]]:
    if _PDF_USER_HINT_RES:
        return _PDF_USER_HINT_RES
    for loc in ("en", "ru"):
        tpl = str(var_get("memory.pdf_attach_prefix", loc, default="") or "")
        if not tpl or "{pages}" not in tpl:
            continue
        head, tail = tpl.split("{pages}", 1)
        pat = "^" + re.escape(head) + r"[^)]+" + re.escape(tail) + r"\s*"
        _PDF_USER_HINT_RES.append(re.compile(pat, re.DOTALL))
    return _PDF_USER_HINT_RES


def _strip_pdf_model_hint_from_stored_user_text(text: str | None) -> str:
    """Strip LLM service prefix from legacy DB rows (hidden in UI)."""
    if not text:
        return text or ""
    for rx in _pdf_user_hint_regexes():
        text = rx.sub("", text, 1)
    return text.lstrip()


def normalize_research_url(url: str) -> str:
    """Same URL normalization for SERP / fetch / dedupe (lower scheme+host, path without trailing /)."""
    s = (url or "").strip()
    if not s:
        return ""
    p = urlparse(s)
    if not p.netloc:
        return s.lower()
    path = (p.path or "/").rstrip("/") or "/"
    return urlunparse(
        (
            (p.scheme or "https").lower(),
            p.netloc.lower(),
            path,
            "",
            p.query,
            "",
        )
    )


class ChatRepository:
    def __init__(self, db_path):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        if "gallery.db" in self.db_path:
            self._init_gallery_db()
        else:
            self._init_db()

    def _get_connection(self):
        """Open connection and load sqlite-vec extension."""
        conn = sqlite3.connect(self.db_path)
        if sqlite_vec:
            sqlite_vec.load(conn)
        else:
            pass
        return conn

    def _init_db(self):
        """Initialize all required tables in one place."""
        with self._get_connection() as conn:
            # History table (training export)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_msg TEXT,
                    assistant_reply TEXT,
                    is_verified INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Chat session tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT DEFAULT 'New chat',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Messages table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                )
            """)

            # Per-session compressed context (rolling summary)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_context_summary (
                    session_id INTEGER PRIMARY KEY,
                    summary_text TEXT,
                    covered_messages INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_limbic_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_perception_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    perception_stopped_at TEXT
                )
            """)
            self._migrate_limbic_to_model_scope(conn)

            # Long-term memory metadata table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_msg TEXT,
                    assistant_reply TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # --- Research pipeline: web/telegram/text/image/video ---
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    user_query_raw TEXT NOT NULL,
                    user_query_norm TEXT,
                    query_embedding BLOB,
                    mode TEXT DEFAULT 'investigation',
                    status TEXT DEFAULT 'running',
                    retention_class TEXT DEFAULT 'warm',
                    expires_at DATETIME,
                    is_pinned INTEGER DEFAULT 0,
                    importance_score REAL DEFAULT 0.5,
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    parent_run_id INTEGER,
                    notes TEXT,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id),
                    FOREIGN KEY (parent_run_id) REFERENCES research_runs(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    step_type TEXT NOT NULL,
                    input_json TEXT,
                    output_json TEXT,
                    status TEXT DEFAULT 'pending',
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    finished_at DATETIME,
                    error_text TEXT,
                    FOREIGN KEY (run_id) REFERENCES research_runs(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_kind TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    display_name TEXT,
                    region TEXT,
                    language TEXT,
                    is_verified INTEGER DEFAULT 0,
                    trust_weight REAL DEFAULT 0.5,
                    is_active INTEGER DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_kind, source_key)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER,
                    origin_type TEXT NOT NULL,
                    external_id TEXT,
                    media_type TEXT NOT NULL,
                    url TEXT,
                    canonical_url TEXT,
                    title TEXT,
                    author TEXT,
                    published_at DATETIME,
                    language TEXT,
                    region TEXT,
                    content_hash TEXT,
                    simhash TEXT,
                    phash TEXT,
                    retention_class TEXT DEFAULT 'warm',
                    expires_at DATETIME,
                    is_pinned INTEGER DEFAULT 0,
                    is_dataset_candidate INTEGER DEFAULT 0,
                    ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES sources(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifact_payloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id INTEGER NOT NULL,
                    text_content TEXT,
                    compressed_text TEXT,
                    summary_short TEXT,
                    summary_long TEXT,
                    keywords_json TEXT,
                    entities_json TEXT,
                    manipulation_flags_json TEXT,
                    redaction_level TEXT DEFAULT 'none',
                    quality_score REAL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifact_media_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id INTEGER NOT NULL,
                    file_role TEXT DEFAULT 'original',
                    storage_path TEXT NOT NULL,
                    mime_type TEXT,
                    width INTEGER,
                    height INTEGER,
                    duration_sec REAL,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    run_id INTEGER NOT NULL,
                    artifact_id INTEGER NOT NULL,
                    step_id INTEGER,
                    rank_score REAL,
                    is_selected_for_report INTEGER DEFAULT 0,
                    dedup_group_id TEXT,
                    PRIMARY KEY (run_id, artifact_id),
                    FOREIGN KEY (run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(id),
                    FOREIGN KEY (step_id) REFERENCES research_steps(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    prev_run_id INTEGER NOT NULL,
                    similarity_score REAL,
                    diff_summary TEXT,
                    new_items_count INTEGER DEFAULT 0,
                    dropped_items_count INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (prev_run_id) REFERENCES research_runs(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    report_type TEXT DEFAULT 'full',
                    report_markdown TEXT,
                    report_json TEXT,
                    bias_warnings_json TEXT,
                    is_baseline INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES research_runs(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    step_id INTEGER,
                    tool_name TEXT NOT NULL,
                    request_json TEXT,
                    response_json TEXT,
                    latency_ms INTEGER,
                    status TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES research_runs(id),
                    FOREIGN KEY (step_id) REFERENCES research_steps(id)
                )
            """)

            # Virtual table for vectors (sqlite-vec)
            # 384 dimensions (paraphrase-multilingual-MiniLM-L12-v2)
            if sqlite_vec:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS long_term_vec USING vec0(
                        id INTEGER PRIMARY KEY,
                        embedding FLOAT[384]
                    )
                """)

            try:
                conn.execute("ALTER TABLE chat_messages ADD COLUMN image_path TEXT")
                conn.commit()
            except Exception:
                # Column may already exist: SQLite will raise, which is safe to ignore here.
                pass

            # Research pipeline indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_session ON research_runs(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_norm ON research_runs(user_query_norm)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_steps_run ON research_steps(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_source ON artifacts(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_media_type ON artifacts(media_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_expires_at ON artifacts(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_research_runs_expires_at ON research_runs(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_run_artifacts_step ON run_artifacts(step_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_run ON research_reports(run_id)")

    # --- Session methods ---

    def start_session(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO chat_sessions (title, created_at) VALUES (?, datetime('now', 'localtime'))",
                (str(var_get("memory.default_chat_title", "en") or "New chat"),),
            )
            conn.commit()
            new_id = cursor.lastrowid
            self.update_session_time(new_id)
            return new_id

    def add_chat_message(self, session_id, role, content, image_path=None):
        if role == "user" and is_telegram_channel_user_content(content):
            return
        # Pack list as JSON string
        if isinstance(image_path, list):
            import json

            image_path = json.dumps(image_path)

        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, image_path) VALUES (?, ?, ?, ?)",
                (session_id, role, content, image_path),
            )
        self.update_session_time(session_id)

    def update_session_time(self, session_id):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now', 'localtime') WHERE id = ?", (session_id,)
            )
            conn.commit()

    def get_all_sessions(self):
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, title, strftime('%d.%m %H:%M', created_at)
                FROM chat_sessions
                ORDER BY updated_at DESC
            """)
            return [ChatSession(id=row[0], title=row[1], display_date=row[2]) for row in cursor.fetchall()]

    def get_session_messages(self, session_id):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT role, content, image_path FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            messages = []
            for row in cursor.fetchall():
                role, content, raw_path = row[0], row[1], row[2]
                if role == "user" and is_telegram_channel_user_content(content):
                    continue
                if role == "user" and content:
                    content = _strip_pdf_model_hint_from_stored_user_text(content)

                # Unpack JSON path array
                final_images = None
                if raw_path:
                    raw_path = raw_path.strip()
                    if raw_path.startswith("["):  # JSON array stored in DB
                        try:
                            final_images = json.loads(raw_path)
                        except Exception:
                            final_images = [raw_path]
                    else:
                        # Plain string (legacy row)
                        final_images = [raw_path] if raw_path else None

                messages.append(Message(role=role, content=content, image_url=final_images))
            return messages

    def get_last_session_id(self):
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT id FROM chat_sessions ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return row[0] if row else None

    def delete_session(self, session_id, model_type=None, model_id=None):
        """Delete session, messages, and all media files for that session."""
        import shutil

        # 1. If model info passed — compute and remove media folder
        if model_type and model_id:
            safe_type = str(model_type).lower().replace(" ", "_").replace("-", "_")
            from infrastructure.paths import lira_data

            media_path = str(lira_data("media", f"{safe_type}-{model_id}", session_id))

            if os.path.exists(media_path):
                try:
                    shutil.rmtree(media_path)
                    pass
                except Exception:
                    pass

        # 2. Delete DB rows (transaction via context manager)
        with self._get_connection() as conn:
            conn.execute("DELETE FROM chat_context_summary WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
            pass

    def get_context_summary(self, session_id):
        """Return session summary context if present."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT summary_text, covered_messages FROM chat_context_summary WHERE session_id = ?", (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {"summary": row[0] or "", "covered_messages": int(row[1] or 0)}

    def save_context_summary(self, session_id, summary_text, covered_messages):
        """Create or update session summary context."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO chat_context_summary (session_id, summary_text, covered_messages, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(session_id) DO UPDATE SET
                    summary_text = excluded.summary_text,
                    covered_messages = excluded.covered_messages,
                    updated_at = datetime('now', 'localtime')
                """,
                (session_id, summary_text, int(covered_messages)),
            )
            conn.commit()

    @staticmethod
    def _parse_limbic_state_json(raw: str | None) -> dict | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        if "current" in data and isinstance(data["current"], dict):
            return data["current"]
        return data

    def _migrate_limbic_to_model_scope(self, conn) -> None:
        """One-time: migrate last per-session state into model_limbic_state."""
        row = conn.execute("SELECT 1 FROM model_limbic_state WHERE id = 1").fetchone()
        if row:
            return
        try:
            legacy = conn.execute(
                """
                SELECT state_json FROM chat_limbic_state
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError:
            legacy = None
        if not legacy or not legacy[0]:
            return
        conn.execute(
            """
            INSERT INTO model_limbic_state (id, state_json, updated_at)
            VALUES (1, ?, datetime('now', 'localtime'))
            """,
            (legacy[0],),
        )

    def get_limbic_state(self):
        """Seven-dimensional limbic vector for current model DB or None."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT state_json FROM model_limbic_state WHERE id = 1",
            )
            row = cursor.fetchone()
        return self._parse_limbic_state_json(row[0] if row else None)

    def save_limbic_state(self, state_dict):
        """Save model limbic state (one row per DB file)."""
        payload = json.dumps({"v": 1, "current": state_dict}, ensure_ascii=False)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_limbic_state (id, state_json, updated_at)
                VALUES (1, ?, datetime('now', 'localtime'))
                ON CONFLICT(id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = datetime('now', 'localtime')
                """,
                (payload,),
            )
            conn.commit()

    def get_perception_stopped_at(self) -> str | None:
        """UTC ISO timestamp of last perception daemon stop() for this model DB."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT perception_stopped_at FROM model_perception_meta WHERE id = 1",
            ).fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])

    def set_perception_stopped_at(self, iso_timestamp: str | None) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_perception_meta (id, perception_stopped_at)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    perception_stopped_at = excluded.perception_stopped_at
                """,
                (iso_timestamp,),
            )
            conn.commit()

    def rename_session(self, session_id, new_title):
        with self._get_connection() as conn:
            conn.execute("UPDATE chat_sessions SET title = ? WHERE id = ?", (new_title, session_id))
            conn.commit()

    # --- Research pipeline ---

    def create_research_run(
        self,
        session_id,
        user_query_raw,
        user_query_norm=None,
        mode="investigation",
        status="running",
        retention_class="warm",
        expires_at=None,
        notes=None,
    ):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO research_runs
                    (session_id, user_query_raw, user_query_norm, mode, status, retention_class, expires_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_query_raw, user_query_norm, mode, status, retention_class, expires_at, notes),
            )
            conn.commit()
            return cur.lastrowid

    def finish_research_run(self, run_id, status="done", notes=None):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE research_runs
                SET status = ?, notes = COALESCE(?, notes), finished_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (status, notes, run_id),
            )
            conn.commit()

    def add_research_step(self, run_id, step_type, input_json=None, status="pending"):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO research_steps (run_id, step_type, input_json, status)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, step_type, input_json, status),
            )
            conn.commit()
            return cur.lastrowid

    def finish_research_step(self, step_id, status="done", output_json=None, error_text=None):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE research_steps
                SET status = ?, output_json = COALESCE(?, output_json), error_text = ?, finished_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (status, output_json, error_text, step_id),
            )
            conn.commit()

    def upsert_source(
        self,
        source_kind,
        source_key,
        display_name=None,
        region=None,
        language=None,
        is_verified=0,
        trust_weight=0.5,
    ):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sources (source_kind, source_key, display_name, region, language, is_verified, trust_weight, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(source_kind, source_key) DO UPDATE SET
                    display_name = COALESCE(excluded.display_name, sources.display_name),
                    region = COALESCE(excluded.region, sources.region),
                    language = COALESCE(excluded.language, sources.language),
                    updated_at = datetime('now', 'localtime')
                """,
                (source_kind, source_key, display_name, region, language, is_verified, trust_weight),
            )
            cur = conn.execute(
                "SELECT id FROM sources WHERE source_kind = ? AND source_key = ?",
                (source_kind, source_key),
            )
            row = cur.fetchone()
            conn.commit()
            return row[0] if row else None

    def add_artifact(
        self,
        source_id,
        origin_type,
        media_type,
        url=None,
        canonical_url=None,
        title=None,
        author=None,
        published_at=None,
        language=None,
        region=None,
        content_hash=None,
        retention_class="warm",
        expires_at=None,
    ):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO artifacts
                    (source_id, origin_type, media_type, url, canonical_url, title, author, published_at, language, region, content_hash, retention_class, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    origin_type,
                    media_type,
                    url,
                    canonical_url,
                    title,
                    author,
                    published_at,
                    language,
                    region,
                    content_hash,
                    retention_class,
                    expires_at,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def add_artifact_payload(
        self,
        artifact_id,
        text_content=None,
        compressed_text=None,
        summary_short=None,
        summary_long=None,
        keywords_json=None,
        entities_json=None,
        manipulation_flags_json=None,
        redaction_level="none",
        quality_score=None,
    ):
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO artifact_payloads
                    (artifact_id, text_content, compressed_text, summary_short, summary_long, keywords_json, entities_json, manipulation_flags_json, redaction_level, quality_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    text_content,
                    compressed_text,
                    summary_short,
                    summary_long,
                    keywords_json,
                    entities_json,
                    manipulation_flags_json,
                    redaction_level,
                    quality_score,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def link_run_artifact(self, run_id, artifact_id, step_id=None, rank_score=None, is_selected_for_report=0):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO run_artifacts (run_id, artifact_id, step_id, rank_score, is_selected_for_report)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, artifact_id) DO UPDATE SET
                    step_id = COALESCE(excluded.step_id, run_artifacts.step_id),
                    rank_score = COALESCE(excluded.rank_score, run_artifacts.rank_score)
                """,
                (run_id, artifact_id, step_id, rank_score, is_selected_for_report),
            )
            conn.commit()

    def get_last_research_run_id(self, session_id):
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT id FROM research_runs WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def get_saved_web_search_results_for_run(self, run_id: int, session_id: int):
        """
        Saved web_search results for run_id (origin_type=web_search only), no HTTP.
        Empty if run missing or belongs to another session.
        Each row: (url, title, rank_score, snippet).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM research_runs WHERE id = ? AND session_id = ?",
                (run_id, session_id),
            ).fetchone()
            if not row:
                return None
            cur = conn.execute(
                """
                SELECT a.url, a.title, ra.rank_score,
                       COALESCE(NULLIF(TRIM(ap.summary_short), ''),
                                NULLIF(TRIM(ap.text_content), ''), '') AS snippet
                FROM run_artifacts ra
                JOIN artifacts a ON a.id = ra.artifact_id
                LEFT JOIN artifact_payloads ap ON ap.artifact_id = a.id
                WHERE ra.run_id = ? AND a.origin_type = 'web_search'
                ORDER BY COALESCE(ra.rank_score, 999999), a.id
                """,
                (run_id,),
            )
            return cur.fetchall()

    def get_fetch_urls_normalized_for_run(self, run_id: int):
        """Normalized successful fetch_url URLs per run (status=done) so URLs are not re-offered after network errors."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT input_json FROM research_steps
                WHERE run_id = ? AND step_type = 'fetch_url' AND status = 'done'
                """,
                (run_id,),
            )
            out = set()
            for (ij,) in cur.fetchall():
                try:
                    data = json.loads(ij or "{}")
                    u = (data.get("url") or "").strip()
                    if u:
                        out.add(normalize_research_url(u))
                except Exception:
                    continue
            return out

    # --- Global history (LiraMemory) methods ---

    def save_interaction(self, user_text, assistant_text, is_verified=0):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO history (user_msg, assistant_reply, is_verified) VALUES (?, ?, ?)",
                (user_text, assistant_text, is_verified),
            )

    def update_last_interaction_status(self, status):
        """Set verification flag on the latest history row."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Find latest row id
            cursor.execute("SELECT id FROM history ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE history SET is_verified = ? WHERE id = ?", (status, row[0]))
                conn.commit()
                return True
        return False

    def set_verified_for_pair(self, user_text, assistant_text, status=1):
        """Mark is_verified on latest id row with given user/assistant pair."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE history SET is_verified = ?
                WHERE id = (
                    SELECT id FROM history
                    WHERE user_msg = ? AND assistant_reply = ?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (status, user_text, assistant_text),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_raw_history_for_search(self, limit=400):
        """Fetch latest rows for semantic search."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT user_msg, assistant_reply, is_verified
                FROM history
                WHERE assistant_reply IS NOT NULL AND assistant_reply != ''
                ORDER BY id DESC LIMIT ?
            """,
                (limit,),
            )
            return cursor.fetchall()

    def get_verified_history(self):
        """Return verified rows not yet in long-term memory."""
        with self._get_connection() as conn:
            # History rows missing from long_term_memory by text
            cursor = conn.execute("""
                SELECT h.user_msg, h.assistant_reply 
                FROM history h
                WHERE h.is_verified != 0
                AND NOT EXISTS (
                    SELECT 1 FROM long_term_memory ltm 
                    WHERE ltm.user_msg = h.user_msg AND ltm.assistant_reply = h.assistant_reply
                )
            """)
            return cursor.fetchall()

    def cleanup_unverified_history(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM history WHERE is_verified = 0")
            conn.commit()

    def cleanup_all_history(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM history")
            conn.commit()

    def _purge_research_run_ids(self, conn, run_ids):
        """
        Cascade-delete research_runs and linked rows (steps, run_artifacts, reports, tool_events).
        Does not delete artifacts — run orphan cleanup afterward.
        """
        if not run_ids:
            return 0
        uniq = sorted({int(r) for r in run_ids})
        ph = ",".join("?" * len(uniq))
        cur = conn.cursor()
        cur.execute(
            f"UPDATE research_runs SET parent_run_id = NULL WHERE parent_run_id IN ({ph})",
            uniq,
        )
        cur.execute(f"SELECT id FROM research_steps WHERE run_id IN ({ph})", uniq)
        step_ids = [row[0] for row in cur.fetchall()]
        if step_ids:
            sph = ",".join("?" * len(step_ids))
            cur.execute(f"DELETE FROM tool_events WHERE step_id IN ({sph})", step_ids)
        cur.execute(f"DELETE FROM tool_events WHERE run_id IN ({ph})", uniq)
        cur.execute(f"DELETE FROM research_reports WHERE run_id IN ({ph})", uniq)
        cur.execute(
            f"DELETE FROM run_comparisons WHERE run_id IN ({ph}) OR prev_run_id IN ({ph})",
            uniq + uniq,
        )
        cur.execute(f"DELETE FROM run_artifacts WHERE run_id IN ({ph})", uniq)
        cur.execute(f"DELETE FROM research_steps WHERE run_id IN ({ph})", uniq)
        cur.execute(f"DELETE FROM research_runs WHERE id IN ({ph})", uniq)
        return cur.rowcount

    def _delete_orphan_artifacts_and_unused_sources(self, conn):
        """Artifacts with no run_artifacts/sources links and no artifact references."""
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM artifact_payloads WHERE artifact_id IN (
                SELECT a.id FROM artifacts a
                WHERE NOT EXISTS (SELECT 1 FROM run_artifacts ra WHERE ra.artifact_id = a.id)
            )
            """
        )
        n_payloads = cur.rowcount
        cur.execute(
            """
            DELETE FROM artifact_media_files WHERE artifact_id IN (
                SELECT a.id FROM artifacts a
                WHERE NOT EXISTS (SELECT 1 FROM run_artifacts ra WHERE ra.artifact_id = a.id)
            )
            """
        )
        n_media = cur.rowcount
        cur.execute(
            """
            DELETE FROM artifacts WHERE NOT EXISTS (
                SELECT 1 FROM run_artifacts ra WHERE ra.artifact_id = artifacts.id
            )
            """
        )
        n_art = cur.rowcount
        cur.execute(
            """
            DELETE FROM sources WHERE NOT EXISTS (
                SELECT 1 FROM artifacts a WHERE a.source_id = sources.id
            )
            """
        )
        n_src = cur.rowcount
        return n_payloads, n_media, n_art, n_src

    def cleanup_for_sleep_mode(self):
        """
        Background cleanup for sleep_mode: dangling research after chat deletion,
        expired runs (expires_at, unpinned), summaries without session, orphan artifacts.
        Do not call for gallery.db.
        """
        if "gallery.db" in self.db_path:
            return {}
        stats = {
            "research_runs_removed": 0,
            "orphan_context_summaries": 0,
            "orphan_artifacts_removed": 0,
            "orphan_sources_removed": 0,
        }
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id FROM research_runs r
                WHERE NOT EXISTS (SELECT 1 FROM chat_sessions s WHERE s.id = r.session_id)
                """
            )
            orphan_ids = [row[0] for row in cur.fetchall()]
            cur.execute(
                """
                SELECT id FROM research_runs
                WHERE expires_at IS NOT NULL
                  AND expires_at < datetime('now', 'localtime')
                  AND (is_pinned IS NULL OR is_pinned = 0)
                """
            )
            expired_ids = [row[0] for row in cur.fetchall()]
            to_purge = sorted(set(orphan_ids + expired_ids))
            stats["research_runs_removed"] = self._purge_research_run_ids(conn, to_purge)
            pl, mf, art, src = self._delete_orphan_artifacts_and_unused_sources(conn)
            stats["orphan_artifacts_removed"] = int(art)
            stats["orphan_sources_removed"] = int(src)
            stats["artifact_payloads_removed"] = int(pl)
            stats["artifact_media_files_removed"] = int(mf)
            cur.execute(
                """
                DELETE FROM chat_context_summary
                WHERE session_id NOT IN (SELECT id FROM chat_sessions)
                """
            )
            stats["orphan_context_summaries"] = cur.rowcount
            conn.commit()
        return stats

    def add_long_term_entry(self, user_msg, assistant_reply, embedding):
        """Save text and its vector."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO long_term_memory (user_msg, assistant_reply) VALUES (?, ?)", (user_msg, assistant_reply)
            )
            new_id = cur.lastrowid

            if sqlite_vec and embedding:
                # Pack float list as float32 binary
                vector_blob = struct.pack(f"{len(embedding)}f", *embedding)
                cur.execute("INSERT INTO long_term_vec(id, embedding) VALUES (?, ?)", (new_id, vector_blob))
            conn.commit()

    def search_long_term(self, query_vector, limit=3):
        """
        Semantic search in vec0 table.
        query_vector: list of floats (query embedding).
        """
        if not sqlite_vec:
            return []

        # Convert float list to float32 BLOB for sqlite-vec
        vector_blob = struct.pack(f"{len(query_vector)}f", *query_vector)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 
                    ltm.user_msg, 
                    ltm.assistant_reply, 
                    ltv.distance
                FROM long_term_vec ltv
                JOIN long_term_memory ltm ON ltv.id = ltm.id
                WHERE ltv.embedding MATCH ? AND k = ?
                ORDER BY ltv.distance
            """,
                (vector_blob, limit),
            )
            return cursor.fetchall()

    def find_similar_knowledge(self, query_vector, threshold=0.92):
        """
        Find ID of the nearest row via sqlite-vec.
        query_vector: list of floats (new question embedding).
        """
        import struct

        # 1. Pack vector as float32 BLOB
        vector_blob = struct.pack(f"{len(query_vector)}f", *query_vector)

        with self._get_connection() as conn:
            # 2. Find nearest vector via MATCH
            # sqlite-vec distance (lower is closer)
            # float32 cosine distance 0.0 = identical, 2.0 = opposite
            # ~0.92 similarity ≈ distance < 0.16 (normalization-dependent)
            cursor = conn.execute(
                """
                                  SELECT id,
                                         distance
                                  FROM long_term_vec
                                  WHERE embedding MATCH ?
                                    AND k = 1
                                  ORDER BY distance
                                  """,
                (vector_blob,),
            )

            row = cursor.fetchone()
            if row:
                entry_id, distance = row
                # sqlite-vec typically uses L2 or cosine.
                # Very small distance means duplicate.
                if distance < 0.2:  # High similarity (~0.9+ cosine)
                    return entry_id

            return None

    def delete_long_term_entry(self, entry_id):
        """Delete row from main and vector tables."""
        with self._get_connection() as conn:
            # 1. Clear vector table
            conn.execute("DELETE FROM long_term_vec WHERE id = ?", (entry_id,))
            # 2. Clear main table
            conn.execute("DELETE FROM long_term_memory WHERE id = ?", (entry_id,))
            conn.commit()
            pass

    def _init_gallery_db(self):
        """Create generation history tables if missing."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt TEXT,
                    negative_prompt TEXT,
                    file_path TEXT,
                    model_name TEXT,
                    seed INTEGER,
                    settings_json TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._ensure_gallery_description_column(conn)
            self._ensure_gallery_vectors_table(conn)
            conn.commit()

    def _ensure_gallery_vectors_table(self, conn) -> None:
        """Embeddings for gallery_search (see scripts/maintenance/index_gallery.py)."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gallery_vectors (
                id INTEGER PRIMARY KEY,
                generation_id INTEGER UNIQUE,
                embedding BLOB,
                FOREIGN KEY (generation_id) REFERENCES generations (id)
            )
        """)

    def _ensure_gallery_description_column(self, conn) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(generations)").fetchall()}
        if "description" not in cols:
            conn.execute("ALTER TABLE generations ADD COLUMN description TEXT")

    def add_generation(self, prompt, negative_prompt, file_path, model_name, semantic_engine=None):
        """Save generation and index vector for search."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Save row in generations
            cursor.execute(
                "INSERT INTO generations (prompt, negative_prompt, file_path, model_name) VALUES (?, ?, ?, ?)",
                (prompt, negative_prompt, file_path, model_name),
            )
            gen_id = cursor.lastrowid

            # 2. Index on the fly when semantic engine provided
            if semantic_engine and hasattr(semantic_engine, "embedder"):
                try:
                    # Build vector (float32)
                    vector = semantic_engine.embedder.encode(prompt).astype("float32").tobytes()

                    # Insert into vector table (created by setup script)
                    cursor.execute(
                        "INSERT INTO gallery_vectors (generation_id, embedding) VALUES (?, ?)", (gen_id, vector)
                    )
                except Exception:
                    pass

            conn.commit()
            return gen_id

    def list_generations_missing_description(self, limit: int | None = None) -> list[tuple[int, str]]:
        """Rows missing description: (id, file_path)."""
        sql = """
            SELECT id, file_path FROM generations
            WHERE file_path IS NOT NULL AND TRIM(file_path) != ''
              AND (description IS NULL OR TRIM(description) = '')
            ORDER BY id ASC
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._get_connection() as conn:
            return [(int(r[0]), str(r[1])) for r in conn.execute(sql).fetchall()]

    def count_generations_missing_description(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM generations
                WHERE file_path IS NOT NULL AND TRIM(file_path) != ''
                  AND (description IS NULL OR TRIM(description) = '')
                """
            ).fetchone()
            return int(row[0]) if row else 0

    def list_generations_with_description(self, limit: int | None = None) -> list[tuple[int, str, str]]:
        sql = """
            SELECT id, file_path, description FROM generations
            WHERE file_path IS NOT NULL AND TRIM(file_path) != ''
              AND description IS NOT NULL AND TRIM(description) != ''
            ORDER BY id ASC
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._get_connection() as conn:
            return [(int(r[0]), str(r[1]), str(r[2] or "")) for r in conn.execute(sql).fetchall()]

    def list_generations_bad_description(self) -> list[tuple[int, str]]:
        bad: list[tuple[int, str]] = []
        for gen_id, file_path, desc in self.list_generations_with_description():
            if is_bad_gallery_description(desc):
                bad.append((gen_id, file_path))
        return bad

    def count_generations_bad_description(self) -> int:
        return len(self.list_generations_bad_description())

    def list_generations_for_description_repair(self, limit: int | None = None) -> list[tuple[int, str]]:
        """Missing + broken descriptions (for regen)."""
        seen: set[int] = set()
        out: list[tuple[int, str]] = []
        for gen_id, path in self.list_generations_missing_description():
            if gen_id not in seen:
                out.append((gen_id, path))
                seen.add(gen_id)
        for gen_id, path in self.list_generations_bad_description():
            if gen_id not in seen:
                out.append((gen_id, path))
                seen.add(gen_id)
        out.sort(key=lambda x: x[0])
        if limit is not None:
            out = out[: int(limit)]
        return out

    def count_generations_for_description_repair(self) -> int:
        return len(self.list_generations_for_description_repair())

    def get_generation_description(self, gen_id: int) -> str | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT description FROM generations WHERE id = ?",
                (int(gen_id),),
            ).fetchone()
            if not row or row[0] is None:
                return None
            return str(row[0]).strip() or None

    def clear_generation_description(self, gen_id: int) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE generations SET description = NULL WHERE id = ?",
                (int(gen_id),),
            )
            conn.execute(
                "DELETE FROM gallery_vectors WHERE generation_id = ?",
                (int(gen_id),),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_generation_description(
        self,
        gen_id: int,
        description: str | None,
        *,
        semantic_engine=None,
        skip_vector: bool = False,
        locale: str | None = None,
    ) -> bool:
        from infrastructure.model_tasks.gallery.quality import (
            resolve_gallery_description_locale,
        )

        desc = sanitize_gallery_description(description)
        loc = resolve_gallery_description_locale(locale)
        if is_bad_gallery_description(desc, loc):
            return self.clear_generation_description(int(gen_id))
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE generations SET description = ? WHERE id = ?",
                (desc or None, int(gen_id)),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "DELETE FROM gallery_vectors WHERE generation_id = ?",
                (int(gen_id),),
            )
            if desc and not skip_vector:
                try:
                    from infrastructure.model_tasks.gallery.embedder import (
                        get_gallery_embedder,
                    )

                    vector = get_gallery_embedder().encode_document_bytes(desc)
                    conn.execute(
                        "INSERT INTO gallery_vectors (generation_id, embedding) VALUES (?, ?)",
                        (int(gen_id), vector),
                    )
                except Exception:
                    pass
            conn.commit()
            return True

    def get_all_generations(self, model_filter="all", sort_order="DESC"):
        with self._get_connection() as conn:
            order = "DESC" if sort_order.upper() == "DESC" else "ASC"
            query_fields = "id, prompt, file_path, model_name, strftime('%d.%m.%Y %H:%M', timestamp), description"

            if model_filter == "all":
                query = f"SELECT {query_fields} FROM generations ORDER BY id {order}"
                rows = conn.execute(query).fetchall()
            else:
                query = f"SELECT {query_fields} FROM generations WHERE model_name LIKE ? ORDER BY id {order}"
                rows = conn.execute(query, (f"%{model_filter}%",)).fetchall()

            return [
                {
                    "id": r[0],
                    "prompt": r[1],
                    "path": r[2],
                    "model": r[3],
                    "date": r[4],
                    "description": r[5] or "",
                }
                for r in rows
            ]

    def delete_generation_entry(self, gen_id):
        """Delete generation DB row and file on disk."""
        try:
            with self._get_connection() as conn:
                # 1. Fetch file path first
                cursor = conn.execute("SELECT file_path FROM generations WHERE id = ?", (gen_id,))
                row = cursor.fetchone()

                if row:
                    file_path = row[0]

                    # 2. Delete file from disk
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            pass
                        except Exception:
                            pass

                    conn.execute(
                        "DELETE FROM gallery_vectors WHERE generation_id = ?",
                        (gen_id,),
                    )
                    conn.execute("DELETE FROM generations WHERE id = ?", (gen_id,))
                    conn.commit()
                    pass
                    return True  # success

            return False
        except Exception:
            pass
            return False

    def search_by_text(self, query, limit=5):
        """Plain SQL substring search."""
        with self._get_connection() as conn:
            # Match words in prompt or model name
            sql = """
                  SELECT id, prompt, file_path, model_name
                  FROM generations
                  WHERE prompt LIKE ? \
                     OR model_name LIKE ?
                  ORDER BY id DESC LIMIT ? \
                  """
            like_query = f"%{query}%"
            return conn.execute(sql, (like_query, like_query, limit)).fetchall()

    def gallery_vector_index_stats(self) -> dict:
        """Summary: description vs gallery_vectors (search debug)."""
        with self._get_connection() as conn:
            with_file = conn.execute(
                """
                SELECT COUNT(*) FROM generations
                WHERE file_path IS NOT NULL AND TRIM(file_path) != ''
                """
            ).fetchone()[0]
            with_desc = conn.execute(
                """
                SELECT COUNT(*) FROM generations
                WHERE description IS NOT NULL AND TRIM(description) != ''
                """
            ).fetchone()[0]
            vectors = conn.execute("SELECT COUNT(*) FROM gallery_vectors").fetchone()[0]
            desc_and_vec = conn.execute(
                """
                SELECT COUNT(*) FROM generations g
                INNER JOIN gallery_vectors gv ON gv.generation_id = g.id
                WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
                """
            ).fetchone()[0]
            desc_no_vec = conn.execute(
                """
                SELECT COUNT(*) FROM generations g
                LEFT JOIN gallery_vectors gv ON gv.generation_id = g.id
                WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
                  AND gv.generation_id IS NULL
                """
            ).fetchone()[0]
            orphan_vectors = conn.execute(
                """
                SELECT COUNT(*) FROM gallery_vectors gv
                LEFT JOIN generations g ON g.id = gv.generation_id
                WHERE g.id IS NULL
                """
            ).fetchone()[0]
        return {
            "with_file": int(with_file),
            "with_description": int(with_desc),
            "vector_rows": int(vectors),
            "desc_indexed": int(desc_and_vec),
            "desc_missing_vector": int(desc_no_vec),
            "orphan_vectors": int(orphan_vectors),
        }

    def cleanup_orphan_gallery_vectors(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM gallery_vectors
                WHERE generation_id NOT IN (SELECT id FROM generations)
                """
            )
            conn.commit()
            return int(cur.rowcount)

    def search_gallery_semantic(self, query_vector, limit=5, *, described_only: bool = False):
        """
        Semantic search over gallery_vectors.
        Returns (id, prompt, file_path, cosine_score, description) sorted by score descending.
        described_only: rows with non-empty description (embedding from description, not legacy prompt).
        """
        with self._get_connection() as conn:
            self._ensure_gallery_vectors_table(conn)
            if described_only:
                cursor = conn.execute(
                    """
                    SELECT gv.generation_id, gv.embedding
                    FROM gallery_vectors gv
                    INNER JOIN generations g ON g.id = gv.generation_id
                    WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
                    """
                )
            else:
                cursor = conn.execute("SELECT generation_id, embedding FROM gallery_vectors")
            rows = cursor.fetchall()

            if not rows:
                return []

            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity

            # 2. Match vectors
            scored_results = []
            for gen_id, emb_blob in rows:
                # BLOB back to numpy array
                emb = np.frombuffer(emb_blob, dtype="float32")
                # Cosine similarity (0 to 1)
                score = cosine_similarity([query_vector], [emb])[0][0]
                scored_results.append((gen_id, score))

            # 3. Sort: most similar first
            scored_results.sort(key=lambda x: x[1], reverse=True)
            top_slice = scored_results[:limit]
            top_ids = [r[0] for r in top_slice]
            score_by_id = {int(gid): float(sc) for gid, sc in top_slice}

            if not top_ids:
                return []

            # 4. Load generations rows by matched IDs
            placeholders = ",".join(["?"] * len(top_ids))
            safe_ids = [int(x) for x in top_ids]
            order_case = " ".join([f"WHEN id = {iid} THEN {i}" for i, iid in enumerate(safe_ids)])

            sql = f"""
                SELECT id, prompt, file_path, description
                FROM generations
                WHERE id IN ({placeholders})
                ORDER BY CASE {order_case} END
            """
            rows_gen = conn.execute(sql, safe_ids).fetchall()
            out = []
            for row in rows_gen:
                gid = int(row[0])
                sc = score_by_id.get(gid, 0.0)
                desc = (row[3] or "").strip() if len(row) > 3 else ""
                out.append((row[0], row[1], row[2], sc, desc))
            return out

    def search_gallery_hybrid(
        self,
        query: str,
        query_vector,
        *,
        limit: int = 10,
        pool: int = 150,
        min_score: float = 0.55,
    ) -> list[tuple]:
        """
        Batch cosine search on gallery_vectors (normalized embeddings).
        Returns (id, prompt, file_path, score, description, score, score).
        """
        import numpy as np

        with self._get_connection() as conn:
            self._ensure_gallery_vectors_table(conn)
            rows = conn.execute(
                """
                SELECT g.id, g.prompt, g.file_path, g.description, gv.embedding
                FROM gallery_vectors gv
                INNER JOIN generations g ON g.id = gv.generation_id
                WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
                """
            ).fetchall()

        if not rows:
            return []

        ids: list[int] = []
        prompts: list[str] = []
        paths: list[str] = []
        descs: list[str] = []
        emb_list: list[np.ndarray] = []

        for rid, prompt, path, desc, blob in rows:
            if not blob:
                continue
            ids.append(int(rid))
            prompts.append(prompt or "")
            paths.append(path or "")
            descs.append((desc or "").strip())
            emb_list.append(np.frombuffer(blob, dtype=np.float32))

        if not emb_list:
            return []

        E = np.vstack(emb_list)
        q = np.asarray(query_vector, dtype=np.float32)
        vec_scores = E @ q

        scored: list[tuple] = []
        for i, vs in enumerate(vec_scores):
            sc = float(vs)
            if sc >= min_score:
                scored.append((ids[i], prompts[i], paths[i], sc, descs[i], sc, sc))

        scored.sort(key=lambda x: x[3], reverse=True)

        if len(scored) > pool:
            scored = scored[:pool]

        top = scored[:limit]
        return list(top)

    def search_gallery_by_tokens(self, tokens: list[str], limit: int = 10) -> list:
        """All tokens must appear in description or prompt (AND)."""
        clean = [t.strip().lower() for t in tokens if t and len(t.strip()) >= 2]
        if not clean:
            return []
        clauses = []
        params: list = []
        for t in clean[:8]:
            like = f"%{t}%"
            clauses.append("(description LIKE ? OR prompt LIKE ?)")
            params.extend([like, like])
        where = " AND ".join(clauses)
        sql = f"""
            SELECT id, prompt, file_path, model_name, description
            FROM generations
            WHERE {where}
            ORDER BY id DESC
            LIMIT ?
        """
        params.append(int(limit))
        with self._get_connection() as conn:
            return conn.execute(sql, params).fetchall()

    def get_images_by_ids(self, ids):
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        with self._get_connection() as conn:
            return conn.execute(
                f"""
                SELECT prompt, file_path, model_name 
                FROM generations WHERE id IN ({placeholders})
            """,
                ids,
            ).fetchall()

    def search_gallery_by_text(self, text, limit=10):
        """Text search on description (first) and prompt."""
        with self._get_connection() as conn:
            like = f"%{text}%"
            sql = """
                SELECT id, prompt, file_path, model_name, description
                FROM generations
                WHERE description LIKE ? OR prompt LIKE ?
                ORDER BY
                    CASE WHEN description LIKE ? THEN 0 ELSE 1 END,
                    id DESC
                LIMIT ?
            """
            return conn.execute(sql, (like, like, like, limit)).fetchall()
