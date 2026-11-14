# Repo notes for Claude

- `viralclip/` is the ViralClip app: AI video clipping plus YouTube/Twitch automation. Everything else at the repo root is an unrelated donation website; don't touch it.
- When the user says **"deploy agents"**, invoke the `deploy-agents` skill. The agents are in `.claude/agents/` (viralclip-backend, viralclip-frontend, viralclip-qa, viralclip-perf).
- Status and next tasks: `viralclip/DEVELOPMENT_STATUS.md`. Design: `viralclip/ARCHITECTURE.md`.
- Tests:
  - backend: `cd viralclip/backend && .venv/bin/python -m pytest -q`
  - frontend: `cd viralclip/frontend && npx tsc --noEmit && npx eslint . && npm run build`
  - browser end-to-end: `frontend/e2e/clips.e2e.cjs`
- Use a dedicated port per stack you start, and stop processes by port. Broad `pkill` kills other agents' servers.
