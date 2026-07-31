import os
import json
import re
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import inspect, text
from dotenv import load_dotenv

def load_environment(project_root=None):
    root = Path(project_root or Path(__file__).resolve().parent).resolve()
    load_dotenv(root / ".env", override=False)
    return root

PROJECT_ROOT = load_environment()

app = Flask(__name__)
# NEVER hardcode this in real code — set FLASK_SECRET_KEY in your environment.
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-me')

# 1. DATABASE SETUP
# Locally this defaults to SQLite (zero setup). In production, set DATABASE_URL
# to a Postgres connection string — most hosts (Render, Railway, etc.) have an
# ephemeral filesystem, so a SQLite file gets wiped on every deploy/restart.
_database_url = os.environ.get('DATABASE_URL', 'sqlite:///clavi.db')
if _database_url.startswith('postgres://'):
    # Some hosts still hand out the old 'postgres://' scheme; SQLAlchemy 1.4+/2.x needs 'postgresql://'.
    _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 2. LOGIN MANAGER SETUP
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'signup'  # new visitors are sent to sign up first; the page links to /login for existing users

# Keep users signed in long-term (1 year) instead of forgetting them when the
# browser session ends — this is what makes signup a true one-time step.
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=365)
app.config['REMEMBER_COOKIE_SECURE'] = False  # Render terminates TLS upstream; browser still sees https
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

# 3. APP DOMAIN (used for password-reset links etc.)
YOUR_DOMAIN = os.environ.get('APP_DOMAIN', 'http://localhost:5000')


def get_admin_emails():
    configured = os.environ.get('ADMIN_EMAILS', '') or os.environ.get('OWNER_EMAIL', '')
    if not configured:
        return set()
    return {
        email.strip().lower()
        for email in configured.replace(';', ',').split(',')
        if email.strip()
    }


def has_unlimited_access(user):
    if user is None:
        return False

    if getattr(user, 'is_admin', False):
        return True

    email = getattr(user, 'email', '') or ''
    normalized_email = email.strip().lower()
    if normalized_email in get_admin_emails():
        return True

    user_id = getattr(user, 'id', None)
    return user_id == 1

# -------------------------------------------------------------------------
# PASSWORD RESET (stateless tokens — no extra DB column needed)
# -------------------------------------------------------------------------
reset_serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
RESET_TOKEN_MAX_AGE = 3600  # 1 hour

# SMTP config — set these to actually send reset emails. If SMTP_HOST is
# unset, the reset link is written to the server log instead (handy for
# local dev), never shown in the browser.
SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER')
SMTP_PASS = os.environ.get('SMTP_PASS')
MAIL_FROM = os.environ.get('MAIL_FROM', SMTP_USER or 'no-reply@clavi.app')


def send_reset_email(to_email, reset_url):
    if not SMTP_HOST:
        app.logger.info(f"[password reset] SMTP not configured — reset link for {to_email}: {reset_url}")
        return

    msg = MIMEText(
        f"Someone requested a password reset for your Clavis account.\n\n"
        f"Reset your password here (link expires in 1 hour):\n{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email."
    )
    msg['Subject'] = 'Reset your Clavis password'
    msg['From'] = MAIL_FROM
    msg['To'] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [to_email], msg.as_string())

# -------------------------------------------------------------------------
# GROQ CONFIGURATION (replaces Ollama)
# -------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')  # set this in your environment
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
GROQ_VISION_MODEL = os.environ.get('GROQ_VISION_MODEL', 'meta-llama/llama-4-scout-17b-16e-instruct')
MAX_MESSAGE_LENGTH = 800
MAX_HISTORY_MESSAGES = 12  # trim long conversations before sending to the API

if not GROQ_API_KEY:
    app.logger.warning("GROQ_API_KEY is not present in the process environment at startup")

# -------------------------------------------------------------------------
# DATABASE MODELS
# -------------------------------------------------------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_premium = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    expiry_date = db.Column(db.DateTime, nullable=True)
    mpesa_code = db.Column(db.String(20), nullable=True)
    mpesa_tier = db.Column(db.String(20), nullable=True)
    mpesa_status = db.Column(db.String(20), nullable=True)  # 'pending', 'approved', 'rejected'
    mpesa_submitted_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def subscription_expired(self):
        if has_unlimited_access(self):
            return False
        if not self.expiry_date:
            return True
        return datetime.now() > self.expiry_date


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -------------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------------
def call_groq(messages, model=None):
    api_key = os.environ.get('GROQ_API_KEY') or GROQ_API_KEY
    if not api_key:
        app.logger.error("Groq request failed because GROQ_API_KEY is missing from the runtime environment")
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your server environment or to the project .env file."
        )
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or GROQ_MODEL,
                "messages": messages,
                "temperature": 0.3,
            },
            timeout=45,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Groq error: {str(e)}")
    except (KeyError, IndexError):
        raise RuntimeError("Groq returned an unexpected response shape")


