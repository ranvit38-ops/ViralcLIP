---
name: deploy-agents
description: Use when the user says "deploy agents" (or "send the agents", "agents go") for ViralClip. Plans the outstanding work, fans it out to the viralclip-* subagents in parallel, merges their results, runs every test and pushes.
---
# Deploy agents

The user wants the work done in parallel by the project's agents without having to explain how.

## 1. Decide the work (no questions unless truly blocked)
- If the user said what they want alongside "deploy agents", that is the work.
- Otherwise, take it from `viralclip/DEVELOPMENT_STATUS.md` ("Next tasks", "Known limits", "Not verified") plus any open bugs mentioned in the conversation.
- Split the work into independent chunks that touch different files. Usually:
  - one **viralclip-backend** task
  - one **viralclip-frontend** task
  - **viralclip-perf** if speed is a concern
  - **viralclip-qa** last, or alongside the others when you only need verification
- Tell the user the plan in 3–6 bullet lines, then launch without waiting.

## 2. Launch
- Use the Agent tool once per chunk:
  - `subagent_type` = the agent name
  - `isolation: "worktree"`
  - `run_in_background: true`
- Each prompt needs:
  - the goal
  - the files it owns
  - the files it must not touch, i.e. those owned by the other agents
  - "commit on your worktree branch and report test results"
- Never give two agents the same files.

## 3. Merge and verify
- As each agent finishes, merge its worktree branch into the working branch and resolve conflicts. Shared files (models.py, main.py, shell.tsx NAV, ui.tsx) are usually additive, so keep both sides.
- New Alembic migrations from different agents must chain: fix `down_revision` if two agents both branched from the same head.
- After all merges:
  - Run `cd viralclip/backend && .venv/bin/python -m pytest -q`.
  - In `viralclip/frontend`, run `npx tsc --noEmit && npx eslint . && npm run build`.
  - Run the **viralclip-qa** agent (or `frontend/e2e/clips.e2e.cjs` yourself) against the merged build.
- Update `viralclip/DEVELOPMENT_STATUS.md`, commit, push.

## 4. Report
- Say what is done and verified, what failed, and what still needs the user (real Google/Twitch accounts, etc.).
- If an agent hit a usage limit, resume it with SendMessage when the limit resets. Otherwise finish its work yourself from its worktree.
