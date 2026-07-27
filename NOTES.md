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

## 📋 Other things to come back to

_Add more here as they come up_
