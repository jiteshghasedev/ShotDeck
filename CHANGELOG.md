# ShotDeck changelog

Two version numbers live in this repo:

- **Flow** (desktop app, and the `config` module FlowDash imports):
  `APP_VERSION` in `config.py`. Shown in the Flow window title and on
  FlowDash (hover the 5and8 logo). The build names its tarball
  `dist/Flow-<APP_VERSION>-el9.tar.gz`.
- **flow_dcc** (rez package for Nuke, Maya and the other DCCs):
  `version` in `rez/flow_dcc/package.py`.

From 1.3.1, `APP_VERSION` is the repo's release: the git tag is
`v<APP_VERSION>`, so the version Flow and FlowDash show matches the tag.

For each release: bump the number that changed, add a section here, commit,
then tag it (`git tag v1.3.1`).

## flow_dcc 1.6.0 (unreleased)

flow_dcc
- Work files are named `<sequence>_<shot>_<login>_<department>_v0001`
  (for example `SEQ001_AD1030_jitesh_comp_v0001.nk`), in every DCC. The
  project code is gone from the name. Files named the old way are still
  listed in Load File, and an artist's next version carries on from them.
- Nuke on a shot: the shot's first Nuke script is still built (plate, movie,
  Write) and saved as the artist's v0001. Once any department folder has a
  script, Nuke opens with a dialog instead: pick a department (roto, prep,
  comp, lighting, asset, plus the task's own step), then Open one of its
  scripts or New. New builds a script with the plate and Write, saved as
  the artist's next version in that department (v0001 for an artist new to
  it). Load File... in the 5and8 menu opens the same dialog.
- The picked department is kept for the session, so Save, Version Up,
  Publish and the Write's render path all use its folder,
  `<shot>/nuke/<department>/scene`. Set the list with
  `FLOW_DEPARTMENTS=roto,prep,comp,lighting,asset`.

## Flow 1.3.2, flow_dcc 1.5.0 (2026-11-09)

FlowDash 1.2.0 shows the same note pictures in the browser.

Flow
- Task notes (the home page's Notes column) show each note's pictures --
  RV annotations -- in its bubble. A click shows the picture full screen;
  the arrow keys step through the note's others, Esc or a click closes.
- Pictures are downloaded once per user, to `flow_note_images_<user>` in the
  temp folder.

flow_dcc
- Task Panel: every note on the task's shot or asset now shows too, as in
  Flow -- the shot's own notes and its other tasks' notes were missing.
- Notes are grouped by the task they were written for, this task first.
- A note's pictures (RV annotations) show under its text, sized to the
  panel; a click opens one full size.

## Flow 1.3.1 (2026-11-09)

`APP_VERSION` jumps from 1.1.0 to 1.3.1 to match the git tag `v1.3.1`.

- RV's **Send Annotations to ShotGrid** and the DCC Task panel failed with
  "SG_SCRIPT_KEY is not set" when the key came from Flow's built-in default
  rather than `/etc/sgdesk/sgdesk.env`. Flow now passes `SG_SCRIPT_NAME` and
  `SG_SCRIPT_KEY` on to everything it launches.

## flow_dcc 1.4.0 (2026-11-08)

- Nuke and Maya: **5and8 > Task Panel...** shows the launched task as
  ShotGrid has it:
  - status, artists, reviewers and due date
  - the newest 20 Versions with thumbnails
  - every note on the task or on those Versions (RV annotations included),
    with replies; the author is the note's signature
- The artist can reply to a note, open a note's images, and set the task's
  status with an optional note. Replies and notes are signed like every Flow
  note. Flow's own status mails (production for `cmpt`/`pkg`, artists for
  `nts`/`iad`/`fin`) are not sent from a status set here.
- `flow_dcc.__version__` matches the package version again.

## flow_dcc 1.3.0 (2026-11-08)

- RV: **Flow > Send Annotations to ShotGrid...** sends every frame with a
  drawing on it, not only the frame on screen. Each Version gets one Note:
  - subject: `AD1030_v002 annotations by Priya, frames 1012, 1020`
  - content: signed `Priya: ...` like every Flow note; the note text is
    optional when frames are drawn on
  - attachments: `AD1030_v002_annotation_f1012_priya.png`, one per frame
  - links: the Version, its shot or asset, and its task
- With nothing drawn, the frame on screen goes up, as before.

## Flow 1.1.0, flow_dcc 1.2.0 (2026-11-08)

Commit fa25643. FlowDash 1.1.0 needs this version.

Flow
- Setting a task to Notes (`nts`), Internal Approved (`iad`) or Final (`fin`)
  asks for a note and mails it to the task's artists, not the reviewer.
  `FLOW_REVIEW_RESULT_STATUSES` changes the list.
- Status mails say "Changed by".
- Version number in the window title (`APP_VERSION`).

flow_dcc
- Each artist gets their own work file, starting at `<prefix>_<user>_v0001`.
- Opening a task opens the artist's newest file as it is: no automatic
  version-up.
- An artist new to a task gets their own v0001, not someone else's file.
- Nuke: a fresh script is set up only when the artist has no file yet.

## Flow 1.0.0, flow_dcc 1.1.0 (2026-11-02)

Baseline before versioning (commit 1bf97af).

- Load File window, first-file setup and work-file locks in the DCCs.
- Task reviewer column; reviewers editable from FlowDash.
- Review player with wipe and notes; local movie cache.
- Status mail shared with FlowDash.
