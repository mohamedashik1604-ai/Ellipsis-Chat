# Groq LLM integration

The application constructs a Groq SDK client using the `GROQ_API_KEY` environment variable.

| Task | Model identifier in source |
|---|---|
| Main assistant replies | `qwen/qwen3.8-27b` |
| Auto-generated conversation titles | `qwen/qwen3.8-27b` |
| Follow-up query rewriting | `openai/gpt-oss-20b` |

The system prompt frames Ellipsis as an assay-development assistant; `RAG_INSTRUCTIONS` direct answers to use supplied AGM passages and cite `[n]` references. Query rewriting helps resolve follow-ups such as “what about its limitations?” to standalone search questions by including recent conversation messages.
