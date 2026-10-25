import re
import logging
import threading

from db_connection import EMBEDDING_MODEL, search_chunks

# ============================ SETTINGS ============================
TOP_K = 6                    # excerpts sent to the model
MAX_DIST = 0.65              # cosine distance cutoff; lower = stricter
MAX_CONTEXT_CHARS = 7000     # overall cap on the excerpts text
MAX_SEQ_TOKENS = 256         # must match ingest.py
REWRITE_MODEL = "openai/gpt-oss-20b"
REWRITE_EFFORT = "low"
HISTORY_TURNS = 4            # recent messages used to rewrite a follow-up question
# ==================================================================

RAG_INSTRUCTIONS = (
    "\n\nYou answer questions about assay development using numbered excerpts from the "
    "NCATS Assay Guidance Manual, given below under MANUAL EXCERPTS.\n"
    "- Base every factual statement on the excerpts. Do not add concentrations, thresholds, "
    "formulas, protocol steps or numbers that are not in them.\n"
    "- Cite the excerpts you use as [1], [2], placed right after the statement they support.\n"
    "- If the excerpts only partly answer the question, answer the part they cover and say "
    "plainly what the manual excerpts do not cover.\n"
    "- If the excerpts do not answer the question, or the note says none were found, say the "
    "manual passages retrieved do not cover it. Do not fill the gap from memory.\n"
    "- Greetings and small talk need no citations.\n\n"
)

REWRITE_PROMPT = (
    "Rewrite the user's last message as one standalone search query for a manual on assay "
    "development. Resolve pronouns and references (\"it\", \"that\", \"the second one\") using "
    "the conversation. Keep technical terms. Reply with the query only, no quotes, no "
    "explanation. If the message is already standalone, repeat it unchanged."
)

_model = None
_model_lock = threading.Lock()
_MARK_RE = re.compile(r"\n*\[RETRIEVED: [^\]]+\]\s*$")
_CITE_RE = re.compile(r"\s*\[\d+\]")


# ----------------------------- embedding -----------------------------
def _embed(text):
    global _model
    if _model is None:
        with _model_lock:                      # load once, even if users arrive together
            if _model is None:
                from sentence_transformers import SentenceTransformer
                m = SentenceTransformer(EMBEDDING_MODEL)
                m.max_seq_length = MAX_SEQ_TOKENS
                _model = m
    return _model.encode(text, normalize_embeddings=True).tolist()


def warm_up():
    """Loads the embedding model and opens the DB pool before the first question."""
    try:
        search_chunks(_embed("warm up"), top_k=1)
    except Exception:
        logging.exception("Warm-up failed (the first question will do the work instead)")


# ----------------------------- follow-up rewrite -----------------------------
def _recent_text(history):
    lines = []
    for m in history[-HISTORY_TURNS:]:
        text = _CITE_RE.sub("", _MARK_RE.sub("", str(m.get("content") or "")))
        lines.append(f"{m['role']}: {text[:500]}")
    return "\n".join(lines)


def _standalone_query(client, history, user_msg):
    """A short follow-up like 'what about its limitations?' is useless to a vector search on its own."""
    if not history:
        return user_msg
    kwargs = dict(
        model=REWRITE_MODEL,
        messages=[
            {"role": "system", "content": REWRITE_PROMPT},
            {"role": "user",
             "content": f"Conversation:\n{_recent_text(history)}\n\nLast message: {user_msg}"},
        ],
        temperature=0,
        max_tokens=800,                        # reasoning models spend part of this thinking
    )
    try:
        try:
            resp = client.chat.completions.create(reasoning_effort=REWRITE_EFFORT, **kwargs)
        except Exception:
            resp = client.chat.completions.create(**kwargs)   # model/SDK without the option
        q = (resp.choices[0].message.content or "").strip().strip('"')
        return q if 3 <= len(q) <= 400 else user_msg
    except Exception:
        logging.exception("Query rewrite failed; using the message as written")
        return user_msg


# ----------------------------- context building -----------------------------
def _pages(h):
    a, b = h["page_start"], h["page_end"]
    if not a:
        return ""
    return f"p. {a}" if not b or a == b else f"pp. {a}\u2013{b}"


def _body(h):
    """chunk_text starts with its own heading line; the label already shows it."""
    text = h["text"]
    first, _, rest = text.partition("\n")
    return rest.strip() if rest and first.strip() == (h["section"] or "").strip() else text


def _label(hits):
    """Short text stored with the answer and shown as 'Retrieved using ...' under it."""
    seen, out = set(), []
    for h in hits:
        part = f"{(h['section'] or h['chapter'] or 'Manual').split(' > ')[-1]}, {_pages(h)}".rstrip(", ")
        if part not in seen:
            seen.add(part)
            out.append(part)
    return "; ".join(out[:4]) + (f" (+{len(out) - 4} more)" if len(out) > 4 else "")


def retrieve_context(client, history, user_msg):
    """
    Returns (context_text, debug_dict).
    history = earlier messages [{"role", "content"}], NOT including user_msg.
    debug_dict["sources"] = [{n, chunk_id, section, page_start, page_end, dist}, ...]
    debug_dict["label"]   = short citation text for display / storage
    """
    dbg = {"query": user_msg, "sources": [], "label": "", "errors": []}
    try:
        query = _standalone_query(client, history, user_msg)
        dbg["query"] = query
        hits = search_chunks(_embed(query), top_k=TOP_K, max_dist=MAX_DIST)

        blocks, used, kept = [], 0, []
        for h in hits:
            n = len(kept) + 1
            block = f"[{n}] {h['section'] or h['chapter']} ({_pages(h)})\n{_body(h)}"
            if kept and used + len(block) > MAX_CONTEXT_CHARS:
                break
            blocks.append(block)
            kept.append(h)
            used += len(block)

        dbg["sources"] = [
            {"n": i + 1, "chunk_id": h["chunk_id"], "section": h["section"],
             "page_start": h["page_start"], "page_end": h["page_end"], "dist": h["dist"]}
            for i, h in enumerate(kept)
        ]
        dbg["label"] = _label(kept) if kept else ""

        if not blocks:
            return ("MANUAL EXCERPTS\nNone of the manual's passages were close enough to this "
                    "question. No excerpts were found.", dbg)
        return "MANUAL EXCERPTS\n\n" + "\n\n".join(blocks), dbg
    except Exception as e:
        logging.exception("Retrieval failed")
        dbg["errors"].append(f"retrieve_context: {e}")
        return "", dbg
