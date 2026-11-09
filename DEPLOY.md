# Clavis — Deployment Guide

Clavis is a Flask application that serves the student tutor and calls Groq's
API from the server. API credentials must only be configured as environment
variables; never put them in browser code or commit them to the repository.

## 1. Run locally

Install the pinned dependencies with Python 3.12:

```
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set real values for `FLASK_SECRET_KEY` and
`GROQ_API_KEY`. Generate a strong Flask key with:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Keep `.env` private and out of version control. Start the development server
with `python app.py`, then open http://localhost:5000.

## 2. Deploy with Render

The repository includes `render.yaml` for a Render Blueprint deployment. In
Render, create a Blueprint from the repository and provide values for the
unsynced secrets when prompted:

- `FLASK_SECRET_KEY`: a long, random value. Keep it stable; changing it
  invalidates existing sessions and signed reset/verification links.
- `GROQ_API_KEY`: the API key used by the server-side tutor requests.

The Blueprint sets `FLASK_ENV=production`, disables Flask debug mode, uses
`/healthz` for health checks, and starts Gunicorn with the configured threaded
worker. The health endpoint only confirms that the web process responds; it
does not validate external services.

If configuring a Web Service manually instead of using the Blueprint, use:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn --worker-class gthread --threads 4 --timeout 90 app:app`
- **Health check path:** `/healthz`
- **Runtime:** Python version specified in `runtime.txt`

Add credentials through Render's Environment settings, not in `render.yaml`
or source files. Set `APP_DOMAIN` to the canonical public URL. Set
`DATABASE_URL` to a persistent PostgreSQL database for production; without
it, the app uses local SQLite, which is not suitable for durable data on an
ephemeral web-service filesystem.

After deployment, verify `/healthz`, log in, and test a tutor request. Check
the Render service logs if the build, database connection, or AI request
fails.

## 3. AI usage and costs

Tutor and generated-content requests use the configured Groq models. Review
current usage and pricing in your Groq account, and configure model overrides
with `GROQ_MODEL` and `GROQ_VISION_MODEL` only when needed. The application
also applies per-user daily call limits; these can be adjusted with
`FREE_DAILY_AI_LIMIT` and `PREMIUM_DAILY_AI_LIMIT`.

## 4. Renewal reminder emails
Users whose trial or premium access is about to expire can be emailed a
reminder automatically, but this needs to be triggered once a day by
something outside the web app itself (Flask doesn't run background jobs).
Two options — pick whichever fits your host:

**Option A — a real scheduled job** (Render Cron Job, GitHub Actions on a
schedule, or a cron entry on your own server) that runs:
```
python send_renewal_reminders.py
```
with the same environment variables as the web app (`DATABASE_URL`,
`SMTP_*`, `APP_DOMAIN`).

**Option B — a free URL-pinging service** (cron-job.org, UptimeRobot, etc.)
if your host doesn't support running a separate scheduled process. Set a
`CRON_SECRET` environment variable on the web app, then have the pinger hit:
```
POST https://your-app-domain.com/internal/send-expiry-reminders?key=YOUR_CRON_SECRET
```
once a day. The endpoint is disabled (returns 404) until `CRON_SECRET` is set.

Students can also add a phone number in Settings to get a text reminder
alongside the email one. This needs an Africa's Talking account — set
`AFRICASTALKING_USERNAME` and `AFRICASTALKING_API_KEY` to activate SMS.
Without those set, phone numbers are still collected and stored, but
sending just logs to the console instead (same behavior as email without
SMTP configured) — nothing breaks, it just won't actually text anyone yet.

For WhatsApp instead of SMS: it's the same Africa's Talking account, but
needs an extra step — register and get a WhatsApp sender number approved
in your AT dashboard (involves Meta business verification, can take a
few days). Once you have one, set `AFRICASTALKING_WHATSAPP_NUMBER` and
reminders will use WhatsApp instead of SMS automatically — no other code
changes needed. One important caveat: WhatsApp's own rules generally
require proactive, business-initiated messages like this (outside a
24-hour window where the student messaged you first) to use a
pre-approved message template rather than free text. Check your AT
WhatsApp dashboard for template requirements before relying on this for
real reminders — if a send fails for that reason, it'll fall back to
logging rather than crash anything, but it also won't have reached the
student, so it's worth testing directly first.

## 5. Database backups
The app has no automatic backups of its own — if you're on a free-tier
Postgres database, it can be reset or lost without much warning. There's
a lightweight, dependency-free backup built in: it exports every row of
every table to JSON, zips it, and emails it to you. No `pg_dump` or
Postgres client tools needed since it goes through the same SQLAlchemy
connection the app already uses.

Trigger it the same two ways as reminders:

**Option A:**
```
python backup_database.py
```

**Option B:**
```
POST https://your-app-domain.com/internal/backup-database?key=YOUR_CRON_SECRET
```
(same `CRON_SECRET` as reminders)

Set `BACKUP_EMAIL` to choose where it goes (defaults to `MAIL_FROM` if
unset). Run it weekly at minimum — daily if you want tighter recovery
points. Keep in mind this emails your full user database (including
password hashes, which are safely hashed, but still) — make sure
`BACKUP_EMAIL` is an inbox only you control.

## 6. Login security
Repeated failed login attempts now lock an account out temporarily
(`LOGIN_LOCKOUT_THRESHOLD` failed attempts within one session of tries,
locked for `LOGIN_LOCKOUT_MINUTES` — both default to 5 and 15, override
via env vars if you want different values). This protects against
password brute-forcing now that real payments and personal data are
involved. A locked-out user can still reset their password via
"Forgot password?" to regain access immediately, without waiting out
the lockout.

## 7. School registration requests
The signup page lets a registrant choose an individual or school account.
School applications include the school's contact and location details plus a
PNG/JPEG badge (up to 2 MB). The badge is stored in the database, not on the
web service's temporary filesystem. A full Clavis admin can review applications
at **School requests** from the admin area and approve or reject them. Approval
activates the applicant's school-admin account; rejection can include a reason.

Password-reset and account-verification emails are sent using the Resend HTTPS
API. Follow Resend's [verified-domain setup](https://resend.com/docs/dashboard/domains/introduction)
and [send-email API](https://resend.com/docs/api-reference/emails/send-email),
create an API key, then set these in the Render service's **Environment**
settings:

- `RESEND_API_KEY`: the Resend API key (keep it secret).
- `RESEND_FROM`: a sender using the verified domain, such as
  `Clavis <accounts@your-verified-domain.com>`.

The app only reports a reset email as sent after Resend accepts the request.
If either setting is missing or Resend rejects the request, check the service
logs for the failure. Verification uses a signed email link that expires after
24 hours.

Other app emails (such as school decisions and renewal reminders) still use
SMTP. Set `SMTP_HOST`, `MAIL_FROM`, and, for authenticated SMTP, `SMTP_USER` and
`SMTP_PASS` for those messages. `SMTP_PORT` defaults to `587` with STARTTLS.
Keep all email credentials in Render's environment settings, never in source
code.

Database backups include school badges, encoded into the JSON backup, so
backup size grows with uploaded badges.
