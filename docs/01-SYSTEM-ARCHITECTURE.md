# System architecture

## Online request flow

```mermaid
sequenceDiagram
    participant User
    participant UI as Streamlit
    participant DB as Oracle
    participant RAG as rag.py
    participant Groq
    User->>UI: Enter question
    UI->>DB: Read/create conversation
    UI->>RAG: retrieve_context(client, history, question)
    opt Follow-up history exists
      RAG->>Groq: Rewrite question as standalone search query
      Groq-->>RAG: Retrieval query
    end
    RAG->>RAG: Encode query using MiniLM-L12-v2
    RAG->>DB: VECTOR_DISTANCE(embedding, query, COSINE)
    DB-->>RAG: Up to 6 nearby chunks + metadata
    RAG-->>UI: Numbered excerpts + source metadata
    UI->>Groq: System instructions + retrieved context + user message
    Groq-->>UI: Answer with [n] citations
    UI->>DB: Store messages and message_sources
    UI-->>User: Render answer + source label
```

## Offline document preparation

PDF → PyMuPDF text blocks → cleaned paragraphs → bookmark-derived section paths → overlapping chunks → 384-dimension embeddings → `DOCUMENTS` and `DOC_CHUNKS`.

## Infrastructure boundary

A GoDaddy-registered domain (heyellipsis.in) is delegated to Cloudflare DNS and Cloudflare Tunnel exposes the local Streamlit service. 

## Persistence

`APP_USERS → CONVERSATIONS → MESSAGES → MESSAGE_SOURCES ← DOC_CHUNKS ← DOCUMENTS`. See [schema chapter](04-DATABASE-SCHEMA.md).
