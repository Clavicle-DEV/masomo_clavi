import os
import json
import re
import smtplib
import io
import zipfile
import hashlib
import secrets
import warnings
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
try:
    from flask_limiter import Limiter
except ImportError:
    class Limiter:
        """Compatibility fallback when Flask-Limiter is not installed."""
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, _name):
            return lambda *args, **kwargs: (lambda view: view)
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import deferred
from dotenv import load_dotenv
from PIL import Image, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

def load_environment(project_root=None):
    root = Path(project_root or Path(__file__).resolve().parent).resolve()
    load_dotenv(root / ".env", override=False)
    return root

PROJECT_ROOT = load_environment()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-me')


_static_version_cache = {}

def static_versioned(filename):
    """Returns the normal /static/<filename> URL with a ?v=<hash> query string
    based on the file's actual current content, so browsers are forced to
    fetch a fresh copy the instant the file changes on deploy — instead of
    silently serving a stale cached CSS/JS file forever, which is exactly
    what happened before this existed."""
    full_path = os.path.join(app.static_folder, filename)
    try:
        mtime = os.path.getmtime(full_path)
    except OSError:
        return url_for('static', filename=filename)
    cached = _static_version_cache.get(filename)
    if cached and cached[0] == mtime:
        version = cached[1]
    else:
        import hashlib
        with open(full_path, 'rb') as f:
            version = hashlib.md5(f.read()).hexdigest()[:10]
        _static_version_cache[filename] = (mtime, version)
    return f"{url_for('static', filename=filename)}?v={version}"


@app.context_processor
def inject_static_versioned():
    return {'static_versioned': static_versioned}

_database_url = os.environ.get('DATABASE_URL', 'sqlite:///clavi.db')
if _database_url.startswith('postgres://'):
    _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'signup'

def rate_limit_key():
    """Rate-limit by logged-in account when we have one (so a shared school
    WiFi doesn't get one student's usage blocking everyone else on it), and
    fall back to IP address for routes hit before login (signup/login itself)."""
    try:
        if current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return request.remote_addr or 'unknown'

def is_unlimited_rate_limit_user():
    """Admins and co-admins already have unlimited AI-call quota
    (has_unlimited_access) — exempt them from rate limiting too."""
    try:
        return current_user.is_authenticated and has_unlimited_access(current_user)
    except Exception:
        return False

limiter = Limiter(
    key_func=rate_limit_key,
    app=app,
    default_limits=["200 per hour"],
    default_limits_exempt_when=is_unlimited_rate_limit_user,
    storage_uri="memory://",
)
limiter.exempt(app.view_functions['static'])


@app.errorhandler(429)
def handle_rate_limit(e):
    return jsonify({"error": "You're sending requests a bit fast — please wait a moment and try again."}), 429

app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=365)
app.config['REMEMBER_COOKIE_SECURE'] = False
app.config['REMEMBER_COOKIE_HTTPONLY'] = True

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


# Co-admins get free unlimited access and can approve/reject M-Pesa requests,
# but they don't get to see total-user counts or revenue figures — those stay
# on the analytics/users pages, which are gated to full admins only.
CO_ADMIN_EMAILS = {
    'silasclavicle@gmail.com',
    'kaybrighton7@gmail.com',
}


def is_co_admin(user):
    if user is None:
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    return email in CO_ADMIN_EMAILS


def is_full_admin(user):
    if user is None:
        return False
    if getattr(user, 'is_admin', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    if email in get_admin_emails() and email not in CO_ADMIN_EMAILS:
        return True
    return False


def has_unlimited_access(user):
    if user is None:
        return False
    if is_full_admin(user):
        return True
    if is_co_admin(user):
        return True
    return False

reset_serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
RESET_TOKEN_MAX_AGE = 3600
EMAIL_VERIFY_TOKEN_MAX_AGE = 86400

SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER')
SMTP_PASS = os.environ.get('SMTP_PASS')
MAIL_FROM = os.environ.get('MAIL_FROM', SMTP_USER or 'no-reply@clavi.app')

# SMS via Africa's Talking (https://africastalking.com). Leave unset to run
# in log-only mode — reminders still work by email either way.
AT_USERNAME = os.environ.get('AFRICASTALKING_USERNAME')
AT_API_KEY = os.environ.get('AFRICASTALKING_API_KEY')
AT_SENDER_ID = os.environ.get('AFRICASTALKING_SENDER_ID')  # optional shortcode/alphanumeric ID
AT_SMS_URL = "https://api.africastalking.com/version1/messaging"

# WhatsApp — also via Africa's Talking, same account/credentials as SMS, but
# needs one extra thing: a WhatsApp sender number registered and approved in
# your AT dashboard (this is a separate provisioning step from SMS — it
# involves Meta business verification and takes some days). Leave
# AFRICASTALKING_WHATSAPP_NUMBER unset to skip WhatsApp and fall back to SMS.
# Note: WhatsApp's own rules mean a proactive reminder like this one may need
# to be sent as a pre-approved message template rather than free text once
# outside a 24-hour customer-initiated conversation window — check your AT
# WhatsApp dashboard for template requirements before relying on this in
# production.
AT_WHATSAPP_NUMBER = os.environ.get('AFRICASTALKING_WHATSAPP_NUMBER')
AT_WHATSAPP_URL = "https://chat.africastalking.com/whatsapp/message/send"


def normalize_kenyan_phone(raw):
    """Best-effort normalize a Kenyan number to E.164 (+2547XXXXXXXX /
    +2541XXXXXXXX). Returns None if it doesn't look like a valid number."""
    if not raw:
        return None
    digits = re.sub(r'[^\d+]', '', raw.strip())
    if digits.startswith('+254') and len(digits) == 13:
        return digits
    if digits.startswith('254') and len(digits) == 12:
        return '+' + digits
    if digits.startswith('0') and len(digits) == 10:
        return '+254' + digits[1:]
    if digits.startswith('7') or digits.startswith('1'):
        if len(digits) == 9:
            return '+254' + digits
    return None


def send_sms(phone_number, message):
    """Send an SMS via Africa's Talking. No-ops (just logs) if credentials
    aren't configured, mirroring how email behaves without SMTP set up."""
    if not AT_USERNAME or not AT_API_KEY:
        app.logger.info(f"[sms] Africa's Talking not configured — would text {phone_number}: {message}")
        return False

    payload = {
        "username": AT_USERNAME,
        "to": phone_number,
        "message": message,
    }
    if AT_SENDER_ID:
        payload["from"] = AT_SENDER_ID

    headers = {
        "apiKey": AT_API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    resp = requests.post(AT_SMS_URL, data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return True


def send_whatsapp(phone_number, message):
    """Send a WhatsApp message via Africa's Talking. No-ops (just logs) if
    the WhatsApp sender number isn't configured — callers should fall back
    to send_sms() in that case."""
    if not AT_USERNAME or not AT_API_KEY or not AT_WHATSAPP_NUMBER:
        app.logger.info(f"[whatsapp] Africa's Talking WhatsApp not configured — would message {phone_number}: {message}")
        return False

    payload = {
        "username": AT_USERNAME,
        "waNumber": AT_WHATSAPP_NUMBER,
        "phoneNumber": phone_number,
        "body": {"message": message},
    }
    headers = {
        "apikey": AT_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(AT_WHATSAPP_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return True


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


def send_verification_email(to_email, verify_url):
    if not SMTP_HOST:
        app.logger.info(f"[email verify] SMTP not configured — verify link for {to_email}: {verify_url}")
        return

    msg = MIMEText(
        f"Welcome to Clavis!\n\n"
        f"Please verify your email to unlock full tutor access (link expires in 24 hours):\n{verify_url}\n\n"
        f"If you didn't create this account, you can safely ignore this email."
    )
    msg['Subject'] = 'Verify your Clavis account'
    msg['From'] = MAIL_FROM
    msg['To'] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [to_email], msg.as_string())


def send_expiry_reminder_email(to_email, is_premium, expiry_date):
    if not SMTP_HOST:
        app.logger.info(f"[expiry reminder] SMTP not configured — would remind {to_email}, expiry {expiry_date}")
        return

    when = expiry_date.strftime('%d %b %Y at %I:%M %p') if expiry_date else 'soon'
    pay_url = f"{YOUR_DOMAIN}/pay-mpesa"

    if is_premium:
        body = (
            f"Your Clavis premium access expires on {when}.\n\n"
            f"Renew here to keep unlimited tutor access, without losing your streak:\n{pay_url}\n\n"
            f"If you've already renewed, you can ignore this message."
        )
        subject = 'Your Clavis premium access expires soon'
    else:
        body = (
            f"Your Clavis free trial expires on {when}.\n\n"
            f"Choose a plan to keep studying with your AI tutor after that:\n{pay_url}\n\n"
            f"If you've already upgraded, you can ignore this message."
        )
        subject = 'Your Clavis free trial is ending soon'

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = MAIL_FROM
    msg['To'] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [to_email], msg.as_string())


def sms_expiry_reminder_text(is_premium, expiry_date):
    when = expiry_date.strftime('%d %b') if expiry_date else 'soon'
    pay_url = f"{YOUR_DOMAIN}/pay-mpesa"
    if is_premium:
        return f"Clavis: your premium access expires {when}. Renew here to keep unlimited tutor access: {pay_url}"
    return f"Clavis: your free trial expires {when}. Choose a plan to keep studying: {pay_url}"


GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')
GROQ_VISION_MODEL = os.environ.get('GROQ_VISION_MODEL', 'qwen/qwen3.6-27b')
MAX_MESSAGE_LENGTH = 800
MAX_HISTORY_MESSAGES = 12

# Daily cap on AI calls (tutor messages + topic lists + generated tests
# combined) per user, to protect the Groq free-tier quota from bursts or
# throwaway accounts. Admins/co-admins are exempt. Override via env if needed.
FREE_DAILY_AI_LIMIT = int(os.environ.get('FREE_DAILY_AI_LIMIT', '40'))
PREMIUM_DAILY_AI_LIMIT = int(os.environ.get('PREMIUM_DAILY_AI_LIMIT', '200'))

if not GROQ_API_KEY:
    app.logger.warning("GROQ_API_KEY is not present in the process environment at startup")

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_premium = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    expiry_date = db.Column(db.DateTime, nullable=True)
    mpesa_code = db.Column(db.String(20), nullable=True)
    mpesa_tier = db.Column(db.String(20), nullable=True)
    mpesa_status = db.Column(db.String(20), nullable=True)
    mpesa_submitted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True)
    referral_code = db.Column(db.String(12), unique=True, nullable=True)
    referred_by_id = db.Column(db.Integer, nullable=True)
    email_verified = db.Column(db.Boolean, default=True)
    daily_ai_calls = db.Column(db.Integer, default=0)
    daily_ai_calls_date = db.Column(db.String(10), nullable=True)
    expiry_reminder_for = db.Column(db.DateTime, nullable=True)
    display_name = db.Column(db.String(40), nullable=True)
    leaderboard_opt_in = db.Column(db.Boolean, default=True)
    phone_number = db.Column(db.String(20), nullable=True)
    student_track = db.Column(db.String(20), nullable=True)  # 'campus' or 'highschool'
    elementary_grade = db.Column(db.String(12), nullable=True)
    campus_course = db.Column(db.String(100), nullable=True)
    campus_year = db.Column(db.String(12), nullable=True)
    favorite_subject = db.Column(db.String(100), nullable=True)
    challenging_subject = db.Column(db.String(100), nullable=True)
    challenging_subjects_json = db.Column(db.Text, nullable=True)
    study_schedule_json = db.Column(db.Text, nullable=True)
    daily_goal_minutes = db.Column(db.Integer, nullable=True)
    starting_topic = db.Column(db.String(100), nullable=True)
    onboarding_completed = db.Column(db.Boolean, default=True, nullable=False)
    failed_login_attempts = db.Column(db.Integer, default=0)
    lockout_until = db.Column(db.DateTime, nullable=True)
    daily_challenge_date = db.Column(db.String(10), nullable=True)  # date the cached question is for
    daily_challenge_json = db.Column(db.Text, nullable=True)  # cached {subject, question, options, correct, explanation}
    daily_challenge_answered = db.Column(db.Boolean, default=False)  # answered (correctly or not) today
    daily_challenge_streak = db.Column(db.Integer, default=0)
    daily_challenge_best_streak = db.Column(db.Integer, default=0)
    daily_challenge_last_correct_date = db.Column(db.String(10), nullable=True)

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


def password_validation_error(password):
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r'[A-Za-z]', password):
        return "Password must include at least one letter."
    if not re.search(r'[0-9]', password):
        return "Password must include at least one number."
    return None


def find_user_by_email(email):
    normalized_email = (email or '').strip().lower()
    if not normalized_email:
        return None
    return User.query.filter(func.lower(func.trim(User.email)) == normalized_email).first()


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def check_and_consume_ai_call(user):
    """Enforce the daily AI-call cap. Returns (allowed, remaining_or_None).

    Resets the counter automatically at the start of a new day. Users with
    unlimited access (full admins, co-admins) are exempt. Active premium
    users get a higher cap than free-trial users.
    """
    if has_unlimited_access(user):
        return True, None

    if user.is_premium and user.expiry_date and user.expiry_date > datetime.now():
        limit = PREMIUM_DAILY_AI_LIMIT
    else:
        limit = FREE_DAILY_AI_LIMIT

    today = datetime.now().strftime('%Y-%m-%d')
    if user.daily_ai_calls_date != today:
        user.daily_ai_calls_date = today
        user.daily_ai_calls = 0

    if (user.daily_ai_calls or 0) >= limit:
        return False, 0

    user.daily_ai_calls = (user.daily_ai_calls or 0) + 1
    db.session.commit()
    return True, limit - user.daily_ai_calls


class PaymentLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tier = db.Column(db.String(20), nullable=True)
    amount = db.Column(db.Integer, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)


class UserStats(db.Model):
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    total = db.Column(db.Integer, default=0)
    by_subject_json = db.Column(db.Text, nullable=True)  # JSON-encoded {subject: count}
    last_active_date = db.Column(db.String(10), nullable=True)  # 'YYYY-MM-DD'
    streak = db.Column(db.Integer, default=0)
    freezes_available = db.Column(db.Integer, default=1)  # protects the streak if exactly one day is missed
    weekly_total = db.Column(db.Integer, default=0)  # questions asked since week_start_date (Monday)
    week_start_date = db.Column(db.String(10), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)


class ExamResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100))
    level = db.Column(db.String(50))
    grade_form = db.Column(db.String(50))
    paper = db.Column(db.Integer)
    paper_count = db.Column(db.Integer)
    score = db.Column(db.Integer)
    total_marks = db.Column(db.Integer)
    percent = db.Column(db.Integer)
    student_name = db.Column(db.String(150))
    created_at = db.Column(db.DateTime)


class TopicProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(50), nullable=False)
    grade_form = db.Column(db.String(50), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    taught = db.Column(db.Boolean, default=False, nullable=False)
    passed = db.Column(db.Boolean, default=False, nullable=False)
    score = db.Column(db.Integer, nullable=True)
    total_marks = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'subject', 'level', 'grade_form', 'topic',
            name='uq_topic_progress_scope',
        ),
    )


class StudySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(50), nullable=False)
    grade_form = db.Column(db.String(50), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        db.Index('ix_study_session_user_created', 'user_id', 'created_at'),
    )


class PromotionExam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_key = db.Column(db.String(64), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    student_track = db.Column(db.String(20), nullable=False)
    level = db.Column(db.String(50), nullable=False)
    grade_form = db.Column(db.String(50), nullable=False)
    subjects_json = db.Column(db.Text, nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='ready')
    score = db.Column(db.Integer, nullable=True)
    total_marks = db.Column(db.Integer, nullable=True)
    result_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    county = db.Column(db.String(80), nullable=False)
    town = db.Column(db.String(80), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    official_email = db.Column(db.String(150), nullable=False)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        db.Index('ix_school_status_requested', 'status', 'requested_at'),
    )


class SchoolMembership(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('school_id', 'user_id', name='uq_school_membership'),
        db.Index('ix_school_membership_school_status', 'school_id', 'status'),
    )


class CertificateApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    student_track = db.Column(db.String(20), nullable=False)
    course_level = db.Column(db.String(50), nullable=False)
    learner_name = db.Column(db.String(100), nullable=False)
    photo_data = deferred(db.Column(db.LargeBinary, nullable=False))
    photo_mime = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    certificate_number = db.Column(db.String(32), unique=True, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'student_track', name='uq_certificate_application_track'),
    )


