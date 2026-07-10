"""Email confirmation for a completed publish run.

Sends a "briefings published" summary to the owner after ``publish_feed.py``
succeeds. Used by the unattended 5 AM scheduled run (``daily_run.ps1`` passes
``--email``); interactive runs email via the Gmail integration instead.

Credentials come from the environment so no secret lives in the repo:

    BRIEFING_SMTP_USER   Gmail address the mail is sent from (the App Password's account)
    BRIEFING_SMTP_PASS   a Google *App Password* (not the normal account password)
    BRIEFING_NOTIFY_TO   recipient (optional; defaults to config.NOTIFY_EMAIL)

If the credentials are missing the send is skipped with a warning — a missing
mailbox must never sink a publish that already went out.

One-time setup (Gmail): enable 2-Step Verification, create an App Password at
https://myaccount.google.com/apppasswords, then set the two env vars as *user*
environment variables (so both the scheduled task and interactive shells see them):

    setx BRIEFING_SMTP_USER "wamfour@gmail.com"
    setx BRIEFING_SMTP_PASS "the-16-char-app-password"
"""
import logging
import os
import smtplib
from email.message import EmailMessage

import config

log = logging.getLogger(__name__)

# Status strings publish_feed.py records for prompts that did not produce an episode.
_SKIP_STATUSES = ("NO SCRIPT", "STALE — skipped")


def _is_success(status: str) -> bool:
    """True when ``status`` is a real episode GUID rather than a failure/skip marker."""
    return not (status.startswith("FAILED") or status in _SKIP_STATUSES)


def _human_date(date: str) -> str:
    import datetime as _dt
    d = _dt.date.fromisoformat(date)
    return f"{d:%B} {d.day}, {d.year}"


def build_message(results, date):
    """Compose the confirmation email from publish results.

    ``results`` is publish_feed.py's ``[(name, guid_or_status), ...]``. Returns
    ``(subject, text_body, html_body)`` or ``None`` when nothing published
    successfully (no confirmation is warranted if every prompt failed/was skipped).
    """
    ok = [(n, s) for n, s in results if _is_success(s)]
    bad = [(n, s) for n, s in results if not _is_success(s)]
    if not ok:
        return None

    base = config.FEED_BASE_URL.rstrip("/")
    feed_url = f"{base}/feed.xml"
    human = _human_date(date)
    n = len(ok)
    subject = f"Cautious Optimism Briefings — {human}: {n} episode{'s' if n != 1 else ''} published"

    # ---- plain text ----
    lines = [
        f"Today's briefings are published to the podcast feed ({human}).",
        "",
        f"{n} episode{'s' if n != 1 else ''} published:",
    ]
    for name, guid in ok:
        lines.append(f"  • {name}")
        lines.append(f"      transcript: {base}/transcripts/{guid}.html")
        lines.append(f"      audio:      {base}/audio/{guid}.mp3")
    if bad:
        lines += ["", "Not published this run:"]
        lines += [f"  • {name} — {status}" for name, status in bad]
    lines += [
        "",
        f"Feed: {feed_url}",
        f"Show: {base}/",
        "",
        "Note: this confirms the episodes are live on the RSS feed / GitHub Pages. "
        "Spotify for Creators re-ingests the feed on its own refresh schedule "
        "(usually minutes to a few hours), after which they appear in Spotify.",
    ]
    text = "\n".join(lines)

    # ---- html ----
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    rows = ""
    for name, guid in ok:
        rows += (
            f"<li style='margin:0 0 .6rem'><strong>{esc(name)}</strong><br>"
            f"<a href='{base}/transcripts/{guid}.html'>Transcript</a> &nbsp;·&nbsp; "
            f"<a href='{base}/audio/{guid}.mp3'>Audio</a></li>"
        )
    bad_html = ""
    if bad:
        items = "".join(f"<li>{esc(name)} — {esc(status)}</li>" for name, status in bad)
        bad_html = f"<p style='color:#b00'><strong>Not published this run:</strong></p><ul>{items}</ul>"
    html = f"""\
<div style="font:15px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px">
  <p>Today's briefings are published to the podcast feed (<strong>{esc(human)}</strong>).</p>
  <p><strong>{n} episode{'s' if n != 1 else ''} published:</strong></p>
  <ul style="padding-left:1.1rem">{rows}</ul>
  {bad_html}
  <p><a href="{feed_url}">Feed</a> &nbsp;·&nbsp; <a href="{base}/">Show page</a></p>
  <p style="color:#666;font-size:13px">This confirms the episodes are live on the RSS feed /
  GitHub Pages. Spotify for Creators re-ingests the feed on its own refresh schedule (usually
  minutes to a few hours), after which they appear in Spotify.</p>
</div>"""
    return subject, text, html


def send_publish_summary(results, date) -> bool:
    """Send the confirmation email for a publish run. Returns True if a message was
    sent, False if skipped (nothing to confirm, or credentials unset). Never raises
    for a mail failure — publishing has already succeeded by the time we get here."""
    built = build_message(results, date)
    if built is None:
        log.info("notify: no successful episodes — skipping confirmation email")
        return False
    subject, text, html = built

    user = os.environ.get("BRIEFING_SMTP_USER")
    password = os.environ.get("BRIEFING_SMTP_PASS")
    to_addr = os.environ.get("BRIEFING_NOTIFY_TO", config.NOTIFY_EMAIL)
    if not user or not password:
        log.warning("notify: BRIEFING_SMTP_USER / BRIEFING_SMTP_PASS not set — "
                    "skipping confirmation email to %s", to_addr)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
        log.info("notify: confirmation email sent to %s", to_addr)
        return True
    except Exception as e:  # pragma: no cover - network/credential failure
        log.exception("notify: failed to send confirmation email: %s", e)
        return False
