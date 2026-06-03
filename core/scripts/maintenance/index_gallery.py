import os
import sys
import sqlite3

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../.."))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "core", "scripts", "chat"))

os.environ.setdefault("LIRA_ROOT", PROJECT_ROOT)

from infrastructure.memory.repo import ChatRepository
from infrastructure.model_tasks.gallery.embedder import get_gallery_embedder
from infrastructure.paths import lira_data


def run_full_indexing():
    """Reindex gallery_vectors from description (rows without description are skipped)."""
    db_path = str(lira_data("db", "gallery.db"))
    ChatRepository(db_path)
    embedder = get_gallery_embedder()
    print(f"[index_gallery] embedder={embedder.model_name!r} format={embedder.embedding_format!r}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cols = {row[1] for row in cursor.execute("PRAGMA table_info(generations)").fetchall()}
    if "description" not in cols:
        cursor.execute("ALTER TABLE generations ADD COLUMN description TEXT")
        conn.commit()

    orphan = cursor.execute(
        """
        DELETE FROM gallery_vectors
        WHERE generation_id NOT IN (SELECT id FROM generations)
        """
    ).rowcount
    if orphan:
        conn.commit()
        print(f"[index_gallery] removed {orphan} orphan vector row(s)")

    cursor.execute("""
        SELECT g.id, g.description
        FROM generations g
        WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
    """)
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        print("[index_gallery] no rows with description")
        return

    indexed = 0
    failed = 0
    for gen_id, description in rows:
        try:
            text = (description or "").strip()
            if not text:
                continue
            vector = embedder.encode_document_bytes(text)
            cursor.execute(
                "DELETE FROM gallery_vectors WHERE generation_id = ?",
                (gen_id,),
            )
            cursor.execute(
                "INSERT INTO gallery_vectors (generation_id, embedding) VALUES (?, ?)",
                (gen_id, vector),
            )
            indexed += 1
            if indexed % 50 == 0:
                conn.commit()
        except Exception:
            failed += 1

    conn.commit()

    with_desc = cursor.execute(
        """
        SELECT COUNT(*) FROM generations
        WHERE description IS NOT NULL AND TRIM(description) != ''
        """
    ).fetchone()[0]
    desc_vec = cursor.execute(
        """
        SELECT COUNT(*) FROM generations g
        INNER JOIN gallery_vectors gv ON gv.generation_id = g.id
        WHERE g.description IS NOT NULL AND TRIM(g.description) != ''
        """
    ).fetchone()[0]
    vectors = cursor.execute("SELECT COUNT(*) FROM gallery_vectors").fetchone()[0]

    conn.close()
    print(
        f"[index_gallery] indexed={indexed} failed={failed} "
        f"desc={with_desc} desc_with_vector={desc_vec} vector_rows={vectors}"
    )


if __name__ == "__main__":
    run_full_indexing()
