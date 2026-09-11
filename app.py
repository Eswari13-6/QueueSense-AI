"""
app.py
------
Main Flask application entry point for QueueSense-AI.

Organizes routes into three Blueprints (MVC-style "controllers"):
  * auth_bp   -> registration / login / logout for both users and admins
  * user_bp   -> user dashboard, booking, tracking, history, profile
  * admin_bp  -> admin dashboard, queue control, services, analytics, reports

All actual business logic lives in the dedicated modules (token_manager,
queue_manager, analytics, reports, prediction) — routes here stay thin and
focused on request/response handling, validation, and session management.
"""

import os
import logging
import re
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    jsonify, send_from_directory, Blueprint
)
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config, ensure_directories
from database import db, init_db, today_str
import token_manager
import queue_manager
import analytics
import reports
from ai_model import predictor
from prediction import generate_suggestions


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app():
    ensure_directories()

    app = Flask(__name__)
    app.config.from_object(Config)

    _configure_logging()

    with app.app_context():
        init_db()
        predictor.ensure_ready()

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_helpers():
        return {"today": lambda: today_str()}

    @app.route("/")
    def home():
        return render_template("index.html")

    @app.route("/services-info")
    def services_info():
        services = token_manager.get_all_services()
        return render_template("services.html", services=services)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("index.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        logging.getLogger("queuesense.app").exception("Internal server error")
        flash("Something went wrong. Please try again.", "danger")
        return redirect(url_for("home"))

    return app


def _configure_logging():
    os.makedirs(Config.LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(Config.LOG_FILE),
            logging.StreamHandler(),
        ],
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9\-\s]{7,15}$")


def is_valid_email(value: str) -> bool:
    return bool(value and EMAIL_RE.match(value.strip()))


