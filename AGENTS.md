# AGENTS.md — instructions for Codex

**Read `CLAUDE.md` in this directory first, and follow it.** It is this project's
operating manual, maintained alongside Claude Code (the primary agent). Where it says
"Claude", read "the coding agent working here", which includes you. Its paths are
literal: `C:\Users\jroyp\.claude\...` is correct. There is no `.Codex` equivalent.

This file used to be a copy of `CLAUDE.md` with "Claude" blindly replaced by "Codex".
That copy drifted, and the replacement broke real references (`claude -p` became
`Codex -p`, `.claude\commands\finish.md` became `.Codex\...`). **Do not regenerate it
as a copy.** Keep project rules in `CLAUDE.md` and put only Codex-specific deltas here.

## Codex-specific rules (from the 2026-10-02 Windows trial)

**Git**
- Git metadata lives outside the workspace, in `C:\Users\jroyp\gitdirs\<repo>`, and
  is read-only to your sandbox. Git writes (fetch, pull, add, commit, push) need
  escalation, so request it the normal way.
- Never work around a denial: no `GIT_DIR` redirection, no copying the repo, and no
  config or ACL edits.
- One active writer per repo. If Claude may be working in this repo, stop and ask JP.
- Stage explicit paths only. Never `git add -A` or `git add .`.
- **Never disable hooks.** No `-c core.hooksPath=...` and no `--no-verify`. If a hook
  blocks a commit, report it; do not bypass it.
- Never amend, rebase, `reset --hard`, `git clean` or force-push unless JP explicitly
  asks for that exact operation.
- Before rewriting any commit, check whether it is already pushed (`git status -sb`).
  If it is, stop and say so.
- After any git work, report whether the local branch matches its remote, including
  any ahead/behind counts.

**Files**
- `.bat`, `.cmd` and `.ps1` files must keep CRLF line endings. Your patch tool can
  write LF on the lines it changes, so check the bytes after every edit.
- Do not run the `codex` CLI from inside your own sandbox.
