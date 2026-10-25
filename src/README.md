# Source code (public)

These files represent the actual implementation and are published as **reference source**, not as an automated GitHub deployment.

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI, login, conversations, model calls |
| `db_connection.py` | Oracle connection pool, users, messages, cosine vector retrieval |
| `ingest.py` | One-off PDF ingestion and Sentence Transformer embeddings |
| `rag.py` | Query rewrite, embeddings, retrieved excerpts and citation context |
