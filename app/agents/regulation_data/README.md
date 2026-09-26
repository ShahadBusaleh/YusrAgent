# Regulation update agent — data files

- `digicert_global_g2_tls_rsa_sha256_2020_ca1.pem` — public DigiCert intermediate
  certificate (valid to 2031-03-29). laws.boe.gov.sa does not send it, so TLS
  verification fails without it. It is ADDED to the certifi bundle; verification
  stays on. Source: http://cacerts.digicert.com/DigiCertGlobalG2TLSRSASHA2562020CA1-1.crt
- `simulated_*.json` — SIMULATED data for the demo (REGULATION_SOURCE=simulated).
  The amendment is fictional and is never presented as real law. Simulate mode
  only runs against a demo DB copy, a demo Qdrant collection and a demo
  policy-text folder (see TEAM.md, Phase 4).
- `boe_labor_law_excerpt.html` — a verbatim excerpt of the official Labor Law
  page (5 article blocks, fetched 2026-09-26), used only by the parser tests.
