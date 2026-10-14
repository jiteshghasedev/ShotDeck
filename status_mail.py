"""Mail production when an artist changes a task's status in Flow.

Set in Flow to                        mails
config.PRODUCTION_MAIL_STATUSES       production: config.PRODUCTION_MAIL
  ("cmpt", "pkg")

config.REVIEW_MAIL_STATUS ("prw") also asks the artist for a note, but the
mail to the leads -- and the approved / needs-changes mail back to the
artist -- comes from the SG daemon's sg_task_status_mail plugin
(vfx-ingest-pipeline), so a status set on the ShotGrid site mails too.

The mail carries the task and the artist's note. The SMTP side is the
pipeline's own services.mailer.send_email, imported from PIPELINE_ROOT the
way the ingest API does it, so there is one mailer and one set of SMTP_*
settings for the studio.

FLOW_MAIL_OFF=1 turns it off; FLOW_MAIL_REDIRECT=<addr> sends every mail to
that one address instead, for testing.
"""

import html
import os
import smtplib  # noqa: F401 -- the frozen app must bundle it for the mailer
import sys

import applog
import config

log = applog.get()


def wants_mail(code):
    """Whether Flow asks the artist for a note when setting this status."""
    return code == config.REVIEW_MAIL_STATUS or \
        code in config.PRODUCTION_MAIL_STATUSES


def message(task, artist, email, note, old, new):
    """(subject, html body) for one status change."""
    entity = (task.get("entity") or {}).get("name") or ""
    name = task.get("content") or ""
    project = (task.get("project") or {}).get("name") or ""
    step = (task.get("step") or {}).get("name") or ""
    what = f"set to {new}"
    subject = f"[{project}] {entity} {name} {what} - {artist}"
    rows = [("Project", project), ("Shot / Asset", entity), ("Task", name),
            ("Step", step), ("Status", f"{old or '-'} -> {new}"),
            ("Artist", f"{artist} <{email}>" if email else artist)]
    body = (
        "<p><b>{0}</b>: <b>{1}</b> {2}.</p><table>{3}</table>"
        "<p><b>Note</b></p><p>{4}</p>"
        '<p><a href="{5}">Open the task in ShotGrid</a></p>').format(
            html.escape(artist), html.escape(f"{entity} {name}".strip()),
            html.escape(what),
            "".join(f"<tr><td><b>{html.escape(k)}</b></td>"
                    f"<td>{html.escape(v)}</td></tr>" for k, v in rows if v),
            html.escape(note or "(no note)").replace("\n", "<br>"),
            config.entity_url("Task", task["id"]))
    return subject, body


def _to(sg, task, new):
    if new in config.PRODUCTION_MAIL_STATUSES:
        return list(config.PRODUCTION_MAIL), "FLOW_PRODUCTION_MAIL"
    return [], ""


def send(sg, task, artist, email, note, old, new):
    """Mail whoever this status is for. Returns who it went to; [] if nobody."""
    if os.environ.get("FLOW_MAIL_OFF") == "1":
        return []
    to, source = _to(sg, task, new)
    if not to:
        if source:
            log.info("nobody in %s; no mail for task %s going to %s",
                     source, task["id"], new)
        return []
    redirect = os.environ.get("FLOW_MAIL_REDIRECT")
    if redirect:
        log.info("mail for %s redirected to %s", to, redirect)
        to = [redirect]
    subject, body = message(task, artist, email, note, old, new)
    _send_email()(to, subject, body)
    log.info("%s mail for task %s sent to %s", new, task["id"], to)
    return to


def _send_email():
    if config.PIPELINE_ROOT not in sys.path:
        sys.path.insert(0, config.PIPELINE_ROOT)
    from services.mailer import send_email    # pipeline's SMTP helper
    return send_email
