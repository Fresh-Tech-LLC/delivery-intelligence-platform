# Agent Guardrails

These instructions apply to any agent working in this repository.

## Workspace Root Verification

- Before any edit, commit, push, or other mutating action, verify the intended project root.
- Treat a mismatch as likely if the user references files or open tabs from a different absolute path, names a different repo or folder, or the conversation cwd is one of several sibling repos or worktrees.
- If a mismatch is suspected, stop and explicitly confirm which root to use before proceeding.
- A warning is not enough. Do not edit files or run git actions until the root is confirmed.
- After confirmation, restate the confirmed root path in the next user update before taking action.

## Git Safety

- Commits may be performed after implementation, but pushes, merges, rebases, branch switches, and worktree-affecting git actions require explicit confirmation in the current turn.
- A cancelled or declined approval is a stop signal. Do not retry the same action through another approved path in the same turn.
- Never use broad staging commands such as `git add -u`, `git add .`, or `git add -A` unless the user explicitly asks to stage all changes.
- Default to staging explicit file paths only.
- Always inspect `git status --short` and `git diff --staged --stat` before committing.

## Dirty Worktree And Existing Changes

- Assume unrelated tracked changes may already exist.
- Do not commit or push pre-existing user changes unless the user explicitly asks for that bundle.
- If a target file already contains unrelated edits, stage only the intended hunks or stop and surface the conflict.

## Worktree Awareness

- Always check the current branch and worktree before git actions.
- Explicitly state which branch and worktree will receive the commit before performing git actions.
- If the current branch is `main`, call that out and use extra caution around staging scope.

## Local-Only Files

- Treat `.env`, `local_data/`, `data/`, caches, and runtime artifacts as local unless the user explicitly says to include them.
- Do not rely on `.gitignore` alone as the safety mechanism.

## Communication Rules

- Before any commit, list the exact files intended for the commit.
- Before any push, state the destination branch and wait for explicit confirmation.
- If a prior approval was cancelled or rejected, state that no further git action will be taken without a fresh request.
