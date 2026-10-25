import os
import array
import hashlib
import hmac
import threading

import oracledb

# CLOB columns come back as plain str instead of LOB objects. Without this every
# CLOB read is an extra database round trip (one per message / chunk).
oracledb.defaults.fetch_lobs = False

# Environment variables win if set; the fallbacks keep your current setup working.
DB_USER = os.environ.get("DB_USER", "sandbox")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_DSN = os.environ.get("DB_DSN", "localhost:1521/FREEPDB1")

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L12-v2"

# ---------------------------------------------------------------------------
# Connection pool: connections are reused instead of opened on every call.
# The pool is created lazily, once per process, and is thread-safe.
# ---------------------------------------------------------------------------
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = oracledb.create_pool(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    dsn=DB_DSN,
                    min=2,
                    max=10,
                    increment=1,
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=10000,   # ms: queue briefly instead of failing at once
                    timeout=300,          # s: close idle extra connections
                    stmtcachesize=50,
                )
    return _pool


def get_connection():
    """
    Returns a connection from the pool.
    Use it as a context manager: `with get_connection() as conn:`.
    Leaving the block returns the connection to the pool (it is not closed),
    and any uncommitted work is rolled back.
    """
    return _get_pool().acquire()


def _read_lob(value):
    """CLOB columns come back as LOB objects; convert them to str."""
    return value.read() if hasattr(value, "read") else value


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
def create_conversation(user_id, title="New Chat"):
    """Creates a new conversation thread and returns its ID."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            out_val = cursor.var(int)
            sql = """INSERT INTO conversations (user_id, title)
                     VALUES (:1, :2) RETURNING id INTO :3"""
            cursor.execute(sql, [user_id, title, out_val])
            conn.commit()
            return out_val.getvalue()[0]


def get_user_conversations(user_id):
    """Fetches all conversations for a user, sorted newest first."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            sql = """SELECT id, title
                     FROM conversations
                     WHERE user_id = :1
                     ORDER BY id DESC"""
            cursor.execute(sql, [user_id])
            # Returns a list of dicts: [{'id': 12, 'title': 'New Chat'}, ...]
            return [{"id": row[0], "title": row[1]} for row in cursor]


def rename_conversation(conversation_id, new_title, user_id=None):
    """
    Changes a conversation's title.
    Pass user_id to restrict the change to that user's chats.
    Returns True if a row was updated, False otherwise.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if user_id is None:
                cursor.execute(
                    "UPDATE conversations SET title = :1 WHERE id = :2",
                    [new_title, conversation_id],
                )
            else:
                cursor.execute(
                    "UPDATE conversations SET title = :1 WHERE id = :2 AND user_id = :3",
                    [new_title, conversation_id, user_id],
                )
            updated = cursor.rowcount
            conn.commit()
            return updated > 0


def delete_conversation(conversation_id, user_id=None):
    """
    Deletes a conversation and all of its messages in one transaction.
    Pass user_id to make sure the chat belongs to that user.
    Returns True if the conversation was deleted, False otherwise.
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if user_id is not None:
                cursor.execute(
                    "SELECT 1 FROM conversations WHERE id = :1 AND user_id = :2",
                    [conversation_id, user_id],
                )
                if cursor.fetchone() is None:
                    return False
            # messages first, because they reference the conversation
            cursor.execute(
                "DELETE FROM messages WHERE conversation_id = :1", [conversation_id]
            )
            cursor.execute("DELETE FROM conversations WHERE id = :1", [conversation_id])
            deleted = cursor.rowcount
            conn.commit()
            return deleted > 0


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def save_message(conversation_id, role, content):
    """Saves a single message (user or assistant) and returns its new ID."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            out_val = cursor.var(int)
            sql = """INSERT INTO messages (conversation_id, role, content)
                     VALUES (:1, :2, :3) RETURNING id INTO :4"""
            cursor.execute(sql, [conversation_id, role, content, out_val])
            conn.commit()
            return out_val.getvalue()[0]


def save_sources(message_id, sources):
    """
    Records which manual chunks supported an answer (citation / audit trail).
    sources = [{"n": 1, "chunk_id": 123, "dist": 0.31}, ...]
    """
    if not sources:
        return
    rows = [(message_id, s["n"], s["chunk_id"], round(float(s["dist"]), 6)) for s in sources]
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO message_sources (message_id, rank_no, chunk_id, distance) "
                "VALUES (:1, :2, :3, :4)",
                rows,
            )
        conn.commit()


def get_chat_history(conversation_id):
    """Retrieves all messages for a specific conversation in chronological order."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            sql = """SELECT role, content FROM messages
                     WHERE conversation_id = :1
                     ORDER BY id ASC"""
            cursor.execute(sql, [conversation_id])
            return [{"role": row[0], "content": _read_lob(row[1])} for row in cursor]


# ---------------------------------------------------------------------------
# RAG retrieval  (tables: doc_chunks, documents)
# ---------------------------------------------------------------------------
def search_chunks(query_vec, top_k=6, max_dist=0.65, skip_references=True):
    """
    Nearest manual chunks to the query by cosine distance (0 = identical).
    Chunks farther than `max_dist` are dropped.
    skip_references=True leaves out bibliography sections (about 11% of the manual), which
    only match on method keywords and add no usable content. Rows stay in the table.
    Returns a list of dicts, best first:
      {chunk_id, chapter, section, page_start, page_end, text, dist}
    """
    # FETCH APPROX uses a vector index when one exists and falls back to an
    # exact scan when it doesn't, so it is safe either way.
    # only sections whose LAST heading is exactly "References" (not "Reference Compounds")
    ref_filter = (
        "AND NOT REGEXP_LIKE(section, '(^|> )References?[[:space:]]*$', 'i')"
        if skip_references else ""
    )
    sql = f"""
        SELECT chunk_id, chapter, section, page_start, page_end, chunk_text,
               VECTOR_DISTANCE(embedding, :q, COSINE) AS dist
          FROM doc_chunks
         WHERE embedding_model = :model
           {ref_filter}
         ORDER BY dist
         FETCH APPROX FIRST {int(top_k)} ROWS ONLY
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, q=array.array("f", query_vec), model=EMBEDDING_MODEL)
            rows = cur.fetchall()
    return [
        {
            "chunk_id": r[0], "chapter": r[1], "section": r[2],
            "page_start": r[3], "page_end": r[4],
            "text": _read_lob(r[5]), "dist": float(r[6]),
        }
        for r in rows
        if r[6] is not None and r[6] <= max_dist and r[5]
    ]


# ---------------------------------------------------------------------------
# Users / login  (table: app_users(id, email, password_hash, created_at))
# ---------------------------------------------------------------------------
def verify_password(password, stored):
    """Constant-time check of a password against a stored hash string."""
    try:
        scheme, iters, salt_hex, hash_hex = (stored or "").split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


# never matches; makes a lookup for an unknown user cost the same as a real one
_DUMMY_HASH = "pbkdf2$200000$" + "00" * 16 + "$" + "00" * 32


def authenticate_user(login, password):
    """Returns the user's id if login (the EMAIL column) and password match, else None."""
    login = (login or "").strip().lower()
    if not login or not password:
        return None
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, password_hash FROM app_users WHERE LOWER(email) = :1",
                [login],
            )
            row = cursor.fetchone()
    if row is None:
        verify_password(password, _DUMMY_HASH)   # same cost whether or not the user exists
        return None
    return int(row[0]) if verify_password(password, row[1]) else None