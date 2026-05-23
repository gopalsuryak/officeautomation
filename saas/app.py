import json
import logging
import os
import sqlite3
import secrets
from datetime import date, timedelta
from functools import wraps

from flask import Flask, flash as flask_flash, g, jsonify, redirect, render_template, request, session, url_for, Response
from werkzeug.security import check_password_hash, generate_password_hash

import accounting_connectors
import accounting_data_viewer
import ai_sync
import automation_registry
import automation_service
import billing
import client_entities
import compliance_tasks
import credential_vault
import dashboard_service
import db
import document_communication
import document_workflow
import email_dry_run
import email_operations
import email_provider_settings
import email_qa_dashboard
import email_readiness
import email_queue
import gstr3b_review_pack
import gst_dashboard
import gst_reconciliation
import gst_working_note
import manual_upload_importer
import manual_upload_parser
import manual_uploads
import plans
import portal_readiness
import review_workflow
import security
import usage
import voice_assistant
import smtp_sender
from orchestrator import get_orchestrator

app = Flask(__name__)


def flash(message, category="message"):
	if isinstance(message, Exception):
		message = "Action failed. Please review the inputs and try again."
	elif isinstance(message, str):
		lowered = message.lower()
		if category in {"warning", "danger", "error"} and (
			"traceback" in lowered
			or "exception" in lowered
			or "failed" in lowered
			or "error" in lowered
			or ":" in message
		):
			message = "Action failed. Please review the inputs and try again."
	return flask_flash(message, category)


def _is_production() -> bool:
	env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
	return env in {"prod", "production"}


def _configure_secret_key() -> None:
	secret_key = os.environ.get("SECRET_KEY", "").strip()
	if secret_key:
		app.config["SECRET_KEY"] = secret_key
		return

	if _is_production():
		raise RuntimeError("SECRET_KEY must be set in production.")

	logging.warning("SECRET_KEY is not set. Falling back to an unsafe development key.")
	app.config["SECRET_KEY"] = "dev-only-insecure-secret-key"


def _configure_session_cookies() -> None:
	app.config["SESSION_COOKIE_HTTPONLY"] = True
	app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
	app.config["SESSION_COOKIE_SECURE"] = _is_production()
	app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


_configure_secret_key()
_configure_session_cookies()
db.init_db()

_CSRF_SESSION_KEY = "_csrf_token"


@app.after_request
def _apply_security_headers(response):
	return security.security_headers(response)


@app.context_processor
def _inject_base_context():
	current_tenant = security.get_current_tenant()
	current_role = security.get_current_role() if security.get_current_user_id() else None

	def csrf_token():
		token = session.get(_CSRF_SESSION_KEY)
		if not token:
			token = secrets.token_urlsafe(32)
			session[_CSRF_SESSION_KEY] = token
		return token

	return {
		"current_tenant": dict(current_tenant) if current_tenant else None,
		"current_role": current_role,
		"csrf_token": csrf_token,
		"on_gstr3b_review_packs": request.endpoint
		in {"gstr3b_review_packs_list", "gstr3b_review_pack_detail", "gstr3b_review_pack_status_update"},
	}


def _request_csrf_token():
	return (
		request.form.get("csrf_token")
		or request.headers.get("X-CSRFToken")
		or request.headers.get("X-CSRF-Token")
	)


def _csrf_token_is_valid() -> bool:
	expected = session.get(_CSRF_SESSION_KEY)
	provided = _request_csrf_token()
	if not expected or not provided:
		return False
	return secrets.compare_digest(str(expected), str(provided))


@app.before_request
def _protect_unsafe_requests():
	if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
		return None
	if not request.endpoint or request.endpoint == "static":
		return None
	if _csrf_token_is_valid():
		return None

	logging.warning("CSRF validation failed for endpoint %s", request.endpoint)
	message = "Your session security token is missing or expired. Please try again."
	if request.is_json:
		return jsonify({"ok": False, "error": message}), 400
	flash(message, "warning")
	return redirect(request.referrer or url_for("dashboard"))


def _tenant_id_or_none():
	return security.get_current_tenant_id()


def _to_dict(row):
	if row is None:
		return None
	if isinstance(row, dict):
		return row
	return dict(row)


def _rows_to_dicts(rows):
	out = []
	for row in rows or []:
		item = _to_dict(row)
		if item is not None:
			out.append(item)
	return out


def _json_list(value):
	if not value:
		return []
	try:
		parsed = json.loads(value)
	except (TypeError, ValueError):
		return []
	return parsed if isinstance(parsed, list) else []


def login_required(view_func):
	@wraps(view_func)
	def wrapped(*args, **kwargs):
		if not security.get_current_user_id():
			flash("Please log in first.", "warning")
			return redirect(url_for("login"))

		tenant_id = _tenant_id_or_none()
		if not tenant_id:
			flash("Tenant context not found. Please log in again.", "warning")
			session.clear()
			return redirect(url_for("login"))

		g.current_user_id = security.get_current_user_id()
		g.current_tenant_id = tenant_id
		return view_func(*args, **kwargs)

	return wrapped


def _safe_int_from_form(name):
	raw = (request.form.get(name) or "").strip()
	if not raw:
		return None
	try:
		return int(raw)
	except ValueError:
		return None


def _audit_logs_for_tenant(tenant_id, filters=None, limit=100):
	filters = filters or {}
	where = ["tenant_id = ?"]
	params = [tenant_id]

	if filters.get("action"):
		where.append("action LIKE ?")
		params.append(f"%{filters['action']}%")
	if filters.get("entity_type"):
		where.append("entity_type LIKE ?")
		params.append(f"%{filters['entity_type']}%")
	if filters.get("user_id"):
		where.append("CAST(user_id AS TEXT) = ?")
		params.append(filters["user_id"])

	with db.get_db() as conn:
		rows = conn.execute(
			f"""
			SELECT *
			FROM audit_logs
			WHERE {' AND '.join(where)}
			ORDER BY datetime(created_at) DESC, id DESC
			LIMIT ?
			""",
			tuple(params + [max(1, int(limit))]),
		).fetchall()
	return _rows_to_dicts(rows)


def _latest_ai_output_for_task(tenant_id, task_id):
	with db.get_db() as conn:
		row = conn.execute(
			"""
			SELECT *
			FROM ai_outputs
			WHERE tenant_id = ? AND task_id = ?
			ORDER BY datetime(created_at) DESC, id DESC
			LIMIT 1
			""",
			(tenant_id, task_id),
		).fetchone()

	if not row:
		return None

	data = dict(row)
	data["risk_flags_list"] = _json_list(data.get("risk_flags_json"))
	data["missing_inputs_list"] = _json_list(data.get("missing_inputs_json"))
	data["applicable_laws_list"] = _json_list(data.get("applicable_laws_json"))
	data["document_requests_list"] = _json_list(data.get("document_requests_json"))
	return data


def _task_ai_payload(task, tenant_id):
	document_requests = _rows_to_dicts(
		document_workflow.list_document_requests_for_task(tenant_id, task["id"])
	)
	return {
		"task": {
			"id": task["id"],
			"task_type": task.get("task_type"),
			"title": task.get("title"),
			"description": task.get("description"),
			"period": task.get("period"),
			"financial_year": task.get("financial_year"),
			"status": task.get("status"),
		},
		"document_requests": [
			{
				"document_name": r.get("document_name"),
				"description": r.get("description"),
				"status": r.get("status"),
			}
			for r in document_requests
			if isinstance(r, dict)
		],
		"constraints": {
			"no_external_send": True,
			"no_filing": True,
			"no_rpa": True,
			"no_credentials_to_ai": True,
		},
	}


@app.get("/")
def home():
	if security.get_current_user_id():
		return redirect(url_for("dashboard"))
	return render_template("index.html", plans=billing.PLANS)


