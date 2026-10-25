"""
One-off ingestion: PDF(s) -> clean text -> section-aware chunks -> embeddings -> Oracle.

"""
import re
import sys
import array
import hashlib
import logging
import argparse
from pathlib import Path
from collections import Counter

# ============================ CONFIG ============================
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L12-v2"   # must match the query side (384 dims)
MAX_SEQ_TOKENS = 256        # MiniLM defaults to 128 and silently truncates; 256 ~ 1000 characters
TARGET_CHARS = 800          # a chunk is closed once it reaches about this size
MAX_CHARS = 1000            # hard ceiling for one chunk (keeps it under MAX_SEQ_TOKENS)
OVERLAP_CHARS = 150         # tail of the previous chunk repeated at the start of the next
MIN_CHARS = 40              # chunks shorter than this are dropped (stray headings, page debris)
EMBED_BATCH = 32
HEADER_FOOTER_ZONE = 0.07   # top/bottom 7% of a page is checked for running headers/footers
REPEAT_SHARE = 0.30         # a header/footer line appears on more than 30% of pages
# ================================================================

log = logging.getLogger("ingest")


# ----------------------------- text cleaning -----------------------------
def _norm_line(s):
    """Digits -> 0 so 'Page 12' and 'Page 13' count as the same running footer."""
    return re.sub(r"\d+", "0", re.sub(r"\s+", " ", s)).strip().lower()


def _clean_paragraph(text):
    text = text.replace("\u00ad", "")                 # soft hyphens
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)      # re-join words split across lines
    text = re.sub(r"\s*\n\s*", " ", text)             # a block is one paragraph
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_pages(doc):
    """
    -> [(page_no, [paragraph, ...]), ...], page_no starting at 1.
    Uses PyMuPDF text blocks (one block ~ one paragraph), sorted in reading order,
    with repeated headers/footers and bare page numbers removed.
    """
    raw = []                                          # (page_no, text, in_margin_zone)
    for pno in range(len(doc)):
        page = doc[pno]
        h = page.rect.height
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]   # type 0 = text
        blocks.sort(key=lambda b: (round(b[1] / 5), b[0]))
        for b in blocks:
            txt = _clean_paragraph(b[4])
            if not txt:
                continue
            in_zone = b[1] < h * HEADER_FOOTER_ZONE or b[3] > h * (1 - HEADER_FOOTER_ZONE)
            raw.append((pno + 1, txt, in_zone))

    n_pages = max(1, len(doc))
    repeats = Counter()
    for pno in {p for p, _, _ in raw}:
        seen = {_norm_line(t) for p, t, z in raw if p == pno and z}
        repeats.update(seen)
    running = {k for k, c in repeats.items() if n_pages >= 8 and c / n_pages > REPEAT_SHARE}

    pages = {}
    for pno, txt, in_zone in raw:
        if in_zone and (_norm_line(txt) in running or re.fullmatch(r"[\divxlc\s.\-]+", txt.lower())):
            continue
        pages.setdefault(pno, []).append(txt)
    return sorted(pages.items())


# ----------------------------- section paths -----------------------------
def section_path_per_page(doc, fallback_chapter):
    """
    Uses the PDF's bookmarks (table of contents) to give every page a heading path.
    -> {page_no: [heading, subheading, ...]}. Without bookmarks, the file name is the chapter.
    """
    toc = [(lvl, title.strip(), pg) for lvl, title, pg in doc.get_toc() if pg and pg >= 1]
    paths, stack, i = {}, [], 0
    for pno in range(1, len(doc) + 1):
        while i < len(toc) and toc[i][2] <= pno:      # every bookmark that starts on or before this page
            lvl, title, _ = toc[i]
            stack = stack[: max(0, lvl - 1)] + [title]
            i += 1
        paths[pno] = list(stack) if stack else [fallback_chapter]
    return paths


# ----------------------------- chunking -----------------------------
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def _split_long(par):
    """A paragraph longer than MAX_CHARS is cut at sentence ends (or hard-cut as a last resort)."""
    out, cur = [], ""
    for s in _SENT_RE.split(par):
        while len(s) > MAX_CHARS:                     # no sentence breaks at all
            if cur:
                out.append(cur)
                cur = ""
            cut = s.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > MAX_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_paragraphs(paras):
    """
    paras = [(page_no, text), ...] of ONE section, in order.
    -> [(page_start, page_end, text), ...] with ~TARGET_CHARS per chunk and OVERLAP_CHARS overlap.
    """
    units = []
    for pg, t in paras:
        for piece in (_split_long(t) if len(t) > MAX_CHARS else [t]):
            units.append((pg, piece))

    chunks, cur, cur_len, fresh = [], [], 0, False   # fresh = cur holds text not yet emitted

    def flush():
        chunks.append((min(p for p, _ in cur), max(p for p, _ in cur),
                       " ".join(t for _, t in cur)))

    def overlap_tail():
        """Closing sentence(s) of the chunk just emitted, up to OVERLAP_CHARS, to open the next one."""
        tail, size = [], 0
        for pg, text in reversed(cur):
            if size + len(text) <= OVERLAP_CHARS:
                tail.insert(0, (pg, text))
                size += len(text) + 1
                continue
            kept = []                                 # paragraph too long: keep only its last sentences
            for sent in reversed(_SENT_RE.split(text)):
                if size + sum(len(k) + 1 for k in kept) + len(sent) > OVERLAP_CHARS:
                    break
                kept.insert(0, sent)
            if kept:
                tail.insert(0, (pg, " ".join(kept)))
                size += sum(len(k) + 1 for k in kept)
            break
        return tail, size

    for pg, t in units:
        if cur and fresh and cur_len + len(t) + 1 > MAX_CHARS:
            flush()
            cur, cur_len = overlap_tail()
            fresh = False
        if cur and cur_len + len(t) + 1 > MAX_CHARS:  # no room for the overlap: start clean
            cur, cur_len = [], 0
        cur.append((pg, t))
        cur_len += len(t) + 1
        fresh = True
        if cur_len >= TARGET_CHARS:
            flush()
            cur, cur_len = overlap_tail()
            fresh = False
    if cur and fresh:
        flush()
    return [c for c in chunks if len(c[2]) >= MIN_CHARS]