REFERRAL_BONUS_DAYS = 3


def generate_referral_code():
    import random
    import string
    for _ in range(20):  # extremely unlikely to loop more than once
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not User.query.filter_by(referral_code=code).first():
            return code
    # Fallback — astronomically unlikely, but never leave a user without a code.
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))


def default_display_name(email):
    """A safe, non-identifying default name for the leaderboard — the local
    part of the email, title-cased, never the full email address."""
    local_part = (email or '').split('@')[0]
    cleaned = ''.join(ch if ch.isalnum() else ' ' for ch in local_part).strip()
    name = cleaned.split(' ')[0] if cleaned else 'Student'
    return (name[:1].upper() + name[1:])[:40] if name else 'Student'

def call_groq(messages, model=None, max_tokens=None):
    api_key = os.environ.get('GROQ_API_KEY') or GROQ_API_KEY
    if not api_key:
        app.logger.error("Groq request failed because GROQ_API_KEY is missing from the runtime environment")
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your server environment or to the project .env file."
        )
    try:
        payload = {
            "model": model or GROQ_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens or 2000,
        }
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Groq error: {str(e)}")
    except (KeyError, IndexError):
        raise RuntimeError("Groq returned an unexpected response shape")


def extract_json(text):
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def validate_topic_mastery_test(test_data):
    if not isinstance(test_data, dict):
        raise ValueError("Expected a JSON object for the mastery test")
    questions = test_data.get("questions")
    if (
        not isinstance(questions, list)
        or len(questions) != 30
        or type(test_data.get("total_marks")) is not int
        or test_data["total_marks"] != 30
    ):
        raise ValueError("Mastery tests must contain exactly 30 one-mark questions")

    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict) or question.get("type") != "mcq":
            raise ValueError("Every mastery-test question must be multiple choice")
        options = question.get("options")
        if (
            type(question.get("id")) is not int
            or question["id"] != index
            or type(question.get("marks")) is not int
            or question["marks"] != 1
            or not isinstance(question.get("question"), str)
            or not question["question"].strip()
            or not isinstance(options, dict)
            or set(options) != {"A", "B", "C", "D"}
            or any(not isinstance(value, str) or not value.strip() for value in options.values())
            or not isinstance(question.get("correct_option"), str)
            or question["correct_option"] not in {"A", "B", "C", "D"}
            or not isinstance(question.get("explanation"), str)
            or not question["explanation"].strip()
        ):
            raise ValueError("Mastery-test question is missing valid one-mark MCQ fields")
    return test_data


def validate_class_promotion_test(test_data, subjects):
    if (
        not isinstance(test_data, dict)
        or type(test_data.get("total_marks")) is not int
        or test_data["total_marks"] != 30
        or not isinstance(test_data.get("questions"), list)
        or len(test_data["questions"]) != 30
    ):
        raise ValueError("Promotion exams must contain exactly 30 one-mark questions")

    covered_subjects = set()
    for index, question in enumerate(test_data["questions"], start=1):
        if not isinstance(question, dict) or question.get("type") != "mcq":
            raise ValueError("Every promotion-exam question must be multiple choice")
        question_subject = question.get("subject")
        options = question.get("options")
        if (
            type(question.get("id")) is not int
            or question["id"] != index
            or type(question.get("marks")) is not int
            or question["marks"] != 1
            or not isinstance(question_subject, str)
            or question_subject not in subjects
            or not isinstance(question.get("question"), str)
            or not question["question"].strip()
            or not isinstance(options, dict)
            or set(options) != {"A", "B", "C", "D"}
            or any(not isinstance(value, str) or not value.strip() for value in options.values())
            or not isinstance(question.get("correct_option"), str)
            or question["correct_option"] not in {"A", "B", "C", "D"}
            or not isinstance(question.get("explanation"), str)
            or not question["explanation"].strip()
        ):
            raise ValueError("Promotion-exam question is missing valid subject or MCQ fields")
        covered_subjects.add(question_subject)

    if covered_subjects != set(subjects):
        raise ValueError("Promotion exam must include every subject for the learner's level")
    return test_data


def describe_level(level, grade_form):
    if level == "cbc":
        return f"{grade_form or 'Grade 6'} (CBC) in Kenya"
    if level == "upper_primary":
        return f"{grade_form or 'Grade 4'} Upper Primary in Kenya"
    if level == "junior_school":
        return f"{grade_form or 'Grade 7'} Junior School in Kenya"
    if level == "senior_school":
        return f"{grade_form or 'Grade 10'} Senior School in Kenya"
    if level == "campus":
        return f"{grade_form or 'Year 1'} university/campus in Kenya"
    return f"{grade_form or 'Form 1'} secondary school (KCSE track) in Kenya"


DIAGRAM_INSTRUCTIONS = (
    " You may include ONE real diagram using SVG, but ONLY for these safe categories where accurate "
    "proportions are easy to get right: a graph of a function or data (axes, curve/bars, labelled "
    "points), a number line, a geometric shape or construction (with correctly labelled sides/angles), "
    "or a simple boxes-and-arrows flowchart/process diagram. "
    "Do NOT draw biological structures (cells, organs, plants, animals, human anatomy), chemical "
    "structures or lab apparatus, maps, circuits, or anything else where a real textbook diagram has "
    "specific, precise proportions or standard symbols — an SVG you improvise for these is very likely "
    "to be subtly wrong, and a wrong diagram is worse than none because a student could copy it into an "
    "exam. For those topics, instead describe the structure clearly in words, list and explain its "
    "labelled parts one by one, and tell the learner to check their textbook or a proper reference "
    "image for exactly how it should look and be drawn/labelled. "
    "When a diagram IS one of the safe categories above, put it on its own lines wrapped exactly like "
    "this, with nothing else inside the tags: "
    "[DIAGRAM]<svg viewBox=\"0 0 320 220\" xmlns=\"http://www.w3.org/2000/svg\">...</svg>[/DIAGRAM] "
    "Use viewBox=\"0 0 320 220\" so it fits a phone screen; only use basic shapes and text (rect, circle, "
    "ellipse, line, polygon, polyline, path, text); label parts clearly with short <text> elements; "
    "never include <script>, event handler attributes (like onclick), <image>, <foreignObject>, or any "
    "external links or references. Only add a diagram when it truly helps and fits a safe category — "
    "most messages need no diagram at all."
)

MATH_NOTATION_INSTRUCTIONS = (
    "Write every mathematical expression, equation, or formula wrapped in LaTeX delimiters so it can be "
    "typeset properly: use \\( ... \\) for anything inline within a sentence, and \\[ ... \\] only for a "
    "standalone equation on its own line. Use standard LaTeX commands (e.g. \\frac{a}{b}, \\sqrt{x}, "
    "x^{2}, \\sin, \\theta, \\times, \\leq). Never write a bare equation with no delimiters, and never mix "
    "plain-text math (like \"x^2\" or \"sqrt(2)/2\") outside of these delimiters."
)

EXAM_DIAGRAM_INSTRUCTIONS = (
    "Real exam papers usually include a diagram for at least a few questions — a labelled geometric "
    "figure, a graph to read or sketch on, a number line, or a simple flowchart — wherever the question "
    "describes one (e.g. a triangle with given side lengths, a graph of a function, a shape to "
    "construct). For each question where a real exam paper would show a figure, include one using the "
    "same safe-category SVG approach: wrap it in [DIAGRAM]<svg viewBox=\"0 0 320 220\" "
    "xmlns=\"http://www.w3.org/2000/svg\">...</svg>[/DIAGRAM] inside that question's \"question\" text, "
    "using only basic shapes and labelled <text> elements, sized to fit a phone screen. Only do this for "
    "geometry figures, graphs, number lines, and simple flowcharts — never for biology, anatomy, "
    "chemistry apparatus, maps, or circuits, since an improvised diagram for those is too likely to be "
    "inaccurate for a real exam. Not every question needs a diagram — only the ones that would genuinely "
    "have one in a real paper."
)


def build_teaching_instructions(topic, all_topics):
    topic_list = ", ".join(t for t in (all_topics or []) if t)
    return (
        f" Right now you are TEACHING the topic '{topic}' from a structured course covering these "
        f"topics in order: {topic_list or topic}. "
        f"Teach in small steps: cover ONE idea or sub-part of '{topic}' per message, never the whole "
        f"topic at once. End every teaching message with a short check like a mini question or 'does "
        f"that make sense so far?' and wait — do not move ahead until the learner responds. " +
        DIAGRAM_INSTRUCTIONS +
        f" Once you have fully covered '{topic}' and the learner seems ready to move on, end that "
        f"message with the exact tag [TOPIC_COMPLETE] on its own at the very end (this tag is hidden "
        f"from the learner, so never mention or explain it)."
    )


def build_system_prompt(subject, level, grade_form=None):
    level_desc = describe_level(level, grade_form)
    identity = (
        f"Your name is Clavis, an AI tutor built for Kenyan students. If asked your name, what you're "
        f"called, who made you, or what model/AI you are, simply say you're Clavis — never mention "
        f"ChatGPT, GPT, OpenAI, Groq, or any other underlying company or model name, even if asked directly "
        f"or told that's what you 'really' are. "
    )
    if level == "campus":
        return (
            identity +
            f"You are a knowledgeable, encouraging tutor helping a {level_desc} student with {subject}. "
            f"Treat them as a capable adult learner: be clear and precise, use proper terminology (defining "
            f"it briefly the first time it comes up), and go a level deeper than a high-school explanation "
            f"would. It's fine to reference real frameworks, theories, or Kenyan/East African examples where "
            f"relevant, and to walk through the reasoning behind a concept rather than just stating it. "
            f"Do not use markdown formatting like asterisks for bold or bullet points (no **word** and no "
            f"lines starting with *). Write in plain sentences, and if you need a list, use a dash (-) or "
            f"simply number the points (1., 2., 3.). " +
            MATH_NOTATION_INSTRUCTIONS +
            DIAGRAM_INSTRUCTIONS
        )
    return (
        identity +
        f"You are a warm, patient, encouraging tutor helping a {level_desc} student with {subject}. "
        f"Explain things the way you'd explain them to a curious young child: use very simple, everyday "
        f"words, short sentences, and relatable examples before introducing any technical term. Build up "
        f"gently step by step rather than jumping straight to the formal definition. Still be accurate and "
        f"complete enough for their level — simple language, not simplified content. Use examples relevant "
        f"to Kenya. "
        f"Do not use markdown formatting like asterisks for bold or bullet points (no **word** and no lines "
        f"starting with *). Write in plain sentences, and if you need a list, use a dash (-) or simply number "
        f"the points (1., 2., 3.). " +
        MATH_NOTATION_INSTRUCTIONS +
        DIAGRAM_INSTRUCTIONS
    )

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        ref_code = (request.form.get('ref') or '').strip().upper()
        if not email or not password:
            flash('Email and password are required.', 'danger')
            return redirect(url_for('signup'))

        password_error = password_validation_error(password)
        if password_error:
            flash(password_error, 'danger')
            return redirect(url_for('signup'))

        if find_user_by_email(email):
            flash('An account already exists for this email. Log in or use Forgot password.', 'danger')
            return redirect(url_for('signup'))

        trial_end = datetime.now() + timedelta(days=1)

        ADMIN_EMAIL = "silasbarry805@gmail.com"

        is_owner = email == ADMIN_EMAIL.lower()
        is_new_co_admin = email in CO_ADMIN_EMAILS
        gets_free_unlimited = is_owner or is_new_co_admin

        referrer = User.query.filter_by(referral_code=ref_code).first() if ref_code else None
        if referrer and not gets_free_unlimited:
            # Give the new student a bonus on top of the standard trial, for
            # signing up via a friend's invite.
            trial_end = trial_end + timedelta(days=REFERRAL_BONUS_DAYS)

        new_user = User(
            email=email,
            is_admin=is_owner,
            is_premium=gets_free_unlimited,
            expiry_date=None if gets_free_unlimited else trial_end,
            created_at=datetime.now(),
            referral_code=generate_referral_code(),
            referred_by_id=referrer.id if referrer else None,
            email_verified=gets_free_unlimited,
            display_name=None,
            student_track=None,
            onboarding_completed=False,
        )
        new_user.set_password(password)
        db.session.add(new_user)

        if referrer and not has_unlimited_access(referrer):
            # Reward the referrer too — extend from whichever is later: their
            # current expiry, or now (covers an already-expired trial).
            base = referrer.expiry_date if (referrer.expiry_date and referrer.expiry_date > datetime.now()) else datetime.now()
            referrer.expiry_date = base + timedelta(days=REFERRAL_BONUS_DAYS)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if find_user_by_email(email):
                flash('An account already exists for this email. Log in or use Forgot password.', 'danger')
            else:
                app.logger.exception("Could not create a new account after a database integrity error")
                flash('We could not create your account right now. Please try again.', 'danger')
            return redirect(url_for('signup'))

        if not new_user.email_verified:
            token = reset_serializer.dumps(email, salt='email-verify')
            verify_url = f"{YOUR_DOMAIN}/verify-email/{token}"
            try:
                send_verification_email(email, verify_url)
            except Exception as e:
                app.logger.error(f"Failed to send verification email to {email}: {e}")

        login_user(new_user, remember=True)
        flash('Your account is ready. Let’s set up your learning path.', 'success')
        return redirect(url_for('onboarding', step='name'))

    return render_template('auth.html', mode='signup', ref_code=request.args.get('ref', ''), student_track=request.args.get('type', ''))


