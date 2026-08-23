"""
Sends renewal/trial-expiry reminder emails to users whose access is about to
run out. Run this once a day from an external scheduler — it is NOT started
automatically by the web app.

Usage:
    python send_renewal_reminders.py

Environment: reads the same .env / environment variables as app.py
(DATABASE_URL, SMTP_*, APP_DOMAIN, etc.) — run it from the project root, or
wherever those are already configured (e.g. as a Render Cron Job using this
repo, or a scheduled GitHub Actions job that has the same secrets set).

Alternative: if your host doesn't support running a separate script/cron
job, you can instead have any free external "ping a URL on a schedule"
service (cron-job.org, UptimeRobot, GitHub Actions on a schedule, etc.) hit:

    POST https://your-app-domain.com/internal/send-expiry-reminders?key=YOUR_CRON_SECRET

once a day, where YOUR_CRON_SECRET matches the CRON_SECRET environment
variable set on the web app. That endpoint runs the exact same logic as
this script and requires no separate process.
"""
import app as appmod

if __name__ == '__main__':
    with appmod.app.app_context():
        result = appmod.run_expiry_reminders()
        print(f"Sent {result['emails_sent']} email(s), {result['whatsapp_sent']} WhatsApp message(s), and {result['sms_sent']} SMS reminder(s).")
