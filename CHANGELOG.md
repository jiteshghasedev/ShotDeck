# ShotDeck changelog

Two version numbers live in this repo:

- **Flow** (desktop app, and the `config` module FlowDash imports):
  `APP_VERSION` in `config.py`. Shown in the Flow window title and on
  FlowDash (hover the 5and8 logo). The build names its tarball
  `dist/Flow-<APP_VERSION>-el9.tar.gz`.
- **flow_dcc** (rez package for Nuke, Maya and the other DCCs):
  `version` in `rez/flow_dcc/package.py`.

For each release: bump the number that changed, add a section here, commit,
then tag it (`git tag v1.1.0`).

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