LOGIN_LOCKOUT_THRESHOLD = int(os.environ.get('LOGIN_LOCKOUT_THRESHOLD', '5'))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get('LOGIN_LOCKOUT_MINUTES', '15'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = find_user_by_email(email)

        if not email or not password:
            flash('Enter both your email address and password.', 'danger')
            return render_template('auth.html', mode='login', student_track=request.args.get('type', ''))

        if not user:
            flash('No account was found for that email. Check the spelling or create an account.', 'danger')
            return render_template('auth.html', mode='login', student_track=request.args.get('type', ''))

        if user.lockout_until and user.lockout_until > datetime.now():
            minutes_left = max(1, int((user.lockout_until - datetime.now()).total_seconds() // 60) + 1)
            flash(f"Too many failed attempts. Please try again in {minutes_left} minute(s), or reset your password.", "danger")
            return render_template('auth.html', mode='login', student_track=request.args.get('type', ''))

        if user.check_password(password):
            user.failed_login_attempts = 0
            user.lockout_until = None
            db.session.commit()
            login_user(user, remember=True)
            if is_full_admin(user) and user.onboarding_completed:
                return redirect(url_for('onboarding', step='path'))
            return redirect(url_for('onboarding', step='name') if not user.onboarding_completed else url_for('home'))

        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= LOGIN_LOCKOUT_THRESHOLD:
            user.lockout_until = datetime.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
            db.session.commit()
            flash(f"Too many failed attempts. Please try again in {LOGIN_LOCKOUT_MINUTES} minutes, or reset your password.", "danger")
            return render_template('auth.html', mode='login', student_track=request.args.get('type', ''))
        db.session.commit()
        flash('That password does not match this email. Try again or use Forgot password.', 'danger')

    return render_template('auth.html', mode='login', student_track=request.args.get('type', ''))


ELEMENTARY_GRADES = {
    'Lower classes': [f'Grade {grade}' for grade in range(3, 7)],
    'Junior School': [f'Grade {grade}' for grade in range(7, 10)],
    'Senior School': [f'Grade {grade}' for grade in range(10, 13)],
}

ELEMENTARY_SUBJECTS = {
    'Lower classes': [
        'Mathematics', 'English', 'Kiswahili', 'Science and Technology',
        'Social Studies', 'Creative Arts', 'Agriculture',
    ],
    'Junior School': [
        'Mathematics', 'English', 'Kiswahili', 'Science and Technology',
        'Social Studies', 'Pre-Technical Studies', 'Agriculture', 'Creative Arts',
        'ICT', 'Business Studies',
    ],
    'Senior School': [
        'Mathematics', 'English', 'Physics', 'Chemistry', 'Agriculture',
        'Home Science', 'Creative Arts', 'Biology', 'Kiswahili', 'Geography',
        'Religious Education', 'Business Studies', 'History', 'Computer Studies',
    ],
}

ELEMENTARY_SUBJECTS_BY_GRADE = {
    grade: ELEMENTARY_SUBJECTS[elementary_stage]
    for elementary_stage, grades in ELEMENTARY_GRADES.items()
    for grade in grades
}

ONBOARDING_STEPS = ('name', 'path', 'level', 'subjects', 'schedule', 'goals')
CAMPUS_YEARS = [f'Year {year}' for year in range(1, 6)]


def elementary_stage_for_grade(grade):
    for stage, grades in ELEMENTARY_GRADES.items():
        if grade in grades:
            return stage
    return None


def onboarding_subject_choices(user):
    if user.student_track == 'campus':
        return DAILY_CHALLENGE_SUBJECTS['campus']
    return ELEMENTARY_SUBJECTS_BY_GRADE.get(user.elementary_grade, [])


def challenging_subjects_for_user(user):
    try:
        saved_subjects = json.loads(user.challenging_subjects_json or '[]')
    except (TypeError, json.JSONDecodeError):
        saved_subjects = []
    if not isinstance(saved_subjects, list):
        saved_subjects = []
    choices = onboarding_subject_choices(user)
    subjects = list(dict.fromkeys(
        subject for subject in saved_subjects
        if isinstance(subject, str) and subject in choices
    ))
    if not subjects and user.challenging_subject in choices:
        subjects = [user.challenging_subject]
    return subjects


def default_study_schedule(user):
    previous_goal = getattr(user, 'daily_goal_minutes', None)
    daily_hours = round((previous_goal or 60) / 30) / 2
    daily_hours = min(12, max(0.5, daily_hours))
    return {
        'version': 2,
        'mode': 'all',
        'subjects': onboarding_subject_choices(user),
        'daily_hours': daily_hours,
    }


def study_schedule_for_user(user):
    choices = onboarding_subject_choices(user)
    try:
        saved = json.loads(user.study_schedule_json or '{}')
    except (TypeError, json.JSONDecodeError):
        saved = {}
    if not isinstance(saved, dict) or saved.get('version') != 2:
        saved = default_study_schedule(user)

    mode = saved.get('mode')
    if mode not in ('all', 'selected'):
        mode = 'all'
    selected = saved.get('subjects')
    if not isinstance(selected, list):
        selected = []
    selected = list(dict.fromkeys(
        subject for subject in selected
        if isinstance(subject, str) and subject in choices
    ))
    if mode == 'all':
        selected = choices
    elif not selected:
        mode = 'all'
        selected = choices

    try:
        daily_hours = float(saved.get('daily_hours', 1))
    except (TypeError, ValueError):
        daily_hours = 1
    if daily_hours < 0.5 or daily_hours > 12 or daily_hours * 2 != int(daily_hours * 2):
        daily_hours = 1
    return {
        'version': 2,
        'mode': mode,
        'subjects': selected,
        'daily_hours': daily_hours,
        'minutes_per_subject': float(daily_hours * 60 / len(selected)) if selected else 0.0,
    }


def preferred_timetable_subjects(user, schedule=None):
    schedule = schedule or study_schedule_for_user(user)
    if schedule['mode'] == 'selected':
        return schedule['subjects']
    choices = onboarding_subject_choices(user)
    preferred = [user.favorite_subject] + challenging_subjects_for_user(user)
    selected = list(dict.fromkeys(subject for subject in preferred if subject in choices))
    return selected or choices[:1]


def has_current_study_schedule(user):
    try:
        saved = json.loads(user.study_schedule_json or '{}')
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(saved, dict) and saved.get('version') == 2


def save_study_schedule(user, form):
    mode = form.get('timetable_mode', '').strip()
    if mode not in ('all', 'selected'):
        raise ValueError('Choose all subjects or choose the subjects for your timetable.')
    try:
        daily_hours = float(form.get('daily_hours', ''))
    except (TypeError, ValueError):
        raise ValueError('Choose how many hours you want to study each day.')
    if daily_hours < 0.5 or daily_hours > 12 or daily_hours * 2 != int(daily_hours * 2):
        raise ValueError('Choose a daily study time between 0.5 and 12 hours in half-hour steps.')

    choices = onboarding_subject_choices(user)
    if mode == 'all':
        selected = choices
    else:
        selected = list(dict.fromkeys(form.getlist('timetable_subjects')))
        if not selected or any(subject not in choices for subject in selected):
            raise ValueError('Choose at least one valid subject for your timetable.')

    user.study_schedule_json = json.dumps({
        'version': 2,
        'mode': mode,
        'subjects': selected,
        'daily_hours': daily_hours,
    })
    user.daily_goal_minutes = int(daily_hours * 60)


def study_timetable_for_user(user):
    schedule = study_schedule_for_user(user)
    minutes_per_subject = schedule['minutes_per_subject']
    minutes_label = (
        f'{int(minutes_per_subject)} min'
        if minutes_per_subject.is_integer()
        else f'{minutes_per_subject:.2f} min'
    )
    return {
        'daily_hours': schedule['daily_hours'],
        'subjects': [
            {
                'name': subject,
                'minutes': minutes_label,
            }
            for subject in schedule['subjects']
        ],
        'mode': schedule['mode'],
        'total_subjects': len(schedule['subjects']),
    }


@app.route('/onboarding/<step>', methods=['GET', 'POST'])
@login_required
def onboarding(step):
    if step not in ONBOARDING_STEPS:
        return redirect(url_for('onboarding', step='name'))
    admin_path_editor = (
        is_full_admin(current_user)
        and current_user.onboarding_completed
        and step in ('path', 'level')
    )
    if current_user.onboarding_completed and not admin_path_editor:
        return redirect(url_for('home'))

    prerequisites = {
        'name': bool((current_user.display_name or '').strip()),
        'path': current_user.student_track in ('campus', 'highschool'),
        'level': bool(
            current_user.student_track == 'campus'
            and current_user.campus_course
            and current_user.campus_year
            or current_user.student_track != 'campus'
            and current_user.elementary_grade
        ),
        'subjects': bool(
            current_user.favorite_subject
            and current_user.favorite_subject in onboarding_subject_choices(current_user)
            and challenging_subjects_for_user(current_user)
        ),
        'schedule': has_current_study_schedule(current_user),
    }
    if admin_path_editor:
        if step == 'level' and current_user.student_track not in ('campus', 'highschool'):
            return redirect(url_for('onboarding', step='path'))
    else:
        for required_step in ONBOARDING_STEPS[:ONBOARDING_STEPS.index(step)]:
            if not prerequisites.get(required_step, False):
                return redirect(url_for('onboarding', step=required_step))

    choices = onboarding_subject_choices(current_user)
    if request.method == 'POST':
        if step == 'name':
            name = ''.join(
                character for character in request.form.get('display_name', '').strip()
                if character.isalnum() or character in ' -_'
            )[:40].strip()
            if not name:
                flash('Please enter the name you want Clavis to use.', 'danger')
                return redirect(url_for('onboarding', step=step))
            current_user.display_name = name

        elif step == 'path':
            track = request.form.get('student_track', '').strip().lower()
            if track not in ('elementary', 'campus'):
                flash('Choose Elementary or Campus to continue.', 'danger')
                return redirect(url_for('onboarding', step=step))
            if current_user.student_track and not is_full_admin(current_user):
                flash('Your learning path is already locked for this account.', 'danger')
                return redirect(url_for('onboarding', step='level'))
            selected_track = 'campus' if track == 'campus' else 'highschool'
            if current_user.student_track != selected_track:
                current_user.student_track = selected_track
                current_user.elementary_grade = None
                current_user.campus_course = None
                current_user.campus_year = None
                if admin_path_editor:
                    current_user.favorite_subject = None
                    current_user.challenging_subject = None
                    current_user.challenging_subjects_json = None
                    current_user.study_schedule_json = None
                    current_user.daily_goal_minutes = None
                    current_user.starting_topic = None

        elif step == 'level':
            if current_user.student_track == 'campus':
                course = request.form.get('campus_course', '').strip()
                year = request.form.get('campus_year', '').strip()
                if course not in DAILY_CHALLENGE_SUBJECTS['campus'] or year not in CAMPUS_YEARS:
                    flash('Choose a Campus subject/course and year from the available options.', 'danger')
                    return redirect(url_for('onboarding', step=step))
                if current_user.campus_course and not is_full_admin(current_user):
                    course, year = current_user.campus_course, current_user.campus_year
                current_user.campus_course = course
                current_user.campus_year = year
                current_user.elementary_grade = None
            else:
                grade = request.form.get('elementary_grade', '').strip()
                if not elementary_stage_for_grade(grade):
                    flash('Choose a valid grade from 3 to 12.', 'danger')
                    return redirect(url_for('onboarding', step=step))
                if current_user.elementary_grade and not (is_full_admin(current_user) or is_co_admin(current_user)):
                    grade = current_user.elementary_grade
                current_user.elementary_grade = grade
                current_user.campus_course = None
                current_user.campus_year = None
            if admin_path_editor:
                choices = onboarding_subject_choices(current_user)
                if choices:
                    current_user.favorite_subject = choices[0]
                    current_user.challenging_subject = choices[0]
                    current_user.challenging_subjects_json = json.dumps([choices[0]])
                    schedule = default_study_schedule(current_user)
                    current_user.study_schedule_json = json.dumps(schedule)
                    current_user.daily_goal_minutes = int(schedule['daily_hours'] * 60)
                current_user.starting_topic = 'topic_1'

        elif step == 'subjects':
            choices = onboarding_subject_choices(current_user)
            favorite = request.form.get('favorite_subject', '').strip()
            challenging = list(dict.fromkeys(request.form.getlist('challenging_subjects')))
            if favorite not in choices or not challenging or any(subject not in choices for subject in challenging):
                flash('Choose a favourite subject and at least one challenging subject from your learning path.', 'danger')
                return redirect(url_for('onboarding', step=step))
            current_user.favorite_subject = favorite
            current_user.challenging_subject = challenging[0]
            current_user.challenging_subjects_json = json.dumps(challenging)

        elif step == 'schedule':
            try:
                save_study_schedule(current_user, request.form)
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('onboarding', step=step))

        elif step == 'goals':
            starting_topic = request.form.get('starting_topic', '').strip()
            if starting_topic == 'continue':
                starting_topic = request.form.get('continue_topic', '').strip()
                if not starting_topic:
                    flash('Enter the topic you want to continue from.', 'danger')
                    return redirect(url_for('onboarding', step=step))
            if starting_topic not in ('topic_1', 'continue') and not re.fullmatch(r'[\w -]{1,100}', starting_topic):
                flash('Enter a valid topic to continue from, or choose Topic 1.', 'danger')
                return redirect(url_for('onboarding', step=step))
            current_user.starting_topic = starting_topic or 'topic_1'
            current_user.onboarding_completed = True

        db.session.commit()
        if step == 'goals':
            return redirect(url_for('home'))
        if step == 'level' and admin_path_editor:
            return redirect(url_for('home'))
        next_step = ONBOARDING_STEPS[ONBOARDING_STEPS.index(step) + 1]
        return redirect(url_for('onboarding', step=next_step))

    schedule = study_schedule_for_user(current_user)
    if step == 'schedule' and not current_user.study_schedule_json:
        schedule = default_study_schedule(current_user)
    return render_template(
        'onboarding.html',
        step=step,
        step_number=(1 if step == 'path' else 2) if admin_path_editor else ONBOARDING_STEPS.index(step) + 1,
        step_count=2 if admin_path_editor else len(ONBOARDING_STEPS),
        admin_path_editor=admin_path_editor,
        elementary_grades=ELEMENTARY_GRADES,
        campus_courses=DAILY_CHALLENGE_SUBJECTS['campus'],
        campus_years=CAMPUS_YEARS,
        daily_hours_options=[index / 2 for index in range(1, 25)],
        subjects=choices,
        challenging_subjects=challenging_subjects_for_user(current_user),
        schedule=schedule,
        timetable_subjects=preferred_timetable_subjects(current_user, schedule),
        stage=elementary_stage_for_grade(current_user.elementary_grade),
        user=current_user,
    )


@app.route('/study-timetable', methods=['GET', 'POST'])
@login_required
def study_timetable():
    choices = onboarding_subject_choices(current_user)
    if not choices:
        flash('Choose your learning path and class before setting up a timetable.', 'warning')
        return redirect(url_for('onboarding', step='level'))

    if request.method == 'POST':
        favorite = request.form.get('favorite_subject', '').strip()
        challenging = list(dict.fromkeys(request.form.getlist('challenging_subjects')))
        if favorite not in choices or not challenging or any(subject not in choices for subject in challenging):
            flash('Choose a favourite subject and at least one challenging subject from your learning path.', 'danger')
            return redirect(url_for('study_timetable'))
        try:
            save_study_schedule(current_user, request.form)
        except ValueError as error:
            flash(str(error), 'danger')
            return redirect(url_for('study_timetable'))
        current_user.favorite_subject = favorite
        current_user.challenging_subject = challenging[0]
        current_user.challenging_subjects_json = json.dumps(challenging)
        db.session.commit()
        flash('Your study preferences and timetable have been saved.', 'success')
        return redirect(url_for('home'))

    return render_template(
        'study_timetable.html',
        user=current_user,
        subjects=choices,
        challenging_subjects=challenging_subjects_for_user(current_user),
        schedule=study_schedule_for_user(current_user),
        timetable_subjects=preferred_timetable_subjects(current_user),
        daily_hours_options=[index / 2 for index in range(1, 25)],
        favorite_subject=current_user.favorite_subject,
    )


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = find_user_by_email(email)

        if user:
            token = reset_serializer.dumps(email, salt='password-reset')
            reset_url = f"{YOUR_DOMAIN}/reset-password/{token}"
            try:
                send_reset_email(email, reset_url)
            except Exception as e:
                app.logger.error(f"Failed to send reset email to {email}: {e}")

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

    user = find_user_by_email(email)
    if not user:
        flash("That reset link isn't valid. Please request a new one.", "danger")
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        password_error = password_validation_error(password)
        if password_error:
            flash(password_error, "danger")
            return render_template('auth.html', mode='reset', token=token)

        if password != confirm:
            flash("Passwords don't match.", "danger")
            return render_template('auth.html', mode='reset', token=token)

        user.set_password(password)
        db.session.commit()
        flash("Password updated — you can log in now.", "success")
        return redirect(url_for('login'))

    return render_template('auth.html', mode='reset', token=token)

@app.route('/verify-email/<token>')
def verify_email(token):
    try:
        email = reset_serializer.loads(token, salt='email-verify', max_age=EMAIL_VERIFY_TOKEN_MAX_AGE)
    except SignatureExpired:
        flash("That verification link has expired — request a new one from the banner on your account.", "danger")
        return redirect(url_for('home') if current_user.is_authenticated else url_for('login'))
    except BadSignature:
        flash("That verification link isn't valid.", "danger")
        return redirect(url_for('home') if current_user.is_authenticated else url_for('login'))

    user = find_user_by_email(email)
    if not user:
        flash("That verification link isn't valid.", "danger")
        return redirect(url_for('login'))

    if not user.email_verified:
        user.email_verified = True
        db.session.commit()
    flash("Email verified — you're all set!", "success")
    return redirect(url_for('home') if current_user.is_authenticated else url_for('login'))


@app.route('/resend-verification', methods=['POST'])
@login_required
def resend_verification():
    if current_user.email_verified:
        flash("Your email is already verified.", "success")
        return redirect(url_for('home'))

    token = reset_serializer.dumps(current_user.email, salt='email-verify')
    verify_url = f"{YOUR_DOMAIN}/verify-email/{token}"
    try:
        send_verification_email(current_user.email, verify_url)
        flash("Verification email sent — check your inbox.", "success")
    except Exception as e:
        app.logger.error(f"Failed to send verification email to {current_user.email}: {e}")
        flash("Couldn't send the email right now — please try again shortly.", "danger")
    return redirect(url_for('home'))

@app.route('/')
def home():
    if current_user.is_authenticated:
        if not current_user.onboarding_completed:
            return redirect(url_for('onboarding', step='name'))
        if onboarding_subject_choices(current_user) and not has_current_study_schedule(current_user):
            return redirect(url_for('study_timetable'))
        if not current_user.referral_code:
            current_user.referral_code = generate_referral_code()
            db.session.commit()
        referral_link = f"{request.host_url}signup?ref={current_user.referral_code}"
        current_grade = (
            current_user.campus_year if current_user.student_track == 'campus'
            else current_user.elementary_grade or ''
        )
        course_completed = has_completed_highest_course(current_user)
        certificate_application_record = None
        if current_user.student_track == 'campus':
            certificate_application_record = CertificateApplication.query.filter_by(
                user_id=current_user.id,
                student_track='campus',
            ).first()
        timetable = study_timetable_for_user(current_user)
        return render_template(
            'index.html',
            user=current_user,
            subscription_expired=current_user.subscription_expired,
            is_admin_user=has_unlimited_access(current_user),
            is_full_admin_user=is_full_admin(current_user),
            mpesa_prices=MPESA_TIER_PRICES,
            referral_link=referral_link,
            referral_bonus_days=REFERRAL_BONUS_DAYS,
            email_verified=current_user.email_verified,
            student_track=current_user.student_track or 'highschool',
            learning_level=current_grade,
            student_track_locked=(not is_full_admin(current_user)) and current_user.student_track is not None,
            course_completed=course_completed,
            certificate_application=certificate_application_record,
            timetable=timetable,
            challenging_subjects=challenging_subjects_for_user(current_user),
        )

    return render_template(
        'landing.html',
        title='Clavis',
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
    return send_from_directory(os.path.join(PROJECT_ROOT, 'static', 'images'), 'logo.png', mimetype='image/png')


@app.route('/sw.js')
def serve_service_worker():
    # Must be served from the root path (not /static/sw.js) so its scope
    # covers the whole site — that's what makes the app installable.
    response = send_from_directory(
        os.path.join(PROJECT_ROOT, 'static'), 'sw.js', mimetype='application/javascript'
    )
    response.headers['Service-Worker-Allowed'] = '/'
    return response

MPESA_PAYEE_NAME = 'SHARON'
MPESA_PAYEE_NUMBER = '0718675377'
MPESA_TIER_PRICES = {
    "day": os.environ.get('MPESA_PRICE_DAY', '50'),
    "month": os.environ.get('MPESA_PRICE_MONTH', '1300'),
}
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
    return render_template('admin_mpesa.html', requests=pending, is_full_admin_user=is_full_admin(current_user))


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
    try:
        amount = int(MPESA_TIER_PRICES.get(user.mpesa_tier, 0))
    except (TypeError, ValueError):
        amount = 0
    db.session.add(PaymentLog(user_id=user.id, tier=user.mpesa_tier, amount=amount, approved_at=datetime.now()))
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


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    if not is_full_admin(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('admin_mpesa_requests') if is_co_admin(current_user) else url_for('home'))

    now = datetime.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    total_users = User.query.count()
    new_this_week = User.query.filter(User.created_at != None, User.created_at >= week_ago).count()
    active_premium = User.query.filter(User.is_premium == True, User.expiry_date != None, User.expiry_date > now).count()

    all_payments = PaymentLog.query.order_by(PaymentLog.approved_at.desc()).all()
    total_revenue = sum(p.amount or 0 for p in all_payments)
    revenue_this_month = sum(p.amount or 0 for p in all_payments if p.approved_at and p.approved_at >= month_ago)
    recent_payments = all_payments[:15]

    referral_counts = {}
    for u in User.query.filter(User.referred_by_id != None).all():
        referral_counts[u.referred_by_id] = referral_counts.get(u.referred_by_id, 0) + 1
    top_referrer_rows = []
    for referrer_id, count in sorted(referral_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]:
        referrer = User.query.get(referrer_id)
        if referrer:
            top_referrer_rows.append((referrer.email, count))

    return render_template(
        'admin_analytics.html',
        total_users=total_users,
        new_this_week=new_this_week,
        active_premium=active_premium,
        total_revenue=total_revenue,
        revenue_this_month=revenue_this_month,
        recent_payments=recent_payments,
        top_referrers=top_referrer_rows,
    )


@app.route('/admin/users')
@login_required
def admin_users():
    if not is_full_admin(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('admin_mpesa_requests') if is_co_admin(current_user) else url_for('home'))

    now = datetime.now()
    week_ago = now - timedelta(days=7)

    all_users = User.query.order_by(User.created_at.desc().nullslast()).all()

    rows = []
    paid_count = 0
    new_count = 0
    for u in all_users:
        if has_unlimited_access(u):
            status = 'Owner / Unlimited'
        elif u.is_premium and u.expiry_date and u.expiry_date > now:
            status = 'Paid — active'
            paid_count += 1
        elif u.expiry_date and u.expiry_date > now:
            status = 'Free trial — active'
        else:
            status = 'Expired'
        if u.created_at and u.created_at >= week_ago:
            new_count += 1
        rows.append({
            'email': u.email,
            'created_at': u.created_at,
            'status': status,
            'expiry_date': u.expiry_date,
        })

    return render_template(
        'admin_users.html',
        rows=rows,
        total_users=len(all_users),
        paid_count=paid_count,
        new_count=new_count,
    )

@app.route('/api/tutor', methods=['POST'])
@login_required
@limiter.limit("15 per minute; 100 per hour", exempt_when=is_unlimited_rate_limit_user)
def api_tutor():
    if not current_user.email_verified:
        return jsonify({"error": "Please verify your email to start chatting with the tutor — check your inbox, or resend the link from the banner above."}), 403
    if current_user.subscription_expired and not has_unlimited_access(current_user):
        return jsonify({"error": "Your trial has ended — please choose a plan."}), 402

    allowed, remaining = check_and_consume_ai_call(current_user)
    if not allowed:
        return jsonify({"error": "You've hit today's message limit — it resets tomorrow. Upgrade for a higher daily limit."}), 429

    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')
    response_style = data.get('responseStyle', 'detailed')
    language = data.get('language', 'en')
    attachment = data.get('attachment')
    messages = data.get('messages', [])
    teach_topic = data.get('teachTopic')
    teach_topics = data.get('teachTopics')

    if not subject or not messages:
        return jsonify({"error": "subject and messages are required"}), 400

    system_prompt = build_system_prompt(subject, level, grade_form)
    if teach_topic:
        system_prompt += build_teaching_instructions(teach_topic, teach_topics)
    if response_style == 'concise':
        system_prompt += " Keep answers brief and focused: use only the key steps, then offer to explain more."
    else:
        system_prompt += " Give a clear, supportive explanation with enough working for the learner to understand why."
    if language == 'sw':
        system_prompt += (
            " Respond entirely in Kiswahili (Swahili), including your explanation and any examples. "
            "Keep technical/subject terms understandable — you may keep a widely-used English technical "
            "term in brackets after its Kiswahili explanation if there is no common Kiswahili equivalent."
        )
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
        answer = call_groq(groq_messages, vision_model, max_tokens=2500)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    topic_complete = False
    if teach_topic and '[TOPIC_COMPLETE]' in answer:
        topic_complete = True
        answer = answer.replace('[TOPIC_COMPLETE]', '').rstrip()

    return jsonify({"answer": answer, "topicComplete": topic_complete})


@app.route('/api/topics', methods=['POST'])
@login_required
@limiter.limit("10 per minute; 60 per hour", exempt_when=is_unlimited_rate_limit_user)
def api_topics():
    if not current_user.email_verified:
        return jsonify({"error": "Please verify your email to continue — check your inbox, or resend the link from the banner above."}), 403
    allowed, remaining = check_and_consume_ai_call(current_user)
    if not allowed:
        return jsonify({"error": "You've hit today's message limit — it resets tomorrow. Upgrade for a higher daily limit."}), 429
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
@limiter.limit("6 per minute; 30 per hour", exempt_when=is_unlimited_rate_limit_user)
def api_generate_test():
    if not current_user.email_verified:
        return jsonify({"error": "Please verify your email to continue — check your inbox, or resend the link from the banner above."}), 403
    if current_user.subscription_expired and not has_unlimited_access(current_user):
        return jsonify({"error": "Your trial has ended — please choose a plan."}), 402

    allowed, remaining = check_and_consume_ai_call(current_user)
    if not allowed:
        return jsonify({"error": "You've hit today's message limit — it resets tomorrow. Upgrade for a higher daily limit."}), 429

    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')
    topic = data.get('topic')
    all_topics = data.get('allTopics', [])
    paper = data.get('paper')          # e.g. 1, 2, 3 — present only for formal exam papers
    paper_count = data.get('paperCount')

    if not subject:
        return jsonify({"error": "subject is required"}), 400

    level_desc = describe_level(level, grade_form)

    if paper:
        paper_style = {
            1: "an objective/theory-focused paper — mostly shorter questions testing recall and understanding across the whole syllabus",
            2: "a structured-question paper — longer, multi-part questions that build on each other and test application, similar to a Paper 2 style",
            3: "a practical/applied paper — questions framed around practical scenarios, data, or experiments, similar to a Paper 3 style",
        }.get(paper, "a mixed theory and application paper")
        scope_note = f"Paper {paper}" + (f" of {paper_count}" if paper_count else "")
        prompt = f"""Create a full mock exam paper for a {level_desc} student on {subject} — {scope_note}, styled in the spirit of {paper_style}.
Cover a broad spread of the whole {subject} syllabus for this level (not just one topic), the way a real end-of-course paper would.
Group questions into 2 logical sections (e.g. "SECTION A" for shorter/objective questions, "SECTION B" for longer/structured ones) — adjust section names to fit {subject} convention if there's a more standard pair of names.
{MATH_NOTATION_INSTRUCTIONS}
{EXAM_DIAGRAM_INSTRUCTIONS}
Return ONLY valid JSON in exactly this shape, no commentary, no markdown fences:
{{
  "time_allowed": "<realistic time allocation for this level and paper, e.g. '1 Hour 30 Minutes' or '2 Hours 30 Minutes'>",
  "instructions": ["Write your name and admission number in the spaces provided.", "Answer ALL questions.", "<2 to 4 more short, realistic exam instructions specific to {subject}>"],
  "total_marks": <int>,
  "questions": [
    {{"id": 1, "section": "SECTION A", "type": "mcq", "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "A", "marks": 1, "explanation": "..."}},
    {{"id": 2, "section": "SECTION B", "type": "short", "question": "...", "marks": 3, "model_answer": "...", "marking_points": ["...", "...", "..."]}}
  ]
}}
Include 10 to 14 questions total, mixing "mcq" and "short" types appropriately for this paper's style. Number "id" sequentially starting at 1."""
    elif topic:
        prompt = f"""Create a mastery test for a {level_desc} student on {subject}, covering only the topic '{topic}'.
The learner must earn every mark to demonstrate mastery and unlock the next topic.
{MATH_NOTATION_INSTRUCTIONS}
Return ONLY valid JSON in exactly this shape, no commentary, no markdown fences:
{{
  "total_marks": 30,
  "questions": [
    {{"id": 1, "type": "mcq", "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "A", "marks": 1, "explanation": "..."}}
  ]
}}
Include exactly 30 distinct, accurate, age-appropriate multiple-choice questions. Each question must have exactly 1 mark, exactly four concise options named A, B, C, and D, one unambiguously correct option, and a brief explanation. Number "id" sequentially from 1 to 30. Do not include short-answer questions or diagrams."""
    else:
        scope = f"all of these topics: {', '.join(all_topics)}"
        prompt = f"""Create a short test for a {level_desc} student on {subject}, covering {scope}.
{MATH_NOTATION_INSTRUCTIONS}
{EXAM_DIAGRAM_INSTRUCTIONS}
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
        raw = call_groq(
            [{"role": "user", "content": prompt}],
            max_tokens=6000 if paper or topic else 3000,
        )
        test_data = extract_json(raw)
        if "questions" not in test_data or "total_marks" not in test_data:
            raise ValueError("Response missing required fields")
        if topic and not paper:
            validate_topic_mastery_test(test_data)
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": f"Could not generate test: {str(e)}"}), 502

    return jsonify(test_data)


def week_start_str(d=None):
    """Monday-based ISO week start, as 'YYYY-MM-DD'."""
    d = d or datetime.now()
    return (d - timedelta(days=d.weekday())).strftime('%Y-%m-%d')


@app.route('/api/sync-stats', methods=['GET', 'POST'])
@login_required
def api_sync_stats():
    row = UserStats.query.get(current_user.id)

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if row is None:
            row = UserStats(user_id=current_user.id)
            db.session.add(row)

        new_total = int(data.get('total', 0) or 0)
        old_total = row.total or 0
        delta = max(0, new_total - old_total)  # ignore any decrease (e.g. a stale/reset client)

        current_week = week_start_str()
        if row.week_start_date != current_week:
            row.weekly_total = 0
            row.week_start_date = current_week
        row.weekly_total = (row.weekly_total or 0) + delta

        row.total = new_total
        row.by_subject_json = json.dumps(data.get('bySubject', {}) or {})
        row.last_active_date = data.get('lastActiveDate')
        row.streak = int(data.get('streak', 0) or 0)
        if 'freezesAvailable' in data:
            row.freezes_available = max(0, min(2, int(data.get('freezesAvailable', 1) or 0)))
        row.updated_at = datetime.now()
        db.session.commit()
        return jsonify({"status": "ok"})

    if row is None:
        return jsonify({"total": 0, "bySubject": {}, "lastActiveDate": None, "streak": 0, "freezesAvailable": 1})
    try:
        by_subject = json.loads(row.by_subject_json) if row.by_subject_json else {}
    except (TypeError, ValueError):
        by_subject = {}
    return jsonify({
        "total": row.total or 0,
        "bySubject": by_subject,
        "lastActiveDate": row.last_active_date,
        "streak": row.streak or 0,
        "freezesAvailable": row.freezes_available if row.freezes_available is not None else 1,
    })


@app.route('/api/topic-progress', methods=['GET', 'POST'])
@login_required
def api_topic_progress():
    data = request.args if request.method == 'GET' else (request.get_json(silent=True) or {})
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')

    if not all(isinstance(value, str) for value in (subject, level, grade_form)):
        return jsonify({"error": "subject, level, and gradeForm are required"}), 400

    subject = subject.strip()
    level = level.strip()
    grade_form = grade_form.strip()
    if not subject or not level or not grade_form:
        return jsonify({"error": "subject, level, and gradeForm are required"}), 400
    if len(subject) > 100 or len(level) > 50 or len(grade_form) > 50:
        return jsonify({"error": "The selected subject or level is too long"}), 400

    scope = {
        "user_id": current_user.id,
        "subject": subject,
        "level": level,
        "grade_form": grade_form,
    }

    if request.method == 'GET':
        rows = TopicProgress.query.filter_by(**scope).all()
        return jsonify({
            row.topic: {
                "taught": bool(row.taught),
                "done": (
                    row.score is not None
                    and row.total_marks is not None
                    and row.score == row.total_marks
                ),
                "score": (
                    f"{row.score}/{row.total_marks}"
                    if row.score is not None and row.total_marks is not None
                    else None
                ),
            }
            for row in rows
        })

    topic = data.get('topic')
    if not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > 100:
        return jsonify({"error": "A valid topic is required"}), 400
    taught = data.get('taught')
    if taught is not None and not isinstance(taught, bool):
        return jsonify({"error": "taught must be true or false"}), 400

    has_score = 'score' in data or 'total' in data
    score = data.get('score')
    total = data.get('total')
    if has_score and (
        type(score) is not int
        or type(total) is not int
        or total <= 0
        or score < 0
        or score > total
    ):
        return jsonify({"error": "score and total must be valid marks"}), 400
    if taught is None and not has_score:
        return jsonify({"error": "Provide taught status or a test score"}), 400

    row = TopicProgress.query.filter_by(**scope, topic=topic.strip()).first()
    if row is None:
        row = TopicProgress(**scope, topic=topic.strip())
        db.session.add(row)

    if taught:
        row.taught = True
    if has_score:
        is_better_score = (
            row.score is None
            or row.total_marks is None
            or score * row.total_marks > row.score * total
            or (
                score * row.total_marks == row.score * total
                and total == 30
                and row.total_marks != 30
            )
        )
        if is_better_score:
            row.score = score
            row.total_marks = total
        row.passed = bool(row.passed) or score == total
    row.updated_at = datetime.now()
    db.session.commit()
    return jsonify({
        "taught": bool(row.taught),
        "done": (
            row.score is not None
            and row.total_marks is not None
            and row.score == row.total_marks
        ),
        "score": f"{row.score}/{row.total_marks}" if row.score is not None else None,
    })


def summarize_learning_topics(subjects, rows):
    rows_by_subject = {}
    for row in rows:
        rows_by_subject.setdefault(row.subject, []).append(row)

    subject_progress = []
    for subject in subjects:
        subject_rows = rows_by_subject.get(subject, [])
        started = len(subject_rows)
        taught = sum(bool(row.taught) for row in subject_rows)
        mastered = sum(
            row.score == 30 and row.total_marks == 30
            for row in subject_rows
        )
        latest_scored = next(
            (row for row in subject_rows if row.score is not None and row.total_marks),
            None,
        )
        subject_progress.append({
            "subject": subject,
            "topicsStarted": started,
            "topicsTaught": taught,
            "topicsMastered": mastered,
            "latestScore": (
                f"{latest_scored.score}/{latest_scored.total_marks}"
                if latest_scored else None
            ),
            "lastUpdated": (
                latest_scored.updated_at.isoformat()
                if latest_scored and latest_scored.updated_at else None
            ),
        })
    return subject_progress


def summarize_study_sessions(rows, daily_goal_minutes, recent_rows=None):
    seconds_by_subject = {}
    for row in rows:
        seconds_by_subject[row.subject] = (
            seconds_by_subject.get(row.subject, 0) + row.duration_seconds
        )

    total_seconds = sum(seconds_by_subject.values())
    goal_seconds = max(0, daily_goal_minutes or 0) * 60
    return {
        "todaySeconds": total_seconds,
        "goalSeconds": goal_seconds,
        "goalPercent": min(100, round(total_seconds * 100 / goal_seconds)) if goal_seconds else 0,
        "subjects": [
            {"subject": subject, "seconds": seconds}
            for subject, seconds in sorted(
                seconds_by_subject.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ],
        "recent": [
            {
                "subject": row.subject,
                "durationSeconds": row.duration_seconds,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in (recent_rows if recent_rows is not None else rows)[:8]
        ],
    }


def study_sessions_for_today(user_id):
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_next_day = start_of_day + timedelta(days=1)
    return (
        StudySession.query
        .filter(
            StudySession.user_id == user_id,
            StudySession.created_at >= start_of_day,
            StudySession.created_at < start_of_next_day,
        )
        .order_by(StudySession.created_at.desc())
        .all()
    )


def recent_study_sessions_for_user(user_id):
    return (
        StudySession.query
        .filter_by(user_id=user_id)
        .order_by(StudySession.created_at.desc())
        .limit(8)
        .all()
    )


def school_listing_validation_error(data):
    if not isinstance(data, dict):
        return "Enter the school details before submitting."
    name = data.get('name')
    county = data.get('county')
    town = data.get('town', '')
    description = data.get('description', '')
    official_email = data.get('officialEmail')
    if not all(isinstance(value, str) for value in (name, county, town, description, official_email)):
        return "Enter valid school details."
    name = name.strip()
    county = county.strip()
    town = town.strip()
    description = description.strip()
    official_email = official_email.strip().lower()
    if not 2 <= len(name) <= 120:
        return "School name must be between 2 and 120 characters."
    if not 2 <= len(county) <= 80:
        return "Enter the county where the school is located."
    if len(town) > 80 or len(description) > 500:
        return "Town or description is too long."
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', official_email) or len(official_email) > 150:
        return "Enter a valid official school contact email."
    return None


def serialize_school_membership(membership, user):
    grade = user.campus_year if user.student_track == 'campus' else user.elementary_grade
    return {
        "id": membership.id,
        "displayName": user.display_name or "Learner",
        "grade": grade or "Level not set",
        "requestedAt": membership.requested_at.isoformat() if membership.requested_at else None,
    }


def school_directory_payload(user):
    schools = School.query.filter_by(status='approved').order_by(func.lower(School.name)).all()
    school_ids = [school.id for school in schools]
    memberships = SchoolMembership.query.filter(
        SchoolMembership.user_id == user.id,
        SchoolMembership.school_id.in_(school_ids),
    ).all() if school_ids else []
    memberships_by_school = {membership.school_id: membership for membership in memberships}
    directory = [
        {
            "id": school.id,
            "name": school.name,
            "county": school.county,
            "town": school.town,
            "description": school.description,
            "managedByMe": school.admin_user_id == user.id,
            "membershipStatus": (
                memberships_by_school[school.id].status if school.id in memberships_by_school else None
            ),
        }
        for school in schools
    ]
    approved_school_ids = [
        school_id for school_id, membership in memberships_by_school.items()
        if membership.status == 'approved'
    ]
    communities = {}
    if approved_school_ids:
        community_rows = (
            SchoolMembership.query
            .filter(
                SchoolMembership.school_id.in_(approved_school_ids),
                SchoolMembership.status == 'approved',
            )
            .order_by(SchoolMembership.requested_at.asc())
            .all()
        )
        member_users = {
            learner.id: learner
            for learner in User.query.filter(
                User.id.in_([membership.user_id for membership in community_rows])
            ).all()
        } if community_rows else {}
        for membership in community_rows:
            learner = member_users.get(membership.user_id)
            if learner:
                communities.setdefault(membership.school_id, []).append(
                    serialize_school_membership(membership, learner)
                )
    for school_item in directory:
        if school_item["membershipStatus"] == 'approved':
            school_item["members"] = communities.get(school_item["id"], [])

    managed_schools = School.query.filter_by(admin_user_id=user.id).all()
    managed = []
    for school in managed_schools:
        entry = {
            "id": school.id,
            "name": school.name,
            "county": school.county,
            "town": school.town,
            "status": school.status,
            "joinRequests": [],
            "members": [],
        }
        if school.status == 'approved':
            pending_memberships = (
                SchoolMembership.query
                .filter_by(school_id=school.id, status='pending')
                .order_by(SchoolMembership.requested_at.asc())
                .all()
            )
            member_rows = (
                SchoolMembership.query
                .filter_by(school_id=school.id, status='approved')
                .order_by(SchoolMembership.requested_at.asc())
                .all()
            )
            pending_users = {
                learner.id: learner
                for learner in User.query.filter(
                    User.id.in_([membership.user_id for membership in pending_memberships])
                ).all()
            } if pending_memberships else {}
            approved_users = {
                learner.id: learner
                for learner in User.query.filter(
                    User.id.in_([membership.user_id for membership in member_rows])
                ).all()
            } if member_rows else {}
            entry["joinRequests"] = [
                serialize_school_membership(membership, pending_users[membership.user_id])
                for membership in pending_memberships
                if membership.user_id in pending_users
            ]
            entry["members"] = [
                serialize_school_membership(membership, approved_users[membership.user_id])
                for membership in member_rows
                if membership.user_id in approved_users
            ]
        managed.append(entry)

    pending_school_requests = []
    if is_full_admin(user):
        requests = (
            School.query.filter_by(status='pending')
            .order_by(School.requested_at.asc())
            .all()
        )
        requesters = {
            requester.id: requester
            for requester in User.query.filter(
                User.id.in_([school.admin_user_id for school in requests])
            ).all()
        } if requests else {}
        pending_school_requests = [
            {
                "id": school.id,
                "name": school.name,
                "county": school.county,
                "town": school.town,
                "description": school.description,
                "officialEmail": school.official_email,
                "requesterEmail": requesters[school.admin_user_id].email if school.admin_user_id in requesters else None,
                "requestedAt": school.requested_at.isoformat() if school.requested_at else None,
            }
            for school in requests
        ]
    return {
        "schools": directory,
        "managedSchools": managed,
        "pendingSchoolRequests": pending_school_requests,
        "canRequestListing": bool(user.email_verified),
    }


@app.route('/api/schools', methods=['GET', 'POST'])
@login_required
def api_schools():
    if request.method == 'GET':
        return jsonify(school_directory_payload(current_user))

    if not current_user.email_verified:
        return jsonify({"error": "Verify your email before requesting a school listing."}), 403
    data = request.get_json(silent=True) or {}
    validation_error = school_listing_validation_error(data)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    name = data["name"].strip()
    county = data["county"].strip()
    duplicate = School.query.filter(
        School.status.in_(('pending', 'approved')),
        func.lower(func.trim(School.name)) == name.lower(),
        func.lower(func.trim(School.county)) == county.lower(),
    ).first()
    if duplicate:
        return jsonify({"error": "A listing for this school is already pending or approved. Contact Clavis support if it needs updating."}), 409
    existing_request = School.query.filter_by(
        admin_user_id=current_user.id,
        status='pending',
    ).first()
    if existing_request:
        return jsonify({"error": "You already have a school listing request awaiting review."}), 409

    school = School(
        name=name,
        county=county,
        town=data.get('town', '').strip(),
        description=data.get('description', '').strip(),
        official_email=data['officialEmail'].strip().lower(),
        admin_user_id=current_user.id,
    )
    db.session.add(school)
    db.session.commit()
    return jsonify({"message": "School request submitted for Clavis review.", "schoolId": school.id}), 201


@app.route('/api/schools/<int:school_id>/listing-review', methods=['POST'])
@login_required
def api_review_school_listing(school_id):
    if not is_full_admin(current_user):
        return jsonify({"error": "Clavis platform admin access is required."}), 403
    school = School.query.get_or_404(school_id)
    if school.status != 'pending':
        return jsonify({"error": "This school request has already been reviewed."}), 409
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action not in ('approve', 'reject'):
        return jsonify({"error": "Choose approve or reject."}), 400
    school.status = 'approved' if action == 'approve' else 'rejected'
    school.reviewed_at = datetime.now()
    school.reviewed_by_id = current_user.id
    db.session.commit()
    return jsonify({"message": f"School listing {school.status}.", "status": school.status})


@app.route('/api/schools/<int:school_id>/join', methods=['POST'])
@login_required
def api_request_school_membership(school_id):
    if not current_user.email_verified:
        return jsonify({"error": "Verify your email before requesting to join a school."}), 403
    school = School.query.filter_by(id=school_id, status='approved').first_or_404()
    if school.admin_user_id == current_user.id:
        return jsonify({"error": "You already manage this school."}), 409
    membership = SchoolMembership.query.filter_by(
        school_id=school.id,
        user_id=current_user.id,
    ).first()
    if membership and membership.status in ('pending', 'approved'):
        return jsonify({"error": "You already have a pending request or membership at this school."}), 409
    if membership is None:
        membership = SchoolMembership(school_id=school.id, user_id=current_user.id)
        db.session.add(membership)
    else:
        membership.status = 'pending'
        membership.requested_at = datetime.now()
        membership.reviewed_at = None
        membership.reviewed_by_id = None
    db.session.commit()
    return jsonify({"message": "Join request sent to the school admin."}), 201


@app.route('/api/schools/<int:school_id>/members/<int:membership_id>/review', methods=['POST'])
@login_required
def api_review_school_membership(school_id, membership_id):
    school = School.query.filter_by(id=school_id, status='approved').first_or_404()
    if school.admin_user_id != current_user.id:
        return jsonify({"error": "Only this school's admin can review join requests."}), 403
    membership = SchoolMembership.query.filter_by(
        id=membership_id,
        school_id=school.id,
        status='pending',
    ).first_or_404()
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    if action not in ('approve', 'reject'):
        return jsonify({"error": "Choose approve or reject."}), 400
    membership.status = 'approved' if action == 'approve' else 'rejected'
    membership.reviewed_at = datetime.now()
    membership.reviewed_by_id = current_user.id
    db.session.commit()
    return jsonify({"message": f"Join request {membership.status}."})


@app.route('/api/learning-progress')
@login_required
def api_learning_progress():
    level, grade = current_promotion_level(current_user)
    if not level or not grade:
        return jsonify({"error": "Choose your learning path and level to see your progress."}), 400

    subjects = onboarding_subject_choices(current_user)
    rows = TopicProgress.query.filter_by(
        user_id=current_user.id,
        level=level,
        grade_form=grade,
    ).order_by(TopicProgress.updated_at.desc()).all()
    subject_progress = summarize_learning_topics(subjects, rows)

    promotion_rows = (
        PromotionExam.query
        .filter_by(user_id=current_user.id, student_track=current_user.student_track)
        .order_by(PromotionExam.created_at.desc())
        .limit(10)
        .all()
    )
    promotion_passes = PromotionExam.query.filter_by(
        user_id=current_user.id,
        student_track=current_user.student_track,
        status='passed',
    ).count()
    timetable = study_timetable_for_user(current_user)
    study_summary = summarize_study_sessions(
        study_sessions_for_today(current_user.id),
        current_user.daily_goal_minutes,
        recent_study_sessions_for_user(current_user.id),
    )
    promotion_exams = []
    for exam in promotion_rows:
        completed_course = False
        if exam.result_json:
            try:
                completed_course = bool(json.loads(exam.result_json).get('courseCompleted'))
            except (AttributeError, TypeError, json.JSONDecodeError):
                app.logger.warning("Invalid promotion result data for exam %s", exam.id)
        promotion_exams.append({
            "level": exam.grade_form,
            "status": exam.status,
            "score": exam.score,
            "total": exam.total_marks or 30,
            "passed": exam.status == 'passed',
            "completedCourse": completed_course,
            "date": exam.created_at.isoformat() if exam.created_at else None,
        })
    return jsonify({
        "track": current_user.student_track,
        "level": level,
        "grade": grade,
        "course": current_user.campus_course if current_user.student_track == 'campus' else None,
        "favoriteSubject": current_user.favorite_subject,
        "challengingSubjects": challenging_subjects_for_user(current_user),
        "subjects": subject_progress,
        "topicTotals": {
            "started": sum(item["topicsStarted"] for item in subject_progress),
            "taught": sum(item["topicsTaught"] for item in subject_progress),
            "mastered": sum(item["topicsMastered"] for item in subject_progress),
        },
        "promotionExams": promotion_exams,
        "promotionPasses": promotion_passes,
        "timetable": {
            "dailyHours": timetable["daily_hours"],
            "totalSubjects": timetable["total_subjects"],
            "subjects": timetable["subjects"],
            "dailyGoalMinutes": current_user.daily_goal_minutes or 0,
        },
        "study": study_summary,
    })


@app.route('/api/study-sessions', methods=['GET', 'POST'])
@login_required
def api_study_sessions():
    if request.method == 'GET':
        return jsonify(summarize_study_sessions(
            study_sessions_for_today(current_user.id),
            current_user.daily_goal_minutes,
            recent_study_sessions_for_user(current_user.id),
        ))

    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    level = data.get('level')
    grade_form = data.get('gradeForm')
    duration_seconds = data.get('durationSeconds')
    if (
        not isinstance(subject, str)
        or not isinstance(level, str)
        or not isinstance(grade_form, str)
        or type(duration_seconds) is not int
    ):
        return jsonify({"error": "A subject, level, grade, and duration are required."}), 400

    subject = subject.strip()
    level = level.strip()
    grade_form = grade_form.strip()
    if not subject or not level or not grade_form or len(subject) > 100:
        return jsonify({"error": "Choose a valid subject, level, and grade."}), 400
    if duration_seconds <= 0:
        return jsonify({"error": "Study time must be greater than zero."}), 400

    current_level, current_grade = current_promotion_level(current_user)
    if (level, grade_form) != (current_level, current_grade):
        return jsonify({"error": "The submitted study period does not match your enrolled level."}), 400

    scheduled_subject = next(
        (item for item in study_timetable_for_user(current_user)["subjects"] if item["name"] == subject),
        None,
    )
    if not scheduled_subject:
        return jsonify({"error": "This subject is not in your saved study timetable."}), 400
    scheduled_minutes = float(scheduled_subject["minutes"].replace(" min", ""))
    if duration_seconds > round(scheduled_minutes * 60):
        return jsonify({"error": "The recorded time exceeds this subject's scheduled study period."}), 400

    db.session.add(StudySession(
        user_id=current_user.id,
        subject=subject,
        level=level,
        grade_form=grade_form,
        duration_seconds=duration_seconds,
    ))
    db.session.commit()
    return jsonify(summarize_study_sessions(
        study_sessions_for_today(current_user.id),
        current_user.daily_goal_minutes,
        recent_study_sessions_for_user(current_user.id),
    )), 201


def current_promotion_level(user):
    if user.student_track == 'campus':
        if user.campus_year not in CAMPUS_YEARS:
            return None, None
        return 'campus', user.campus_year
    if user.student_track != 'highschool':
        return None, None
    grade = user.elementary_grade or ''
    match = re.fullmatch(r'Grade (1[0-2]|[3-9])', grade)
    if not match:
        return None, None
    number = int(match.group(1))
    level = 'senior_school' if number >= 10 else 'junior_school' if number >= 7 else 'upper_primary'
    return level, grade


def next_promotion_level(user):
    level, grade = current_promotion_level(user)
    if level == 'campus':
        year = int(grade.split()[-1])
        return (f'Year {year + 1}', year == 5)
    if grade:
        number = int(grade.split()[-1])
        return (f'Grade {number + 1}', number == 12)
    return None, False


def has_completed_highest_course(user):
    if user.student_track != 'campus':
        return False
    grade = user.campus_year or ''
    if grade != 'Year 5':
        return False
    return PromotionExam.query.filter_by(
        user_id=user.id,
        student_track='campus',
        grade_form=grade,
        status='passed',
    ).first() is not None


@app.route('/api/promotion-exam', methods=['POST'])
@login_required
@limiter.limit("2 per minute; 10 per hour", exempt_when=is_unlimited_rate_limit_user)
def api_create_promotion_exam():
    if not current_user.email_verified:
        return jsonify({"error": "Please verify your email before taking a promotion exam."}), 403
    if current_user.subscription_expired and not has_unlimited_access(current_user):
        return jsonify({"error": "Your trial has ended — please choose a plan."}), 402

    level, grade = current_promotion_level(current_user)
    if not level or not grade:
        return jsonify({"error": "Complete your learning-path setup before taking a promotion exam."}), 400
    if grade in ('Grade 12', 'Year 5') and PromotionExam.query.filter_by(
        user_id=current_user.id,
        student_track=current_user.student_track,
        grade_form=grade,
        status='passed',
    ).first():
        return jsonify({"error": "You have already completed the highest available level."}), 409

    if level == 'campus':
        if current_user.campus_course not in DAILY_CHALLENGE_SUBJECTS['campus']:
            return jsonify({"error": "Choose a valid Campus course before taking a promotion exam."}), 400
        subjects = [current_user.campus_course]
    else:
        subjects = CLASS_SUBJECTS_BY_LEVEL.get(level)
    if not subjects:
        app.logger.error("No class promotion subjects configured for level %s", level)
        return jsonify({"error": "Promotion exams are not configured for this learning level yet."}), 503

    allowed, remaining = check_and_consume_ai_call(current_user)
    if not allowed:
        return jsonify({"error": "You've hit today's message limit — it resets tomorrow. Upgrade for a higher daily limit."}), 429

    subject_list = ', '.join(subjects)
    exam_scope = (
        f"the Campus course {subject_list}"
        if level == 'campus'
        else f"all subjects for this class: {subject_list}"
    )
    prompt = f"""Create one combined class promotion exam for a {describe_level(level, grade)} learner in Kenya.
The exam covers {exam_scope}.
Write exactly 30 questions total. Distribute them as evenly as possible across these subjects, and include every subject at least once. Each question must be a one-mark, age-appropriate multiple-choice question with four concise options. All questions must be unambiguous and have one correct answer.
Return ONLY valid JSON with this shape and no markdown:
{{
  "total_marks": 30,
  "questions": [
    {{"id": 1, "subject": "{subjects[0]}", "type": "mcq", "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "A", "marks": 1, "explanation": "..."}}
  ]
}}
Use sequential question IDs from 1 to 30. Use only the exact subject names listed above. Do not add short-answer questions or diagrams."""

    try:
        raw = call_groq([{"role": "user", "content": prompt}], max_tokens=6500)
        exam_data = validate_class_promotion_test(extract_json(raw), subjects)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": f"Could not create a valid promotion exam: {error}"}), 502

    exam = PromotionExam(
        exam_key=secrets.token_urlsafe(32),
        user_id=current_user.id,
        student_track=current_user.student_track,
        level=level,
        grade_form=grade,
        subjects_json=json.dumps(subjects),
        questions_json=json.dumps(exam_data["questions"]),
    )
    db.session.add(exam)
    db.session.commit()
    return jsonify({
        "examId": exam.exam_key,
        "level": level,
        "gradeForm": grade,
        "scopeLabel": f"Campus course: {subject_list}" if level == 'campus' else "All class subjects",
        "totalMarks": 30,
        "passMark": 21,
        "questions": [{
            "id": question["id"],
            "subject": question["subject"],
            "type": question["type"],
            "question": question["question"],
            "options": question["options"],
            "marks": question["marks"],
        } for question in exam_data["questions"]],
    })


@app.route('/api/promotion-exam/<exam_key>/submit', methods=['POST'])
@login_required
def api_submit_promotion_exam(exam_key):
    exam = PromotionExam.query.filter_by(
        exam_key=exam_key,
        user_id=current_user.id,
    ).first()
    if exam is None:
        return jsonify({"error": "This exam is no longer available. Start a new promotion exam."}), 404
    if exam.status != 'ready':
        if exam.result_json:
            return jsonify(json.loads(exam.result_json))
        return jsonify({"error": "This exam has already been submitted."}), 409

    level, grade = current_promotion_level(current_user)
    if current_user.student_track != exam.student_track or level != exam.level or grade != exam.grade_form:
        return jsonify({"error": "Your learning level changed after this exam was created."}), 409

    data = request.get_json(silent=True) or {}
    answers = data.get('answers')
    if not isinstance(answers, dict):
        return jsonify({"error": "Submit your selected answers to finish the exam."}), 400

    questions = json.loads(exam.questions_json)
    question_ids = {str(question["id"]) for question in questions}
    if set(answers) - question_ids or any(
        not isinstance(answer, str) or answer not in {"A", "B", "C", "D"}
        for answer in answers.values()
    ):
        return jsonify({"error": "One or more submitted answers are invalid."}), 400

    score = sum(
        question["marks"]
        for question in questions
        if answers.get(str(question["id"])) == question["correct_option"]
    )
    passed = score >= 21
    exam.score = score
    exam.total_marks = 30
    exam.status = 'passed' if passed else 'failed'

    next_grade, course_completed = next_promotion_level(current_user)
    if passed and not course_completed:
        if exam.student_track == 'campus':
            current_user.campus_year = next_grade
        else:
            current_user.elementary_grade = next_grade
    result = {
        "score": score,
        "total": 30,
        "percent": round(score / 30 * 100),
        "passed": passed,
        "previousGrade": exam.grade_form,
        "nextGrade": next_grade if passed and not course_completed else exam.grade_form,
        "courseCompleted": bool(passed and course_completed),
        "review": [{
            "id": question["id"],
            "subject": question["subject"],
            "question": question["question"],
            "correctOption": question["correct_option"],
            "explanation": question["explanation"],
            "yourAnswer": answers.get(str(question["id"])),
        } for question in questions],
    }
    exam.result_json = json.dumps(result)
    db.session.commit()
    return jsonify(result)


def certificate_photo_mime(photo_data):
    if photo_data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if photo_data.startswith(b'\xff\xd8\xff') and photo_data.endswith(b'\xff\xd9'):
        return 'image/jpeg'
    if (
        len(photo_data) >= 12
        and photo_data.startswith(b'RIFF')
        and photo_data[8:12] == b'WEBP'
    ):
        return 'image/webp'
    return None


def normalize_certificate_photo(photo_data):
    if certificate_photo_mime(photo_data) is None:
        raise ValueError("Upload a valid JPG, PNG, or WEBP image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(photo_data)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError("Upload a valid JPG, PNG, or WEBP image.")
                source.verify()
            with Image.open(io.BytesIO(photo_data)) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((1600, 2000), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=88, optimize=True)
                return output.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError) as error:
        raise ValueError("This photo could not be processed. Please choose another image.") from error


def _certificate_text(value):
    return str(value or '').encode('cp1252', errors='replace').decode('cp1252')


def render_certificate_pdf(application):
    page_width, page_height = landscape(A4)
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=(page_width, page_height), pageCompression=1)
    document.setTitle(f"Clavis Campus Certificate {application.certificate_number}")
    document.setAuthor("Clavis Campus")

    forest = HexColor('#183D2D')
    deep_forest = HexColor('#102C20')
    gold = HexColor('#B7924B')
    pale_gold = HexColor('#E8D9B5')
    paper = HexColor('#FBF8F0')
    ink = HexColor('#26372D')
    muted = HexColor('#637266')

    document.setFillColor(paper)
    document.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    document.setStrokeColor(forest)
    document.setLineWidth(2.2)
    document.rect(24, 24, page_width - 48, page_height - 48, fill=0, stroke=1)
    document.setStrokeColor(gold)
    document.setLineWidth(0.8)
    document.rect(32, 32, page_width - 64, page_height - 64, fill=0, stroke=1)

    for x, y, horizontal, vertical in (
        (40, 40, 1, 1),
        (page_width - 40, 40, -1, 1),
        (40, page_height - 40, 1, -1),
        (page_width - 40, page_height - 40, -1, -1),
    ):
        document.setStrokeColor(gold)
        document.setLineWidth(0.75)
        document.line(x, y, x + 18 * horizontal, y)
        document.line(x, y, x, y + 18 * vertical)
        document.circle(x + 5 * horizontal, y + 5 * vertical, 1.7, fill=1, stroke=0)

    center_x = page_width / 2
    document.setFillColor(forest)
    document.setFont('Times-Bold', 13)
    document.drawCentredString(center_x, page_height - 69, "C L A V I S   C A M P U S")
    document.setFillColor(muted)
    document.setFont('Helvetica', 7.5)
    document.drawCentredString(center_x, page_height - 84, "LEARNING  ·  PURPOSE  ·  ACHIEVEMENT")

    medallion_y = page_height - 135
    document.setFillColor(forest)
    document.setStrokeColor(gold)
    document.setLineWidth(1.4)
    document.circle(center_x, medallion_y, 25, fill=1, stroke=1)
    document.setStrokeColor(pale_gold)
    document.setLineWidth(0.7)
    document.circle(center_x, medallion_y, 20, fill=0, stroke=1)
    document.setFillColor(pale_gold)
    document.setFont('Times-Bold', 12)
    document.drawCentredString(center_x, medallion_y - 4, "C")
    document.setFillColor(gold)
    document.setFont('Times-Bold', 7)
    document.drawCentredString(center_x, page_height - 174, "CERTIFICATE OF COMPLETION")

    document.setFillColor(deep_forest)
    document.setFont('Times-Roman', 29)
    document.drawCentredString(center_x, page_height - 218, "Certificate of Achievement")
    document.setFillColor(muted)
    document.setFont('Times-Italic', 13)
    document.drawCentredString(center_x, page_height - 244, "This distinction is proudly presented to")

    learner_name = _certificate_text(application.learner_name)
    name_font_size = 27
    name_lines = simpleSplit(learner_name, 'Times-BoldItalic', name_font_size, page_width - 190)
    while name_font_size > 15 and (
        len(name_lines) > 2
        or any(
            pdfmetrics.stringWidth(line, 'Times-BoldItalic', name_font_size) > page_width - 190
            for line in name_lines
        )
    ):
        name_font_size -= 1
        name_lines = simpleSplit(learner_name, 'Times-BoldItalic', name_font_size, page_width - 190)
    document.setFillColor(forest)
    document.setFont('Times-BoldItalic', name_font_size)
    name_baseline = page_height - 280
    for line in name_lines:
        document.drawCentredString(center_x, name_baseline, line)
        name_baseline -= name_font_size + 3
    underline_y = name_baseline + 1
    document.setStrokeColor(gold)
    document.setLineWidth(0.8)
    document.line(center_x - 120, underline_y, center_x + 120, underline_y)

    document.setFillColor(ink)
    document.setFont('Times-Roman', 13)
    document.drawCentredString(center_x, underline_y - 24, "in recognition of successfully completing the")
    course_title = _certificate_text(application.course_level)
    course_font_size = 17
    course_lines = simpleSplit(course_title, 'Times-Bold', course_font_size, page_width - 190)
    while course_font_size > 12 and (
        len(course_lines) > 2
        or any(
            pdfmetrics.stringWidth(line, 'Times-Bold', course_font_size) > page_width - 190
            for line in course_lines
        )
    ):
        course_font_size -= 1
        course_lines = simpleSplit(course_title, 'Times-Bold', course_font_size, page_width - 190)
    document.setFillColor(forest)
    document.setFont('Times-Bold', course_font_size)
    course_baseline = underline_y - 51
    for line in course_lines:
        document.drawCentredString(center_x, course_baseline, line)
        course_baseline -= course_font_size + 2
    document.setFillColor(muted)
    document.setFont('Times-Italic', 11)
    document.drawCentredString(center_x, course_baseline - 7, "through the Clavis Campus learning programme.")

    photo_data = getattr(application, 'photo_data', None)
    if photo_data:
        with Image.open(io.BytesIO(photo_data)) as source:
            portrait = ImageOps.exif_transpose(source).convert('RGB')
            portrait.thumbnail((420, 500), Image.Resampling.LANCZOS)
            portrait_buffer = io.BytesIO()
            portrait.save(portrait_buffer, format='JPEG', quality=88)
        portrait_buffer.seek(0)
        photo_x, photo_y, photo_width, photo_height = 65, 93, 75, 95
        document.setFillColor(HexColor('#FFFFFF'))
        document.roundRect(photo_x - 3, photo_y - 3, photo_width + 6, photo_height + 6, 3, fill=1, stroke=0)
        document.drawImage(
            ImageReader(portrait_buffer),
            photo_x,
            photo_y,
            width=photo_width,
            height=photo_height,
            preserveAspectRatio=True,
            anchor='c',
            mask='auto',
        )
        document.setStrokeColor(gold)
        document.setLineWidth(0.8)
        document.roundRect(photo_x - 3, photo_y - 3, photo_width + 6, photo_height + 6, 3, fill=0, stroke=1)

    seal_x, seal_y = page_width - 100, 145
    document.setFillColor(HexColor('#F3EDDE'))
    document.setStrokeColor(gold)
    document.setLineWidth(1.2)
    document.circle(seal_x, seal_y, 29, fill=1, stroke=1)
    document.setStrokeColor(forest)
    document.setLineWidth(0.8)
    document.circle(seal_x, seal_y, 24, fill=0, stroke=1)
    document.setFillColor(forest)
    document.setFont('Times-Bold', 7)
    document.drawCentredString(seal_x, seal_y + 5, "CLAVIS")
    document.setFont('Helvetica', 5.5)
    document.drawCentredString(seal_x, seal_y - 5, "CAMPUS")
    document.setFillColor(gold)
    document.circle(seal_x, seal_y, 1.7, fill=1, stroke=0)

    document.setStrokeColor(gold)
    document.setLineWidth(0.7)
    document.line(190, 104, 355, 104)
    document.line(455, 104, 620, 104)
    document.setFillColor(ink)
    document.setFont('Helvetica-Bold', 8)
    document.drawCentredString(272.5, 89, "ISSUED BY CLAVIS CAMPUS")
    document.setFillColor(muted)
    document.setFont('Helvetica', 7)
    document.drawCentredString(272.5, 76, application.reviewed_at.strftime('%d %B %Y') if application.reviewed_at else '')
    document.setFillColor(ink)
    document.setFont('Helvetica-Bold', 8)
    document.drawCentredString(537.5, 89, "CERTIFICATE NUMBER")
    document.setFillColor(muted)
    document.setFont('Helvetica', 7)
    document.drawCentredString(537.5, 76, _certificate_text(application.certificate_number))
    document.setFillColor(muted)
    document.setFont('Helvetica', 6)
    document.drawCentredString(center_x, 49, "A Clavis Campus record of learning and achievement")

    document.showPage()
    document.save()
    return output.getvalue()


def certificate_csrf_token():
    token = session.get('certificate_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['certificate_csrf_token'] = token
    return token


def certificate_csrf_valid():
    token = session.get('certificate_csrf_token')
    submitted_token = request.form.get('csrf_token', '')
    return bool(token and submitted_token and secrets.compare_digest(token, submitted_token))


@app.route('/certificate/application', methods=['GET', 'POST'])
@login_required
def certificate_application():
    is_admin = has_unlimited_access(current_user)
    if current_user.student_track != 'campus' and not is_admin:
        flash("Campus certificates are available to Campus learners only.", "warning")
        return redirect(url_for('home'))
    if not current_user.email_verified and not is_admin:
        flash("Please verify your email before applying for a certificate.", "danger")
        return redirect(url_for('home'))
    if not is_admin and not has_completed_highest_course(current_user):
        flash("Pass your Campus Year 5 promotion exam before applying for a certificate.", "warning")
        return redirect(url_for('home'))

    application = CertificateApplication.query.filter_by(
        user_id=current_user.id,
        student_track='campus',
    ).first()

    if request.method == 'POST':
        if not certificate_csrf_valid():
            flash("Your session expired. Please refresh the page and try again.", "danger")
            return redirect(url_for('certificate_application'))
        if application and application.status != 'rejected':
            flash("Your certificate application has already been submitted.", "warning")
            return redirect(url_for('certificate_application'))

        learner_name = (request.form.get('learner_name') or '').strip()
        photo = request.files.get('learner_photo')
        if not learner_name or len(learner_name) > 100:
            flash("Enter the name you want printed on the certificate (up to 100 characters).", "danger")
            return redirect(url_for('certificate_application'))
        if photo is None or not photo.filename:
            flash("Choose a clear portrait photo to include with your application.", "danger")
            return redirect(url_for('certificate_application'))

        photo_data = photo.stream.read(3 * 1024 * 1024 + 1)
        if len(photo_data) > 3 * 1024 * 1024:
            flash("The photo must be 3 MB or smaller.", "danger")
            return redirect(url_for('certificate_application'))
        if certificate_photo_mime(photo_data) is None:
            flash("Upload a valid JPG, PNG, or WEBP image.", "danger")
            return redirect(url_for('certificate_application'))
        try:
            photo_data = normalize_certificate_photo(photo_data)
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for('certificate_application'))
        photo_mime = 'image/jpeg'

        if is_admin:
            course = request.form.get('campus_course', '').strip()
            year = request.form.get('campus_year', '').strip()
            if course not in DAILY_CHALLENGE_SUBJECTS['campus'] or year not in CAMPUS_YEARS:
                flash("Choose a valid Campus course and year for this certificate.", "danger")
                return redirect(url_for('certificate_application'))
            course_level = f"{year} — {course}"
        else:
            course_level = f"{current_user.campus_year} — {current_user.campus_course}"
        if application is None:
            application = CertificateApplication(
                user_id=current_user.id,
                student_track='campus',
                course_level=course_level,
                learner_name=learner_name,
                photo_data=photo_data,
                photo_mime=photo_mime,
            )
            db.session.add(application)
        else:
            application.course_level = course_level
            application.learner_name = learner_name
            application.photo_data = photo_data
            application.photo_mime = photo_mime
            application.status = 'pending'
            application.certificate_number = None
            application.submitted_at = datetime.now()
            application.reviewed_at = None
            application.reviewed_by_id = None
        db.session.commit()
        flash("Your application was submitted. An admin will review it.", "success")
        return redirect(url_for('certificate_application'))

    return render_template(
        'certificate_application.html',
        application=application,
        user=current_user,
        is_admin_applicant=is_admin,
        csrf_token=certificate_csrf_token(),
        course_level=(
            f"{current_user.campus_year} — {current_user.campus_course}"
            if current_user.student_track == 'campus' and current_user.campus_course
            else None
        ),
        campus_courses=DAILY_CHALLENGE_SUBJECTS['campus'],
        campus_years=CAMPUS_YEARS,
    )


@app.route('/admin/certificates')
@login_required
def admin_certificates():
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    applications = (
        CertificateApplication.query
        .filter_by(student_track='campus')
        .order_by(CertificateApplication.submitted_at.desc())
        .all()
    )
    users = {user.id: user for user in User.query.filter(
        User.id.in_([application.user_id for application in applications])
    ).all()} if applications else {}
    return render_template(
        'admin_certificates.html',
        applications=applications,
        users=users,
        csrf_token=certificate_csrf_token(),
    )


@app.route('/admin/certificates/preview')
@login_required
def admin_certificate_preview():
    if not has_unlimited_access(current_user):
        return jsonify({"error": "Certificate preview not found."}), 404
    preview_application = type('CertificatePreview', (), {
        'certificate_number': 'CLV-2026-PREVIEW',
        'learner_name': 'Sample Campus Graduate',
        'course_level': 'Year 5 — Computer Science',
        'reviewed_at': datetime.now(),
        'photo_data': None,
    })()
    response = Response(render_certificate_pdf(preview_application), mimetype='application/pdf')
    response.headers['Content-Disposition'] = 'inline; filename="clavis-certificate-preview.pdf"'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/admin/certificates/<int:application_id>/photo')
@login_required
def admin_certificate_photo(application_id):
    if not has_unlimited_access(current_user):
        return jsonify({"error": "Admin access required."}), 403
    application = CertificateApplication.query.get_or_404(application_id)
    response = Response(application.photo_data, mimetype=application.photo_mime)
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/certificate/<int:application_id>/download')
@login_required
def download_certificate(application_id):
    application = CertificateApplication.query.get_or_404(application_id)
    is_applicant = application.user_id == current_user.id
    if not is_applicant and not has_unlimited_access(current_user):
        return jsonify({"error": "Certificate not found."}), 404
    if application.student_track != 'campus' or application.status != 'issued':
        return jsonify({"error": "This certificate is not available for download."}), 404

    pdf_data = render_certificate_pdf(application)
    filename = f"{application.certificate_number or 'clavis-campus-certificate'}.pdf"
    response = Response(pdf_data, mimetype='application/pdf')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/admin/certificates/<int:application_id>/issue', methods=['POST'])
@login_required
def admin_issue_certificate(application_id):
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    if not certificate_csrf_valid():
        flash("Your session expired. Please refresh the page and try again.", "danger")
        return redirect(url_for('admin_certificates'))
    application = CertificateApplication.query.get_or_404(application_id)
    if application.status != 'pending':
        flash("This certificate application has already been reviewed.", "warning")
        return redirect(url_for('admin_certificates'))
    applicant = User.query.get(application.user_id)
    if applicant is None or (
        not has_unlimited_access(applicant)
        and not has_completed_highest_course(applicant)
    ):
        flash("The learner must still have completed their highest level before a certificate can be issued.", "danger")
        return redirect(url_for('admin_certificates'))
    application.status = 'issued'
    application.certificate_number = (
        f"CLV-{datetime.now().year}-{secrets.token_hex(4).upper()}"
    )
    application.reviewed_at = datetime.now()
    application.reviewed_by_id = current_user.id
    db.session.commit()
    flash(f"Certificate issued for {application.learner_name}.", "success")
    return redirect(url_for('admin_certificates'))


@app.route('/admin/certificates/<int:application_id>/reject', methods=['POST'])
@login_required
def admin_reject_certificate(application_id):
    if not has_unlimited_access(current_user):
        flash("Admin access required.", "danger")
        return redirect(url_for('home'))
    if not certificate_csrf_valid():
        flash("Your session expired. Please refresh the page and try again.", "danger")
        return redirect(url_for('admin_certificates'))
    application = CertificateApplication.query.get_or_404(application_id)
    if application.status != 'pending':
        flash("This certificate application has already been reviewed.", "warning")
        return redirect(url_for('admin_certificates'))
    application.status = 'rejected'
    application.reviewed_at = datetime.now()
    application.reviewed_by_id = current_user.id
    db.session.commit()
    flash(f"Rejected the certificate application from {application.learner_name}.", "warning")
    return redirect(url_for('admin_certificates'))


@app.route('/api/exam-results', methods=['GET', 'POST'])
@login_required
def api_exam_results():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        result = ExamResult(
            user_id=current_user.id,
            subject=data.get('subject'),
            level=data.get('level'),
            grade_form=data.get('gradeForm'),
            paper=data.get('paper'),
            paper_count=data.get('paperCount'),
            score=data.get('score'),
            total_marks=data.get('total'),
            percent=data.get('percent'),
            student_name=data.get('studentName'),
            created_at=datetime.now(),
        )
        db.session.add(result)
        db.session.commit()
        return jsonify({"status": "ok"})

    rows = ExamResult.query.filter_by(user_id=current_user.id).order_by(ExamResult.created_at.desc()).limit(50).all()
    return jsonify([{
        "subject": r.subject,
        "level": r.level,
        "gradeForm": r.grade_form,
        "paper": r.paper,
        "paperCount": r.paper_count,
        "score": r.score,
        "total": r.total_marks,
        "percent": r.percent,
        "studentName": r.student_name,
        "date": r.created_at.isoformat() if r.created_at else None,
    } for r in rows])


@app.route('/api/exam-progress')
@login_required
def api_exam_progress():
    rows = (
        ExamResult.query
        .filter_by(user_id=current_user.id)
        .filter(ExamResult.percent != None)
        .order_by(ExamResult.created_at.asc())
        .all()
    )

    if not rows:
        return jsonify({
            "totalPapers": 0,
            "averagePercent": None,
            "bySubject": [],
            "weakestSubject": None,
            "strongestSubject": None,
            "recentTrend": [],
        })

    by_subject = {}
    for r in rows:
        subject = r.subject or 'Unknown'
        by_subject.setdefault(subject, []).append(r.percent)

    subject_summaries = []
    for subject, percents in by_subject.items():
        avg = sum(percents) / len(percents)
        subject_summaries.append({
            "subject": subject,
            "average": round(avg, 1),
            "attempts": len(percents),
            "latest": percents[-1],
        })
    subject_summaries.sort(key=lambda s: s['average'])

    # Only call out a "weakest" subject once there's enough signal — a single
    # bad paper shouldn't be branded a weak spot.
    eligible = [s for s in subject_summaries if s['attempts'] >= 2]
    weakest = eligible[0] if eligible else None
    strongest = max(eligible, key=lambda s: s['average']) if eligible else None

    all_percents = [r.percent for r in rows]
    recent = rows[-10:]

    return jsonify({
        "totalPapers": len(rows),
        "averagePercent": round(sum(all_percents) / len(all_percents), 1),
        "bySubject": sorted(subject_summaries, key=lambda s: -s['average']),
        "weakestSubject": weakest,
        "strongestSubject": strongest,
        "recentTrend": [{
            "subject": r.subject,
            "percent": r.percent,
            "date": r.created_at.isoformat() if r.created_at else None,
        } for r in recent],
    })


DAILY_CHALLENGE_SUBJECTS = {
    'campus': ['Mathematics', 'Statistics', 'Accounting', 'Economics', 'Business Studies', 'Marketing',
               'Finance', 'Human Resource Management', 'Public Administration', 'Law', 'Computer Science',
               'Psychology', 'Sociology', 'Political Science', 'Communication and Media Studies',
               'Education', 'History', 'Literature', 'Philosophy', 'Kiswahili'],
    'highschool': ['Mathematics', 'English', 'Physics', 'Chemistry', 'Biology', 'Kiswahili', 'Geography',
                   'History', 'Business Studies', 'Computer Studies', 'Agriculture', 'Home Science',
                   'Religious Education', 'Creative Arts'],
}

CLASS_SUBJECTS_BY_LEVEL = {
    'upper_primary': ['Mathematics', 'English', 'Kiswahili', 'Science and Technology', 'Social Studies', 'Agriculture', 'Creative Arts'],
    'junior_school': ['Mathematics', 'English', 'Kiswahili', 'Science and Technology', 'Social Studies', 'Pre-Technical Studies', 'Agriculture', 'Creative Arts', 'ICT', 'Business Studies'],
    'senior_school': ['Mathematics', 'English', 'Physics', 'Chemistry', 'Agriculture', 'Home Science', 'Creative Arts', 'Biology', 'Kiswahili', 'Geography', 'Religious Education', 'Business Studies', 'History', 'Computer Studies'],
    'campus': ['Mathematics', 'Statistics', 'Accounting', 'Economics', 'Business Studies', 'Marketing', 'Finance', 'Human Resource Management', 'Public Administration', 'Law', 'Computer Science', 'Psychology', 'Sociology', 'Political Science', 'Communication and Media Studies', 'Education', 'History', 'Literature', 'Philosophy', 'Kiswahili'],
}


def pick_daily_subject(user):
    subjects = onboarding_subject_choices(user)
    if not subjects:
        track = user.student_track or 'highschool'
        subjects = DAILY_CHALLENGE_SUBJECTS.get(track, DAILY_CHALLENGE_SUBJECTS['highschool'])
    seed = f"{user.id}-{datetime.now().strftime('%Y-%m-%d')}"
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(subjects)
    return subjects[idx]


def generate_daily_challenge(user):
    subject = pick_daily_subject(user)
    track = user.student_track or 'highschool'
    if track == 'campus':
        level_desc = "a university/campus student in Kenya"
    elif user.elementary_grade:
        stage = elementary_stage_for_grade(user.elementary_grade)
        level_desc = f"a Kenyan {stage} student in {user.elementary_grade} following CBC"
    else:
        level_desc = "a Kenyan school student following CBC"

    prompt = (
        f"Create ONE bite-sized multiple-choice quiz question in {subject}, suitable for {level_desc}. "
        f"It should be fun, thought-provoking, and answerable in under 30 seconds — a quick daily brain "
        f"teaser, not a full exam question. Return ONLY valid JSON, nothing else, in this exact shape:\n"
        f'{{"question": "...", "options": ["...", "...", "...", "..."], "correct_index": 0, "explanation": "one friendly sentence explaining the answer"}}\n'
        f"The options array must have exactly 4 items. correct_index is 0-based (0-3)."
    )
    raw = call_groq([{"role": "user", "content": prompt}], max_tokens=500)
    data = extract_json(raw)
    if not isinstance(data.get('options'), list) or len(data['options']) != 4:
        raise ValueError("Malformed daily challenge response (options)")
    if not isinstance(data.get('correct_index'), int) or not (0 <= data['correct_index'] < 4):
        raise ValueError("Malformed daily challenge response (correct_index)")
    data['subject'] = subject
    return data


@app.route('/api/daily-challenge')
@login_required
def api_daily_challenge():
    if not current_user.email_verified:
        return jsonify({"error": "Please verify your email to unlock the daily challenge."}), 403

    today = datetime.now().strftime('%Y-%m-%d')

    if current_user.daily_challenge_date != today or not current_user.daily_challenge_json:
        allowed, remaining = check_and_consume_ai_call(current_user)
        if not allowed:
            return jsonify({"error": "You've hit today's message limit — it resets tomorrow."}), 429
        try:
            data = generate_daily_challenge(current_user)
        except Exception as e:
            app.logger.error(f"Failed to generate daily challenge for {current_user.email}: {e}")
            return jsonify({"error": "Could not load today's challenge — try again in a moment."}), 502
        current_user.daily_challenge_date = today
        current_user.daily_challenge_json = json.dumps(data)
        current_user.daily_challenge_answered = False
        db.session.commit()
    else:
        data = json.loads(current_user.daily_challenge_json)

    response = {
        "subject": data['subject'],
        "question": data['question'],
        "options": data['options'],
        "answered": bool(current_user.daily_challenge_answered),
        "streak": current_user.daily_challenge_streak or 0,
        "bestStreak": current_user.daily_challenge_best_streak or 0,
    }
    if current_user.daily_challenge_answered:
        response["correctIndex"] = data['correct_index']
        response["explanation"] = data.get('explanation', '')
        response["wasCorrect"] = bool(data.get('was_correct'))
    return jsonify(response)


@app.route('/api/daily-challenge/answer', methods=['POST'])
@login_required
def api_daily_challenge_answer():
    today = datetime.now().strftime('%Y-%m-%d')
    if current_user.daily_challenge_date != today or not current_user.daily_challenge_json:
        return jsonify({"error": "No active challenge — refresh and try again."}), 400
    if current_user.daily_challenge_answered:
        return jsonify({"error": "You've already answered today's challenge."}), 400

    body = request.get_json(silent=True) or {}
    chosen_index = body.get('choiceIndex')
    if not isinstance(chosen_index, int):
        return jsonify({"error": "Invalid answer"}), 400

    challenge = json.loads(current_user.daily_challenge_json)
    correct = (chosen_index == challenge.get('correct_index'))
    challenge['was_correct'] = correct
    current_user.daily_challenge_json = json.dumps(challenge)
    current_user.daily_challenge_answered = True

    if correct:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        if current_user.daily_challenge_last_correct_date == yesterday:
            current_user.daily_challenge_streak = (current_user.daily_challenge_streak or 0) + 1
        else:
            current_user.daily_challenge_streak = 1
        current_user.daily_challenge_last_correct_date = today
        if current_user.daily_challenge_streak > (current_user.daily_challenge_best_streak or 0):
            current_user.daily_challenge_best_streak = current_user.daily_challenge_streak
    else:
        current_user.daily_challenge_streak = 0

    db.session.commit()
    return jsonify({
        "correct": correct,
        "correctIndex": challenge.get('correct_index'),
        "explanation": challenge.get('explanation', ''),
        "streak": current_user.daily_challenge_streak or 0,
        "bestStreak": current_user.daily_challenge_best_streak or 0,
    })


@app.route('/api/profile', methods=['GET', 'POST'])
@login_required
def api_profile():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if 'displayName' in data:
            name = (data.get('displayName') or '').strip()
            # Keep it short and plain — this is shown to other students on
            # the leaderboard, so no emails, no wild-length input.
            name = ''.join(ch for ch in name if ch.isalnum() or ch in ' -_')[:40].strip()
            current_user.display_name = name or default_display_name(current_user.email)
        if 'leaderboardOptIn' in data:
            current_user.leaderboard_opt_in = bool(data.get('leaderboardOptIn'))
        if 'phoneNumber' in data:
            raw = (data.get('phoneNumber') or '').strip()
            if not raw:
                current_user.phone_number = None
            else:
                normalized = normalize_kenyan_phone(raw)
                if not normalized:
                    return jsonify({"error": "That doesn't look like a valid Kenyan phone number (e.g. 07XXXXXXXX)."}), 400
                current_user.phone_number = normalized
        if 'studentTrack' in data:
            track = (data.get('studentTrack') or '').strip().lower()
            if track in ('campus', 'highschool', 'elementary'):
                # Same rule as login: locked in after the first choice for
                # everyone except the site owner's own account.
                if is_full_admin(current_user) or current_user.student_track is None:
                    current_user.student_track = 'highschool' if track == 'elementary' else track
                else:
                    return jsonify({"error": "Your study track is locked in and can't be changed here. Contact support if this needs to change."}), 403
        db.session.commit()
        return jsonify({"status": "ok"})

    return jsonify({
        "displayName": current_user.display_name or default_display_name(current_user.email),
        "leaderboardOptIn": current_user.leaderboard_opt_in if current_user.leaderboard_opt_in is not None else True,
        "phoneNumber": current_user.phone_number or '',
        "studentTrack": current_user.student_track or 'highschool',
    })


@app.route('/api/leaderboard')
@login_required
def api_leaderboard():
    period = request.args.get('period', 'all')
    if period not in ('all', 'week', 'inviters'):
        period = 'all'

    if period == 'inviters':
        # A completely different metric — how many friends you've referred —
        # not tied to UserStats at all.
        referral_counts = dict(
            db.session.query(User.referred_by_id, db.func.count(User.id))
            .filter(User.referred_by_id != None)
            .group_by(User.referred_by_id)
            .all()
        )
        inviters = User.query.filter(User.id.in_(referral_counts.keys())).all() if referral_counts else []
        entries = []
        for user in inviters:
            if is_full_admin(user) or is_co_admin(user):
                continue
            opted_in = user.leaderboard_opt_in if user.leaderboard_opt_in is not None else True
            entries.append({
                "user_id": user.id,
                "name": user.display_name or default_display_name(user.email),
                "total": referral_counts.get(user.id, 0),
                "streak": 0,  # not a meaningful metric for this board
                "opted_in": bool(opted_in),
            })
    else:
        rows = (
            db.session.query(User, UserStats)
            .join(UserStats, UserStats.user_id == User.id)
            .all()
        )
        current_week = week_start_str()
        entries = []
        for user, stats in rows:
            # Admins/co-admins aren't students competing for a place on the board.
            if is_full_admin(user) or is_co_admin(user):
                continue
            opted_in = user.leaderboard_opt_in if user.leaderboard_opt_in is not None else True
            if period == 'week':
                score = (stats.weekly_total or 0) if stats.week_start_date == current_week else 0
            else:
                score = stats.total or 0
            entries.append({
                "user_id": user.id,
                "name": user.display_name or default_display_name(user.email),
                "total": score,
                "streak": stats.streak or 0,
                "opted_in": bool(opted_in),
            })

    visible = [e for e in entries if e['opted_in'] and e['total'] > 0]
    visible.sort(key=lambda e: (-e['total'], -e['streak']))
    for i, e in enumerate(visible):
        e['rank'] = i + 1

    me = next((e for e in entries if e['user_id'] == current_user.id), None)
    my_rank = me.get('rank') if (me and me['opted_in'] and me['total'] > 0) else None

    return jsonify({
        "period": period,
        "top": [
            {"rank": e['rank'], "name": e['name'], "total": e['total'], "streak": e['streak'], "isMe": e['user_id'] == current_user.id}
            for e in visible[:20]
        ],
        "myRank": my_rank,
        "myTotal": me['total'] if me else 0,
        "totalRanked": len(visible),
        "optedIn": bool(current_user.leaderboard_opt_in) if current_user.leaderboard_opt_in is not None else True,
    })


@app.route('/healthz')
@limiter.exempt
def healthz():
    return jsonify({"status": "ok"})


def export_database_to_json():
    """Dump every row of every table to a JSON-serializable dict, keyed by
    table name. Used for lightweight, dependency-free database backups —
    doesn't need pg_dump or any Postgres client tools to be installed."""
    def serialize_value(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    data = {}
    for model in (User, PaymentLog, UserStats, ExamResult):
        rows = []
        for obj in model.query.all():
            rows.append({col.name: serialize_value(getattr(obj, col.name)) for col in model.__table__.columns})
        data[model.__tablename__] = rows
    return data


def run_database_backup():
    """Export the full database to JSON, zip it, and email it as an
    attachment to BACKUP_EMAIL (falling back to MAIL_FROM). Returns True if
    it was actually sent, False if SMTP isn't configured (logs instead)."""
    data = export_database_to_json()
    total_rows = sum(len(v) for v in data.values())

    if not SMTP_HOST:
        app.logger.info(f"[backup] SMTP not configured — would back up {total_rows} rows across {len(data)} tables")
        return False

    backup_to = os.environ.get('BACKUP_EMAIL') or MAIL_FROM
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    json_bytes = json.dumps(data, indent=2).encode('utf-8')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"clavi-backup-{stamp}.json", json_bytes)
    buf.seek(0)

    msg = MIMEMultipart()
    msg['Subject'] = f"Clavis database backup — {datetime.now().strftime('%Y-%m-%d')}"
    msg['From'] = MAIL_FROM
    msg['To'] = backup_to
    summary = ', '.join(f"{name} ({len(rows)})" for name, rows in data.items())
    msg.attach(MIMEText(f"Automated database backup attached.\n\nTables: {summary}\nTotal rows: {total_rows}\n"))

    attachment = MIMEApplication(buf.read(), _subtype='zip')
    attachment.add_header('Content-Disposition', 'attachment', filename=f"clavi-backup-{stamp}.zip")
    msg.attach(attachment)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [backup_to], msg.as_string())
    return True


@app.route('/internal/backup-database', methods=['GET', 'POST'])
def internal_backup_database():
    secret = os.environ.get('CRON_SECRET')
    provided = request.args.get('key') or request.headers.get('X-Cron-Secret')
    if not secret or provided != secret:
        return jsonify({"error": "Not found"}), 404

    sent = run_database_backup()
    return jsonify({"status": "ok", "emailed": sent})


def run_expiry_reminders(hours_ahead=36):
    """Email (and message via WhatsApp or SMS, if a phone number + provider
    are configured) users whose access expires within `hours_ahead` hours
    and who haven't already been reminded for this specific expiry date.
    Intended to be triggered once a day by an external scheduler (cron,
    GitHub Actions, Render Cron Job, or a pinged HTTP endpoint) — see
    /internal/send-expiry-reminders.
    Returns {"emails_sent": int, "whatsapp_sent": int, "sms_sent": int}.
    """
    now = datetime.now()
    window_end = now + timedelta(hours=hours_ahead)

    candidates = User.query.filter(
        User.expiry_date != None,
        User.expiry_date > now,
        User.expiry_date <= window_end,
    ).all()

    emails_sent = 0
    whatsapp_sent = 0
    sms_sent = 0
    for user in candidates:
        if has_unlimited_access(user):
            continue
        # Already reminded for this exact expiry cycle (renewing resets expiry_date,
        # which naturally clears this guard for the next cycle).
        if user.expiry_reminder_for and user.expiry_reminder_for == user.expiry_date:
            continue

        emailed = False
        try:
            send_expiry_reminder_email(user.email, bool(user.is_premium), user.expiry_date)
            emailed = True
            emails_sent += 1
        except Exception as e:
            app.logger.error(f"Failed to send expiry reminder email to {user.email}: {e}")

        if user.phone_number:
            text_msg = sms_expiry_reminder_text(bool(user.is_premium), user.expiry_date)
            # Prefer WhatsApp when it's configured (richer, usually cheaper);
            # fall back to SMS otherwise — never send both for one reminder.
            messaged = False
            if AT_WHATSAPP_NUMBER:
                try:
                    if send_whatsapp(user.phone_number, text_msg):
                        whatsapp_sent += 1
                        messaged = True
                except Exception as e:
                    app.logger.error(f"Failed to send expiry reminder WhatsApp message to {user.phone_number}: {e}")
            if not messaged:
                try:
                    if send_sms(user.phone_number, text_msg):
                        sms_sent += 1
                except Exception as e:
                    app.logger.error(f"Failed to send expiry reminder SMS to {user.phone_number}: {e}")

        # Mark this cycle reminded as long as at least one channel was attempted
        # successfully, so we don't spam retries on a temporary SMTP hiccup forever.
        if emailed:
            user.expiry_reminder_for = user.expiry_date
            db.session.commit()

    return {"emails_sent": emails_sent, "whatsapp_sent": whatsapp_sent, "sms_sent": sms_sent}


@app.route('/internal/send-expiry-reminders', methods=['GET', 'POST'])
def internal_send_expiry_reminders():
    secret = os.environ.get('CRON_SECRET')
    provided = request.args.get('key') or request.headers.get('X-Cron-Secret')
    if not secret or provided != secret:
        return jsonify({"error": "Not found"}), 404

    result = run_expiry_reminders()
    return jsonify({"status": "ok", **result})


@app.route('/debug/config')
def debug_config():
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
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "mpesa_submitted_at" TIMESTAMP'))
                    db.session.commit()
                if 'created_at' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "created_at" TIMESTAMP'))
                    db.session.commit()
                if 'referral_code' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "referral_code" VARCHAR(12)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "referred_by_id" INTEGER'))
                    db.session.commit()
                if 'email_verified' not in columns:
                    # DEFAULT TRUE grandfathers in everyone who signed up before
                    # this feature existed — only new signups start unverified.
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "email_verified" BOOLEAN DEFAULT TRUE'))
                    db.session.commit()
                if 'daily_ai_calls' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_ai_calls" INTEGER DEFAULT 0'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_ai_calls_date" VARCHAR(10)'))
                    db.session.commit()
                if 'expiry_reminder_for' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "expiry_reminder_for" TIMESTAMP'))
                    db.session.commit()
                if 'display_name' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "display_name" VARCHAR(40)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "leaderboard_opt_in" BOOLEAN DEFAULT TRUE'))
                    db.session.commit()
                if 'phone_number' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "phone_number" VARCHAR(20)'))
                    db.session.commit()
                if 'student_track' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "student_track" VARCHAR(20)'))
                    db.session.commit()
                onboarding_columns = {
                    'elementary_grade': 'VARCHAR(12)',
                    'campus_course': 'VARCHAR(100)',
                    'campus_year': 'VARCHAR(12)',
                    'favorite_subject': 'VARCHAR(100)',
                    'challenging_subject': 'VARCHAR(100)',
                    'challenging_subjects_json': 'TEXT',
                    'study_schedule_json': 'TEXT',
                    'daily_goal_minutes': 'INTEGER',
                    'starting_topic': 'VARCHAR(100)',
                    'onboarding_completed': 'BOOLEAN DEFAULT TRUE NOT NULL',
                }
                for column, column_type in onboarding_columns.items():
                    if column not in columns:
                        db.session.execute(text(
                            f'ALTER TABLE "user" ADD COLUMN "{column}" {column_type}'
                        ))
                db.session.commit()
                if 'failed_login_attempts' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "failed_login_attempts" INTEGER DEFAULT 0'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "lockout_until" TIMESTAMP'))
                    db.session.commit()
                if 'daily_challenge_date' not in columns:
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_date" VARCHAR(10)'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_json" TEXT'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_answered" BOOLEAN DEFAULT FALSE'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_streak" INTEGER DEFAULT 0'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_best_streak" INTEGER DEFAULT 0'))
                    db.session.execute(text('ALTER TABLE "user" ADD COLUMN "daily_challenge_last_correct_date" VARCHAR(10)'))
                    db.session.commit()
            if 'user_stats' in inspector.get_table_names():
                stats_columns = {column['name'] for column in inspector.get_columns('user_stats')}
                if 'freezes_available' not in stats_columns:
                    # DEFAULT 1 gives everyone (existing accounts included) an
                    # immediate freeze available, matching what a new signup gets.
                    db.session.execute(text('ALTER TABLE "user_stats" ADD COLUMN "freezes_available" INTEGER DEFAULT 1'))
                    db.session.commit()
                if 'weekly_total' not in stats_columns:
                    db.session.execute(text('ALTER TABLE "user_stats" ADD COLUMN "weekly_total" INTEGER DEFAULT 0'))
                    db.session.execute(text('ALTER TABLE "user_stats" ADD COLUMN "week_start_date" VARCHAR(10)'))
                    db.session.commit()
        except Exception as exc:
            app.logger.warning(f"Could not ensure admin column exists: {exc}")


ensure_database_schema()

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode)