def build_chunks(doc, fallback_chapter):
    """-> [{'chapter','section','page_start','page_end','text'}, ...] in document order."""
    pages = extract_pages(doc)
    paths = section_path_per_page(doc, fallback_chapter)

    # group consecutive pages that sit under the same heading path
    groups, last_key = [], None
    for pno, paras in pages:
        key = tuple(paths[pno])
        if key != last_key:
            groups.append((key, []))
            last_key = key
        groups[-1][1].extend((pno, p) for p in paras)

    out = []
    for key, paras in groups:
        chapter, section = key[0], " > ".join(key)
        for ps, pe, text in chunk_paragraphs(paras):
            out.append({"chapter": chapter, "section": section,
                        "page_start": ps, "page_end": pe,
                        "text": f"{section}\n{text}"})     # heading prefix improves retrieval
    return out


# ----------------------------- database -----------------------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_lob(v):
    return v.read() if hasattr(v, "read") else v


def store(conn, source_name, path, sha, page_count, rows, vectors, force):
    """Replaces any earlier load of this file, then inserts the document and its chunks."""
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, file_sha256 FROM documents WHERE file_name = :1", [path.name])
        for doc_id, old_sha in cur.fetchall():
            if old_sha == sha and not force:
                log.info("%s is already loaded and unchanged; skipping (use --force to redo)", path.name)
                return False
            cur.execute("DELETE FROM documents WHERE doc_id = :1", [doc_id])   # chunks go with it

        out_id = cur.var(int)
        cur.execute(
            """INSERT INTO documents (source_name, file_name, file_sha256, page_count)
               VALUES (:1, :2, :3, :4) RETURNING doc_id INTO :5""",
            [source_name, path.name, sha, page_count, out_id],
        )
        doc_id = out_id.getvalue()[0]

        data = [
            (doc_id, i + 1, r["chapter"][:300], r["section"][:500], r["page_start"], r["page_end"],
             r["text"], len(r["text"]), array.array("f", vec), EMBEDDING_MODEL)
            for i, (r, vec) in enumerate(zip(rows, vectors))
        ]
        cur.executemany(
            """INSERT INTO doc_chunks (doc_id, chunk_no, chapter, section, page_start, page_end,
                                       chunk_text, char_count, embedding, embedding_model)
               VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10)""",
            data,
        )
    conn.commit()
    return True


# ----------------------------- main -----------------------------
def ingest_file(path, source_name, model, force):
    import fitz                                       # PyMuPDF
    from db_connection import get_connection

    log.info("Reading %s", path.name)
    sha = _sha256(path)
    with fitz.open(path) as doc:
        rows = build_chunks(doc, fallback_chapter=path.stem)
        page_count = len(doc)
    if not rows:
        log.warning("No text found in %s (scanned PDF? it needs OCR first)", path.name)
        return
    sizes = [len(r["text"]) for r in rows]
    log.info("%d chunks from %d pages (chars per chunk: min %d, avg %d, max %d)",
             len(rows), page_count, min(sizes), sum(sizes) // len(sizes), max(sizes))

    log.info("Embedding with %s ...", EMBEDDING_MODEL)
    vectors = model.encode([r["text"] for r in rows], batch_size=EMBED_BATCH,
                           normalize_embeddings=True, show_progress_bar=True).tolist()

    with get_connection() as conn:
        if store(conn, source_name, path, sha, page_count, rows, vectors, force):
            log.info("Loaded %s into DOC_CHUNKS", path.name)


def main():
    ap = argparse.ArgumentParser(description="Load PDF(s) into SANDBOX.DOC_CHUNKS")
    ap.add_argument("path", help="a PDF file, or a folder containing PDFs")
    ap.add_argument("--source-name", default="NCATS Assay Guidance Manual")
    ap.add_argument("--force", action="store_true", help="re-load even if the file is unchanged")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    p = Path(args.path)
    files = sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    if not files or not all(f.is_file() for f in files):
        sys.exit(f"No PDF found at {p}")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)
    model.max_seq_length = MAX_SEQ_TOKENS

    for f in files:
        ingest_file(f, args.source_name, model, args.force)


if __name__ == "__main__":
    main()
