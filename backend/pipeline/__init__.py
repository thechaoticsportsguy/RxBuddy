"""
RxBuddy Pipeline v2 — Modular, deterministic, fast.

Architecture:
  1. classify_fast()       → zero-AI intent classification
  2. check_cache()         → PostgreSQL 7-day TTL cache
  3. extract_drugs()       → drug extraction + RxNorm normalization
  4. fetch_all_apis()      → async parallel API fetching (≤1.5s timeout each)
  5. compute_verdict()     → deterministic backend decision (PRIMARY TRUTH)
  6. generate_explanation() → Claude generates explanation text ONLY
  7. enforce_verdict()     → hard verdict enforcement (overwrites Claude)
  8. clean_response()      → max 2 sentences, no fluff, no markdown
  9. cache_result()        → store in PostgreSQL
 10. return response

Claude NEVER decides the verdict. The backend decision engine is the
single source of truth for SAFE / CAUTION / AVOID / CONSULT_PHARMACIST.
"""
