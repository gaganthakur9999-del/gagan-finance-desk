"""
sync_v2_ui.py - Phase-5 Sync V2 status & conflict PRESENTATION (Streamlit).

Presentation only: never starts a sync session, never calls engine.run_once(),
and never touches Neon. The engine is only used for read-only conflict listing
and the existing Phase-4 resolution backend when the user explicitly chooses an
action.
"""
from datetime import datetime

import streamlit as st

import sync_v2_state as state
from syncv2 import protocol as P

_DOT = {
    state.STATUS_SYNCED: ("#2e9e5b", "●"),
    state.STATUS_SYNCING: ("#d9822b", "●"),
    state.STATUS_OFFLINE: ("#d9822b", "●"),
    state.STATUS_NEEDS_ATTENTION: ("#d9822b", "●"),
    state.STATUS_CONFLICT: ("#d64545", "●"),
    state.STATUS_ERROR: ("#d64545", "●"),
    state.STATUS_BUSY: ("#2e9e5b", "◐"),
    state.STATUS_READY: ("#7f8c9b", "○"),
}


def status_markup(status):
    colour, dot = _DOT.get(status, ("#7f8c9b", "●"))
    return '<span style="color:%s">%s</span> %s' % (colour, dot,
                                                    state.status_label(status))


def flash():
    msg = st.session_state.pop("sv2_flash", None)
    if msg:
        if msg.startswith("Error"):
            st.error(msg)
        else:
            st.success(msg)


def _month_label(value):
    try:
        month, year = (value or "").split("_")
        return datetime.strptime(month, "%B").strftime("%B %Y")
    except (ValueError, AttributeError):
        return value or ""


def _record_title(view):
    bits = [view["label"]]
    if view["detail"]:
        bits.append("Invoice %s" % view["detail"])
    return " — ".join(bits)


def _collect_views(engine, db_path):
    """Read-only collection: server conflict rows + local record display info."""
    rows = engine.get_open_conflicts() if engine is not None else []
    return state.build_conflict_views(rows,
                                      lambda sid: state.read_local_record(db_path, sid))


def render_settings_section(db_path, engine=None, sync_running=False):
    """Settings-page Sync V2 status block (transitional; distinct from Old Sync).

    Never starts synchronization. When no engine/server is attached the section
    shows local readiness only.
    """
    flash()
    local = state.read_local_sync_status(db_path)
    if engine is None:
        # Transitional phase: without a connected Sync V2 service the section
        # shows local readiness only and never claims Sync V2 is active.
        status = state.STATUS_READY
    else:
        status = state.classify_status(local, engine_busy=True,
                                       sync_running=sync_running)
    st.markdown("### 🔄 Sync V2 — Status")
    st.caption("New synchronisation preview. The classic **Sync Now** above is "
               "unchanged and still controls the old sync.")
    st.markdown("**%s**" % status_markup(status))

    detail_state = local.get("state") or {}
    with st.expander("Sync V2 status details", expanded=False):
        last_sync = state.format_last_sync(detail_state.get("last_success_at"))
        if last_sync:
            st.write("Last successful sync: **%s** (%s)" % (
                last_sync, state.human_ago(detail_state.get("last_success_at"))))
        else:
            st.write("No Sync V2 sync has run yet.")
        outbox = local.get("outbox") or {}
        pending = (outbox.get("pending", 0) or 0) + (outbox.get("in_flight", 0) or 0)
        st.write("Changes saved locally, not yet pushed: **%d**" % pending)
        if detail_state.get("last_error"):
            st.write("Last sync note: %s" % detail_state.get("last_error"))
        if engine is None:
            st.caption("Sync V2 is not connected yet. Conflict review becomes "
                       "available in a later phase.")
        else:
            conflicts = engine.get_open_conflicts()
            st.write("Conflicts needing review: **%d**" % len(conflicts))

    if engine is not None:
        st.caption("Review and resolve below. Sync V2 is not run automatically.")
        views = _collect_views(engine, db_path)
        if views:
            st.markdown("#### Records needing attention")
            render_attention_center(engine, db_path)
        else:
            st.write("No records currently need attention.")


