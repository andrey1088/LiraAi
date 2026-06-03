import os
import shutil
import sqlite3
import sys
from pathlib import Path

# Path setup so script sees infrastructure modules  # noqa: E402
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from infrastructure.config.repo import ConfigRepository
from infrastructure.paths import config_path, lira_data
from infrastructure.memory.repo import ChatRepository
from infrastructure.semantic.engine import SemanticEngine


def consolidate_gallery():
    """Move files into folders and purge DB rows for deleted images."""
    pass

    db_path = str(lira_data("db", "gallery.db"))
    if not os.path.exists(db_path):
        pass
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id, file_path, model_name FROM generations")
        rows = cursor.fetchall()

        moved_count = 0
        deleted_count = 0

        for row_id, old_path, model_name in rows:
            # 1. Check file exists
            if not os.path.exists(old_path):
                cursor.execute("DELETE FROM generations WHERE id = ?", (row_id,))
                deleted_count += 1
                continue

            # 2. Move into model subfolder
            base_dir = os.path.dirname(old_path)
            # Sanitize folder name (like ImageGenerator)
            safe_name = "".join([c if c.isalnum() else "_" for c in model_name])

            # Skip if already in model folder
            current_folder = os.path.basename(base_dir)
            if current_folder != safe_name:
                new_dir = os.path.join(base_dir, safe_name)
                os.makedirs(new_dir, exist_ok=True)

                new_path = os.path.join(new_dir, os.path.basename(old_path))

                try:
                    shutil.move(old_path, new_path)
                    cursor.execute("UPDATE generations SET file_path = ? WHERE id = ?", (new_path, row_id))
                    moved_count += 1
                except Exception:
                    pass

        conn.commit()
        conn.close()

        pass
        pass
        pass

    except Exception:
        pass


def sleep_mode(config_repo=None, engine=None):
    pass

    consolidate_gallery()

    # Use passed objects or create new when run manually
    config = config_repo if config_repo else ConfigRepository(str(config_path()))
    if engine is None:
        engine = SemanticEngine()

    db_paths = set()
    for model in config.config.get("models", []):
        if "db_path" in model:
            db_paths.add(os.path.expanduser(model["db_path"]))

    for path in db_paths:
        if os.path.exists(path):
            pass
            repo = ChatRepository(path)

            # 0. Orphan/expired research (web_search/fetch), sessionless summary, unlinked artifacts run
            try:
                hy = repo.cleanup_for_sleep_mode()
                if hy and any(int(hy.get(k, 0) or 0) for k in hy):
                    print(f"[sleep] DB hygiene {path}: {hy}")
            except Exception as e:
                print(f"[sleep] DB hygiene failed for {path}: {e}")

            # 1. Purge junk (unverified global history)
            repo.cleanup_unverified_history()

            # 2. Promote knowledge
            new_entries = repo.get_verified_history()
            if not new_entries:
                pass
                continue

            pass
            for u, a in new_entries:
                # 1. Embed question for duplicate search
                query_vector = engine.embedder.encode(u).tolist()

                # 2. Find similar question by VECTOR not text
                existing_id = repo.find_similar_knowledge(query_vector, threshold=0.92)

                if existing_id:
                    repo.delete_long_term_entry(existing_id)
                    pass

                # 3. Build final Q+A vector for storage
                full_text = f"Q: {u} A: {a}"
                full_vector = engine.embedder.encode(full_text).tolist()

                repo.add_long_term_entry(u, a, full_vector)
                pass

            repo.cleanup_all_history()

    pass


if __name__ == "__main__":
    sleep_mode()
