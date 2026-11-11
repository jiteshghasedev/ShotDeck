"""The launch context, as the DCC sees it.

flow_context already parses the context file Flow writes; this is a
thin, DCC-facing view over it that never raises and never needs the file to
exist. Outside a Flow launch every field is empty and `has_task` is False,
so a menu action can say "relaunch from Flow" instead of tracebacking at an
artist mid-shot.
"""

import os

# Variables the adapters and the verification steps depend on.
REQUIRED = [
    "FLOW_PROJECT_NAME",
    "FLOW_ENTITY_NAME",
    "FLOW_TASK_NAME",
    "FLOW_STEP",
    "FLOW_SOFTWARE",
]


class Context(object):
    """Flat, read-only view of one Flow launch."""

    def __init__(self, data=None):
        data = data or {}
        project = data.get("project") or {}
        software = data.get("software") or {}
        task = data.get("task") or {}

        self.project_name = project.get("name") or _env("FLOW_PROJECT_NAME")
        self.project_code = project.get("code") or _env("FLOW_PROJECT_CODE")
        self.project_id = project.get("id") or _int("FLOW_PROJECT_ID")
        self.software = (software.get("code")
                         or _env("FLOW_SOFTWARE")).lower()
        self.software_version = (software.get("version")
                                 or _env("FLOW_SOFTWARE_VERSION"))
        self.task_id = task.get("id") or _int("FLOW_TASK_ID")
        self.task_name = task.get("name") or _env("FLOW_TASK_NAME")
        self.step = task.get("step") or _env("FLOW_STEP")
        # The folder and file-name department: the task's step unless the
        # artist picked another one (roto, prep...) in Nuke's launch dialog.
        self.department = (_env("FLOW_DEPARTMENT") or self.step or "").lower()
        self.entity_type = task.get("entity_type") or _env("FLOW_ENTITY_TYPE")
        self.entity_id = task.get("entity_id") or _int("FLOW_ENTITY_ID")
        self.entity_name = task.get("entity_name") or _env("FLOW_ENTITY_NAME")
        self.sequence = data.get("sequence") or _env("FLOW_SEQUENCE")
        self.user = _env("FLOW_USER")
        self.site = data.get("site") or _env("FLOW_SITE")
        # Written by Flow, which already knows the templates. The DCC side
        # deliberately does not re-derive it -- one convention, one place.
        self.entity_root = _env("FLOW_ENTITY_ROOT")

    @property
    def has_task(self):
        return bool(self.task_id)

    def missing(self):
        """Which REQUIRED variables are not set. Empty list means a good launch."""
        return [name for name in REQUIRED if not os.environ.get(name)]

    def __bool__(self):
        return bool(self.project_name or self.task_id)

    __nonzero__ = __bool__          # DCCs still shipping Python 2 interpreters

    def __repr__(self):
        return ("<Flow context project={0!r} entity={1!r} task={2!r} "
                "step={3!r} software={4!r}>".format(
                    self.project_name, self.entity_name, self.task_name,
                    self.step, self.software))

    def summary(self):
        """The Context panel: pipeline coordinates, in reading order.

        Deliberately not every variable -- this is what an artist reads to
        answer "am I in the right shot", so it stops at the step.
        """
        rows = [
            ("Project", self.project_name),
            ("Sequence", self.sequence),
            (self.entity_type or "Entity", self.entity_name),
            ("Task", self.task_name),
            ("Step", self.step),
            ("Software", "{0} {1}".format(self.software,
                                          self.software_version).strip()),
            ("User", self.user),
        ]
        return "\n".join("{0:<12}{1}".format(k, v or "-") for k, v in rows)


def _env(name):
    return os.environ.get(name, "")


def _int(name):
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() else None


def get():
    """The current context. Prefers flow_context, falls back to the env.

    flow_context may legitimately be missing: launcher.py skips injecting
    it when it has not been released yet, and the flat FLOW_* variables are
    exported either way.
    """
    try:
        import flow_context
    except ImportError:
        return Context()

    try:
        return Context(flow_context.get().as_dict())
    except Exception:               # a broken context file must not stop a DCC
        return Context()