def is_valid_phone(value: str) -> bool:
    return bool(value and PHONE_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "admin_id" not in session:
            flash("Admin login required.", "warning")
            return redirect(url_for("auth.admin_login"))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Blueprint: Auth
# ---------------------------------------------------------------------------
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or len(full_name) < 2:
            flash("Please enter your full name.", "danger")
            return render_template("register.html")
        if not is_valid_email(email):
            flash("Please enter a valid email address.", "danger")
            return render_template("register.html")
        if not is_valid_phone(phone):
            flash("Please enter a valid phone number.", "danger")
            return render_template("register.html")
        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html")
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        existing = db.execute_one("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            flash("An account with this email already exists.", "danger")
            return render_template("register.html")

        db.execute(
            """INSERT INTO users (full_name, email, phone, password_hash)
               VALUES (?, ?, ?, ?)""",
            (full_name, email, phone, generate_password_hash(password)),
            commit=True,
        )
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = db.execute_one("SELECT * FROM users WHERE email = ?", (email,))
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["full_name"]
        flash(f"Welcome back, {user['full_name']}!", "success")
        return redirect(url_for("user.dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        admin = db.execute_one("SELECT * FROM admins WHERE username = ?", (username,))
        if not admin or not check_password_hash(admin["password_hash"], password):
            flash("Invalid admin credentials.", "danger")
            return render_template("admin_login.html")

        session.clear()
        session["admin_id"] = admin["id"]
        session["admin_name"] = admin["full_name"]
        flash(f"Welcome, {admin['full_name']}!", "success")
        return redirect(url_for("admin.dashboard"))

    return render_template("admin_login.html")


@auth_bp.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Admin logged out.", "info")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Blueprint: User
# ---------------------------------------------------------------------------
user_bp = Blueprint("user", __name__, url_prefix="/user")


@user_bp.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    active_tokens = token_manager.get_user_active_tokens(user_id)

    enriched = []
    for t in active_tokens:
        position = token_manager.queue_position(t["id"])
        enriched.append({**dict(t), **position})

    history = token_manager.get_user_history(user_id)[:5]
    services = token_manager.get_all_services()

    return render_template(
        "user_dashboard.html",
        active_tokens=enriched,
        history=history,
        services=services,
    )


@user_bp.route("/book", methods=["GET", "POST"])
@login_required
def book_token():
    services = token_manager.get_all_services()

    if request.method == "POST":
        service_id = request.form.get("service_id", type=int)
        priority = "normal"

        if not service_id:
            flash("Please choose a service.", "danger")
            return render_template("book_token.html", services=services)

        try:
            token = token_manager.book_token(session["user_id"], service_id, priority)
            flash(f"Token {token['token_number']} booked successfully!", "success")
            return redirect(url_for("user.track_token", token_id=token["id"]))
        except token_manager.TokenLimitExceeded as e:
            flash(str(e), "danger")
        except token_manager.ServicePausedError as e:
            flash(str(e), "warning")
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("book_token.html", services=services)


@user_bp.route("/track/<int:token_id>")
@login_required
def track_token(token_id):
    token = token_manager.get_token(token_id)
    if not token or token["user_id"] != session["user_id"]:
        flash("Token not found.", "danger")
        return redirect(url_for("user.dashboard"))

    position = token_manager.queue_position(token_id)
    return render_template("track_token.html", token=token, position=position)


@user_bp.route("/track/<int:token_id>/status")
@login_required
def track_token_status(token_id):
    """JSON endpoint polled by JS for live queue updates."""
    token = token_manager.get_token(token_id)
    if not token or token["user_id"] != session["user_id"]:
        return jsonify({"error": "not found"}), 404
    position = token_manager.queue_position(token_id)
    return jsonify(position)


@user_bp.route("/cancel/<int:token_id>", methods=["POST"])
@login_required
def cancel_token(token_id):
    ok = token_manager.cancel_token(token_id, session["user_id"])
    if ok:
        flash("Token cancelled.", "info")
    else:
        flash("Unable to cancel this token.", "danger")
    return redirect(url_for("user.dashboard"))


@user_bp.route("/history")
@login_required
def history():
    tokens = token_manager.get_user_history(session["user_id"])
    return render_template("user_dashboard.html", active_tokens=[], history=tokens,
                           services=token_manager.get_all_services())


@user_bp.route("/profile")
@login_required
def profile():
    user = db.execute_one("SELECT * FROM users WHERE id = ?", (session["user_id"],))
    return render_template("profile.html", user=user)


@user_bp.route("/qrcode/<int:token_id>")
@login_required
def download_qr(token_id):
    token = token_manager.get_token(token_id)
    if not token or token["user_id"] != session["user_id"]:
        flash("Token not found.", "danger")
        return redirect(url_for("user.dashboard"))
    filename = f"token_{token_id}.png"
    return send_from_directory(Config.QR_CODE_DIR, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Blueprint: Admin
# ---------------------------------------------------------------------------
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    summary = analytics.todays_summary()
    services = token_manager.get_all_services()

    service_states = []
    for s in services:
        live_queue = queue_manager.get_live_queue(s["id"])
        current = queue_manager.get_current_serving(s["id"])
        service_states.append({
            "service": s,
            "queue_length": len(live_queue),
            "current_serving": current,
            "daily_count": token_manager.daily_token_count(s["id"]),
        })

    return render_template(
        "admin_dashboard.html",
        summary=summary,
        service_states=service_states,
    )


@admin_bp.route("/queue/<int:service_id>")
@admin_required
def view_queue(service_id):
    service = token_manager.get_service(service_id)
    live_queue = queue_manager.get_live_queue(service_id)
    current = queue_manager.get_current_serving(service_id)
    users = db.execute("SELECT id, full_name FROM users ORDER BY full_name")
    return render_template(
        "admin_dashboard.html",
        summary=analytics.todays_summary(),
        service_states=[{
            "service": service,
            "queue_length": len(live_queue),
            "current_serving": current,
            "daily_count": token_manager.daily_token_count(service_id),
        }],
        selected_service=service,
        live_queue=live_queue,
        users=users,
    )


@admin_bp.route("/queue/<int:service_id>/call-next", methods=["POST"])
@admin_required
def call_next(service_id):
    token = queue_manager.call_next(service_id)
    if token:
        flash(f"Called token {token['token_number']}.", "success")
    else:
        flash("No tokens waiting in this queue.", "info")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/token/<int:token_id>/complete", methods=["POST"])
@admin_required
def complete_token(token_id):
    token = queue_manager.complete_token(token_id)
    service_id = token["service_id"] if token else request.form.get("service_id", type=int)
    if token:
        flash(f"Token {token['token_number']} marked completed.", "success")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/token/<int:token_id>/skip", methods=["POST"])
@admin_required
def skip_token(token_id):
    reason = request.form.get("reason", "No-show")
    token = queue_manager.skip_token(token_id, reason)
    service_id = token["service_id"] if token else request.form.get("service_id", type=int)
    if token:
        flash(f"Token {token['token_number']} skipped.", "warning")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/queue/<int:service_id>/pause", methods=["POST"])
@admin_required
def pause_queue(service_id):
    queue_manager.pause_queue(service_id)
    flash("Queue paused.", "warning")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/queue/<int:service_id>/resume", methods=["POST"])
@admin_required
def resume_queue(service_id):
    queue_manager.resume_queue(service_id)
    flash("Queue resumed.", "success")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/queue/<int:service_id>/set-limit", methods=["POST"])
@admin_required
def set_limit(service_id):
    max_tokens = request.form.get("max_tokens", type=int)
    if max_tokens and max_tokens > 0:
        queue_manager.set_max_daily_tokens(service_id, max_tokens)
        flash(f"Daily token limit updated to {max_tokens}.", "success")
    else:
        flash("Please provide a valid limit.", "danger")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/queue/<int:service_id>/issue-priority", methods=["POST"])
@admin_required
def issue_priority(service_id):
    user_id = request.form.get("user_id", type=int)
    priority = request.form.get("priority", "vip")
    if not user_id:
        flash("Please select a user for the priority token.", "danger")
        return redirect(url_for("admin.view_queue", service_id=service_id))

    try:
        token = queue_manager.issue_priority_token(user_id, service_id, priority)
        flash(f"{priority.upper()} token {token['token_number']} issued.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("admin.view_queue", service_id=service_id))


@admin_bp.route("/services", methods=["GET", "POST"])
@admin_required
def manage_services():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            counters = request.form.get("counters", type=int) or 1
            avg_minutes = request.form.get("avg_service_minutes", type=float) or 5.0
            max_tokens = request.form.get("max_daily_tokens", type=int) or Config.DEFAULT_MAX_DAILY_TOKENS

            if not name:
                flash("Service name is required.", "danger")
            else:
                try:
                    db.execute(
                        """INSERT INTO services (name, description, counters, avg_service_minutes, max_daily_tokens)
                           VALUES (?, ?, ?, ?, ?)""",
                        (name, description, counters, avg_minutes, max_tokens),
                        commit=True,
                    )
                    flash(f"Service '{name}' created.", "success")
                except Exception:
                    flash("A service with that name already exists.", "danger")

        elif action == "update":
            service_id = request.form.get("service_id", type=int)
            counters = request.form.get("counters", type=int)
            avg_minutes = request.form.get("avg_service_minutes", type=float)
            max_tokens = request.form.get("max_daily_tokens", type=int)
            db.execute(
                """UPDATE services SET counters = ?, avg_service_minutes = ?, max_daily_tokens = ?
                   WHERE id = ?""",
                (counters, avg_minutes, max_tokens, service_id),
                commit=True,
            )
            flash("Service updated.", "success")

        elif action == "toggle_active":
            service_id = request.form.get("service_id", type=int)
            db.execute(
                "UPDATE services SET is_active = 1 - is_active WHERE id = ?",
                (service_id,),
                commit=True,
            )
            flash("Service status toggled.", "info")

        return redirect(url_for("admin.manage_services"))

    services = token_manager.get_all_services(active_only=False)
    return render_template("admin_dashboard.html", services=services, manage_mode=True,
                           summary=analytics.todays_summary())


@admin_bp.route("/analytics")
@admin_required
def analytics_view():
    data = {
        "hourly": analytics.hourly_visitors(),
        "daily": analytics.daily_visitors(),
        "monthly": analytics.monthly_visitors(),
        "avg_wait": analytics.average_waiting_time_by_service(),
        "utilization": analytics.counter_utilization(),
        "completed_cancelled": analytics.completed_vs_cancelled(),
        "accuracy": analytics.prediction_accuracy(),
        "breakdown": analytics.service_breakdown(),
    }

    suggestions = []
    for s in token_manager.get_all_services():
        queue_len = token_manager.current_queue_length(s["id"])
        from prediction import predict_wait_time
        pred = predict_wait_time(s["name"], queue_len, s["counters"])
        suggestions.extend(
            generate_suggestions(
                s["name"], queue_len, s["counters"], s["counters"] + 2,
                pred["predicted_wait_minutes"], pred["crowd_level"],
            )
        )

    return render_template("analytics.html", data=data, suggestions=suggestions,
                            model_metrics=predictor.metrics)


@admin_bp.route("/reports", methods=["GET", "POST"])
@admin_required
def reports_view():
    if request.method == "POST":
        report_type = request.form.get("report_type")
        if report_type == "daily":
            reports.generate_daily_report(generated_by=session.get("admin_name", "admin"))
            flash("Daily report generated.", "success")
        elif report_type == "accuracy":
            reports.generate_accuracy_report(generated_by=session.get("admin_name", "admin"))
            flash("Accuracy report generated.", "success")
        return redirect(url_for("admin.reports_view"))

    all_reports = reports.list_reports()
    return render_template("reports.html", reports=all_reports)


@admin_bp.route("/reports/download/<path:filename>")
@admin_required
def download_report(filename):
    return send_from_directory(Config.REPORT_DIR, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
