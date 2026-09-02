"""
syncv2 - Phase-4 synchronization engine package.

Pure Python. NEVER imports streamlit. Modules:
  protocol  - vocabulary, field classification, structured results
  merge     - three-way merge primitives (pure)
  store     - dual-backend persistence primitives
  server    - synchronization coordinator (revisions, idempotency, conflicts)
  engine    - SyncEngine client orchestration (pull/merge/push/finalize)
  retry     - retry scheduling helpers
"""
