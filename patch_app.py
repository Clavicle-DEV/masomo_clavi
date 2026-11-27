"""Updates Mentis's app.py for agent support. Run it from the folder that holds app.py:

    python patch_app.py

It makes app.py.bak first, changes only what is listed below, and is safe to run
twice (it skips anything already done).

  1. Connects the agent inbox:  import hub_support / hub_support.init_app(app, limiter)
  2. Teaches the support bot to say when it can't help. It ends such a reply with a
     hidden marker, which the chat turns into one "Talk to an agent" button.
"""
import re
import shutil
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
if not path.is_file():
    sys.exit(f"Can't find {path}. Run this from the folder that contains app.py.")

raw = path.read_bytes().decode("utf-8")
eol = "\r\n" if raw.count("\r\n") > raw.count("\n") / 2 else "\n"   # keep the file's own line endings
text = raw.replace("\r\n", "\n")
done, skipped = [], []

# ---- 1. connect the hub --------------------------------------------------
if "hub_support.init_app" in text:
    skipped.append("hub_support lines already in app.py")
else:
    found = list(re.finditer(r"^ensure_database_schema\(\)[ \t]*\n", text, re.M))
    if len(found) != 1:
        sys.exit("Could not find the line `ensure_database_schema()` exactly once. Add the two hub_support lines by hand (see INSTALL.md).")
    at = found[0].end()
    text = text[:at] + "\nimport hub_support\nhub_support.init_app(app, limiter)\n" + text[at:]
    done.append("added hub_support import and init_app")

# ---- 2. the support bot says when it can't help ---------------------------
start = text.find("def api_support():")
end = text.find("@app.route('/api/tutor'", start)
if start == -1 or end == -1:
    sys.exit("Could not find api_support() in app.py. Nothing was changed.")
func = text[start:end]

if "[[AGENT]]" in func:
    skipped.append("api_support already updated")
else:
    old_prompt_end = (
        '        "school data, or support contacts. If the question is unrelated to Mentis, politely redirect it."\n'
        "    )\n"
    )
    new_prompt_end = (
        '        "school data, or support contacts. If the question is unrelated to Mentis, politely redirect it. "\n'
        '        "If you cannot fully answer, or the person needs help you cannot give (account, payment or technical "\n'
        '        "problems), or they ask to speak to a person, tell them an agent from the Mentis team can help, and end "\n'
        '        "your reply with the exact text [[AGENT]] on its own line. Never write [[AGENT]] in any other situation."\n'
        "    )\n"
    )
    old_return = '    return jsonify({"answer": answer})\n'
    new_return = (
        "    needs_agent = '[[AGENT]]' in answer\n"
        "    answer = answer.replace('[[AGENT]]', '').strip()\n"
        "    if not answer:\n"
        "        answer = 'I can\\'t help with that one, but an agent from the Mentis team can.'\n"
        "        needs_agent = True\n"
        '    return jsonify({"answer": answer, "needs_agent": needs_agent})\n'
    )
    if func.count(old_prompt_end) != 1 or func.count(old_return) != 1:
        sys.exit("api_support() doesn't look the way I expect, so I changed nothing. Send it to me and I'll adjust.")
    func = func.replace(old_prompt_end, new_prompt_end).replace(old_return, new_return)
    text = text[:start] + func + text[end:]
    done.append("api_support now flags when an agent is needed")

if not done:
    print("Nothing to do:", "; ".join(skipped))
    sys.exit(0)

shutil.copy(path, str(path) + ".bak")
out = text.replace("\n", "\r\n") if eol == "\r\n" else text
path.write_bytes(out.encode("utf-8"))
print("Backup saved as", str(path) + ".bak")
for line in done:
    print("Done:", line)
for line in skipped:
    print("Skipped:", line)
