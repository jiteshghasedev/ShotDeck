"""Production notes on a Version, as ShotGrid models them.

ShotGrid's shape is a Note with Replies hanging off it -- one level, not an
arbitrary tree. That is the native representation and this follows it rather
than inventing nesting ShotGrid cannot store: a reply to a reply is a reply to
the same note, shown under the message it answers. Nothing is kept locally.
ShotGrid is the source of truth for every note, author and timestamp here.

No Qt: the browser renders threads, it does not build them.

Every write goes through the one ShotGrid script user, so the author is not
something ShotGrid can tell apart. Flow signs each note's text with the AD
login instead -- "Jitesh: Fixed the edge" -- and reads that back as the author.
Notes signed the earlier way, "[jitesh] Fixed the edge", still read back.
"""

import re

import applog
import config

log = applog.get()

# "Login: " at the very start of a note -- one word, no spaces -- or the
# earlier "[login] ". The first one wins, so a note typed as "WIP: ..." is
# stored "Jitesh: WIP: ..." and still reads as Jitesh's.
# ponytail: a note typed on the ShotGrid site as "WIP: ..." reads as written
# by "WIP"; sign on the site too, or match against known logins, if that bites.
# The colon must be followed by a space, so "https://..." is not a signature.
_SIGNATURE = re.compile(
    r"^(?:\[([^\]\n]{1,64})\]|([A-Za-z][\w.\-]{0,63}):(?=\s|$))\s*")


def signer_name(login):
    """jitesh -> Jitesh: how a login is written in front of a note."""
    return login[:1].upper() + login[1:] if login else ""


def sign(content, login):
    """content with "Login: " in front, unless it is already signed by login."""
    if not login:
        return content
    if signature(content)[0].lower() == login.lower():
        return content
    return f"{signer_name(login)}: {content}"


def signature(content):
    """(login, text without the tag), or ("", content) for an unsigned note."""
    match = _SIGNATURE.match(content or "")
    if not match:
        return "", content
    return (match.group(1) or match.group(2)).strip(), content[match.end():]


class Message:
    """A note or a reply, flattened into what the UI needs to draw one."""

    def __init__(self, raw, kind, author=None, depth=0):
        self.raw = raw
        self.kind = kind                # "note" or "reply"
        self.id = raw["id"]
        self.entity_type = "Note" if kind == "note" else "Reply"
        self.signed_by, self.content = signature(
            (raw.get("content") or "").strip())
        self.subject = (raw.get("subject") or "").strip()
        self.created_at = raw.get("created_at")
        self.user = raw.get("user") or {}
        self.author = author or {}
        self.depth = depth
        self.replies = []

    @property
    def author_name(self):
        return (self.signed_by or self.author.get("name")
                or self.user.get("name") or "Unknown")

    @property
    def author_role(self):
        """Whatever ShotGrid calls this person, not a role Flow made up."""
        rule = self.author.get("permission_rule_set")
        if isinstance(rule, dict):
            return rule.get("name") or ""
        return rule or ""

    @property
    def from_client(self):
        """Written by someone ShotGrid's permission rule set calls a client."""
        return "client" in self.author_role.lower()

    def written_by(self, user):
        return bool(user) and self.user.get("id") == user.get("id")

    def __repr__(self):
        return f"<{self.entity_type} {self.id} by {self.author_name}>"


