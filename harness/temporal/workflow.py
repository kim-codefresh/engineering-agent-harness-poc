"""
HarnessWorkflow — generic Temporal workflow that interprets any config.yaml.

Key design:
- Activities retry automatically (Temporal handles it)
- After STUCK_THRESHOLD failures of the same step, workflow pauses and
  notifies the human via Linear with recovery options
- Human sends a recovery_action signal: skip_to | retry | cancel
- Workflow resumes from exactly where it was — no restart from beginning
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    import json
    from .activities import (
        NotifyFailureInput,
        NotifyGateInput,
        NotifyStuckInput,
        RunAgentInput,
        RunCodeStepInput,
        load_workflow_config,
        notify_gate_waiting,
        notify_harness_failure,
        notify_stuck,
        run_agent,
        run_code_step,
    )

STUCK_THRESHOLD = 5  # failures before pausing for human input


@dataclass
class WorkflowInput:
    workflow_id: str
    initial_state: dict
    thread_id: str


@workflow.defn
class HarnessWorkflow:

    def __init__(self):
        self._gate_decisions: dict[str, str] = {}
        self._recovery: Optional[dict] = None

    @workflow.run
    async def run(self, input: WorkflowInput) -> dict:
        try:
            return await self._run(input)
        except Exception as e:
            try:
                from .activities import notify_harness_failure, NotifyFailureInput
                await workflow.execute_activity(
                    notify_harness_failure,
                    NotifyFailureInput(
                        thread_id=input.thread_id,
                        workflow_id=workflow.info().workflow_id,
                        error=str(e)[:400],
                        ticket_id=input.initial_state.get("ticket_id", ""),
                    ),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except Exception:
                pass
            raise

    async def _run(self, input: WorkflowInput) -> dict:
        config = await workflow.execute_activity(
            load_workflow_config,
            args=[input.workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        steps_by_id = {s["id"]: s for s in config["steps"]}
        terminal_step = config.get("terminal_step")
        state = dict(input.initial_state)
        state["thread_id"] = input.thread_id
        state["workflow_id"] = workflow.info().workflow_id
        state["run_id"] = workflow.info().run_id

        current_id = config["steps"][0]["id"]

        while current_id:
            step = steps_by_id.get(current_id)
            if not step:
                workflow.logger.error(f"Step '{current_id}' not found")
                break

            step_id = step["id"]
            workflow.logger.info(f"Executing step: {step_id}")

            # ── Agent step ───────────────────────────────────────────────────
            if "agent" in step:
                agent_ref = step["agent"]
                agent_name = agent_ref.get("uses") or agent_ref.get("id", "unknown")
                current_id = await self._run_with_recovery(
                    step_id=step_id,
                    step=step,
                    input=input,
                    state=state,
                    is_agent=True,
                    agent_name=agent_name,
                )
                if current_id == "__cancelled__":
                    return state

            # ── Deterministic step ───────────────────────────────────────────
            elif "deterministic" in step:
                current_id = await self._run_with_recovery(
                    step_id=step_id,
                    step=step,
                    input=input,
                    state=state,
                    is_agent=False,
                )
                if current_id == "__cancelled__":
                    return state

            # ── Human gate ───────────────────────────────────────────────────
            elif "gate" in step:
                gate_cfg = step["gate"]
                field    = gate_cfg["output_field"]
                options  = gate_cfg.get("options", [])

                workflow.logger.info(f"Gate '{step_id}' waiting on '{field}'. Options: {options}")

                # Notify harness server + Linear
                await workflow.execute_activity(
                    notify_gate_waiting,
                    NotifyGateInput(
                        thread_id=input.thread_id,
                        gate=step_id,
                        field=field,
                        options=options,
                        state={k: v for k, v in state.items()
                               if k in ("assessment", "vulnerabilities", "pr", "validation",
                                        "patches", "ticket_id", "target_repo", "branch_name",
                                        "mitigation_details", "step_failure")},
                    ),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

                # Wait for human signal — Temporal preserves state durably
                await workflow.wait_condition(
                    lambda f=field: f in self._gate_decisions
                )
                decision = self._gate_decisions.pop(field)
                state[field] = decision
                workflow.logger.info(f"Gate '{step_id}' decision: {decision}")

                routes = step.get("routes", {})
                current_id = routes.get(decision)

            # Handle terminal step
            if current_id == terminal_step:
                step = steps_by_id[terminal_step]
                if "deterministic" in step:
                    cfg = step["deterministic"]
                    try:
                        result = await workflow.execute_activity(
                            run_code_step,
                            RunCodeStepInput(
                                step_type=cfg["type"],
                                state=state,
                                config=cfg,
                                thread_id=input.thread_id,
                            ),
                            start_to_close_timeout=timedelta(minutes=5),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        state.update(result)
                    except Exception:
                        pass
                break

        return state

    async def _run_with_recovery(self, step_id: str, step: dict, input: WorkflowInput,
                                  state: dict, is_agent: bool,
                                  agent_name: str = "") -> str:
        """
        Run an activity with automatic retry. After STUCK_THRESHOLD failures,
        pause and notify Linear so a human can decide: retry | skip_to | cancel.
        Returns: next step_id, or '__cancelled__'
        """
        consecutive_failures = 0

        while True:
            # Check if human sent a recovery signal while we were retrying
            if self._recovery:
                recovery = self._recovery
                self._recovery = None
                action = recovery.get("action")
                target = recovery.get("step")
                workflow.logger.info(f"Recovery signal received: {action} → {target}")
                if action == "skip_to" and target:
                    return target
                elif action == "cancel":
                    return "__cancelled__"
                # action == "retry": fall through and try again

            try:
                if is_agent:
                    result = await workflow.execute_activity(
                        run_agent,
                        RunAgentInput(
                            agent_id=agent_name,
                            state=state,
                            thread_id=input.thread_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=30),
                        retry_policy=RetryPolicy(
                            maximum_attempts=1,  # workflow handles retry loop
                        ),
                    )
                    state.update(result)
                    return step.get("next")

                else:
                    cfg = step["deterministic"]
                    result = await workflow.execute_activity(
                        run_code_step,
                        RunCodeStepInput(
                            step_type=cfg["type"],
                            state=state,
                            config=cfg,
                            thread_id=input.thread_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=RetryPolicy(
                            maximum_attempts=1,  # workflow handles retry loop
                        ),
                    )
                    state.update(result)
                    # Handle routes: (auto-routing) or next:
                    if "routes" in step:
                        field = cfg.get("output_field", step_id + "_result")
                        decision = state.get(field)
                        next_id = step["routes"].get(decision)
                        workflow.logger.info(f"Auto-route '{step_id}': {field}={decision} → {next_id}")
                        return next_id
                    return step.get("next")

            except Exception as e:
                consecutive_failures += 1
                err_msg = str(e)[:300]
                workflow.logger.error(
                    f"Step '{step_id}' failed (attempt {consecutive_failures}): {err_msg}"
                )

                if consecutive_failures >= STUCK_THRESHOLD:
                    # Pause and notify human
                    workflow.logger.warning(
                        f"Step '{step_id}' stuck after {consecutive_failures} failures — waiting for recovery signal"
                    )
                    state["step_failure"] = {
                        "step": step_id,
                        "error": err_msg,
                        "attempts": consecutive_failures,
                    }

                    # Notify Linear with recovery options
                    try:
                        wf_id = workflow.info().workflow_id
                        run_id = workflow.info().run_id
                        await workflow.execute_activity(
                            notify_stuck,
                            NotifyStuckInput(
                                thread_id=input.thread_id,
                                step_id=step_id,
                                error=err_msg,
                                attempts=consecutive_failures,
                                ticket_id=input.initial_state.get("ticket_id", ""),
                                workflow_id=wf_id,
                                run_id=run_id,
                            ),
                            start_to_close_timeout=timedelta(seconds=15),
                            retry_policy=RetryPolicy(maximum_attempts=1),
                        )
                    except Exception:
                        pass

                    # Wait for human recovery signal (up to 48h)
                    await workflow.wait_condition(
                        lambda: self._recovery is not None,
                        timeout=timedelta(hours=48),
                    )
                    consecutive_failures = 0  # reset after human intervenes

                else:
                    # Backoff before next retry: 10s, 20s, 40s, 80s, 160s
                    backoff = min(10 * (2 ** (consecutive_failures - 1)), 180)
                    workflow.logger.info(f"Retrying '{step_id}' in {backoff}s...")
                    await workflow.sleep(timedelta(seconds=backoff))

    @workflow.signal
    def submit_gate_decision(self, field: str, decision: str) -> None:
        """Human submits a gate decision."""
        self._gate_decisions[field] = decision

    @workflow.signal
    def recovery_action(self, action: str, step: str = "") -> None:
        """
        Human recovery signal for stuck workflows.
        action: 'retry' | 'skip_to' | 'cancel'
        step: target step id (only for skip_to)
        """
        self._recovery = {"action": action, "step": step}

    @workflow.query
    def get_state_summary(self) -> dict:
        return {"recovery_pending": self._recovery is not None}
