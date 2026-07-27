"""
HarnessWorkflow — the single Temporal workflow class that executes any workflow
defined in a config.yaml file.

Workflow developers write YAML. This class interprets it.
No workflow developer ever touches this file.

Architecture:
  - Reads workflow config from the ConfigMap/file at workflow start
  - Executes each step as a Temporal activity
  - Human gates use Temporal signals — no custom HTTP endpoint needed
  - State persists durably in Temporal's log across crashes and redeploys
  - Workflow versioning handles safe redeploys during active runs
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    import json
    from .activities import (
        NotifyGateInput,
        RunAgentInput,
        RunCodeStepInput,
        notify_gate_waiting,
        run_agent,
        run_code_step,
        load_workflow_config,
    )


@dataclass
class WorkflowInput:
    workflow_id: str
    initial_state: dict
    thread_id: str


@workflow.defn
class HarnessWorkflow:
    """
    Generic Temporal workflow that interprets any config.yaml.
    New workflows = new YAML files. This class never changes.
    """

    def __init__(self):
        self._gate_decisions: dict[str, str] = {}

    @workflow.run
    async def run(self, input: WorkflowInput) -> dict:
        # Load workflow config (activity so it's durable and retryable)
        config = await workflow.execute_activity(
            load_workflow_config,
            args=[input.workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        steps_by_id = {s["id"]: s for s in config["steps"]}
        terminal_step = config.get("terminal_step")
        state = dict(input.initial_state)
        state["thread_id"] = input.thread_id

        current_id = config["steps"][0]["id"]

        while current_id:
            step = steps_by_id.get(current_id)
            if not step:
                workflow.logger.error(f"Step '{current_id}' not found in workflow config")
                break

            step_id = step["id"]
            workflow.logger.info(f"Executing step: {step_id}")

            if "agent" in step:
                agent_ref = step["agent"]
                agent_name = agent_ref.get("uses") or agent_ref.get("id", "unknown")
                result = await workflow.execute_activity(
                    run_agent,
                    RunAgentInput(
                        agent_id=agent_name,
                        state=state,
                        thread_id=input.thread_id,
                    ),
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=RetryPolicy(
                        maximum_attempts=3,
                        initial_interval=timedelta(seconds=5),
                    ),
                )
                state.update(result)
                current_id = step.get("next")

            elif "deterministic" in step:
                cfg = step["deterministic"]
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
                current_id = step.get("next")

            elif "gate" in step:
                gate_cfg = step["gate"]
                field    = gate_cfg["output_field"]
                options  = gate_cfg.get("options", [])

                workflow.logger.info(
                    f"Gate '{step_id}' waiting for signal on field '{field}'. "
                    f"Options: {options}"
                )

                # Notify harness server so UI shows the gate
                await workflow.execute_activity(
                    notify_gate_waiting,
                    NotifyGateInput(
                        thread_id=input.thread_id,
                        gate=step_id,
                        field=field,
                        options=options,
                        state={k: v for k, v in state.items()
                               if k in ("assessment", "vulnerabilities", "pr", "validation", "patches",
                                        "ticket_id", "target_repo", "branch_name", "mitigation_details")},
                    ),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

                # Wait for human signal — Temporal pauses here durably
                await workflow.wait_condition(
                    lambda f=field: f in self._gate_decisions
                )
                decision = self._gate_decisions.pop(field)
                state[field] = decision

                workflow.logger.info(f"Gate '{step_id}' received decision: {decision}")
                routes = step.get("routes", {})
                current_id = routes.get(decision)

            # Terminal step: execute it then exit
            if current_id == terminal_step:
                step = steps_by_id[terminal_step]
                if "deterministic" in step:
                    cfg = step["deterministic"]
                    result = await workflow.execute_activity(
                        run_code_step,
                        RunCodeStepInput(
                            step_type=cfg["type"],
                            state=state,
                            config=cfg,
                            thread_id=input.thread_id,
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                    )
                    state.update(result)
                break

        return state

    @workflow.signal
    def submit_gate_decision(self, field: str, decision: str) -> None:
        """Human submits a gate decision. Temporal delivers this as a durable signal."""
        self._gate_decisions[field] = decision

    @workflow.query
    def get_state(self) -> dict:
        """Returns current workflow state for the UI."""
        return {}
