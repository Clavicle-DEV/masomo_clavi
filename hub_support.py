"""Connects Clavis's support bot to the agent inbox on the hub site.

Clavis's server talks to the hub, not the browser, so the secret key never
reaches a student's phone. The browser only calls the three routes below.

Setup, in Clavis's .env:

    HUB_URL=http://127.0.0.1:5001
    HUB_PARTNER_KEY=<a long random key>

The same key goes in the hub's .env as:  PARTNER_KEYS=clavis:<that key>

Then in app.py, after ensure_database_schema() and before
`if __name__ == '__main__':`, add:

    import hub_support
    hub_support.init_app(app, limiter)
"""

import os

import requests
from flask import current_app, jsonify, request, url_for
from flask_login import current_user

HUB_TIMEOUT = 6          # seconds to wait for the hub before giving up
TRANSCRIPT_MAX = 20      # most recent bot messages handed to the agent
BODY_MAX = 800           # same limit as the bot's own messages

UNAVAILABLE = "Agents can't be reached right now. Please try again in a few minutes."
SENDERS = {"user", "assistant", "agent"}


class HubUnavailable(Exception):
    """The hub isn't set up, or couldn't be reached."""


def _settings():
    base = (os.environ.get("HUB_URL") or "").strip().rstrip("/")
    key = (os.environ.get("HUB_PARTNER_KEY") or "").strip()
    return base, key


def _hub(method, path, params=None, payload=None):
    base, key = _settings()
    if not base or not key:
        raise HubUnavailable("HUB_URL or HUB_PARTNER_KEY is not set")
    try:
        return requests.request(
            method,
            f"{base}/partner/v1{path}",
            headers={"Authorization": f"Bearer {key}"},
            params=params,
            json=payload,
            timeout=HUB_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise HubUnavailable(exc.__class__.__name__) from exc


def _json(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _identity():
    """Who the agent sees: the Clavis account id, a display name and the email."""
    email = (current_user.email or "").strip()
    name = (getattr(current_user, "display_name", None) or email.split("@")[0] or "Student").strip()
    return str(current_user.id), name[:120], email[:255]


def _clean_transcript(items):
    """Keep only well-formed user/assistant messages from the browser."""
    cleaned = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        role = "assistant" if item.get("role") == "assistant" else "user"
        cleaned.append({"role": role, "content": text.strip()[:BODY_MAX]})
    return cleaned[-TRANSCRIPT_MAX:]


def _clean_message(m):
    if not isinstance(m, dict) or m.get("sender") not in SENDERS:
        return None
    if not isinstance(m.get("id"), int) or not isinstance(m.get("body"), str):
        return None
    return {"id": m["id"], "sender": m["sender"], "body": m["body"]}


def _clean_conversation(c):
    """Pass on only the fields the widget needs, whatever the hub sent."""
    if not isinstance(c, dict) or not isinstance(c.get("id"), int):
        return None
    messages = [_clean_message(m) for m in c.get("messages") or []]
    return {
        "id": c["id"],
        "status": str(c.get("status", "")),
        "messages": [m for m in messages if m],
    }


def init_app(app, limiter):
    def fail(reason):
        current_app.logger.warning("Hub support call failed: %s", reason)
        return jsonify(error=UNAVAILABLE), 503

    def need_login():
        if current_user.is_authenticated:
            return None
        return (
            jsonify(error="Sign in to talk to an agent.", login_url=url_for("signup")),
            401,
        )

    def agent_current():
        """The student's open agent chat (messages newer than ?after=), or null."""
        denied = need_login()
        if denied:
            return denied
        ext_id, _, _ = _identity()
        try:
            r = _hub(
                "GET",
                "/conversations/current",
                params={"external_user_id": ext_id, "after": request.args.get("after", 0, type=int)},
            )
        except HubUnavailable as exc:
            return fail(exc)
        if r.status_code != 200:
            return fail(f"hub answered {r.status_code}")
        return jsonify(conversation=_clean_conversation(_json(r).get("conversation")))

    def agent_start():
        """Hand the bot chat to an agent. The same student never gets two open chats."""
        denied = need_login()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        transcript = _clean_transcript(data.get("messages"))
        if not any(m["role"] == "user" for m in transcript):
            return jsonify(error="Type your question first, then ask for an agent."), 400
        ext_id, name, email = _identity()
        try:
            r = _hub(
                "POST",
                "/conversations",
                payload={
                    "external_user_id": ext_id,
                    "name": name,
                    "email": email,
                    "transcript": transcript,
                },
            )
        except HubUnavailable as exc:
            return fail(exc)
        if r.status_code not in (200, 201):
            return fail(f"hub answered {r.status_code}")
        conv = _clean_conversation(_json(r).get("conversation"))
        if conv is None:
            return fail("unreadable answer")
        created = r.status_code == 201
        if not created:
            # They already had an open chat (another device, or cleared browser
            # data). The hub keeps that chat as it is, so add the question they
            # just typed to it. Otherwise it would be lost.
            question = next((m["content"] for m in reversed(transcript) if m["role"] == "user"), None)
            already_there = any(
                m["sender"] == "user" and m["body"] == question for m in conv["messages"][-5:]
            )
            if question and not already_there:
                try:
                    added = _hub(
                        "POST",
                        f"/conversations/{conv['id']}/messages",
                        payload={"external_user_id": ext_id, "body": question},
                    )
                    message = _clean_message(_json(added).get("message"))
                    if added.status_code == 201 and message:
                        conv["messages"].append(message)
                except HubUnavailable as exc:
                    current_app.logger.warning("Hub support call failed: %s", exc)
        # created=False tells the widget to show their existing chat.
        return jsonify(conversation=conv, created=created)

    def agent_message():
        denied = need_login()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        body = data.get("body")
        if not isinstance(conv_id, int) or not isinstance(body, str) or not body.strip():
            return jsonify(error="Type a message first."), 400
        if len(body.strip()) > BODY_MAX:
            return jsonify(error=f"Please keep messages under {BODY_MAX} characters."), 400
        ext_id, _, _ = _identity()
        try:
            r = _hub(
                "POST",
                f"/conversations/{conv_id}/messages",
                payload={"external_user_id": ext_id, "body": body.strip()},
            )
        except HubUnavailable as exc:
            return fail(exc)
        if r.status_code in (404, 409):
            return jsonify(error="This conversation was closed.", closed=True), 409
        if r.status_code != 201:
            return fail(f"hub answered {r.status_code}")
        message = _clean_message(_json(r).get("message"))
        if message is None:
            return fail("unreadable answer")
        return jsonify(message=message), 201

    app.add_url_rule(
        "/api/support/agent",
        "hub_agent_current",
        limiter.limit("30 per minute; 600 per hour")(agent_current),
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/support/agent",
        "hub_agent_start",
        limiter.limit("10 per hour")(agent_start),
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/support/agent/message",
        "hub_agent_message",
        limiter.limit("12 per minute; 120 per hour")(agent_message),
        methods=["POST"],
    )
