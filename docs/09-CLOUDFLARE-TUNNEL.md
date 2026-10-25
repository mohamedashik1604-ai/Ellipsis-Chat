# Cloudflare Tunnel

**Deployment method:** Cloudflare Tunnel enables a public hostname to reach a Streamlit instance running behind a private network boundary through outbound `cloudflared` connections.

```mermaid
flowchart LR
 U[Internet user] --> C[Cloudflare edge]
 C --> T[Cloudflare Tunnel connector]
 T --> S[Local Streamlit service]
 S --> O[(Oracle backend)]
```

## Illustrative tunnel routing

```yaml
# public hostname: heyellipsis.in
# service: http://127.0.0.1:8501
```
