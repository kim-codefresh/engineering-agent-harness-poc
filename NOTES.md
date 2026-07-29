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

## 🔌 Port-forward keeps dropping

`kubectl port-forward` drops on pod restarts and connection timeouts — breaks the cloudflared tunnel and webhook.

**Fix:** Convert harness service to NodePort so cloudflared connects directly without port-forward:
```bash
kubectl -n agent-harness patch svc agent-harness -p '{"spec":{"type":"NodePort","ports":[{"port":8000,"targetPort":8000,"nodePort":30080}]}}'
cloudflared tunnel --url http://localhost:30080
```

## 🚨 Gate shows no context — human can't make an informed decision

The gate currently shows option buttons (fix_authorized / mitigation / escalate)
with no context about what the agent actually found or proposed.

**What it should show:**
- CVE details: which package, current version, safe version
- Agent assessment: affected? risk level? reasoning?
- Agent proposal: ready → show the proposed diff + which file changes
- Agent failure: mitigation → show why it failed + what manual action is needed
- Stuck → show a text box to send instructions directly to the agent

**Where the notification should go:** Linear comment on the ticket, with a link
to the harness UI to make the decision. Not just in the harness UI.

**"Jump in" option:** if agent is stuck/looping, human should be able to send
a message directly into the agent's context to guide it.

## 🐛 OpenHands POST /api/conversations returns 422

Our request payload format doesn't match OpenHands API contract.
Need to check OpenHands API docs and fix the `run_openhands.py` skill.
OpenHands is reachable and running — just rejecting our payload format.

Fix: check OpenHands API schema for `/api/conversations` and update
`harness/skills/run_openhands.py` to match.

## 🐛 Re-applying same label doesn't trigger — ignored as duplicate

When the label is already on the ticket and you remove+re-add it, Linear sends `updatedFrom.labelIds` containing the label ID, so our duplicate check blocks it.

**Fix:** the duplicate check is too strict. Should only block if the label was present BEFORE this specific update event, not if it appeared in `updatedFrom` at all. Better approach: use a short-lived dedup cache (Redis or in-memory with TTL) keyed on `(ticket_id, label_id, timestamp)` rather than checking `updatedFrom`.

## 🐛 Multiple workflows receive gate signals simultaneously

When multiple workflows are running (e.g. from retries or duplicate triggers),
broadcasting a gate signal to "all running workflows" causes all of them to proceed
and potentially open duplicate PRs.

Fix: gate signals should target a specific thread_id, not broadcast to all.
The harness UI and Linear comment should include the specific thread_id in the
decision link so humans signal the correct workflow only.

## 📋 Other things to come back to

_Add more here as they come up_
