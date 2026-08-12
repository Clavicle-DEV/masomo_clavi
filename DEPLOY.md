# Clavi — Deployment Guide

This folder is a complete, deployable web app: a Flask backend that serves
the tutor page and safely proxies questions to the Claude API (your API key
never touches the browser).

## Files
- `app.py` — the backend server
- `templates/index.html` — the front-end page (served by Flask)
- `requirements.txt` — Python dependencies
- `Procfile` — tells hosting platforms how to start the app

## 1. Get an API key
1. Go to https://console.anthropic.com
2. Create an account (or log in) and generate an API key
3. Anthropic API usage is pay-as-you-go — add a small amount of credit to start
   (a few dollars covers a lot of student questions with Sonnet pricing)

## 2. Test it locally first
```
pip install -r requirements.txt
```

Set your API key as an environment variable, then run the app:

**Windows (Command Prompt):**
```
set ANTHROPIC_API_KEY=your-key-here
python app.py
```

**Mac/Linux:**
```
export ANTHROPIC_API_KEY=your-key-here
python app.py
```

Open http://localhost:5000 in your browser and try asking a question in each subject.

## 3. Deploy for free on Render
1. Push this folder to a GitHub repository
2. Go to https://render.com, sign up, click **New → Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
5. Under **Environment**, add a variable:
   - Key: `ANTHROPIC_API_KEY`
   - Value: your API key
6. Click **Create Web Service** — Render will build and deploy it
7. You'll get a public URL like `https://clavi.onrender.com` — that's your live site

**Note on Render's free tier:** the free plan "sleeps" after inactivity and takes
~30-60 seconds to wake up on the next visit. Fine for a starting community
tool; if usage grows, a paid tier ($7/mo) keeps it always-on.

## 4. Managing cost
- The backend already limits each request to the last 10 conversation turns
  and caps question length, so costs stay predictable per question.
- Watch usage at https://console.anthropic.com under "Usage" — set a monthly
  budget alert there so you're never surprised by a bill.
- If usage grows beyond what you can personally fund, consider: a simple
  daily question limit per visitor, or looking into Anthropic's programs for
  nonprofits/education (worth checking their site directly for current offers).

## 5. Renewal reminder emails
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
