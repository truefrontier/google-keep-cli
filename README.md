# google-keep-cli

Unofficial read-only CLI for [Google Keep](https://keep.google.com). Sign in once in Chrome. Then list notes, search, and read note text.

Built by [True Frontier](https://truefrontierapps.com). Not affiliated with Google.

Same pattern as [google-photos-cli](https://github.com/truefrontier/google-photos-cli), [loom-cli](https://github.com/truefrontier/loom-cli), and [google-recorder-cli](https://github.com/truefrontier/google-recorder-cli).

The session stays on your machine, in a local Chrome profile. This tool does not print cookies, and it does not ship a cloud login.

## Requirements

- Python 3.10+
- Google Chrome

## Install

```bash
pipx install git+https://github.com/truefrontier/google-keep-cli.git
```

Or from a checkout:

```bash
pipx install .
```

Playwright uses your installed Chrome (`channel="chrome"`). You do not need `playwright install chromium`.

## Commands

```bash
keep login
keep list
keep list --format json
keep list --limit 20
keep list --archived
keep search "picnic"
keep search "picnic" --format json
keep info <id>
keep info <id> --format text
keep get <id>
keep get <id> -o note.txt
```

`keep login` opens Chrome. Sign in, wait until Keep loads, and the window closes. The profile is saved at `~/.keep-cli/chrome-profile/`.

If a command says the session expired, run `keep login` again.

## How it works

After login, the CLI opens Keep with your saved Chrome profile and collects notes from the same `notes/v1/changes` sync the web app uses. Search filters those notes locally by title and indexable text.

Read-only. It cannot create, edit, archive, delete, or share notes.


## Agent output

Inspired by [CLI Printing Press](https://github.com/mvanhorn/cli-printing-press) agent UX:

- Piped stdout auto-uses JSON when `--format` is `table` (no `--json` needed)
- `--compact` for high-gravity fields only
- `--select id,title` to project JSON/CSV fields
- `--csv` for spreadsheet-friendly rows
- `--quiet` / `-q` to suppress status lines
- Exit codes: `0` ok, `2` usage, `3` not found, `4` auth, `5` api/runtime

## Limits

- Sync collects what Keep streams while the page loads. Very large libraries may need a longer settle time in a future release.
- Archived notes are omitted unless you pass `--archived`.
- List items under a parent list are not expanded as separate rows; the parent list’s searchable text is included when Keep provides `indexableText`.
