# Add agent support to Clavis

What this does: your existing support bot now shows on every student page (not only
the landing page), and has a **Talk to an agent** button. It passes the chat to your
agent inbox on the hub site, and the agent's replies appear in the same panel.

The old bot code inside `landing.html` is removed (it is now in one shared file), so
there is only ever one bot. `/api/support` in app.py is unchanged and still answers.

## 1. Back up
Copy your Clavis folder somewhere safe first.

## 2. Copy the files
- `hub_support.py`  ->  next to `app.py`
- everything in `templates\`  ->  your Clavis `templates\` folder (say yes to replace).
  This adds `_support_widget.html` and replaces 7 pages, each by one added line
  (`{% include '_support_widget.html' %}` before `</body>`). On `landing.html` the old
  inline bot is also removed.
  Pages that get the button: landing, auth, index (main app), onboarding, pay_mpesa,
  study_timetable, certificate_application. Admin pages are left alone.

## 3. Two lines in app.py
Find `ensure_database_schema()` near the bottom, just above `if __name__ == '__main__':`.
Add these two lines after it:

    import hub_support
    hub_support.init_app(app, limiter)

## 4. Make a secret key and put it in both .env files
Make the key (run once, copy the output):

    python -c "import secrets; print(secrets.token_urlsafe(32))"

In the **hub's** `.env`:

    PARTNER_KEYS=clavis:PASTE_THE_KEY_HERE

In **Clavis's** `.env`:

    HUB_URL=http://127.0.0.1:5001
    HUB_PARTNER_KEY=PASTE_THE_KEY_HERE

No spaces around `=`, no quotes. The key must be 24+ characters.

## 5. Run both (two terminals)
- Clavis as you do now (port 5000)
- Hub: `venv\Scripts\python.exe run.py` (port 5001 now)

## 6. Try it
1. Sign in to Clavis, open the chat button, ask the bot something.
2. Tap **Talk to an agent**. The panel says it passed your chat on.
3. In the hub, log in and open **Inbox** (`/staff/inbox`). Reply.
4. Within about 8 seconds the reply shows in the student's panel.
   If they have closed the panel, a gold dot appears on the chat button.

## Notes
- Signed-out visitors can use the bot, but **Talk to an agent** asks them to sign in
  first, so the team knows who they are. The agent sees the student's display name
  and email.
- Each student has at most one open agent chat. A second handover adds to it.
- Limits (Clavis side): 10 handovers per hour, 12 agent messages per minute.
- If the hub is off or the key is wrong, students see "Agents can't be reached right
  now" and the bot keeps working. Details go to Clavis's log, never to the student.
- In production use `https://` for `HUB_URL`, keep the key secret, and use a
  different key for each app.
- I could not see `static/css/app.css` or `static/js/app.js`, so check the main app
  page (index) once: the button sits bottom-right, and anything else fixed in that
  corner could overlap it.
