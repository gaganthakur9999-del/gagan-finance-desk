"""
syncv2/protocol.py - shared protocol vocabulary for the Phase-4 sync engine.

Constants, field classification and structured result types only - no DB code,
no UI code, and NO streamlit import. Kept import-light so every module in this
package can depend on it freely.
"""

# ---------------------------------------------------------------- business fields
# The 19 business fields that participate in synchronization. `month` is present in
# the baseline but is DERIVED from bid_date and is never merged as independent
# authoritative business data (see merge.month_from_bid_date).
BUSINESS_FIELDS = [
    "sr_no", "bid_date", "invoice_no", "name", "xcell", "product", "serial_no",
    "price", "emi", "di", "bid", "dp_taken", "scheme", "actual_product",
    "given_prod_price", "phone", "alt_phone", "month", "remarks",
]
SYSTEM_FIELDS = ["id", "sync_id", "row_rev", "server_rev", "base_json",
                 "created_at", "updated_at", "deleted_at"]

# Safe independently mergeable fields (different fields may union).
SAFE_MERGE_FIELDS = {
    "name", "phone", "alt_phone", "product", "actual_product", "xcell",
    "remarks", "bid_date",
}
# Financial fields: different fields may merge; the SAME field changed
# differently on both sides is a CONFLICT (never averaged, never auto-chosen).
FINANCIAL_FIELDS = {"price", "emi", "di", "dp_taken", "given_prod_price"}
# Ordinary mergeable field - duplicates may be advisory, never identity.
BID_FIELD = "bid"
# Serial is ordinary business data (identity is sync_id); divergent => conflict.
SERIAL_FIELD = "serial_no"
# Invoice: special business-key handling (collision engine, never identity).
INVOICE_FIELD = "invoice_no"
# SR: ordering semantics with month-scoped grouped conflicts.
SR_FIELD = "sr_no"
MONTH_FIELD = "month"

# Fields excluded from three-way field merge entirely.
NON_MERGED_FIELDS = {"invoice_no", "sr_no", "month", "deleted_at"}

# ---------------------------------------------------------------- op types
OP_UPSERT = "upsert"          # create or update of a record's business state
OP_DELETE = "delete"          # tombstone: set deleted_at, never physical delete

# ---------------------------------------------------------------- outbox lifecycle
OUTBOX_PENDING = "pending"
OUTBOX_IN_FLIGHT = "in_flight"
OUTBOX_APPLIED = "applied"
OUTBOX_SUPERSEDED = "superseded"
OUTBOX_FAILED = "failed"
# Extension status (documented): op is parked because an open blocking conflict
# exists for its sync_id; it is NOT resent while the conflict is open.
OUTBOX_BLOCKED = "blocked"

OUTBOX_ACTIVE = (OUTBOX_PENDING, OUTBOX_IN_FLIGHT)

# ---------------------------------------------------------------- conflict kinds
CONFLICT_FIELD = "field"            # same business field diverged both sides
CONFLICT_FINANCIAL = "financial"    # same financial field diverged both sides
CONFLICT_SERIAL = "serial"          # serial diverged both sides
CONFLICT_DELETE_EDIT = "delete_edit"  # delete vs edit (either direction)
CONFLICT_SR_ORDER = "sr_ordering"   # month-scoped grouped ordering conflict
CONFLICT_INVOICE = "invoice_collision"  # advisory business-key collision (non-blocking)
CONFLICT_IMPOSSIBLE = "impossible_state"  # base_rev > server_rev etc.

CONFLICT_STATUS_OPEN = "open"
CONFLICT_STATUS_RESOLVED = "resolved"

BLOCKING_CONFLICT_KINDS = {CONFLICT_FIELD, CONFLICT_FINANCIAL, CONFLICT_SERIAL,
                           CONFLICT_DELETE_EDIT, CONFLICT_SR_ORDER,
                           CONFLICT_IMPOSSIBLE}

# ---------------------------------------------------------------- sync session
SESSION_IDLE = "IDLE"
SESSION_CONNECTING = "CONNECTING"
SESSION_PULL = "PULL"
SESSION_MERGE = "MERGE"
SESSION_PUSH = "PUSH"
SESSION_FINALIZE = "FINALIZE"
SESSION_SUCCESS = "SUCCESS"
SESSION_OFFLINE = "OFFLINE"
SESSION_ERROR = "ERROR"
SESSION_CONFLICT = "CONFLICT"
SESSION_NEEDS_ATTENTION = "NEEDS_ATTENTION"
SESSION_BUSY = "BUSY"

# ---------------------------------------------------------------- server revision
# sync_sequence.value semantics:
#   0                = bootstrap baseline (Phase 3). No coordination change issued.
#   N (>0)           = the N-th committed server synchronization state change.
# A pull of "changes after R" returns rows whose records.server_rev > R.
BOOTSTRAP_REVISION = 0


class SyncResult:
    """Structured, machine-readable outcome of a sync operation (not UI strings)."""

    def __init__(self, status=SESSION_SUCCESS, pulled=0, merged=0, pushed=0,
                 conflicts=0, failed=0, changed=False, message="", revision=None,
                 details=None):
        self.status = status
        self.pulled = pulled          # rows returned by incremental pull
        self.merged = merged          # client-side three-way merges completed
        self.pushed = pushed          # outbox ops applied on the server
        self.conflicts = conflicts    # conflicts opened (blocking + advisory)
        self.failed = failed          # permanent failures needing attention
        self.changed = bool(changed)  # "records_changed" signal for cache invalidation
        self.message = message
        self.revision = revision      # server revision watermark after this run
        self.details = details or {}

    def as_dict(self):
        return {k: getattr(self, k) for k in (
            "status", "pulled", "merged", "pushed", "conflicts", "failed",
            "changed", "message", "revision", "details")}

    def __repr__(self):
        return "SyncResult(%s)" % ", ".join(
            "%s=%s" % (k, v) for k, v in self.as_dict().items() if v not in (None, "", {}, 0, False))
