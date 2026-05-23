import db

REVIEW_ACTIONS = [
    "send_for_review",
    "approve",
    "request_changes",
    "mark_filed",
    "close",
    "cancel",
]

ACTION_LABELS = {
    "send_for_review": "Send for Review",
    "approve": "Approve Draft",
    "request_changes": "Request Changes",
    "mark_filed": "Mark Filed",
    "close": "Close Task",
    "cancel": "Cancel Task",
}

ACTION_TO_STATUS = {
    "send_for_review": "under_review",
    "approve": "approved",
    "request_changes": "changes_required",
    "mark_filed": "filed",
    "close": "closed",
    "cancel": "cancelled",
}

VALID_ACTIONS_BY_STATUS = {
    "ai_draft_ready": ["send_for_review", "request_changes", "cancel"],
    "under_review": ["approve", "request_changes", "cancel"],
    "changes_required": ["send_for_review", "cancel"],
    "approved": ["mark_filed", "close"],
    "filed": ["close"],
    "draft": ["cancel"],
    "pending_documents": ["cancel"],
    "ready_for_ai": ["cancel"],
    "ai_queued": ["cancel"],
    "ai_processing": ["cancel"],
    "ai_failed": ["cancel"],
    "closed": [],
    "cancelled": [],
}

_PENDING_FROM_BY_STATUS = {
    "under_review": "reviewer",
    "approved": "none",
    "filed": "none",
    "closed": "none",
    "changes_required": "staff",
    "cancelled": "none",
}


def get_available_review_actions(status):
    current = (status or "").strip().lower()
    actions = VALID_ACTIONS_BY_STATUS.get(current, [])
    return [
        {
            "action": action,
            "label": ACTION_LABELS.get(action, action),
            "target_status": ACTION_TO_STATUS.get(action),
        }
        for action in actions
    ]


def perform_review_action(
    tenant_id,
    task_id,
    action,
    user_id=None,
    comment=None,
    ai_output_id=None,
    ip_address=None,
):
    action_key = (action or "").strip().lower()
    if action_key not in REVIEW_ACTIONS:
        raise ValueError("Invalid review action.")

    with db.get_db() as conn:
        task = conn.execute(
            """
            SELECT * FROM compliance_tasks
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()
        if not task:
            return None

        old_status = task["status"]
        allowed = VALID_ACTIONS_BY_STATUS.get(old_status, [])
        if action_key not in allowed:
            raise ValueError(
                f"Action '{ACTION_LABELS.get(action_key, action_key)}' is not allowed when task is '{old_status}'."
            )

        new_status = ACTION_TO_STATUS[action_key]
        new_pending_from = _PENDING_FROM_BY_STATUS.get(new_status, task["pending_from"] or "staff")

        clean_comment = (comment or "").strip() or None

        conn.execute(
            """
            INSERT INTO review_actions
                (tenant_id, task_id, ai_output_id, reviewer_user_id, action, comment)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, ai_output_id, user_id, action_key, clean_comment),
        )

        conn.execute(
            """
            UPDATE compliance_tasks
            SET status = ?, pending_from = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (new_status, new_pending_from, tenant_id, task_id),
        )
        db.touch_updated_at(conn, "compliance_tasks", task_id)

        conn.execute(
            """
            INSERT INTO task_status_history
                (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, old_status, new_status, user_id, clean_comment),
        )

        action_label = ACTION_LABELS.get(action_key, action_key)
        system_comment = f"Review action: {action_label}."
        if clean_comment:
            system_comment += f" {clean_comment}"
        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, system_comment),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="review_action_performed",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value={"status": old_status, "pending_from": task["pending_from"]},
            new_value={"status": new_status, "pending_from": new_pending_from, "review_action": action_key},
            metadata={"comment": clean_comment, "ai_output_id": ai_output_id},
            ip_address=ip_address,
        )

        updated = conn.execute(
            """
            SELECT * FROM compliance_tasks
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()

    return updated


def get_review_actions_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM review_actions
            WHERE tenant_id = ? AND task_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()


def get_latest_ai_output_id_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM ai_outputs
            WHERE tenant_id = ? AND task_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()
    return row["id"] if row else None