@app.route("/signup", methods=["GET", "POST"])
def signup():
	if request.method == "GET":
		selected_plan = (request.args.get("plan") or "starter").strip().lower()
		if selected_plan not in billing.PLANS:
			selected_plan = "starter"
		return render_template("signup.html", plan=selected_plan)

	name = (request.form.get("name") or "").strip()
	firm_name = (request.form.get("firm_name") or "").strip()
	email = (request.form.get("email") or "").strip().lower()
	phone = (request.form.get("phone") or "").strip() or None
	gstin = (request.form.get("gstin") or "").strip().upper() or None
	password = request.form.get("password") or ""
	plan_key = (request.form.get("plan") or "starter").strip().lower()
	if plan_key not in billing.PLANS:
		plan_key = "starter"

	if not name or not firm_name or not email or len(password) < 8:
		flash("Please fill all required fields and use a password of at least 8 characters.", "warning")
		return render_template("signup.html", plan=plan_key)

	with db.get_db() as conn:
		existing = conn.execute("SELECT id FROM users WHERE email = ? LIMIT 1", (email,)).fetchone()
		if existing:
			flash("Email is already registered. Please log in.", "warning")
			return redirect(url_for("login"))

		cur = conn.execute(
			"""
			INSERT INTO users (email, password_hash, name, firm_name, gstin, phone)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			(email, generate_password_hash(password), name, firm_name, gstin, phone),
		)
		if cur.lastrowid is None:
			raise RuntimeError("Could not create user record.")
		user_id = int(cur.lastrowid)
		tenant_status = "active" if not _is_production() else "pending_payment"
		tenant_cur = conn.execute(
			"""
			INSERT INTO tenants (user_id, plan, status)
			VALUES (?, ?, ?)
			""",
			(user_id, plan_key, tenant_status),
		)
		if tenant_cur.lastrowid is None:
			raise RuntimeError("Could not create tenant record.")
		tenant_id = int(tenant_cur.lastrowid)
		db.ensure_owner_firm_user(user_id, tenant_id)

	session["user_id"] = user_id
	session.permanent = True
	flash("Account created successfully.", "success")

	if tenant_status == "pending_payment":
		return redirect(url_for("checkout", plan=plan_key))
	return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
	if request.method == "GET":
		return render_template("login.html", demo_logins=[])

	email = (request.form.get("email") or "").strip().lower()
	password = request.form.get("password") or ""
	with db.get_db() as conn:
		user = conn.execute("SELECT * FROM users WHERE email = ? LIMIT 1", (email,)).fetchone()

	if not user or not check_password_hash(user["password_hash"], password):
		flash("Invalid email or password.", "danger")
		return render_template("login.html", demo_logins=[])

	session["user_id"] = user["id"]
	session.permanent = True
	flash("Logged in successfully.", "success")
	return redirect(url_for("dashboard"))


@app.get("/logout")
def logout():
	session.clear()
	flash("Logged out.", "success")
	return redirect(url_for("login"))


@app.route("/checkout", methods=["GET"])
@login_required
def checkout():
	plan_key = (request.args.get("plan") or "starter").strip().lower()
	if plan_key not in billing.PLANS:
		plan_key = "starter"

	user_id = g.current_user_id
	with db.get_db() as conn:
		user = conn.execute("SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()

	try:
		order = billing.create_order(plan_key)
	except Exception as exc:  # noqa: BLE001
		flash(f"Could not initialize payment: {exc}", "warning")
		return redirect(url_for("dashboard"))

	return render_template(
		"checkout.html",
		plan=plan_key,
		plan_data=billing.PLANS[plan_key],
		order=order,
		razorpay_key=billing.RAZORPAY_KEY_ID,
		user=dict(user) if user else {},
	)


@app.post("/billing/verify")
@login_required
def billing_verify():
	payload = request.get_json(silent=True) or {}
	order_id = str(payload.get("razorpay_order_id") or "").strip()
	payment_id = str(payload.get("razorpay_payment_id") or "").strip()
	signature = str(payload.get("razorpay_signature") or "").strip()
	plan_key = (payload.get("plan") or "starter").strip().lower()
	if plan_key not in billing.PLANS:
		plan_key = "starter"

	if not order_id or not payment_id or not signature:
		return jsonify({"ok": False, "error": "Missing payment fields."}), 400

	ok = billing.verify_payment(order_id, payment_id, signature)
	if not ok:
		return jsonify({"ok": False}), 400

	tenant_id = g.current_tenant_id
	with db.get_db() as conn:
		conn.execute("UPDATE tenants SET status = 'active', plan = ? WHERE id = ?", (plan_key, tenant_id))
		conn.execute(
			"""
			INSERT INTO subscriptions (tenant_id, razorpay_payment_id, razorpay_order_id, plan, status)
			VALUES (?, ?, ?, ?, 'active')
			""",
			(tenant_id, payment_id, order_id, plan_key),
		)
		db.log_audit(
			conn,
			tenant_id=tenant_id,
			user_id=g.current_user_id,
			action="subscription_activated",
			entity_type="subscription",
			entity_id=order_id,
			old_value=None,
			new_value={"plan": plan_key},
			metadata={"payment_id": payment_id},
			ip_address=security.get_request_ip() or "",
		)

	return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.get("/dashboard")
@login_required
def dashboard():
	tenant_id = g.current_tenant_id
	return render_template(
		"dashboard.html",
		tenant=_to_dict(security.get_current_tenant()),
		summary=dashboard_service.get_dashboard_summary(tenant_id),
		overdue_tasks=_rows_to_dicts(dashboard_service.get_overdue_tasks(tenant_id)),
		due_soon_tasks=_rows_to_dicts(dashboard_service.get_due_soon_tasks(tenant_id)),
		awaiting_review_tasks=_rows_to_dicts(dashboard_service.get_tasks_awaiting_review(tenant_id)),
		pending_document_tasks=_rows_to_dicts(dashboard_service.get_tasks_pending_documents(tenant_id)),
		recent_ai_outputs=_rows_to_dicts(dashboard_service.get_recent_ai_outputs(tenant_id)),
		client_pending_summary=_rows_to_dicts(dashboard_service.get_client_wise_pending_summary(tenant_id)),
		recent_activity=_rows_to_dicts(dashboard_service.get_recent_activity(tenant_id)),
	)


@app.get("/clients")
@login_required
def clients_list():
	tenant_id = g.current_tenant_id
	search = (request.args.get("search") or "").strip()
	status = (request.args.get("status") or "active").strip().lower()
	entity_type = (request.args.get("entity_type") or "").strip()
	clients = client_entities.list_client_entities(tenant_id, search=search or None, status=status, entity_type=entity_type or None)
	return render_template(
		"client_entities/list.html",
		clients=_rows_to_dicts(clients),
		search=search,
		status=status,
		entity_type=entity_type,
	)


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def clients_new():
	if request.method == "GET":
		return render_template("client_entities/new.html")

	tenant_id = g.current_tenant_id
	payload = {
		"name": request.form.get("name"),
		"legal_name": request.form.get("legal_name"),
		"entity_type": request.form.get("entity_type"),
		"pan": request.form.get("pan"),
		"gstin": request.form.get("gstin"),
		"cin": request.form.get("cin"),
		"email": request.form.get("email"),
		"phone": request.form.get("phone"),
		"address": request.form.get("address"),
		"state_code": request.form.get("state_code"),
		"assigned_user_id": _safe_int_from_form("assigned_user_id"),
	}
	try:
		created = client_entities.create_client_entity(
			tenant_id,
			payload,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Client created.", "success")
		return redirect(url_for("clients_detail", client_id=created["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return render_template("client_entities/new.html")


@app.get("/clients/<int:client_id>")
@login_required
def clients_detail(client_id):
	tenant_id = g.current_tenant_id
	client = client_entities.get_client_entity(tenant_id, client_id)
	if not client:
		flash("Client not found.", "warning")
		return redirect(url_for("clients_list"))

	recent_tasks = compliance_tasks.list_compliance_tasks(tenant_id, filters={"client_entity_id": client_id})[:10]
	return render_template(
		"client_entities/detail.html",
		client=_to_dict(client),
		summary=client_entities.get_client_summary(tenant_id, client_id),
		recent_tasks=_rows_to_dicts(recent_tasks),
	)


@app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
@login_required
def clients_edit(client_id):
	tenant_id = g.current_tenant_id
	existing = client_entities.get_client_entity(tenant_id, client_id)
	if not existing:
		flash("Client not found.", "warning")
		return redirect(url_for("clients_list"))

	if request.method == "GET":
		return render_template("client_entities/edit.html", client=_to_dict(existing))

	payload = {
		"name": request.form.get("name"),
		"legal_name": request.form.get("legal_name"),
		"entity_type": request.form.get("entity_type"),
		"pan": request.form.get("pan"),
		"gstin": request.form.get("gstin"),
		"cin": request.form.get("cin"),
		"email": request.form.get("email"),
		"phone": request.form.get("phone"),
		"address": request.form.get("address"),
		"state_code": request.form.get("state_code"),
		"assigned_user_id": _safe_int_from_form("assigned_user_id"),
	}
	try:
		client_entities.update_client_entity(
			tenant_id,
			client_id,
			payload,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Client updated.", "success")
		return redirect(url_for("clients_detail", client_id=client_id))
	except ValueError as exc:
		flash(str(exc), "warning")
		return render_template("client_entities/edit.html", client=_to_dict(existing))


@app.post("/clients/<int:client_id>/deactivate")
@login_required
def clients_deactivate(client_id):
	tenant_id = g.current_tenant_id
	updated = client_entities.deactivate_client_entity(
		tenant_id,
		client_id,
		user_id=g.current_user_id,
		ip_address=security.get_request_ip(),
	)
	if not updated:
		flash("Client not found.", "warning")
		return redirect(url_for("clients_list"))
	flash("Client deactivated.", "success")
	return redirect(url_for("clients_edit", client_id=client_id))


@app.get("/tasks")
@login_required
def tasks_list():
	tenant_id = g.current_tenant_id
	filters = {
		"search": (request.args.get("search") or "").strip() or None,
		"client_entity_id": (request.args.get("client_entity_id") or "").strip() or None,
		"status": (request.args.get("status") or "").strip() or None,
		"task_type": (request.args.get("task_type") or "").strip() or None,
		"priority": (request.args.get("priority") or "").strip() or None,
		"pending_from": (request.args.get("pending_from") or "").strip() or None,
		"due_before": (request.args.get("due_before") or "").strip() or None,
		"due_after": (request.args.get("due_after") or "").strip() or None,
	}
	filtered = {k: v for k, v in filters.items() if v not in (None, "")}

	tasks = _rows_to_dicts(compliance_tasks.list_compliance_tasks(tenant_id, filters=filtered))
	today = request.args.get("_today")
	if not today:
		from datetime import date as _d

		today = _d.today().isoformat()
	for item in tasks:
		due_date = (item.get("due_date") or "").strip()
		item["is_overdue"] = bool(due_date and due_date < today and item.get("status") not in {"filed", "closed", "cancelled"})

	clients = client_entities.list_client_entities(tenant_id, status="active")
	return render_template(
		"compliance_tasks/list.html",
		tasks=tasks,
		filters={k: (v or "") for k, v in filters.items()},
		clients=_rows_to_dicts(clients),
		status_labels=compliance_tasks.STATUS_LABELS,
		task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
	)


@app.route("/tasks/new", methods=["GET", "POST"])
@login_required
def new_task():
	tenant_id = g.current_tenant_id
	if request.method == "GET":
		clients = client_entities.list_client_entities(tenant_id, status="active")
		return render_template(
			"compliance_tasks/new.html",
			clients=_rows_to_dicts(clients),
			task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
		)

	payload = {
		"client_entity_id": _safe_int_from_form("client_entity_id"),
		"task_type": request.form.get("task_type"),
		"title": request.form.get("title"),
		"description": request.form.get("description"),
		"period": request.form.get("period"),
		"financial_year": request.form.get("financial_year"),
		"due_date": request.form.get("due_date"),
		"priority": request.form.get("priority") or "normal",
		"assigned_user_id": _safe_int_from_form("assigned_user_id"),
		"reviewer_user_id": _safe_int_from_form("reviewer_user_id"),
	}

	try:
		created = compliance_tasks.create_compliance_task(
			tenant_id,
			payload,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Task created.", "success")
		return redirect(url_for("task_detail", task_id=created["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		clients = client_entities.list_client_entities(tenant_id, status="active")
		return render_template(
			"compliance_tasks/new.html",
			clients=_rows_to_dicts(clients),
			task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
		)


@app.get("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
	tenant_id = g.current_tenant_id
	task = compliance_tasks.get_compliance_task(tenant_id, task_id)
	if not task:
		flash("Task not found.", "warning")
		return redirect(url_for("tasks_list"))

	task_data = _to_dict(task) or {}
	task_data["paperclip_issue_id"] = bool(task_data.get("paperclip_issue_id"))

	document_requests = _rows_to_dicts(document_workflow.list_document_requests_for_task(tenant_id, task_id))
	drafts = document_communication.list_drafts_for_task(tenant_id, task_id)
	linked_runs = gst_reconciliation.get_reconciliations_for_task(tenant_id, task_id)

	can_send_to_ai = task_data.get("status") == "ready_for_ai"
	can_sync_ai_result = bool(task_data.get("paperclip_issue_id")) and task_data.get("status") in automation_service.SYNC_ELIGIBLE_STATUSES

	return render_template(
		"compliance_tasks/detail.html",
		task=task_data,
		status_labels=compliance_tasks.STATUS_LABELS,
		task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
		next_statuses=compliance_tasks.get_valid_next_statuses(task_data.get("status") or ""),
		comments=_rows_to_dicts(compliance_tasks.list_task_comments(tenant_id, task_id)),
		history=_rows_to_dicts(compliance_tasks.list_task_status_history(tenant_id, task_id)),
		document_requests=document_requests,
		document_status_labels=document_workflow.DOCUMENT_STATUS_LABELS,
		available_review_actions=review_workflow.get_available_review_actions(task_data.get("status")),
		review_actions=_rows_to_dicts(review_workflow.get_review_actions_for_task(tenant_id, task_id)),
		review_action_labels=review_workflow.ACTION_LABELS,
		latest_ai_output=_latest_ai_output_for_task(tenant_id, task_id),
		can_send_to_ai=can_send_to_ai,
		can_sync_ai_result=can_sync_ai_result,
		linked_gst_reconciliations=linked_runs,
		document_communication_drafts=drafts,
	)


@app.get("/tasks/<int:task_id>/edit")
@login_required
def task_edit_page(task_id):
	return redirect(url_for("task_detail", task_id=task_id))


@app.post("/tasks/<int:task_id>/edit")
@login_required
def task_edit(task_id):
	tenant_id = g.current_tenant_id
	payload = {
		"title": request.form.get("title"),
		"description": request.form.get("description"),
		"period": request.form.get("period"),
		"financial_year": request.form.get("financial_year"),
		"due_date": request.form.get("due_date"),
		"priority": request.form.get("priority"),
		"assigned_user_id": _safe_int_from_form("assigned_user_id"),
		"reviewer_user_id": _safe_int_from_form("reviewer_user_id"),
	}
	updated = compliance_tasks.update_compliance_task(
		tenant_id,
		task_id,
		payload,
		user_id=g.current_user_id,
		ip_address=security.get_request_ip(),
	)
	if not updated:
		flash("Task not found.", "warning")
		return redirect(url_for("tasks_list"))
	flash("Task updated.", "success")
	return redirect(url_for("task_detail", task_id=task_id))


@app.post("/tasks/<int:task_id>/status")
@login_required
def task_status(task_id):
	tenant_id = g.current_tenant_id
	new_status = (request.form.get("new_status") or "").strip()
	reason = request.form.get("reason")
	try:
		updated = compliance_tasks.transition_task_status(
			tenant_id,
			task_id,
			new_status,
			user_id=g.current_user_id,
			reason=reason,
			ip_address=security.get_request_ip(),
		)
		if not updated:
			flash("Task not found.", "warning")
		else:
			flash("Task status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("task_detail", task_id=task_id))


@app.route("/tasks/<int:task_id>/comments", methods=["GET", "POST"])
@login_required
def task_comments(task_id):
	tenant_id = g.current_tenant_id
	if request.method == "POST":
		body = (request.form.get("body") or "").strip()
		try:
			compliance_tasks.add_task_comment(tenant_id, task_id, body, user_id=g.current_user_id)
			flash("Comment added.", "success")
		except ValueError as exc:
			flash(str(exc), "warning")
		return redirect(url_for("task_detail", task_id=task_id))

	comments = _rows_to_dicts(compliance_tasks.list_task_comments(tenant_id, task_id))
	return jsonify({"comments": comments})


@app.post("/tasks/<int:task_id>/send-to-ai")
@login_required
def task_send_to_ai(task_id):
	tenant_id = g.current_tenant_id
	task = compliance_tasks.get_compliance_task(tenant_id, task_id)
	if not task:
		flash("Task not found.", "warning")
		return redirect(url_for("tasks_list"))

	try:
		usage.increment_ai_task_usage(tenant_id, amount=1)
		payload = _task_ai_payload(_to_dict(task), tenant_id)
		issue_id = get_orchestrator().create_agent_task(tenant_id, task_id, task["task_type"], payload)
		compliance_tasks.mark_task_ai_queued(
			tenant_id,
			task_id,
			paperclip_issue_id=issue_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Task queued for AI drafting.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	except Exception as exc:  # noqa: BLE001
		flash(f"Could not queue task for AI: {exc}", "danger")

	return redirect(url_for("task_detail", task_id=task_id))


@app.post("/tasks/<int:task_id>/sync-ai")
@login_required
def task_sync_ai(task_id):
	tenant_id = g.current_tenant_id
	try:
		result = ai_sync.sync_paperclip_result_for_task(
			tenant_id,
			task_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		created_count = int(result.get("document_requests_created") or 0)
		flash(f"AI result synced. Document requests added: {created_count}.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	except Exception as exc:  # noqa: BLE001
		flash(f"Could not sync AI result: {exc}", "danger")
	return redirect(url_for("task_detail", task_id=task_id))


@app.post("/tasks/<int:task_id>/review-action")
@login_required
def task_review_action(task_id):
	tenant_id = g.current_tenant_id
	action = request.form.get("action")
	comment = request.form.get("comment")
	ai_output_id = review_workflow.get_latest_ai_output_id_for_task(tenant_id, task_id)

	try:
		updated = review_workflow.perform_review_action(
			tenant_id,
			task_id,
			action,
			user_id=g.current_user_id,
			comment=comment,
			ai_output_id=ai_output_id,
			ip_address=security.get_request_ip(),
		)
		if updated:
			flash("Review action recorded.", "success")
		else:
			flash("Task not found.", "warning")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("task_detail", task_id=task_id))


@app.post("/tasks/<int:task_id>/documents/request")
@login_required
def task_add_document_request(task_id):
	tenant_id = g.current_tenant_id
	try:
		created = document_workflow.add_document_request(
			tenant_id,
			task_id,
			document_name=request.form.get("document_name"),
			description=request.form.get("description"),
			requested_from=request.form.get("requested_from") or "client",
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not created:
			flash("Task not found.", "warning")
		else:
			flash("Document request added.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("task_detail", task_id=task_id))


@app.get("/document-requests")
@login_required
def document_requests_page():
	tenant_id = g.current_tenant_id
	filters = {
		"search": (request.args.get("search") or "").strip(),
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"status": (request.args.get("status") or "").strip().lower(),
	}
	where_clauses = ["dr.tenant_id = ?"]
	params = [tenant_id]

	if filters["search"]:
		where_clauses.append(
			"""
			(
				LOWER(c.name) LIKE ?
				OR LOWER(t.title) LIKE ?
				OR LOWER(dr.document_name) LIKE ?
				OR LOWER(COALESCE(dr.description, '')) LIKE ?
			)
			"""
		)
		search_value = f"%{filters['search'].lower()}%"
		params.extend([search_value, search_value, search_value, search_value])

	if filters["client_entity_id"]:
		try:
			client_entity_id = int(filters["client_entity_id"])
		except ValueError:
			client_entity_id = None
		if client_entity_id:
			where_clauses.append("dr.client_entity_id = ?")
			params.append(client_entity_id)
		else:
			filters["client_entity_id"] = ""

	if filters["status"]:
		where_clauses.append("LOWER(dr.status) = ?")
		params.append(filters["status"])

	with db.get_db() as conn:
		rows = conn.execute(
			f"""
			SELECT
				dr.id AS request_id,
				dr.task_id,
				dr.client_entity_id,
				dr.document_name,
				dr.description,
				dr.requested_from,
				dr.status,
				dr.created_at,
				dr.received_at,
				dr.notes,
				c.name AS client_name,
				t.title AS task_title,
				t.due_date AS task_due_date
			FROM document_requests dr
			LEFT JOIN client_entities c
				ON c.id = dr.client_entity_id
				AND c.tenant_id = dr.tenant_id
			LEFT JOIN compliance_tasks t
				ON t.id = dr.task_id
				AND t.tenant_id = dr.tenant_id
			WHERE {' AND '.join(where_clauses)}
			ORDER BY datetime(dr.created_at) DESC, dr.id DESC
			""",
			params,
		).fetchall()

		status_counts = conn.execute(
			"""
			SELECT status, COUNT(*) AS total
			FROM document_requests
			WHERE tenant_id = ?
			GROUP BY status
			""",
			(tenant_id,),
		).fetchall()

	clients = client_entities.list_client_entities(tenant_id, status="active")
	requests_list = _rows_to_dicts(rows)
	today = date.today().isoformat()
	overdue_count = 0
	for item in requests_list:
		due_date = (item.get("task_due_date") or "").strip()
		item["is_overdue"] = bool(due_date and due_date < today and item.get("status") == "requested")
		if item["is_overdue"]:
			overdue_count += 1

	status_map = {(_to_dict(row).get("status") or "").lower(): int(_to_dict(row).get("total") or 0) for row in status_counts}
	summary = {
		"total_requests": sum(status_map.values()),
		"requested": status_map.get("requested", 0),
		"received": status_map.get("received", 0),
		"accepted": status_map.get("accepted", 0),
		"rejected": status_map.get("rejected", 0),
		"cancelled": status_map.get("cancelled", 0),
		"overdue": overdue_count,
	}
	return render_template(
		"document_requests.html",
		document_requests=requests_list,
		filters=filters,
		clients=_rows_to_dicts(clients),
		summary=summary,
	)


@app.post("/document-requests/<int:request_id>/status")
@login_required
def document_request_status(request_id):
	tenant_id = g.current_tenant_id
	new_status = request.form.get("new_status")
	note = request.form.get("note")
	try:
		updated = document_workflow.update_document_request_status(
			tenant_id,
			request_id,
			new_status,
			user_id=g.current_user_id,
			note=note,
			ip_address=security.get_request_ip(),
		)
		if not updated:
			flash("Document request not found.", "warning")
			return redirect(url_for("tasks_list"))
		flash("Document request updated.", "success")
		return redirect(url_for("task_detail", task_id=updated["task_id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(request.referrer or url_for("tasks_list"))


@app.get("/automation")
@login_required
def automation_center():
	tenant_id = g.current_tenant_id
	filters = {
		"status": (request.args.get("status") or "").strip(),
		"task_type": (request.args.get("task_type") or "").strip(),
		"confidence": (request.args.get("confidence") or "").strip(),
		"has_ai_output": (request.args.get("has_ai_output") or "").strip(),
		"search": (request.args.get("search") or "").strip(),
	}
	jobs = automation_service.list_ai_jobs(tenant_id, filters={k: v for k, v in filters.items() if v})
	return render_template(
		"automation.html",
		summary=automation_service.get_ai_automation_summary(tenant_id),
		connection_health=automation_service.get_ai_connection_health(),
		jobs=jobs,
		filters=filters,
		sync_eligible=automation_service.SYNC_ELIGIBLE_STATUSES,
		retry_eligible=automation_service.RETRY_ELIGIBLE_STATUSES,
		status_labels=compliance_tasks.STATUS_LABELS,
		task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
	)


@app.post("/automation/tasks/<int:task_id>/retry-ai")
@login_required
def automation_retry_ai(task_id):
	tenant_id = g.current_tenant_id
	task = compliance_tasks.get_compliance_task(tenant_id, task_id)
	if not task:
		flash("Task not found.", "warning")
		return redirect(url_for("automation_center"))

	try:
		if task["status"] in {"ai_failed", "changes_required"}:
			compliance_tasks.transition_task_status(
				tenant_id,
				task_id,
				"ready_for_ai",
				user_id=g.current_user_id,
				reason="Retry AI drafting requested from Automation Center",
				ip_address=security.get_request_ip(),
			)
		payload = _task_ai_payload(_to_dict(task), tenant_id)
		issue_id = get_orchestrator().create_agent_task(tenant_id, task_id, task["task_type"], payload)
		compliance_tasks.mark_task_ai_queued(
			tenant_id,
			task_id,
			issue_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Task re-queued for AI drafting.", "success")
	except Exception as exc:  # noqa: BLE001
		flash(f"Retry failed: {exc}", "danger")

	return redirect(url_for("automation_center"))


@app.post("/automation/tasks/<int:task_id>/sync-ai")
@login_required
def automation_sync_ai(task_id):
	return task_sync_ai(task_id)


@app.get("/automation-registry")
@login_required
def automation_registry_page():
	selected = {
		"category": (request.args.get("category") or "").strip(),
		"agent": (request.args.get("agent") or "").strip(),
		"task_type": (request.args.get("task_type") or "").strip(),
		"active_only": (request.args.get("active_only") or "").strip() == "1",
		"search": (request.args.get("search") or "").strip(),
	}
	automations = automation_registry.list_automations(
		category=selected["category"] or None,
		agent=selected["agent"] or None,
		task_type=selected["task_type"] or None,
		active_only=selected["active_only"],
		search=selected["search"] or None,
	)
	categories = sorted({str(a.get("category")) for a in automation_registry.AUTOMATION_REGISTRY if a.get("category")})
	agents = sorted({str(a.get("assigned_agent")) for a in automation_registry.AUTOMATION_REGISTRY if a.get("assigned_agent")})
	task_types = sorted({t for a in automation_registry.AUTOMATION_REGISTRY for t in (a.get("task_types") or [])})
	return render_template(
		"automation_registry.html",
		automations=automations,
		categories=categories,
		agents=agents,
		task_types=task_types,
		selected=selected,
		summary=automation_registry.get_registry_summary(),
	)


@app.get("/credentials")
@login_required
def credentials_page():
	tenant_id = g.current_tenant_id
	filters = {
		"search": (request.args.get("search") or "").strip(),
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"portal_type": (request.args.get("portal_type") or "").strip(),
		"status": (request.args.get("status") or "").strip(),
	}
	return render_template(
		"credentials.html",
		summary=credential_vault.get_credential_summary(tenant_id),
		clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		credentials=credential_vault.list_credentials(tenant_id, filters=filters),
		portal_types=credential_vault.PORTAL_TYPES,
		status_values=sorted(credential_vault.ALLOWED_STATUSES),
		filters=filters,
	)


@app.post("/credentials/new")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def credentials_new():
	tenant_id = g.current_tenant_id
	try:
		credential_vault.create_credential_record(
			tenant_id=tenant_id,
			client_entity_id=_safe_int_from_form("client_entity_id"),
			portal_type=request.form.get("portal_type"),
			display_name=request.form.get("display_name"),
			username=request.form.get("username"),
			secret_value=request.form.get("secret_value"),
			secret_hint=request.form.get("secret_hint"),
			otp_required=bool(request.form.get("otp_required")),
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Credential record created.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("credentials_page"))


@app.get("/credentials/<int:credential_id>")
@login_required
def credential_detail(credential_id):
	tenant_id = g.current_tenant_id
	credential = credential_vault.get_credential(tenant_id, credential_id)
	if not credential:
		flash("Credential not found.", "warning")
		return redirect(url_for("credentials_page"))

	readiness = credential_vault.get_portal_readiness_for_client(tenant_id, credential["client_entity_id"])
	return render_template(
		"credential_detail.html",
		credential=credential,
		credential_readiness=credential_vault.get_credential_readiness(credential),
		readiness=readiness,
		portal_types=credential_vault.PORTAL_TYPES,
		status_values=sorted(credential_vault.ALLOWED_STATUSES),
	)


@app.post("/credentials/<int:credential_id>/check-readiness")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def credential_check_readiness(credential_id):
	tenant_id = g.current_tenant_id
	credential = credential_vault.get_credential(tenant_id, credential_id)
	if not credential:
		flash("Credential not found.", "warning")
		return redirect(url_for("credentials_page"))
	readiness = credential_vault.get_credential_readiness(credential)
	flash(readiness.get("readiness_message") or "Readiness checked.", "info")
	return redirect(url_for("credential_detail", credential_id=credential_id))


@app.post("/credentials/<int:credential_id>/status")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def credential_status_update(credential_id):
	tenant_id = g.current_tenant_id
	try:
		credential_vault.update_credential_status(
			tenant_id,
			credential_id,
			status=request.form.get("status"),
			last_error=request.form.get("last_error"),
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Credential status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("credential_detail", credential_id=credential_id))


@app.post("/credentials/<int:credential_id>/verify-manual")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def credential_verify_manual(credential_id):
	tenant_id = g.current_tenant_id
	try:
		credential_vault.mark_credential_verified(
			tenant_id,
			credential_id,
			login_status=request.form.get("login_status") or "success",
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Manual verification recorded.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("credential_detail", credential_id=credential_id))


@app.get("/portal-readiness")
@login_required
def portal_readiness_page():
	tenant_id = g.current_tenant_id
	selected = {
		"search": (request.args.get("search") or "").strip(),
		"portal_type": (request.args.get("portal_type") or "").strip(),
		"status": (request.args.get("status") or "").strip().lower(),
	}
	rows = portal_readiness.get_client_portal_readiness_matrix(tenant_id, filters=selected)
	return render_template(
		"portal_readiness.html",
		rows=rows,
		summary=portal_readiness.get_portal_readiness_summary(tenant_id),
		clients_needing_attention=portal_readiness.get_clients_needing_attention(tenant_id, limit=15),
		selected=selected,
		portal_columns=portal_readiness.PORTAL_COLUMNS,
		status_options=["ready", "partial", "attention", "not_ready", "missing", "expired", "locked", "error", "disabled"],
	)


@app.get("/accounting-connectors")
@login_required
def accounting_connectors_page():
	tenant_id = g.current_tenant_id
	selected = {
		"search": (request.args.get("search") or "").strip(),
		"provider": (request.args.get("provider") or "").strip(),
		"status": (request.args.get("status") or "").strip(),
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
	}
	return render_template(
		"accounting_connectors.html",
		summary=accounting_connectors.get_connector_summary(tenant_id),
		clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		connections=accounting_connectors.list_connections(tenant_id, filters=selected),
		providers=accounting_connectors.PROVIDERS,
		statuses=sorted(accounting_connectors.STATUSES),
		selected=selected,
	)


@app.post("/accounting-connectors/new")
@login_required
def accounting_connectors_new():
	tenant_id = g.current_tenant_id
	try:
		accounting_connectors.create_connection(
			tenant_id,
			client_entity_id=_safe_int_from_form("client_entity_id"),
			provider=request.form.get("provider"),
			connection_name=request.form.get("connection_name"),
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Accounting connector created.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("accounting_connectors_page"))


@app.get("/accounting-connectors/<int:connection_id>")
@login_required
def accounting_connection_detail(connection_id):
	tenant_id = g.current_tenant_id
	connection = accounting_connectors.get_connection(tenant_id, connection_id)
	if not connection:
		flash("Connection not found.", "warning")
		return redirect(url_for("accounting_connectors_page"))

	previews = manual_upload_parser.list_upload_previews_for_connection(tenant_id, connection_id)
	preview_map = {}
	for p in previews:
		if p and p.get("uploaded_file_id") is not None and p.get("uploaded_file_id") not in preview_map:
			preview_map[p["uploaded_file_id"]] = p

	provider_key = str(connection.get("provider") or "")
	return render_template(
		"accounting_connection_detail.html",
		connection=connection,
		provider_name=accounting_connectors.PROVIDERS.get(provider_key, provider_key),
		guidance=accounting_connectors.get_provider_guidance(connection.get("provider")),
		statuses=sorted(accounting_connectors.STATUSES),
		sync_runs=accounting_connectors.list_sync_runs(tenant_id, connection_id),
		upload_types=sorted(manual_uploads.UPLOAD_TYPES),
		max_file_size_mb=int(manual_uploads.MAX_FILE_SIZE_BYTES / (1024 * 1024)),
		uploaded_files=manual_uploads.list_uploaded_files(tenant_id, connection_id),
		preview_by_uploaded_file_id=preview_map,
		import_summary=manual_upload_importer.get_import_summary_for_connection(tenant_id, connection_id),
	)


@app.post("/accounting-connectors/<int:connection_id>/status")
@login_required
def accounting_connection_status_update(connection_id):
	tenant_id = g.current_tenant_id
	try:
		accounting_connectors.update_connection_status(
			tenant_id,
			connection_id,
			status=request.form.get("status"),
			error=request.form.get("error"),
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Connection status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("accounting_connection_detail", connection_id=connection_id))


@app.post("/accounting-connectors/<int:connection_id>/upload")
@login_required
def accounting_connection_upload(connection_id):
	tenant_id = g.current_tenant_id
	upload_file = request.files.get("file")
	upload_type = request.form.get("upload_type")
	try:
		manual_uploads.save_manual_upload(
			tenant_id,
			connection_id,
			upload_file,
			upload_type,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("File uploaded.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("accounting_connection_detail", connection_id=connection_id))


@app.post("/accounting-uploads/<int:uploaded_file_id>/parse-preview")
@login_required
def accounting_upload_parse_preview(uploaded_file_id):
	tenant_id = g.current_tenant_id
	try:
		manual_upload_parser.parse_uploaded_file_for_preview(
			tenant_id,
			uploaded_file_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Preview parsed.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")

	uploaded = manual_uploads.get_uploaded_file(tenant_id, uploaded_file_id)
	if not uploaded:
		return redirect(url_for("accounting_connectors_page"))
	return redirect(url_for("accounting_connection_detail", connection_id=uploaded["connection_id"]))


@app.get("/accounting-uploads/<int:uploaded_file_id>/preview")
@login_required
def accounting_upload_preview_page(uploaded_file_id):
	tenant_id = g.current_tenant_id
	uploaded_file = manual_uploads.get_uploaded_file(tenant_id, uploaded_file_id)
	if not uploaded_file:
		flash("Uploaded file not found.", "warning")
		return redirect(url_for("accounting_connectors_page"))
	connection = accounting_connectors.get_connection(tenant_id, uploaded_file["connection_id"])
	if not connection:
		flash("Connection not found.", "warning")
		return redirect(url_for("accounting_connectors_page"))
	preview = manual_upload_parser.get_upload_preview(tenant_id, uploaded_file_id)
	provider_key = str(connection.get("provider") or "")
	return render_template(
		"accounting_upload_preview.html",
		uploaded_file=uploaded_file,
		connection=connection,
		provider_name=accounting_connectors.PROVIDERS.get(provider_key, provider_key),
		preview=preview,
	)


@app.post("/accounting-upload-previews/<int:preview_id>/status")
@login_required
def accounting_upload_preview_status_update(preview_id):
	tenant_id = g.current_tenant_id
	status = request.form.get("status")
	try:
		updated = manual_upload_parser.mark_upload_preview_status(
			tenant_id,
			preview_id,
			status,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Preview status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
		updated = None

	if updated:
		return redirect(url_for("accounting_upload_preview_page", uploaded_file_id=updated["uploaded_file_id"]))
	return redirect(url_for("accounting_connectors_page"))


@app.post("/accounting-upload-previews/<int:preview_id>/import")
@login_required
def accounting_upload_preview_import(preview_id):
	tenant_id = g.current_tenant_id
	try:
		result = manual_upload_importer.import_from_preview(
			tenant_id,
			preview_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash(f"Imported preview rows: {result.get('imported_count', 0)}", "success")
	except ValueError as exc:
		flash(str(exc), "warning")

	with db.get_db() as conn:
		row = conn.execute(
			"SELECT uploaded_file_id FROM accounting_upload_previews WHERE tenant_id = ? AND id = ? LIMIT 1",
			(tenant_id, preview_id),
		).fetchone()
	if row:
		return redirect(url_for("accounting_upload_preview_page", uploaded_file_id=row["uploaded_file_id"]))
	return redirect(url_for("accounting_connectors_page"))


@app.get("/accounting-data")
@login_required
def accounting_data_page():
	tenant_id = g.current_tenant_id
	selected = {
		"search": (request.args.get("search") or "").strip(),
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"connection_id": (request.args.get("connection_id") or "").strip(),
		"provider": (request.args.get("provider") or "").strip(),
		"group_name": (request.args.get("group_name") or "").strip(),
		"min_closing_balance": (request.args.get("min_closing_balance") or "").strip(),
		"max_closing_balance": (request.args.get("max_closing_balance") or "").strip(),
	}
	filters = {k: v for k, v in selected.items() if v}
	ledgers = accounting_data_viewer.list_ledgers(tenant_id, filters=filters)
	summary = accounting_data_viewer.get_ledger_summary(tenant_id, filters=filters)
	group_summary = accounting_data_viewer.get_group_summary(tenant_id, filters=filters)
	client_summary = accounting_data_viewer.get_client_accounting_summary(tenant_id, filters=filters)

	all_ledgers = accounting_data_viewer.list_ledgers(tenant_id, filters={})
	group_options = sorted({(l.get("group_name") or "Ungrouped") for l in all_ledgers})

	connections = accounting_connectors.list_connections(tenant_id)
	providers = sorted(accounting_connectors.PROVIDERS.keys())
	return render_template(
		"accounting_data.html",
		ledgers=ledgers,
		summary=summary,
		group_summary=group_summary,
		client_summary=client_summary,
		selected=selected,
		group_options=group_options,
		clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		connections=connections,
		providers=providers,
		provider_labels=accounting_connectors.PROVIDERS,
	)


@app.get("/accounting-data/ledgers/<int:ledger_id>")
@login_required
def accounting_ledger_detail(ledger_id):
	tenant_id = g.current_tenant_id
	ledger = accounting_data_viewer.get_ledger(tenant_id, ledger_id)
	if not ledger:
		flash("Ledger not found.", "warning")
		return redirect(url_for("accounting_data_page"))
	raw_json_pretty = "{}"
	try:
		raw_json_pretty = json.dumps(json.loads(ledger.get("raw_json") or "{}"), indent=2, ensure_ascii=False)
	except (TypeError, ValueError):
		raw_json_pretty = ledger.get("raw_json") or "{}"
	return render_template("accounting_ledger_detail.html", ledger=ledger, raw_json_pretty=raw_json_pretty)


@app.get("/gst-reconciliation")
@login_required
def gst_reconciliation_page():
	tenant_id = g.current_tenant_id
	selected = {
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"search": (request.args.get("search") or "").strip(),
	}
	filters = {k: v for k, v in selected.items() if v}
	client_id = selected["client_entity_id"]
	gstr2b_previews = gst_reconciliation.list_valid_gstr2b_previews(tenant_id, client_entity_id=client_id or None)
	connections = accounting_connectors.list_connections(tenant_id, filters={"provider": "manual_upload"})
	return render_template(
		"gst_reconciliation.html",
		runs=gst_reconciliation.list_reconciliation_runs(tenant_id, filters=filters),
		clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		selected=selected,
		gstr2b_previews=gstr2b_previews,
		connections=connections,
	)


@app.post("/gst-reconciliation/run")
@login_required
def gst_reconciliation_run():
	tenant_id = g.current_tenant_id
	client_entity_id = _safe_int_from_form("client_entity_id")
	gstr2b_preview_id = _safe_int_from_form("gstr2b_preview_id")
	connection_id = _safe_int_from_form("connection_id")
	try:
		run = gst_reconciliation.run_purchase_vs_2b_reconciliation(
			tenant_id,
			client_entity_id,
			gstr2b_preview_id,
			connection_id=connection_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not run:
			flash("Reconciliation did not return a result.", "warning")
			return redirect(url_for("gst_reconciliation_page"))
		flash("GST reconciliation run completed.", "success")
		return redirect(url_for("gst_reconciliation_detail", run_id=run["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("gst_reconciliation_page"))


@app.get("/gst-reconciliation/<int:run_id>")
@login_required
def gst_reconciliation_detail(run_id):
	tenant_id = g.current_tenant_id
	run = gst_reconciliation.get_reconciliation_run(tenant_id, run_id)
	if not run:
		flash("Reconciliation run not found.", "warning")
		return redirect(url_for("gst_reconciliation_page"))

	selected = {
		"match_status": (request.args.get("match_status") or "").strip(),
		"supplier_gstin": (request.args.get("supplier_gstin") or "").strip(),
		"search": (request.args.get("search") or "").strip(),
	}
	result_filters = {k: v for k, v in selected.items() if v}

	linked_review_task = gst_reconciliation.get_linked_task_for_reconciliation(tenant_id, run_id)
	latest_working_note = gst_working_note.get_latest_working_note_for_run(tenant_id, run_id)
	working_notes = gst_working_note.list_working_notes_for_run(tenant_id, run_id)
	latest_pack = gstr3b_review_pack.get_latest_review_pack_for_run(tenant_id, run_id)

	if latest_working_note:
		latest_working_note["risk_flags_list"] = _json_list(latest_working_note.get("risk_flags_json"))
		latest_working_note["document_requests_list"] = _json_list(latest_working_note.get("document_requests_json"))

	client_id = run.get("client_entity_id")
	available_tasks = compliance_tasks.list_compliance_tasks(
		tenant_id,
		filters={"client_entity_id": client_id},
	)

	return render_template(
		"gst_reconciliation_detail.html",
		run=run,
		results=gst_reconciliation.list_reconciliation_results(tenant_id, run_id, filters=result_filters),
		selected=selected,
		status_options=sorted(gst_reconciliation.MATCH_STATUSES),
		linked_review_task=linked_review_task,
		available_review_tasks=_rows_to_dicts(available_tasks),
		available_document_tasks=_rows_to_dicts(available_tasks),
		task_type_labels=compliance_tasks.TASK_TYPE_LABELS,
		task_status_labels=compliance_tasks.STATUS_LABELS,
		latest_working_note=latest_working_note,
		working_notes=working_notes,
		working_note_statuses=sorted(gst_working_note.NOTE_STATUSES),
		latest_gstr3b_review_pack=latest_pack,
	)


@app.post("/gst-reconciliation/<int:run_id>/working-note/generate")
@login_required
def gst_reconciliation_generate_working_note(run_id):
	tenant_id = g.current_tenant_id
	try:
		gst_working_note.create_working_note_for_run(
			tenant_id,
			run_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Working note generated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("gst_reconciliation_detail", run_id=run_id))


@app.post("/gst-reconciliation/working-notes/<int:note_id>/status")
@login_required
def gst_reconciliation_working_note_status_update(note_id):
	tenant_id = g.current_tenant_id
	status = request.form.get("status")
	try:
		note = gst_working_note.update_working_note_status(
			tenant_id,
			note_id,
			status,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not note:
			flash("Working note not found.", "warning")
			return redirect(url_for("gst_reconciliation_page"))
		flash("Working note status updated.", "success")
		return redirect(url_for("gst_reconciliation_detail", run_id=note["reconciliation_run_id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(request.referrer or url_for("gst_reconciliation_page"))


@app.post("/gst-reconciliation/working-notes/<int:note_id>/create-document-requests")
@login_required
def gst_reconciliation_working_note_create_document_requests(note_id):
	tenant_id = g.current_tenant_id
	task_id = request.form.get("task_id")
	run_id = request.form.get("run_id")
	try:
		result = gst_working_note.create_document_requests_from_working_note(
			tenant_id,
			note_id,
			task_id=task_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash(f"Document requests created: {result.get('created_count', 0)}.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	if run_id:
		return redirect(url_for("gst_reconciliation_detail", run_id=int(run_id)))
	return redirect(url_for("gst_reconciliation_page"))


@app.post("/gst-reconciliation/<int:run_id>/create-review-task")
@login_required
def gst_reconciliation_create_review_task(run_id):
	tenant_id = g.current_tenant_id
	task_id = request.form.get("task_id")
	try:
		result = gst_reconciliation.create_or_link_review_task_for_reconciliation(
			tenant_id,
			run_id,
			task_id=task_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("GST review task linked.", "success")
		if result.get("task_id"):
			return redirect(url_for("task_detail", task_id=result["task_id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("gst_reconciliation_detail", run_id=run_id))


@app.post("/gst-reconciliation/<int:run_id>/gstr3b-review-pack/create")
@login_required
def gst_reconciliation_create_gstr3b_review_pack(run_id):
	tenant_id = g.current_tenant_id
	period = (request.form.get("period") or "").strip() or None
	try:
		pack = gstr3b_review_pack.create_review_pack_for_reconciliation(
			tenant_id,
			run_id,
			period=period,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not pack:
			flash("Pack creation returned no result.", "warning")
			return redirect(url_for("gst_reconciliation_detail", run_id=run_id))
		flash("GSTR-3B review pack created.", "success")
		return redirect(url_for("gstr3b_review_pack_detail", pack_id=pack["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("gst_reconciliation_detail", run_id=run_id))


@app.get("/gstr3b-review-packs")
@login_required
def gstr3b_review_packs_list():
	tenant_id = g.current_tenant_id
	filters = {
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"status": (request.args.get("status") or "").strip(),
		"period": (request.args.get("period") or "").strip(),
		"search": (request.args.get("search") or "").strip(),
	}
	cleaned = {k: v for k, v in filters.items() if v}
	return render_template(
		"gstr3b_review_packs.html",
		packs=gstr3b_review_pack.list_review_packs(tenant_id, filters=cleaned),
		summary=gstr3b_review_pack.get_review_pack_register_summary(tenant_id, filters=cleaned),
		active_clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		pack_statuses=sorted(gstr3b_review_pack.PACK_STATUSES),
		filters=filters,
		active_filters=bool(cleaned),
	)


@app.get("/gstr3b-review-packs/<int:pack_id>")
@login_required
def gstr3b_review_pack_detail(pack_id):
	tenant_id = g.current_tenant_id
	pack = gstr3b_review_pack.get_review_pack(tenant_id, pack_id)
	if not pack:
		flash("Review pack not found.", "warning")
		return redirect(url_for("gstr3b_review_packs_list"))

	# Expand JSON blobs expected by the template.
	for key in (
		"sales_summary_json",
		"purchase_summary_json",
		"reconciliation_summary_json",
		"pending_documents_json",
		"risk_flags_json",
		"review_checklist_json",
	):
		pack[key.replace("_json", "")] = _json_list(pack.get(key)) if key.endswith("documents_json") or key.endswith("flags_json") or key.endswith("checklist_json") else {}

	try:
		pack["sales_summary"] = json.loads(pack.get("sales_summary_json") or "{}")
	except (TypeError, ValueError):
		pack["sales_summary"] = {}
	try:
		pack["purchase_summary"] = json.loads(pack.get("purchase_summary_json") or "{}")
	except (TypeError, ValueError):
		pack["purchase_summary"] = {}
	try:
		pack["reconciliation_summary"] = json.loads(pack.get("reconciliation_summary_json") or "{}")
	except (TypeError, ValueError):
		pack["reconciliation_summary"] = {}
	pack["pending_documents"] = _json_list(pack.get("pending_documents_json"))
	pack["risk_flags"] = _json_list(pack.get("risk_flags_json"))
	pack["review_checklist"] = _json_list(pack.get("review_checklist_json"))

	return render_template(
		"gstr3b_review_pack_detail.html",
		pack=pack,
		pack_statuses=sorted(gstr3b_review_pack.PACK_STATUSES),
	)


@app.post("/gstr3b-review-packs/<int:pack_id>/status")
@login_required
def gstr3b_review_pack_status_update(pack_id):
	tenant_id = g.current_tenant_id
	status = (request.form.get("status") or "").strip()
	try:
		gstr3b_review_pack.update_review_pack_status(
			tenant_id,
			pack_id,
			status,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Pack status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("gstr3b_review_pack_detail", pack_id=pack_id))


@app.get("/gst-dashboard")
@login_required
def gst_dashboard_page():
	tenant_id = g.current_tenant_id
	selected = {
		"client_entity_id": (request.args.get("client_entity_id") or "").strip(),
		"match_status": (request.args.get("match_status") or "").strip(),
		"supplier_gstin": (request.args.get("supplier_gstin") or "").strip(),
		"search": (request.args.get("search") or "").strip(),
	}
	unresolved_filters = {k: v for k, v in selected.items() if v}
	unresolved_filters.pop("client_entity_id", None)
	if selected.get("client_entity_id"):
		unresolved_filters["client_entity_id"] = selected["client_entity_id"]

	return render_template(
		"gst_dashboard.html",
		summary=gst_dashboard.get_gst_dashboard_summary(tenant_id),
		clients_needing_attention=gst_dashboard.get_clients_needing_gst_attention(tenant_id, limit=10),
		recent_runs=gst_dashboard.get_recent_gst_reconciliation_runs(tenant_id, limit=10),
		unresolved_exceptions=gst_dashboard.get_unresolved_gst_exceptions(tenant_id, limit=25, filters=unresolved_filters),
		pending_gst_review_tasks=gst_dashboard.get_pending_gst_review_tasks(tenant_id, limit=10),
		pending_gst_document_requests=gst_dashboard.get_pending_gst_document_requests(tenant_id, limit=10),
		clients=_rows_to_dicts(client_entities.list_client_entities(tenant_id, status="active")),
		selected=selected,
		match_status_options=sorted(s for s in gst_reconciliation.MATCH_STATUSES if s != "matched"),
		note_status_labels={"draft": "Draft", "under_review": "Under Review", "approved": "Approved", "archived": "Archived"},
		task_status_labels=compliance_tasks.STATUS_LABELS,
		has_document_requests_route=False,
	)


@app.post("/tasks/<int:task_id>/document-communication-drafts")
@login_required
def create_document_communication_draft_route(task_id):
	tenant_id = g.current_tenant_id
	draft_type = (request.form.get("draft_type") or "email").strip().lower()
	try:
		draft = document_communication.create_document_communication_draft(
			tenant_id,
			task_id,
			draft_type,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not draft:
			flash("Draft creation returned no result.", "warning")
			return redirect(url_for("task_detail", task_id=task_id))
		flash("Document communication draft created.", "success")
		return redirect(url_for("document_communication_draft_detail", draft_id=draft["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("task_detail", task_id=task_id))


@app.get("/document-communications")
@login_required
def document_communications_register():
	tenant_id = g.current_tenant_id
	
	# Get filters from request
	filters = {
		"client_entity_id": request.args.get("client_entity_id"),
		"task_id": request.args.get("task_id"),
		"draft_type": request.args.get("draft_type"),
		"status": request.args.get("status"),
		"search": request.args.get("search"),
	}
	# Remove None values
	filters = {k: v for k, v in filters.items() if v}
	
	# Get drafts and summary
	drafts = document_communication.list_communication_drafts(tenant_id, filters)
	summary = document_communication.get_communication_register_summary(tenant_id, filters)
	
	# Get active clients for filter dropdown
	with db.get_db() as conn:
		clients = conn.execute(
			"SELECT id, name FROM client_entities WHERE tenant_id = ? AND status = 'active' ORDER BY name",
			(tenant_id,)
		).fetchall()
		clients = [dict(row) for row in clients]
		
		# Get recent/open tasks for filter dropdown
		tasks = conn.execute(
			"SELECT id, title FROM compliance_tasks WHERE tenant_id = ? AND status NOT IN ('completed', 'cancelled') ORDER BY due_date LIMIT 20",
			(tenant_id,)
		).fetchall()
		tasks = [dict(row) for row in tasks]
	
	return render_template(
		"document_communication_register.html",
		drafts=drafts,
		summary=summary,
		clients=clients,
		tasks=tasks,
		filters=filters,
	)


@app.get("/document-communication-drafts/<int:draft_id>")
@login_required
def document_communication_draft_detail(draft_id):
	tenant_id = g.current_tenant_id
	draft = document_communication.get_draft(tenant_id, draft_id)
	if not draft:
		flash("Draft not found.", "warning")
		return redirect(url_for("tasks_list"))
	return render_template("document_communication_draft_detail.html", draft=draft)


@app.post("/document-communication-drafts/<int:draft_id>/review")
@login_required
def mark_document_communication_draft_reviewed(draft_id):
	tenant_id = g.current_tenant_id
	draft = document_communication.mark_draft_reviewed(
		tenant_id,
		draft_id,
		user_id=g.current_user_id,
		ip_address=security.get_request_ip(),
	)
	if not draft:
		flash("Draft not found.", "warning")
		return redirect(url_for("tasks_list"))
	flash("Draft marked reviewed.", "success")
	return redirect(url_for("document_communication_draft_detail", draft_id=draft_id))


@app.post("/document-communication-drafts/<int:draft_id>/archive")
@login_required
def archive_document_communication_draft(draft_id):
	tenant_id = g.current_tenant_id
	draft = document_communication.archive_draft(
		tenant_id,
		draft_id,
		user_id=g.current_user_id,
		ip_address=security.get_request_ip(),
	)
	if not draft:
		flash("Draft not found.", "warning")
		return redirect(url_for("tasks_list"))
	flash("Draft archived.", "success")
	return redirect(url_for("document_communication_draft_detail", draft_id=draft_id))


@app.get("/document-communication-drafts/<int:draft_id>/print")
@login_required
def document_communication_draft_print(draft_id):
	tenant_id = g.current_tenant_id
	draft = document_communication.get_draft(tenant_id, draft_id)
	if not draft:
		flash("Draft not found.", "warning")
		return redirect(url_for("document_communications_register"))
	return render_template("document_communication_print.html", draft=draft)


@app.get("/document-communication-drafts/<int:draft_id>/download")
@login_required
def document_communication_draft_download(draft_id):
	tenant_id = g.current_tenant_id
	draft = document_communication.get_draft(tenant_id, draft_id)
	if not draft:
		flash("Draft not found.", "warning")
		return redirect(url_for("document_communications_register"))
	
	# Build plain text content
	lines = [
		"==================================================",
		"DOCUMENT COMMUNICATION DRAFT",
		"==================================================",
		"",
		f"Client: {draft.get('client_name', 'N/A')}",
		f"Task: {draft.get('task_title', 'N/A')}",
		f"Draft Type: {draft.get('draft_type', 'N/A').upper()}",
		f"Status: {draft.get('status', 'N/A').upper()}",
		f"Created: {draft.get('created_at', 'N/A')}",
		"",
		"==================================================",
	]
	if draft.get("subject"):
		lines.extend([
			"SUBJECT",
			"==================================================",
			draft["subject"],
			"",
		])
	lines.extend([
		"BODY",
		"==================================================",
		draft.get("body", ""),
		"",
		"==================================================",
		"IMPORTANT: Draft only. Not sent by CA Assist.",
		"Copy and send manually after review.",
		"==================================================",
	])
	
	content = "\n".join(lines)
	return Response(
		content,
		mimetype="text/plain",
		headers={"Content-Disposition": f"attachment; filename=document-draft-{draft_id}.txt"}
	)


# ── EMAIL QUEUE: REVIEWED EMAIL SENDING FOUNDATION ───────────────────────

@app.post("/document-communication-drafts/<int:draft_id>/queue-email")
@login_required
def queue_email_from_draft(draft_id):
	"""Queue a reviewed email draft for future sending."""
	tenant_id = g.current_tenant_id
	to_email = request.form.get("to_email", "").strip()
	cc_email = request.form.get("cc_email", "").strip() or None
	bcc_email = request.form.get("bcc_email", "").strip() or None
	
	try:
		queue_item = email_queue.queue_reviewed_email_draft(
			tenant_id,
			draft_id,
			to_email=to_email or None,
			cc_email=cc_email,
			bcc_email=bcc_email,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if not queue_item:
			flash("Failed to queue email. Please try again.", "warning")
			return redirect(url_for("document_communication_draft_detail", draft_id=draft_id))
		flash("Email queued for future sending. No email has been sent.", "success")
		return redirect(url_for("email_queue_detail", queue_id=queue_item.get("id") or queue_item.get("queue_id")))
	except ValueError as e:
		flash(str(e), "warning")
		return redirect(url_for("document_communication_draft_detail", draft_id=draft_id))


@app.get("/email-queue")
@login_required
def email_queue_list():
	"""View email queue with filters."""
	tenant_id = g.current_tenant_id
	
	# Get filters
	filters = {}
	client_entity_id_str = request.args.get("client_entity_id")
	if client_entity_id_str:
		try:
			filters["client_entity_id"] = int(client_entity_id_str)
		except (ValueError, TypeError):
			pass
	task_id_str = request.args.get("task_id")
	if task_id_str:
		try:
			filters["task_id"] = int(task_id_str)
		except (ValueError, TypeError):
			pass
	if request.args.get("status"):
		filters["status"] = request.args.get("status")
	if request.args.get("search"):
		filters["search"] = request.args.get("search")
	
	# Load data
	queue_items = email_queue.list_email_queue(tenant_id, filters)
	summary = email_queue.get_email_queue_summary(tenant_id, filters)
	
	# Load clients and tasks for filters
	clients = client_entities.list_client_entities(tenant_id, status="active")
	tasks = compliance_tasks.list_compliance_tasks(tenant_id, {})
	
	return render_template(
		"email_queue.html",
		queue_items=queue_items,
		summary=summary,
		clients=clients or [],
		tasks=tasks or [],
		filters=filters,
	)


@app.get("/email-delivery-logs")
@login_required
def email_delivery_logs_page():
	"""Read-only register for delivery/send log states in email queue."""
	tenant_id = g.current_tenant_id
	filters = {}

	for key in ["client_entity_id", "task_id", "provider_id"]:
		raw = (request.args.get(key) or "").strip()
		if not raw:
			continue
		try:
			filters[key] = int(raw)
		except (ValueError, TypeError):
			pass

	for key in ["status", "date_from", "date_to", "search"]:
		value = (request.args.get(key) or "").strip()
		if value:
			filters[key] = value

	logs = email_queue.list_email_delivery_logs(tenant_id, filters=filters)
	summary = email_queue.get_email_delivery_log_summary(tenant_id, filters=filters)
	clients = client_entities.list_client_entities(tenant_id, status="active") or []
	all_tasks = compliance_tasks.list_compliance_tasks(tenant_id, {}) or []
	tasks = [t for t in all_tasks if t["status"] not in ("closed", "cancelled")][:100]
	providers = email_provider_settings.list_provider_settings(tenant_id, {}) or []

	return render_template(
		"email_delivery_logs.html",
		logs=logs,
		summary=summary,
		clients=clients,
		tasks=tasks,
		providers=providers,
		filters=filters,
	)


@app.get("/email-operations")
@login_required
def email_operations_page():
	"""Central read-only dashboard for email operations monitoring."""
	tenant_id = g.current_tenant_id
	filters = {}

	for key in ["client_entity_id", "task_id", "provider_id"]:
		raw = (request.args.get(key) or "").strip()
		if not raw:
			continue
		try:
			filters[key] = int(raw)
		except (ValueError, TypeError):
			pass

	for key in ["queue_status", "draft_status", "date_from", "date_to", "search"]:
		value = (request.args.get(key) or "").strip()
		if value:
			filters[key] = value

	clients = client_entities.list_client_entities(tenant_id, status="active") or []
	all_tasks = compliance_tasks.list_compliance_tasks(tenant_id, {}) or []
	tasks = [t for t in all_tasks if t["status"] not in ("closed", "cancelled")][:100]
	providers = email_provider_settings.list_provider_settings(tenant_id, {"status": "active"}) or []
	has_active_filters = bool(filters)

	return render_template(
		"email_operations.html",
		summary=email_operations.get_email_operations_summary(tenant_id, filters=filters),
		drafts_awaiting_review=email_operations.get_drafts_awaiting_review(tenant_id, limit=10, filters=filters),
		reviewed_not_queued=email_operations.get_reviewed_drafts_not_queued(tenant_id, limit=10, filters=filters),
		queue_without_provider=email_operations.get_queue_items_without_provider(tenant_id, limit=10, filters=filters),
		ready_to_send_items=email_operations.get_ready_to_send_items(tenant_id, limit=10, filters=filters),
		provider_readiness=email_operations.get_provider_readiness_summary(tenant_id, filters=filters),
		filters=filters,
		clients=clients,
		tasks=tasks,
		providers=providers,
		has_active_filters=has_active_filters,
	)


@app.get("/email-qa-dashboard")
@login_required
def email_qa_dashboard_page():
	"""Final read-only QA dashboard for email module readiness and safety checks."""
	tenant_id = g.current_tenant_id

	return render_template(
		"email_qa_dashboard.html",
		summary=email_qa_dashboard.get_email_qa_summary(tenant_id),
		providers_needing_attention=email_qa_dashboard.get_providers_needing_attention(tenant_id, limit=10),
		failed_items_needing_review=email_qa_dashboard.get_failed_items_needing_review(tenant_id, limit=10),
		approved_items_pending_send=email_qa_dashboard.get_approved_items_pending_send(tenant_id, limit=10),
		failure_rate_by_provider=email_qa_dashboard.get_failure_rate_by_provider(tenant_id, limit=10),
		safety_checklist=email_qa_dashboard.get_safety_checklist(tenant_id),
	)


@app.get("/email-readiness")
@login_required
def email_readiness_page():
	"""Internal production-readiness checklist page for email module safety gating."""
	tenant_id = g.current_tenant_id
	email_readiness.ensure_readiness_checks(tenant_id)
	readiness = email_readiness.get_email_readiness_status(tenant_id)
	return render_template("email_readiness.html", readiness=readiness)


@app.post("/email-readiness/<check_key>")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def email_readiness_update(check_key):
	"""Update a readiness checklist item status and notes (no send action)."""
	tenant_id = g.current_tenant_id
	status = (request.form.get("status") or "").strip().lower()
	notes = (request.form.get("notes") or "").strip() or None
	try:
		email_readiness.update_readiness_check(
			tenant_id,
			check_key,
			status,
			notes=notes,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Readiness check updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_readiness_page"))


@app.get("/email-providers")
@login_required
def email_provider_settings_page():
	"""View and create provider settings for future reviewed email sending."""
	tenant_id = g.current_tenant_id
	filters = {
		"provider_type": (request.args.get("provider_type") or "").strip().lower(),
		"status": (request.args.get("status") or "").strip().lower(),
		"search": (request.args.get("search") or "").strip(),
	}
	clean_filters = {k: v for k, v in filters.items() if v}
	providers = email_provider_settings.list_provider_settings(tenant_id, clean_filters)

	summary = {
		"total": len(providers),
		"draft": sum(1 for p in providers if p.get("status") == "draft"),
		"active": sum(1 for p in providers if p.get("status") == "active"),
		"inactive": sum(1 for p in providers if p.get("status") == "inactive"),
		"error": sum(1 for p in providers if p.get("status") == "error"),
	}

	return render_template(
		"email_provider_settings.html",
		providers=providers,
		filters=filters,
		summary=summary,
		provider_types=email_provider_settings.PROVIDER_TYPES,
		provider_display_names=email_provider_settings.PROVIDER_DISPLAY_NAMES,
		provider_statuses=email_provider_settings.PROVIDER_STATUSES,
	)


@app.post("/email-providers/new")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def email_provider_settings_new():
	"""Create a provider settings record without testing login or sending email."""
	tenant_id = g.current_tenant_id
	payload = {
		"provider_type": request.form.get("provider_type"),
		"display_name": request.form.get("display_name"),
		"from_name": request.form.get("from_name"),
		"from_email": request.form.get("from_email"),
		"smtp_host": request.form.get("smtp_host"),
		"smtp_port": request.form.get("smtp_port"),
		"smtp_username": request.form.get("smtp_username"),
		"smtp_password_secret": request.form.get("smtp_password_secret"),
		"oauth_client_id": request.form.get("oauth_client_id"),
	}
	try:
		created = email_provider_settings.create_provider_setting(
			tenant_id,
			payload,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Email provider setting created.", "success")
		return redirect(url_for("email_provider_setting_detail", provider_id=created["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("email_provider_settings_page"))


@app.get("/email-providers/<int:provider_id>")
@login_required
def email_provider_setting_detail(provider_id):
	"""View email provider detail profile."""
	tenant_id = g.current_tenant_id
	provider = email_provider_settings.get_provider_setting(tenant_id, provider_id)
	if not provider:
		flash("Email provider setting not found.", "warning")
		return redirect(url_for("email_provider_settings_page"))
	return render_template(
		"email_provider_detail.html",
		provider=provider,
		provider_display_names=email_provider_settings.PROVIDER_DISPLAY_NAMES,
		provider_statuses=email_provider_settings.PROVIDER_STATUSES,
		oauth_statuses=email_provider_settings.OAUTH_STATUSES,
	)


@app.post("/email-providers/<int:provider_id>/status")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def email_provider_setting_status_update(provider_id):
	"""Update provider status metadata only."""
	tenant_id = g.current_tenant_id
	status = (request.form.get("status") or "").strip().lower()
	try:
		email_provider_settings.update_provider_status(
			tenant_id,
			provider_id,
			status,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Provider status updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_provider_setting_detail", provider_id=provider_id))


@app.post("/email-providers/<int:provider_id>/default")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def email_provider_setting_set_default(provider_id):
	"""Set one provider as default for the tenant."""
	tenant_id = g.current_tenant_id
	try:
		email_provider_settings.set_default_provider(
			tenant_id,
			provider_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Default provider updated.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_provider_setting_detail", provider_id=provider_id))


@app.post("/email-providers/<int:provider_id>/check")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def email_provider_setting_check(provider_id):
	"""Run local readiness validation only (no login, no email send)."""
	tenant_id = g.current_tenant_id
	try:
		result = email_provider_settings.simulate_provider_check(
			tenant_id,
			provider_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		if result.get("ready"):
			flash("Local readiness check completed. No login or email was sent.", "success")
		else:
			flash(
				f"Local readiness check completed. No login or email was sent. Missing: {result.get('last_error')}",
				"warning",
			)
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_provider_setting_detail", provider_id=provider_id))


@app.get("/email-queue/<int:queue_id>")
@login_required
def email_queue_detail(queue_id):
	"""View email queue item detail."""
	tenant_id = g.current_tenant_id
	item = email_queue.get_email_queue_item(tenant_id, queue_id)
	if not item:
		flash("Queue item not found.", "warning")
		return redirect(url_for("email_queue_list"))
	providers = email_queue.list_available_email_providers_for_queue(tenant_id)
	default_provider = email_queue.get_default_email_provider(tenant_id)
	dry_runs = email_dry_run.list_email_dry_runs_for_queue(tenant_id, queue_id)
	latest_approval = email_queue.get_latest_send_approval_for_queue(tenant_id, queue_id)
	return render_template(
		"email_queue_detail.html",
		item=item,
		providers=providers,
		default_provider=default_provider,
		dry_runs=dry_runs,
		latest_approval=latest_approval,
	)


@app.post("/email-queue/<int:queue_id>/dry-run")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def generate_email_dry_run(queue_id):
	"""Generate local dry-run payload preview for ready_to_send queue item."""
	tenant_id = g.current_tenant_id
	try:
		preview = email_dry_run.build_email_payload_preview(
			tenant_id,
			queue_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Dry-run preview generated. No email has been sent.", "success")
		return redirect(url_for("email_dry_run_detail", preview_id=preview["id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.get("/email-dry-runs/<int:preview_id>")
@login_required
def email_dry_run_detail(preview_id):
	"""View generated dry-run payload preview detail."""
	tenant_id = g.current_tenant_id
	preview = email_dry_run.get_email_dry_run_preview(tenant_id, preview_id)
	if not preview:
		flash("Dry-run preview not found.", "warning")
		return redirect(url_for("email_queue_list"))
	return render_template("email_dry_run_detail.html", preview=preview)


@app.post("/email-dry-runs/<int:preview_id>/approve")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def approve_email_dry_run(preview_id):
	"""Approve a ready dry-run preview for future worker sending gates."""
	tenant_id = g.current_tenant_id
	approval_note = (request.form.get("approval_note") or "").strip() or None
	try:
		approval = email_dry_run.approve_dry_run_for_sending(
			tenant_id,
			preview_id,
			approval_note=approval_note,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Email approved for future sending. No email has been sent.", "success")
		return redirect(url_for("email_queue_detail", queue_id=approval["queue_id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("email_dry_run_detail", preview_id=preview_id))


@app.post("/email-send-approvals/<int:approval_id>/revoke")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def revoke_email_send_approval(approval_id):
	"""Revoke a prior send approval and return queue to ready state if needed."""
	tenant_id = g.current_tenant_id
	try:
		approval = email_dry_run.revoke_send_approval(
			tenant_id,
			approval_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Email send approval revoked. No email has been sent.", "success")
		return redirect(url_for("email_queue_detail", queue_id=approval["queue_id"]))
	except ValueError as exc:
		flash(str(exc), "warning")
		return redirect(url_for("email_queue_list"))


@app.post("/email-queue/<int:queue_id>/status")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def update_email_queue_status(queue_id):
	"""Update email queue item status."""
	tenant_id = g.current_tenant_id
	status = request.form.get("status", "").strip()
	error_message = request.form.get("error_message", "").strip() or None
	
	try:
		item = email_queue.update_email_queue_status(
			tenant_id,
			queue_id,
			status,
			error_message=error_message,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash(f"Queue item status updated to {status}.", "success")
		return redirect(url_for("email_queue_detail", queue_id=queue_id))
	except ValueError as e:
		flash(str(e), "warning")
		return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.post("/email-queue/<int:queue_id>/assign-provider")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def assign_email_queue_provider(queue_id):
	"""Assign an active provider setting to a queue item without sending email."""
	tenant_id = g.current_tenant_id
	provider_id_raw = (request.form.get("provider_id") or "").strip()
	provider_id = provider_id_raw or None
	try:
		email_queue.assign_provider_to_queue_item(
			tenant_id,
			queue_id,
			provider_id=provider_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Provider assigned. No email has been sent.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.post("/email-queue/<int:queue_id>/ready-with-provider")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def mark_email_queue_ready_with_provider(queue_id):
	"""Mark queued item ready after provider assignment without sending email."""
	tenant_id = g.current_tenant_id
	try:
		email_queue.mark_queue_ready_with_provider(
			tenant_id,
			queue_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Queue item marked ready to send. Sending is still disabled.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.post("/email-queue/<int:queue_id>/failure-review")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def add_email_queue_failure_review(queue_id):
	"""Add a manual failure review note for a failed email queue item."""
	tenant_id = g.current_tenant_id
	review_note = (request.form.get("review_note") or "").strip()
	try:
		email_queue.add_failure_review_note(
			tenant_id,
			queue_id,
			review_note,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Failure review note saved. No email has been resent.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.post("/email-queue/<int:queue_id>/reopen-after-failure")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def reopen_email_queue_after_failure(queue_id):
	"""Reopen failed queue item to approved_to_send without sending email."""
	tenant_id = g.current_tenant_id
	reopen_note = (request.form.get("reopen_note") or "").strip() or None
	try:
		email_queue.reopen_failed_email_queue_item(
			tenant_id,
			queue_id,
			reopen_note=reopen_note,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Queue item reopened for manual sending. No email has been sent.", "success")
	except ValueError as exc:
		flash(str(exc), "warning")
	return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.post("/email-queue/<int:queue_id>/send-smtp")
@login_required
@security.require_roles(["owner", "partner", "manager"])
def send_queue_item_via_smtp(queue_id):
	"""Send an approved queue item via SMTP with explicit final confirmation."""
	tenant_id = g.current_tenant_id
	
	# Require explicit final confirmation
	final_confirmation = (request.form.get("final_confirmation") or "").strip()
	if final_confirmation != "SEND":
		flash("Type SEND to confirm manual SMTP sending.", "warning")
		return redirect(url_for("email_queue_detail", queue_id=queue_id))
	
	try:
		smtp_sender.send_approved_queue_item_via_smtp(
			tenant_id,
			queue_id,
			user_id=g.current_user_id,
			ip_address=security.get_request_ip(),
		)
		flash("Email sent successfully via SMTP.", "success")
	except ValueError as exc:
		flash(f"Send failed: {str(exc)}", "danger")
	except Exception as exc:
		flash(f"Unexpected error during send: {str(exc)}", "danger")
	
	return redirect(url_for("email_queue_detail", queue_id=queue_id))


@app.get("/voice-assistant")
@login_required
def voice_assistant_page():
	return render_template("voice_assistant.html")


@app.post("/voice-assistant/parse")
@login_required
def voice_assistant_parse():
	tenant_id = g.current_tenant_id
	payload = request.get_json(silent=True) or {}
	parsed = voice_assistant.parse_voice_command(tenant_id, payload.get("command_text") or "")
	preview = voice_assistant.resolve_command_preview(tenant_id, parsed)
	return jsonify(preview)


@app.post("/voice-assistant/execute")
@login_required
def voice_assistant_execute():
	tenant_id = g.current_tenant_id
	command = request.get_json(silent=True) or {}
	result = voice_assistant.execute_confirmed_command(
		tenant_id,
		command,
		user_id=g.current_user_id,
		ip_address=security.get_request_ip(),
	)
	return jsonify(result)


@app.get("/usage")
@login_required
def usage_page():
	tenant_id = g.current_tenant_id
	return render_template(
		"usage.html",
		usage_summary=usage.get_usage_summary(tenant_id),
		format_limit=plans.format_limit,
	)


@app.get("/audit-logs")
@login_required
def audit_logs():
	tenant_id = g.current_tenant_id
	filters = {
		"action": (request.args.get("action") or "").strip(),
		"entity_type": (request.args.get("entity_type") or "").strip(),
		"user_id": (request.args.get("user_id") or "").strip(),
	}
	return render_template(
		"audit_logs.html",
		logs=_audit_logs_for_tenant(tenant_id, filters=filters, limit=100),
		filters=filters,
	)


# Backward-compatible endpoint alias used by older templates.
app.add_url_rule("/tasks/new", endpoint="legacy_new_task", view_func=new_task, methods=["GET", "POST"])


@app.errorhandler(404)
def _not_found(_error):
	if security.get_current_user_id():
		flash("Page not found.", "warning")
		return redirect(url_for("dashboard"))
	return redirect(url_for("home"))


@app.errorhandler(ValueError)
def _value_error(error):
	flash(str(error), "warning")
	return redirect(request.referrer or url_for("dashboard")), 400


@app.errorhandler(sqlite3.OperationalError)
def _db_error(error):
	flash(f"Database error: {error}", "danger")
	return redirect(request.referrer or url_for("dashboard")), 500


if __name__ == "__main__":
	app.run(debug=not _is_production())
