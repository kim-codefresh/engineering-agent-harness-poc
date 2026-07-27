"""
Temporal worker — polls Temporal server for workflow and activity tasks.

Runs as a separate k8s Deployment from the harness API server.
Each agent invocation becomes a Temporal activity dispatched to this worker.
"""
import asyncio
import concurrent.futures
import logging
import os
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

sys.path.insert(0, str(Path(__file__).parent.parent))

from .workflow import HarnessWorkflow
from .activities import load_workflow_config, notify_gate_waiting, run_agent, run_code_step

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "temporal-frontend.temporal.svc.cluster.local:7233")
TASK_QUEUE    = os.getenv("TEMPORAL_TASK_QUEUE", "harness-queue")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main():
    log.info(f"Connecting to Temporal at {TEMPORAL_HOST}")
    client = await Client.connect(TEMPORAL_HOST)

    log.info(f"Starting worker on task queue: {TASK_QUEUE}")
    # Sync activities need a thread pool executor
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[HarnessWorkflow],
        activities=[load_workflow_config, notify_gate_waiting, run_agent, run_code_step],
        activity_executor=executor,
        max_concurrent_activities=5,
        max_concurrent_workflow_tasks=10,
    )

    log.info("Worker started — polling for tasks")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
