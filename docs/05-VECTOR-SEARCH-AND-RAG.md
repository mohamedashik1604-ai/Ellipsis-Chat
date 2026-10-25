# Oracle AI Vector Search and RAG

## Retrieval settings

| Setting | Value |
|---|---|
| `TOP_K` | 6 |
| `MAX_DIST` | 0.65 cosine distance |
| `MAX_CONTEXT_CHARS` | 7,000 |
| Query embedding model | same 384-dim MiniLM model as ingestion |
| Query rewrite history | last 4 messages |
| Rewrite model | `openai/gpt-oss-20b` |

## Oracle nearest-neighbor lookup

The `db_connection.py` builds a query of this form :

```sql
SELECT chunk_id, chapter, section, page_start, page_end, chunk_text,
       VECTOR_DISTANCE(embedding, :q, COSINE) AS dist
FROM doc_chunks
WHERE embedding_model = :model
ORDER BY dist
FETCH APPROX FIRST 6 ROWS ONLY;
```

The actual function also excludes headings ending in `References` by default and filters results in Python to `distance <= 0.65`. The bind vector uses `array.array("f", query_vec)`.

## Answer grounding

`rag.py` optionally rewrites follow-up queries using recent chat history, embeds the standalone query, gets nearest matching passages, formats them as `[1] Section (pp. X–Y)` excerpts, and sends them to the main application. RAG instructions tell the LLM to support factual statements with these numbered passages and to disclose when retrieved text is insufficient. The `MESSAGE_SOURCES` table stores chunk IDs, rank numbers, and distances corresponding to generated answers.

### UI evidence

![RAG response demonstrating citations](../screenshots/streamlit/03-rag-response-with-citations.png)

*Figure: “Hill slope in bioassay” answer with citation indicators.*