def extract_json(text):
    """Strip markdown code fences and parse the JSON the model returned."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def describe_level(level, grade_form):
    if level == "cbc":
        return f"{grade_form or 'Grade 6'} (CBC) in Kenya"
    if level == "upper_primary":
        return f"{grade_form or 'Grade 4'} Upper Primary in Kenya"
    if level == "junior_school":
        return f"{grade_form or 'Grade 7'} Junior School in Kenya"
    if level == "senior_school":
        return f"{grade_form or 'Grade 10'} Senior School in Kenya"
    return f"{grade_form or 'Form 1'} secondary school (KCSE track) in Kenya"


def build_system_prompt(subject, level, grade_form=None):
    level_desc = describe_level(level, grade_form)
    return (
        f"You are a warm, patient, encouraging tutor helping a {level_desc} student with {subject}. "
        f"Explain concepts clearly, simply, and step by step. Use examples relevant to Kenya."
    )

# -------------------------------------------------------------------------
# AUTHENTICATION ROUTES (Login / Signup)
# -------------------------------------------------------------------------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'danger')
            return redirect(url_for('signup'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'danger')
            return redirect(url_for('signup'))

        trial_end = datetime.now() + timedelta(days=1)

        ADMIN_EMAIL = "silasbarry805@gmail.com"

        is_owner = email == ADMIN_EMAIL.lower()

        new_user = User(
            email=email,
            is_admin=is_owner,
            is_premium=is_owner,
            expiry_date=None if is_owner else trial_end
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user, remember=True)
        return redirect(url_for('home'))

    return render_template('auth.html', mode='signup')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for('home'))
        flash('Invalid credentials!', 'danger')

    return render_template('auth.html', mode='login')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            token = reset_serializer.dumps(email, salt='password-reset')
            reset_url = f"{YOUR_DOMAIN}/reset-password/{token}"
            try:
                send_reset_email(email, reset_url)
            except Exception as e:
                app.logger.error(f"Failed to send reset email to {email}: {e}")

        # Same message whether or not the email exists — avoids leaking
        # which addresses are registered.
        flash("If that email is registered, a reset link is on its way. Check your inbox.", "success")
        return redirect(url_for('login'))

    return render_template('auth.html', mode='forgot')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = reset_serializer.loads(token, salt='password-reset', max_age=RESET_TOKEN_MAX_AGE)
    except SignatureExpired:
        flash("That reset link has expired. Please request a new one.", "danger")
        return redirect(url_for('forgot_password'))
    except BadSignature:
        flash("That reset link isn't valid. Please request a new one.", "danger")
        return redirect(url_for('forgot_password'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("That reset link isn't valid. Please request a new one.", "danger")
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not password or len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template('auth.html', mode='reset', token=token)

        if password != confirm:
            flash("Passwords don't match.", "danger")
            return render_template('auth.html', mode='reset', token=token)

        user.set_password(password)
        db.session.commit()
        flash("Password updated — you can log in now.", "success")
        return redirect(url_for('login'))

    return render_template('auth.html', mode='reset', token=token)

# -------------------------------------------------------------------------
# MAIN APP PAGE
# -------------------------------------------------------------------------
@app.route('/')
def home():
    if current_user.is_authenticated:
        return render_template(
            'index.html',
            user=current_user,
            subscription_expired=current_user.subscription_expired,
            is_admin_user=has_unlimited_access(current_user),
        )

    return render_template(
        'landing.html',
        title='Clavis — Study Help',
        description='A friendly AI study tutor for students in Kenya, with help for Mathematics, English, and more.',
    )


@app.route('/robots.txt')
def robots_txt():
    content = (
        "User-agent: *\n"
        f"Allow: /\n"
        f"Sitemap: {request.host_url}sitemap.xml\n"
    )
    return Response(content, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    urls = [
        request.host_url,
        f"{request.host_url}login",
        f"{request.host_url}signup",
        f"{request.host_url}forgot-password",
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        xml += '  <url>\n'
        xml += f'    <loc>{url}</loc>\n'
        xml += '    <changefreq>weekly</changefreq>\n'
        xml += '    <priority>0.8</priority>\n'
        xml += '  </url>\n'
    xml += '</urlset>\n'
    return Response(xml, mimetype='application/xml')


@app.route('/logo.png')
def serve_logo():
    return send_from_directory(PROJECT_ROOT, 'logo.png', mimetype='image/png+xml')

# -------------------------------------------------------------------------
# MANUAL M-PESA PAYMENT (no business Till/Paybill or bank account required)
#
# How it works:
#   1. Buyer sends money via M-Pesa "Send Money" to MPESA_PAYEE_NUMBER below.
#   2. Buyer copies the M-Pesa confirmation code (e.g. "QGH7X9K2LP") from
#      their SMS and submits it on /pay-mpesa.
#   3. You (the owner) check your M-Pesa messages for that code, then
#      approve it from /admin/mpesa-requests — this flips is_premium=True.
#
# This is intentionally manual — it needs zero API keys, zero business
# registration, and zero approval wait, which is what makes it usable the
# same day. Set MPESA_PAYEE_NAME / MPESA_PAYEE_NUMBER in your environment
# once you know which phone number/name you're collecting on.
# -------------------------------------------------------------------------
MPESA_PAYEE_NAME = 'SHARON'
MPESA_PAYEE_NUMBER = '0718675377'
MPESA_TIER_PRICES = {
    "day": os.environ.get('MPESA_PRICE_DAY', '50'),
    "month": os.environ.get('MPESA_PRICE_MONTH', '1300'),
}
# How long access lasts once a tier is approved.
MPESA_TIER_DURATIONS = {
    "day": timedelta(days=1),
    "month": timedelta(days=30),
}


@app.route('/pay-mpesa', methods=['GET', 'POST'])
@login_required
def pay_mpesa():
    if request.method == 'POST':
        code = (request.form.get('mpesa_code') or '').strip().upper()
        tier = request.form.get('tier') or 'day'
        if tier not in MPESA_TIER_PRICES:
            tier = 'day'
        if not code or len(code) < 6:
            flash("That doesn't look like a valid M-Pesa code. Check your SMS and try again.", "danger")
            return redirect(url_for('pay_mpesa'))
        current_user.mpesa_code = code
        current_user.mpesa_tier = tier
        current_user.mpesa_status = 'pending'
        current_user.mpesa_submitted_at = datetime.utcnow()
        db.session.commit()
        flash("Got it — we'll confirm your payment and unlock access shortly.", "success")
        return redirect(url_for('home'))

    requested_tier = request.args.get('tier', 'day')
    if requested_tier not in MPESA_TIER_PRICES:
        requested_tier = 'day'

    return render_template(
        'pay_mpesa.html',
        payee_name=MPESA_PAYEE_NAME,
        payee_number=MPESA_PAYEE_NUMBER,
        prices=MPESA_TIER_PRICES,
        selected_tier=requested_tier,
    )


@app.route('/admin/mpesa-requests')
@login_required
def admin_mpesa_requests():
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    pending = User.query.filter_by(mpesa_status='pending').order_by(User.mpesa_submitted_at.desc()).all()
    return render_template('admin_mpesa.html', requests=pending)


@app.route('/admin/mpesa-requests/<int:user_id>/approve', methods=['POST'])
@login_required
def admin_approve_mpesa(user_id):
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    user = User.query.get_or_404(user_id)
    duration = MPESA_TIER_DURATIONS.get(user.mpesa_tier, MPESA_TIER_DURATIONS['day'])
    user.is_premium = True
    user.mpesa_status = 'approved'
    user.expiry_date = datetime.now() + duration
    db.session.commit()
    flash(f"Approved {user.email} — access unlocked until {user.expiry_date.strftime('%d %b %Y')}.", "success")
    return redirect(url_for('admin_mpesa_requests'))


@app.route('/admin/mpesa-requests/<int:user_id>/reject', methods=['POST'])
@login_required
def admin_reject_mpesa(user_id):
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    user = User.query.get_or_404(user_id)
    user.mpesa_status = 'rejected'
    db.session.commit()
    flash(f"Rejected the request from {user.email}.", "warning")
    return redirect(url_for('admin_mpesa_requests'))

# -------------------------------------------------------------------------
# TUTOR API ROUTES (now backed by Groq instead of Ollama)
# -------------------------------------------------------------------------
@app.route('/api/tutor', methods=['POST'])
@login_required
def api_tutor():
    if current_user.subscription_expired and not has_unlimited_access(current_user):
        return jsonify({"error": "Your trial has ended — please choose a plan."}), 402

    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')
    response_style = data.get('responseStyle', 'detailed')
    attachment = data.get('attachment')
    messages = data.get('messages', [])

    if not subject or not messages:
        return jsonify({"error": "subject and messages are required"}), 400

    system_prompt = build_system_prompt(subject, level, grade_form)
    if response_style == 'concise':
        system_prompt += " Keep answers brief and focused: use only the key steps, then offer to explain more."
    else:
        system_prompt += " Give a clear, supportive explanation with enough working for the learner to understand why."
    groq_messages = [{"role": "system", "content": system_prompt}]
    for m in messages[-MAX_HISTORY_MESSAGES:]:
        role = 'assistant' if m.get('role') == 'assistant' else 'user'
        content = (m.get('content') or '')[:MAX_MESSAGE_LENGTH]
        groq_messages.append({"role": role, "content": content})

    vision_model = None
    if attachment:
        image_data = attachment.get('data', '') if isinstance(attachment, dict) else ''
        mime_type = attachment.get('type', '') if isinstance(attachment, dict) else ''
        if mime_type not in {'image/jpeg', 'image/png', 'image/webp'} or not image_data.startswith('data:image/'):
            return jsonify({"error": "Please attach a JPG, PNG, or WEBP image."}), 400
        if len(image_data) > 7_000_000:
            return jsonify({"error": "That image is too large. Please choose one under 5 MB."}), 400
        last_message = groq_messages[-1]
        last_message['content'] = [
            {"type": "text", "text": last_message['content']},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]
        vision_model = GROQ_VISION_MODEL

    try:
        answer = call_groq(groq_messages, vision_model)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"answer": answer})


@app.route('/api/topics', methods=['POST'])
@login_required
def api_topics():
    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')

    if not subject:
        return jsonify({"error": "subject is required"}), 400

    level_desc = describe_level(level, grade_form)
    prompt = (
        f"List 6 to 9 study topics for {subject} for a {level_desc} student, "
        f'as a JSON array of short topic name strings, e.g. ["Topic 1", "Topic 2"]. '
        f"Return ONLY the JSON array — no commentary, no markdown fences."
    )

    try:
        raw = call_groq([{"role": "user", "content": prompt}])
        topics = extract_json(raw)
        if not isinstance(topics, list):
            raise ValueError("Expected a JSON array of topics")
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": f"Could not generate topics: {str(e)}"}), 502

    return jsonify({"topics": topics})


@app.route('/api/generate-test', methods=['POST'])
@login_required
def api_generate_test():
    if current_user.subscription_expired and not has_unlimited_access(current_user):
        return jsonify({"error": "Your trial has ended — please choose a plan."}), 402

    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')
    topic = data.get('topic')
    all_topics = data.get('allTopics', [])

    if not subject:
        return jsonify({"error": "subject is required"}), 400

    level_desc = describe_level(level, grade_form)
    scope = f"the topic '{topic}'" if topic else f"all of these topics: {', '.join(all_topics)}"

    prompt = f"""Create a short test for a {level_desc} student on {subject}, covering {scope}.