def render_offline_warning(st_=None, last_sync_text=None, dismiss_key="sv2_offline",
                           on_retry=None):
    """Polished offline dialog. Must NOT gate local record workflows.

    Retry invokes the caller's sync callback (never a hidden background sync).
    Continue Offline simply dismisses; the warning is not re-shown once dismissed
    in this session.
    """
    if st.session_state.get(dismiss_key):
        return
    last_line = ""
    if last_sync_text:
        last_line = " Last successful sync: %s." % last_sync_text
    st.markdown(
        "<div style='border:1px solid #444;border-radius:8px;padding:12px 16px'>"
        "<b>You're offline.</b><br/>"
        "Changes will continue to be saved locally. Remote changes may not be "
        "available until the connection returns.%s</div>" % last_line,
        unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Retry", key=dismiss_key + "_retry", width="stretch",
                     type="primary"):
            if on_retry is not None:
                on_retry()
            else:
                st.session_state[dismiss_key] = False
    with c2:
        if st.button("Continue Offline", key=dismiss_key + "_dismiss",
                     width="stretch"):
            st.session_state[dismiss_key] = True


def _resolve_action(engine, conflict_id, choice, payload=None, label="Resolution"):
    try:
        result = engine.resolve_conflict(conflict_id, choice, payload)
    except Exception as exc:  # noqa: BLE001 - user-facing error only
        st.session_state["sv2_flash"] = "Error: %s" % exc
        st.rerun()
        return
    if result.get("reopened"):
        st.session_state["sv2_flash"] = ("%s could not be applied because the "
                                         "record changed again - please review "
                                         "again." % label)
    elif result.get("converged") is True:
        st.session_state["sv2_flash"] = "%s applied and records converged." % label
    elif result.get("converged") is False:
        st.session_state["sv2_flash"] = ("%s applied. This record still has other "
                                         "conflicts to review." % label)
    else:
        st.session_state["sv2_flash"] = "%s applied." % label
    st.rerun()


def _render_field_conflict(engine, conflict):
    st.markdown("**%s** — %s" % (conflict["field_label"], conflict["kind_label"]))
    cols = st.columns(3)
    with cols[0]:
        st.caption("Previous")
        st.write(conflict["base"] or "—")
    with cols[1]:
        st.caption("Offline")
        st.write(conflict["offline"] or "—")
    with cols[2]:
        st.caption("Online")
        st.write(conflict["online"] or "—")
    key = "sv2_%s" % conflict["id"]
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Keep Offline", key=key + "_off", width="stretch",
                     type="secondary"):
            _resolve_action(engine, conflict["id"], "KEEP_OFFLINE", label="Keep Offline")
    with b2:
        if st.button("Keep Online", key=key + "_on", width="stretch",
                     type="secondary"):
            _resolve_action(engine, conflict["id"], "KEEP_ONLINE", label="Keep Online")
    with b3:
        if st.button("Review & Merge", key=key + "_merge", width="stretch",
                     type="primary"):
            st.session_state["sv2_merge_" + str(conflict["id"])] = True
    if st.session_state.get("sv2_merge_" + str(conflict["id"])):
        st.caption("Choose the final value explicitly. It is applied on both sides.")
        default = conflict["online"] or conflict["offline"] or ""
        value = st.text_input("Final %s" % conflict["field_label"], value=default,
                              key=key + "_merge_value")
def _sequence_lines(entries):
    return ["%d. %s" % (e["position"], e["label"]) for e in entries]


