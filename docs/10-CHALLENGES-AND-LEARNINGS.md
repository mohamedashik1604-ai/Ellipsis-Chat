# Challenges, limitations and learnings

This chapter distinguishes **observed engineering decisions**. 

- **Domain delegation:** copying DNS records before updating nameservers reduces the risk of missing records during delegation changes.
- **Keeping application private:** Cloudflare Tunnel avoids requiring public inbound forwarding to the Streamlit host.
- **Document provenance:** section paths and page ranges make retrieval results easier to trace to source PDF sections.
- **Chunk design:** 800-target / 1000-max characters, 150 overlap and a 256-token embedding ceiling attempt to balance retrieval granularity and context length.
- **Retrieval quality:** a top-6 cosine filter at 0.65 and bibliography-section exclusion are implemented heuristics, not measured accuracy guarantees.
- **Audit trail lifecycle:** `MESSAGE_SOURCES` links retrieval to messages, but cascading deletes can remove that provenance.
- **Deployment portability:** source contained a machine-specific Windows assets path.