Return ONLY valid JSON in exactly this shape, no commentary, no markdown fences:
{{
  "total_marks": <int>,
  "questions": [
    {{"id": 1, "type": "mcq", "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "A", "marks": 1, "explanation": "..."}},
    {{"id": 2, "type": "short", "question": "...", "marks": 3, "model_answer": "...", "marking_points": ["...", "...", "..."]}}
  ]
}}
Include 5 to 8 questions total, mixing "mcq" and "short" types. Number "id" sequentially starting at 1."""

    try:
        raw = call_groq([{"role": "user", "content": prompt}])
        test_data = extract_json(raw)
        if "questions" not in test_data or "total_marks" not in test_data:
            raise ValueError("Response missing required fields")
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": f"Could not generate test: {str(e)}"}), 502

    return jsonify(test_data)


@app.route('/healthz')
def healthz():
    """Simple health check most hosting platforms ping to confirm the app is alive."""
    return jsonify({"status": "ok"})


@app.route('/debug/config')
def debug_config():
    """
    TEMPORARY diagnostic route — shows which env vars actually loaded, without
    leaking full secret values. Delete this route once things are working;
    don't leave it in a production deploy.
    """
    def mask(value):
        if not value:
            return None
        return value[:6] + "..." + f"({len(value)} chars)"

    return jsonify({
        "GROQ_API_KEY": mask(os.environ.get('GROQ_API_KEY')),
        "DATABASE_URL": mask(os.environ.get('DATABASE_URL')),
        "APP_DOMAIN": os.environ.get('APP_DOMAIN'),
        "MPESA_PRICE_DAY": os.environ.get('MPESA_PRICE_DAY'),
        "MPESA_PRICE_MONTH": os.environ.get('MPESA_PRICE_MONTH'),
    })


def ensure_database_schema():
    with app.app_context():
        db.create_all()
        try:
            inspector = inspect(db.engine)
            if 'user' in inspector.get_table_names():
                columns = {column['name'] for column in inspector.get_columns('user')}
                if 'is_admin' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "is_admin" BOOLEAN DEFAULT FALSE'))
                    db.session.commit()
                if 'mpesa_code' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "mpesa_code" VARCHAR(20)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "mpesa_tier" VARCHAR(20)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "mpesa_status" VARCHAR(20)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "mpesa_submitted_at" DATETIME'))
                    db.session.commit()
        except Exception as exc:
            app.logger.warning(f"Could not ensure admin column exists: {exc}")


# Create tables on import, not just when run directly — gunicorn imports this
# module as `app:app` and never executes the `if __name__ == '__main__'` block below.
ensure_database_schema()

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode)
