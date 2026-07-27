# Engineering Agent Harness — Notes & Later Tasks

## 🐛 Harness bugs to fix

### Workflow crashed caused by harness bug — no visibility
When the harness code crashes mid-workflow (e.g. missing import, skill bug), the workflow
fails silently in Temporal. The engineer has no idea unless they're watching Temporal UI.

**What we want:**
- Temporal failure event → post a Linear comment on the ticket automatically
  - Comment body: which step failed, error message, link to Temporal UI
- Harness UI shows a "failed" banner with the error on the Runs view
- Possibly: a health check endpoint that lists recently failed workflows

**Why it matters:**
When the harness is handed off to the next team, they won't be watching logs.
They need to be notified in the tools they already use (Linear).

---

## 🐛 UI shows "running" when Temporal says "failed"

The harness UI Runs view reads from `_active_runs` (in-memory dict set at workflow start).
It never syncs back from Temporal, so a failed/cancelled workflow still shows as "running".

**Fix needed:**
- `/api/runs` already queries Temporal for real status — but the UI doesn't re-render
  the status badge correctly when Temporal says FAILED/CANCELLED
- `refreshRuns()` should update `S.runs` with the real Temporal status
- Show a red "failed" badge + the failure reason (from Temporal history) on the run card
- Auto-refresh should catch this within 10s

## 📋 Other things to come back to

_Add more here as they come up_
