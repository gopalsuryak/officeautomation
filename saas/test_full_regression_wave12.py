import os
import tempfile


def run():
    temp_dir = tempfile.mkdtemp(prefix="ca_assist_wave12_")
    db_path = os.path.join(temp_dir, "wave12_regression.db")
    os.environ["DB_PATH"] = db_path

    import db
    import ai_sync
    import client_entities
    import compliance_tasks
    import document_workflow
    import review_workflow
    import usage

    db.init_db()

    with db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, name, firm_name)
            VALUES (?, ?, ?, ?)
            """,
            ("wave12@example.com", "test-hash", "Wave Tester", "Wave12 CA Firm"),
        )
        user_id = conn.execute("SELECT id FROM users WHERE email=?", ("wave12@example.com",)).fetchone()["id"]

        conn.execute(
            """
            INSERT INTO tenants (user_id, plan, status)
            VALUES (?, ?, ?)
            """,
            (user_id, "starter", "active"),
        )
        tenant_id = conn.execute("SELECT id FROM tenants WHERE user_id=?", (user_id,)).fetchone()["id"]

    db.ensure_owner_firm_user(user_id, tenant_id)

    client = client_entities.create_client_entity(
        tenant_id=tenant_id,
        data={
            "name": "ABC Pvt Ltd",
            "entity_type": "pvt_ltd",
            "pan": "ABCDE1234F",
            "gstin": "22ABCDE1234F1Z5",
            "email": "accounts@abc.example",
        },
        user_id=user_id,
        ip_address="127.0.0.1",
    )

    task = compliance_tasks.create_compliance_task(
        tenant_id=tenant_id,
        data={
            "client_entity_id": client["id"],
            "task_type": "gstr3b",
            "title": "GSTR-3B for ABC - Apr 2026",
            "period": "Apr 2026",
            "financial_year": "2026-27",
            "priority": "normal",
        },
        user_id=user_id,
        ip_address="127.0.0.1",
    )

    created_request = document_workflow.add_document_request(
        tenant_id=tenant_id,
        task_id=task["id"],
        document_name="Sales Register",
        description="Month-wise sales register",
        requested_from="client",
        user_id=user_id,
        ip_address="127.0.0.1",
    )

    updated_request = document_workflow.update_document_request_status(
        tenant_id=tenant_id,
        request_id=created_request["id"],
        new_status="received",
        user_id=user_id,
        note="Received over email",
        ip_address="127.0.0.1",
    )

    normalized = ai_sync.normalize_ai_output_for_db(
        {
            "status_recommendation": "review_required",
            "confidence": "medium",
            "missing_inputs": [],
            "risk_flags": ["Input mismatch check required"],
            "applicable_laws": ["GST Section 39"],
            "document_requests": [],
            "client_message_draft": "Draft prepared.",
            "internal_working_note": "Reconcile outward supplies before filing.",
            "final_output_markdown": "Prepared a draft for review.",
        }
    )

    with db.get_db() as conn:
        ai_output_id = ai_sync.insert_ai_output(
            conn=conn,
            tenant_id=tenant_id,
            task_id=task["id"],
            normalized=normalized,
            provider="test",
            model="test-model",
            prompt_version="wave12_test",
            paperclip_comment_id=None,
        )
        conn.execute(
            """
            UPDATE compliance_tasks
            SET status = 'under_review', pending_from = 'reviewer'
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, task["id"]),
        )
        db.touch_updated_at(conn, "compliance_tasks", task["id"])

    reviewed = review_workflow.perform_review_action(
        tenant_id=tenant_id,
        task_id=task["id"],
        action="approve",
        user_id=user_id,
        comment="Looks good.",
        ai_output_id=ai_output_id,
        ip_address="127.0.0.1",
    )

    usage_summary = usage.get_usage_summary(tenant_id)

    assert client is not None
    assert task is not None
    assert created_request is not None
    assert updated_request is not None
    assert ai_output_id is not None
    assert reviewed is not None
    assert usage_summary["counts"]["clients_count"] >= 1

    print("Wave 12 full regression test passed.")


if __name__ == "__main__":
    run()