def _render_sr_conflict(engine, sr):
    st.markdown("**Ordering conflict — %s**" % _month_label(sr["month"]))
    st.caption("Records were reordered differently on each side.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Offline order**")
        st.write("\n".join(_sequence_lines(sr["offline_seq"])) or "—")
    with c2:
        st.markdown("**Online order**")
        st.write("\n".join(_sequence_lines(sr["online_seq"])) or "—")
    key = "sv2_sr_%s" % sr["id"]
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Keep Offline Order", key=key + "_off", width="stretch",
                     type="secondary"):
            _resolve_action(engine, sr["id"], "KEEP_OFFLINE", label="Keep Offline Order")
    with b2:
        if st.button("Keep Online Order", key=key + "_on", width="stretch",
                     type="secondary"):
            _resolve_action(engine, sr["id"], "KEEP_ONLINE", label="Keep Online Order")
    with b3:
        if st.button("Review / Custom Order", key=key + "_custom", width="stretch",
                     type="primary"):
            st.session_state["sv2_sr_custom_" + str(sr["id"])] = True
    if st.session_state.get("sv2_sr_custom_" + str(sr["id"])):
        by_label = {}
        for entry in sr["offline_seq"] + sr["online_seq"]:
            by_label[entry["label"].strip().upper()] = entry["sync_id"]
        default_lines = _sequence_lines(sr["offline_seq"])
        text = st.text_area("Enter the final order (one customer per line)",
                            value="\n".join(default_lines), height=120,
                            key=key + "_custom_text")
        if st.button("Apply Custom Order", key=key + "_apply", type="primary"):
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            seq = []
            ok = True
            for ln in lines:
                label = ln.split(".", 1)[-1].strip().upper()
                if label not in by_label:
                    ok = False
                    break
                seq.append(by_label[label])
            if not ok or len(seq) != len(by_label):
                st.session_state["sv2_flash"] = ("Custom order must list every "
                                                 "record once - please review.")
                st.rerun()
            else:
                _resolve_action(engine, sr["id"], "MERGE", {"seq": seq},
                                label="Custom Order")


def _render_delete_conflict(engine, view, conflict):
    st.markdown("**Deleted vs changed**")
    st.caption("One side deleted this record while the other side changed it.")
    if conflict.get("online_value"):
        st.caption("Online kept the record deleted.")
    key = "sv2_del_%s" % conflict["id"]
    b1, b2 = st.columns(2)
    with b1:
        if st.button("Keep Offline", key=key + "_off", width="stretch",
                     type="secondary"):
            _resolve_action(engine, conflict["id"], "KEEP_OFFLINE",
                            label="Keep Offline (restore/keep)")
    with b2:
        if st.button("Keep Online", key=key + "_on", width="stretch",
                     type="secondary"):
            _resolve_action(engine, conflict["id"], "KEEP_ONLINE",
                            label="Keep Online (keep deletion)")


def _render_invoice_collision(view, collision):
    st.markdown("**Same invoice number on multiple records**")
    st.caption("The records were NOT merged. Review them and update one of the "
               "invoice numbers if they are genuinely different records.")
    st.write("Invoice: **%s**" % (collision.get("invoice") or collision.get("online_value")
                                  or "—"))


def render_attention_center(engine, db_path):
    """Render the compact attention list and full conflict review UI."""
    views = _collect_views(engine, db_path)
    if not views:
        st.write("No records currently need attention.")
        return
    st.caption("Select a record to review its conflicts.")
    options = []
    for v in views:
        suffix = ""
        for typ in v["conflict_types"]:
            suffix += " · %s" % typ
        options.append("%s%s" % (_record_title(v), suffix))
    choice = st.selectbox("Record with conflicts", options, key="sv2_record_choice")
    chosen = views[options.index(choice)] if options else None
    if chosen is None:
        return
    n = chosen["conflict_count"]
    st.markdown("**%s** — %d conflict%s" % (_record_title(chosen), n,
                                            "" if n == 1 else "s"))
    with st.expander("Review this record", expanded=True):
        for sr in chosen["sr_conflicts"]:
            _render_sr_conflict(engine, sr)
            st.divider()
        for delete in chosen["delete_conflicts"]:
            _render_delete_conflict(engine, chosen, delete)
            st.divider()
        for collision in chosen["invoice_collisions"]:
            _render_invoice_collision(chosen, collision)
            st.divider()
        for field in chosen["field_conflicts"]:
            _render_field_conflict(engine, field)
            st.divider()



