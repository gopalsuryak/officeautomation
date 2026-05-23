"""
SMTP Email Sender — Manual SMTP Sending for Approved Queue Items
CA Assist (Phase: SMTP Manual Send Worker)

This module handles the actual SMTP transmission of approved email queue items.
Only approved_to_send items with active SMTP providers can be sent.
No automatic retry, no bulk send, no background worker.
Manual send only with explicit final confirmation.
"""

import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone

import credential_vault
import db
import email_queue
import email_provider_settings


def build_smtp_message(queue_item, provider):
    """
    Build an EmailMessage object from queue item and provider.
    
    Args:
        queue_item: dict with to_email, cc_email, bcc_email, subject, body
        provider: dict with from_email, from_name
    
    Returns:
        EmailMessage object
    """
    if not queue_item or not provider:
        raise ValueError("Queue item and provider are required")
    
    msg = EmailMessage()
    
    # Set From
    from_email = provider.get("from_email")
    if not from_email:
        raise ValueError("Provider from_email is required")
    
    from_name = provider.get("from_name")
    if from_name:
        msg["From"] = f"{from_name} <{from_email}>"
    else:
        msg["From"] = from_email
    
    # Set To
    to_email = queue_item.get("to_email")
    if not to_email:
        raise ValueError("Queue item to_email is required")
    msg["To"] = to_email
    
    # Set CC if present
    cc_email = queue_item.get("cc_email")
    if cc_email:
        msg["Cc"] = cc_email
    
    # Set BCC if present
    bcc_email = queue_item.get("bcc_email")
    if bcc_email:
        msg["Bcc"] = bcc_email
    
    # Set Subject
    subject = queue_item.get("subject")
    if not subject:
        raise ValueError("Queue item subject is required")
    msg["Subject"] = subject
    
    # Set Body (plain text only for now)
    body = queue_item.get("body")
    if not body:
        raise ValueError("Queue item body is required")
    msg.set_content(body)
    
    return msg


