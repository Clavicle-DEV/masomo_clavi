"""
Backs up the entire database (every row of every table, as JSON) and emails
it as a zip attachment. Run this on a schedule — it is NOT started
automatically by the web app.

Usage:
    python backup_database.py

Environment: reads the same .env / environment variables as app.py
(DATABASE_URL, SMTP_*, etc.), plus:
    BACKUP_EMAIL - where to send the backup (defaults to MAIL_FROM)

Run it from the project root, or wherever those are already configured
(e.g. as a Render Cron Job using this repo, or a scheduled GitHub Actions
job that has the same secrets set). Recommended: weekly at minimum, daily
if you want tighter recovery points.

Alternative: if your host doesn't support running a separate script/cron
job, you can instead have any free external "ping a URL on a schedule"
service (cron-job.org, UptimeRobot, GitHub Actions on a schedule, etc.) hit:

    POST https://your-app-domain.com/internal/backup-database?key=YOUR_CRON_SECRET

once a week, where YOUR_CRON_SECRET matches the CRON_SECRET environment
variable set on the web app (the same one used for renewal reminders).
"""
import app as appmod

if __name__ == '__main__':
    with appmod.app.app_context():
        sent = appmod.run_database_backup()
        if sent:
            print("Backup emailed successfully.")
        else:
            print("SMTP not configured — backup was not emailed (see logs).")
