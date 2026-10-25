# GoDaddy to Cloudflare DNS migration

The domain (heyellipsis.in) was registered with GoDaddy, but authoritative DNS was moved to Cloudflare to support the application’s public routing setup.

## Order of operations

1. Domain was managed through GoDaddy.
2. Domain was added to Cloudflare.
3. Existing DNS records were **migrated from GoDaddy to Cloudflare before any nameserver changes**.
4. GoDaddy nameservers were then changed to the nameservers assigned by Cloudflare.
5. Cloudflare became the DNS-management provider; GoDaddy remained the registrar.
6. Cloudflare Tunnel was used for public app access.

## Conceptual delegation

```mermaid
flowchart LR
 R[GoDaddy domain registrar] -->|delegation updated| N[Cloudflare authoritative nameservers]
 N --> D[Cloudflare DNS zone]
 D --> H[Application hostname / tunnel mapping]
```

**Why order matters:** records should exist in the new authoritative zone before switching delegation, to avoid losing services such as the website or mail. Existing records should be reviewed individually for proxy settings and mail-related compatibility.