def validate_send_preconditions(tenant_id, queue_id):
    """
    Verify all preconditions for manual SMTP sending.
    
    Checks:
    1. queue exists
    2. queue.status = approved_to_send
    3. queue.provider_setting_id exists
    4. provider exists
    5. provider.provider_type = smtp
    6. provider.status = active
    7. latest approval exists and approval_status = approved
    8. to_email exists
    9. subject exists
    10. body exists
    11. smtp_host exists
    12. smtp_port exists
    13. smtp_username exists
    14. smtp_password_secret exists
    15. dry-run preview exists for queue item
    
    Returns:
        dict with "ok" (bool) and "errors" (list of strings)
    """
    errors = []
    
    # Check 1: Queue exists
    queue_item = email_queue.get_email_queue_item(tenant_id, queue_id)
    if not queue_item:
        errors.append("Queue item not found")
        return {"ok": False, "errors": errors}
    
    # Check 2: Queue status = approved_to_send
    if queue_item.get("status") != "approved_to_send":
        errors.append(f"Queue status must be 'approved_to_send', got '{queue_item.get('status')}'")
    
    # Check 3: provider_setting_id exists
    provider_setting_id = queue_item.get("provider_setting_id")
    if not provider_setting_id:
        errors.append("No provider assigned to queue item")
    
    # Early exit if no provider
    if not provider_setting_id:
        return {"ok": False, "errors": errors}
    
    # Check 4: Provider exists
    with db.get_db() as conn:
        provider = conn.execute(
            """
            SELECT *
            FROM email_provider_settings
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, provider_setting_id),
        ).fetchone()
        
        if not provider:
            errors.append("Provider not found")
            return {"ok": False, "errors": errors}
        
        provider = dict(provider)
    
    # Check 5: provider.provider_type = smtp
    if provider.get("provider_type") != "smtp":
        errors.append(f"Provider type must be 'smtp', got '{provider.get('provider_type')}'")
    
    # Check 6: provider.status = active
    if provider.get("status") != "active":
        errors.append(f"Provider status must be 'active', got '{provider.get('status')}'")
    
    # Check 7: Latest approval exists and status = approved
    latest_approval = email_queue.get_latest_send_approval_for_queue(tenant_id, queue_id)
    if not latest_approval:
        errors.append("No approval record found for queue item")
    elif latest_approval.get("approval_status") != "approved":
        errors.append(f"Approval must be 'approved', got '{latest_approval.get('approval_status')}'")
    
    # Check 8: to_email exists and not blank
    if not queue_item.get("to_email") or not queue_item.get("to_email").strip():
        errors.append("Recipient email is required")
    
    # Check 9: subject exists and not blank
    if not queue_item.get("subject") or not queue_item.get("subject").strip():
        errors.append("Subject is required")
    
    # Check 10: body exists and not blank
    if not queue_item.get("body") or not queue_item.get("body").strip():
        errors.append("Body is required")
    
    # Check 11: smtp_host exists
    if not provider.get("smtp_host"):
        errors.append("Provider SMTP host is not configured")
    
    # Check 12: smtp_port exists
    if not provider.get("smtp_port"):
        errors.append("Provider SMTP port is not configured")
    
    # Check 13: smtp_username exists
    if not provider.get("smtp_username"):
        errors.append("Provider SMTP username is not configured")
    
    # Check 14: smtp_password_secret exists
    if not provider.get("smtp_password_secret"):
        errors.append("Provider SMTP password is not configured")
    
    # Check 15: Dry-run preview exists for queue item
    with db.get_db() as conn:
        dry_run = conn.execute(
            """
            SELECT id
            FROM email_dry_run_previews
            WHERE tenant_id = ? AND queue_id = ?
            LIMIT 1
            """,
            (tenant_id, queue_id),
        ).fetchone()
        
        if not dry_run:
            errors.append("No dry-run preview exists for this queue item")
    
    if errors:
        return {"ok": False, "errors": errors}
    
    return {"ok": True, "errors": []}


def send_approved_queue_item_via_smtp(tenant_id, queue_id, user_id=None, ip_address=None):
    """
    Execute SMTP send for an approved queue item.
    
    Behavior:
    - Validate preconditions; raise ValueError if not ok
    - Load queue item and provider
    - Build SMTP message
    - Connect and send via SMTP:
      - Use STARTTLS for port 587
      - Use SMTP_SSL for port 465
    - On success:
      - Update status=sent, sent_at=NOW, clear failed_at/error_message
      - Log action: email_queue_sent
      - Add task comment: "Approved email was sent manually via SMTP."
    - On failure:
      - Update status=failed, failed_at=NOW, set error_message
      - Log action: email_queue_send_failed
      - Add task comment: "Manual SMTP send failed. Review queue error message."
      - Return error or raise
    
    Never log or return smtp_password_secret.
    
    Args:
        tenant_id: tenant ID (required)
        queue_id: queue item ID (required)
        user_id: current user ID (optional)
        ip_address: client IP address (optional)
    
    Returns:
        dict: Updated queue item row
    
    Raises:
        ValueError: If preconditions not met or SMTP send fails
    """
    # Step 1: Validate preconditions
    validation = validate_send_preconditions(tenant_id, queue_id)
    if not validation["ok"]:
        error_msg = "; ".join(validation["errors"])
        raise ValueError(f"Cannot send: {error_msg}")
    
    # Step 2: Load queue item and provider
    queue_item = email_queue.get_email_queue_item(tenant_id, queue_id)
    if not queue_item:
        raise ValueError("Queue item not found during send")
    
    with db.get_db() as conn:
        provider = conn.execute(
            """
            SELECT *
            FROM email_provider_settings
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, queue_item.get("provider_setting_id")),
        ).fetchone()
        
        if not provider:
            raise ValueError("Provider not found during send")
        
        provider = dict(provider)
    
    # Step 3: Build message
    try:
        message = build_smtp_message(queue_item, provider)
    except ValueError as e:
        raise ValueError(f"Failed to build email message: {str(e)}")
    
    # Step 4: Connect and send via SMTP
    smtp_host = provider.get("smtp_host")
    smtp_port = int(provider.get("smtp_port"))
    smtp_username = provider.get("smtp_username")
    smtp_password_secret = credential_vault.decrypt_secret(provider.get("smtp_password_secret"))
    
    success = False
    error_message = None
    
    try:
        # Determine connection type based on port
        if smtp_port == 465:
            # Use SMTP_SSL for port 465
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as server:
                server.login(smtp_username, smtp_password_secret)
                server.send_message(message)
                success = True
        else:
            # Use STARTTLS for port 587 and others
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(smtp_username, smtp_password_secret)
                server.send_message(message)
                success = True
    
    except smtplib.SMTPAuthenticationError as e:
        error_message = "SMTP authentication failed. Check username and password."
    except smtplib.SMTPException as e:
        error_message = f"SMTP error: {str(e)[:100]}"
    except Exception as e:
        error_message = f"Send failed: {str(e)[:100]}"
    
    # Step 5: Update queue status and create audit log/comment
    with db.get_db() as conn:
        if success:
            # Update to sent status
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE email_send_queue
                SET status = 'sent',
                    sent_at = ?,
                    failed_at = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = ? AND id = ?
                """,
                (now, tenant_id, queue_id),
            )
            
            # Log audit action
            db.log_audit(
                conn,
                tenant_id,
                user_id,
                "email_queue_sent",
                "email_send_queue",
                queue_id,
                {"status": "approved_to_send", "sent_at": None},
                {"status": "sent", "sent_at": now},
                {
                    "provider_type": provider.get("provider_type"),
                    "from_email": provider.get("from_email"),
                    "to_email": queue_item.get("to_email"),
                },
                ip_address,
            )
            
            # Add task comment
            task_id = queue_item.get("task_id")
            if task_id:
                conn.execute(
                    """
                    INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
                    VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
                    """,
                    (tenant_id, task_id, "Approved email was sent manually via SMTP."),
                )
        
        else:
            # Update to failed status
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE email_send_queue
                SET status = 'failed',
                    failed_at = ?,
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = ? AND id = ?
                """,
                (now, error_message, tenant_id, queue_id),
            )
            
            # Log audit action
            db.log_audit(
                conn,
                tenant_id,
                user_id,
                "email_queue_send_failed",
                "email_send_queue",
                queue_id,
                {"status": "approved_to_send", "error_message": None},
                {"status": "failed", "error_message": error_message},
                {"provider_type": provider.get("provider_type")},
                ip_address,
            )
            
            # Add task comment
            task_id = queue_item.get("task_id")
            if task_id:
                conn.execute(
                    """
                    INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
                    VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
                    """,
                    (tenant_id, task_id, "Manual SMTP send failed. Review queue error message."),
                )
    
    # Step 6: Return updated queue item
    updated_item = email_queue.get_email_queue_item(tenant_id, queue_id)
    
    if not success:
        raise ValueError(f"SMTP send failed: {error_message}")
    
    return updated_item
