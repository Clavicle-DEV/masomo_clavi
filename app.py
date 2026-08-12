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
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-me')

_database_url = os.environ.get('DATABASE_URL', 'sqlite:///clavi.db')
if _database_url.startswith('postgres://'):
    _database_url = _database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'signup'

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
        f"Explain things the way you'd explain them to a curious young child: use very simple, everyday "
        f"words, short sentences, and relatable examples before introducing any technical term. Build up "
        f"gently step by step rather than jumping straight to the formal definition. Still be accurate and "
        f"complete enough for their level — simple language, not simplified content. Use examples relevant "
        f"to Kenya. "
        f"Do not use markdown formatting like asterisks for bold or bullet points (no **word** and no lines "
        f"starting with *). Write in plain sentences, and if you need a list, use a dash (-) or simply number "
        f"the points (1., 2., 3.)."
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

        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'danger')
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
        )
        new_user.set_password(password)
        db.session.add(new_user)

        if referrer and not has_unlimited_access(referrer):
            # Reward the referrer too — extend from whichever is later: their
            # current expiry, or now (covers an already-expired trial).
            base = referrer.expiry_date if (referrer.expiry_date and referrer.expiry_date > datetime.now()) else datetime.now()
            referrer.expiry_date = base + timedelta(days=REFERRAL_BONUS_DAYS)

        db.session.commit()

        if not new_user.email_verified:
            token = reset_serializer.dumps(email, salt='email-verify')
            verify_url = f"{YOUR_DOMAIN}/verify-email/{token}"
            try:
                send_verification_email(email, verify_url)
            except Exception as e:
                app.logger.error(f"Failed to send verification email to {email}: {e}")

        login_user(new_user, remember=True)
        return redirect(url_for('home'))

    return render_template('auth.html', mode='signup', ref_code=request.args.get('ref', ''))


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

    user = User.query.filter_by(email=email).first()
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
        if not current_user.referral_code:
            current_user.referral_code = generate_referral_code()
            db.session.commit()
        referral_link = f"{request.host_url}signup?ref={current_user.referral_code}"
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

    if not subject or not messages:
        return jsonify({"error": "subject and messages are required"}), 400

    system_prompt = build_system_prompt(subject, level, grade_form)
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

    return jsonify({"answer": answer})


@app.route('/api/topics', methods=['POST'])
@login_required
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
Return ONLY valid JSON in exactly this shape, no commentary, no markdown fences:
{{
  "total_marks": <int>,
  "questions": [
    {{"id": 1, "type": "mcq", "question": "...", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "A", "marks": 1, "explanation": "..."}},
    {{"id": 2, "type": "short", "question": "...", "marks": 3, "model_answer": "...", "marking_points": ["...", "...", "..."]}}
  ]
}}
Include 10 to 14 questions total, mixing "mcq" and "short" types appropriately for this paper's style. Number "id" sequentially starting at 1."""
    else:
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
        raw = call_groq([{"role": "user", "content": prompt}], max_tokens=6000 if paper else 3000)
        test_data = extract_json(raw)
        if "questions" not in test_data or "total_marks" not in test_data:
            raise ValueError("Response missing required fields")
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": f"Could not generate test: {str(e)}"}), 502

    return jsonify(test_data)


@app.route('/api/sync-stats', methods=['GET', 'POST'])
@login_required
def api_sync_stats():
    row = UserStats.query.get(current_user.id)

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if row is None:
            row = UserStats(user_id=current_user.id)
            db.session.add(row)
        row.total = int(data.get('total', 0) or 0)
        row.by_subject_json = json.dumps(data.get('bySubject', {}) or {})
        row.last_active_date = data.get('lastActiveDate')
        row.streak = int(data.get('streak', 0) or 0)
        row.updated_at = datetime.now()
        db.session.commit()
        return jsonify({"status": "ok"})

    if row is None:
        return jsonify({"total": 0, "bySubject": {}, "lastActiveDate": None, "streak": 0})
    try:
        by_subject = json.loads(row.by_subject_json) if row.by_subject_json else {}
    except (TypeError, ValueError):
        by_subject = {}
    return jsonify({
        "total": row.total or 0,
        "bySubject": by_subject,
        "lastActiveDate": row.last_active_date,
        "streak": row.streak or 0,
    })


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


@app.route('/healthz')
def healthz():
    return jsonify({"status": "ok"})


def run_expiry_reminders(hours_ahead=36):
    """Email users whose access expires within `hours_ahead` hours and who
    haven't already been reminded for this specific expiry date. Intended to
    be triggered once a day by an external scheduler (cron, GitHub Actions,
    Render Cron Job, or a pinged HTTP endpoint) — see /internal/send-expiry-reminders.
    Returns the number of reminder emails sent.
    """
    now = datetime.now()
    window_end = now + timedelta(hours=hours_ahead)

    candidates = User.query.filter(
        User.expiry_date != None,
        User.expiry_date > now,
        User.expiry_date <= window_end,
    ).all()

    sent = 0
    for user in candidates:
        if has_unlimited_access(user):
            continue
        # Already reminded for this exact expiry cycle (renewing resets expiry_date,
        # which naturally clears this guard for the next cycle).
        if user.expiry_reminder_for and user.expiry_reminder_for == user.expiry_date:
            continue
        try:
            send_expiry_reminder_email(user.email, bool(user.is_premium), user.expiry_date)
            user.expiry_reminder_for = user.expiry_date
            db.session.commit()
            sent += 1
        except Exception as e:
            app.logger.error(f"Failed to send expiry reminder to {user.email}: {e}")
            db.session.rollback()

    return sent


@app.route('/internal/send-expiry-reminders', methods=['GET', 'POST'])
def internal_send_expiry_reminders():
    secret = os.environ.get('CRON_SECRET')
    provided = request.args.get('key') or request.headers.get('X-Cron-Secret')
    if not secret or provided != secret:
        return jsonify({"error": "Not found"}), 404

    sent = run_expiry_reminders()
    return jsonify({"status": "ok", "reminders_sent": sent})


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
        except Exception as exc:
            app.logger.warning(f"Could not ensure admin column exists: {exc}")


ensure_database_schema()

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug_mode)