class NotesService:
    """Reads and writes the ShotGrid notes on a Version."""

    def __init__(self, sg):
        self.sg = sg

    # -- reading -----------------------------------------------------------

    def threads(self, version_id):
        """[Message, ...] top-level notes, each with .replies filled in.

        Three queries at most, whatever the note count: the notes, their
        replies in one go, and the authors in one go.
        """
        notes = self.sg.notes_for_version(version_id) or []
        if not notes:
            return []

        replies = self.sg.replies_for_notes([n["id"] for n in notes]) or []
        authors = self._authors(notes + replies)

        by_note = {}
        for reply in replies:
            note_id = (reply.get("entity") or {}).get("id")
            by_note.setdefault(note_id, []).append(reply)

        threads = []
        for note in notes:
            message = Message(note, "note",
                              authors.get(self._user_id(note)))
            for reply in by_note.get(note["id"], []):
                message.replies.append(
                    Message(reply, "reply",
                            authors.get(self._user_id(reply)), depth=1))
            threads.append(message)
        return threads

    def _authors(self, rows):
        ids = [self._user_id(r) for r in rows]
        return self.sg.users([i for i in ids if i])

    @staticmethod
    def _user_id(row):
        return (row.get("user") or {}).get("id")

    def activity(self, version, threads=None):
        """Notes, replies and the publish itself on one timeline, newest first.

        Only events ShotGrid actually recorded -- the Version's own creation
        and the messages on it. Nothing is synthesised to pad it out.
        """
        events = []
        created = version.get("created_at")
        if created:
            who = (version.get("user") or version.get("created_by") or {})
            events.append({
                "when": created,
                "kind": "publish",
                "who": who.get("name") or "Someone",
                "text": f"published {version.get('code') or 'a version'}",
            })

        for note in threads if threads is not None else \
                self.threads(version["id"]):
            events.append({
                "when": note.created_at, "kind": "note",
                "who": note.author_name,
                "text": note.subject or note.content,
            })
            for reply in note.replies:
                events.append({
                    "when": reply.created_at, "kind": "reply",
                    "who": reply.author_name, "text": reply.content,
                })

        return sorted([e for e in events if e["when"]],
                      key=lambda e: e["when"], reverse=True)

    def task_chat(self, task_id):
        """Every note and reply on a task as one conversation, oldest first.

        Notes made on the task's versions carry the task too, so they show
        here as well. Three queries, as for threads().
        """
        notes = self.sg.notes_for_tasks([task_id]) or []
        if not notes:
            return []
        replies = self.sg.replies_for_notes([n["id"] for n in notes]) or []
        authors = self._authors(notes + replies)
        messages = (
            [Message(n, "note", authors.get(self._user_id(n))) for n in notes]
            + [Message(r, "reply", authors.get(self._user_id(r)), depth=1)
               for r in replies])
        return sorted(messages, key=lambda m: str(m.created_at or ""))

    def task_summary(self, task_ids):
        """{task_id: (note count, newest note's created_at)} for a task list.

        Only the two fields the column shows, for every task in one pass.
        """
        summary = {}
        for note in self.sg.notes_for_tasks(
                task_ids, fields=["tasks", "created_at"]) or []:
            for link in note.get("tasks") or []:
                count, latest = summary.get(link["id"], (0, None))
                # Notes come back oldest first, so the last one seen is newest.
                summary[link["id"]] = (count + 1,
                                       note.get("created_at") or latest)
        return summary

    # -- writing -----------------------------------------------------------

    def add_note(self, project, version, content, subject="", task=None):
        content = (content or "").strip()
        if not content:
            raise ValueError("A note needs something in it.")
        return self.sg.create_note(project, version, content, subject, task)

    def reply(self, note, content):
        content = (content or "").strip()
        if not content:
            raise ValueError("A reply needs something in it.")
        note_id = note.id if isinstance(note, Message) else note["id"]
        return self.sg.create_reply(note_id, content)

    def can_modify(self, message):
        """Only the author's own messages, and only if we know who they are.

        The real gate is ShotGrid's permissions; this keeps buttons that are
        certain to fail off the screen rather than pretending to be one.

        A signed note is decided by its signature alone: on a shared licence
        everyone can resolve to the same ShotGrid user.
        """
        if message.signed_by:
            return message.signed_by.lower() == \
                (getattr(self.sg, "login", None) or "").lower()
        owner = getattr(self.sg, "owner", None)
        return message.written_by(owner)

    def edit(self, message, content):
        content = (content or "").strip()
        if not content:
            raise ValueError("A note cannot be emptied; delete it instead.")
        if not self.can_modify(message):
            raise PermissionError("You can only edit your own notes.")
        return self.sg.update_note(message.entity_type, message.id, content)

    def delete(self, message):
        if not self.can_modify(message):
            raise PermissionError("You can only delete your own notes.")
        return self.sg.delete_entity(message.entity_type, message.id)
