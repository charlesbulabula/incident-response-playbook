import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from src.models.incident import Incident

logger = logging.getLogger(__name__)

PLAYBOOKS_DIR = os.environ.get("PLAYBOOKS_DIR", "playbooks")

MAX_RETRY_COUNT = 5


class PlaybookExecutionError(Exception):
    pass


class PlaybookExecutor:
    def __init__(self, playbooks_dir: str = PLAYBOOKS_DIR):
        self.playbooks_dir = Path(playbooks_dir)
        self._action_registry: Dict[str, Callable] = {}

    def register_action(self, name: str, handler: Callable):
        self._action_registry[name] = handler
        logger.debug("Registered action handler: %s", name)

    def _load_playbook(self, name: str) -> dict:
        path = self.playbooks_dir / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Playbook '{name}' not found at {path}")
        with open(path) as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict) or "steps" not in data:
            raise ValueError(f"Playbook '{name}' missing required 'steps' field")
        return data

    async def execute_step(
        self,
        step: dict,
        context: Dict[str, Any],
        incident: Incident,
    ) -> Dict[str, Any]:
        action = step.get("action", "")
        params = {**step.get("params", {}), **context}
        timeout_s = step.get("timeout_s", 30)
        retry_count = min(step.get("retry_count", 0), MAX_RETRY_COUNT)
        on_failure = step.get("on_failure", "abort")

        handler = self._action_registry.get(action)
        if not handler:
            return {
                "action": action,
                "status": "error",
                "error": f"No handler registered for action '{action}'",
            }

        last_error = None
        attempts = 0
        max_attempts = retry_count + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await asyncio.wait_for(handler(params, context), timeout=timeout_s)
                else:
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, lambda: handler(params, context)),
                        timeout=timeout_s,
                    )
                context.update(result.get("output", {}))
                incident.add_timeline_entry(
                    actor="playbook-executor",
                    action=f"step_completed:{action}",
                    details=f"attempt={attempts} status={result.get('status', 'unknown')}",
                )
                return {**result, "action": action, "attempts": attempts}

            except asyncio.TimeoutError:
                last_error = f"Timed out after {timeout_s}s"
                logger.warning("Step '%s' timed out (attempt %d/%d)", action, attempts, max_attempts)
            except Exception as exc:
                last_error = str(exc)
                logger.error("Step '%s' error (attempt %d/%d): %s", action, attempts, max_attempts, exc)

            if attempts < max_attempts:
                backoff = min(2 ** (attempts - 1), 30)
                await asyncio.sleep(backoff)

        incident.add_timeline_entry(
            actor="playbook-executor",
            action=f"step_failed:{action}",
            details=f"all {attempts} attempts failed: {last_error}",
        )
        return {
            "action": action,
            "status": "error",
            "error": last_error,
            "attempts": attempts,
            "on_failure": on_failure,
        }

    async def run(self, incident: Incident, playbook_name: str) -> Dict[str, Any]:
        logger.info("Running playbook '%s' for incident %s", playbook_name, incident.short_id())
        playbook = self._load_playbook(playbook_name)

        context: Dict[str, Any] = {
            "incident_id": str(incident.id),
            "incident_severity": incident.severity.value,
            "incident_title": incident.title,
            "affected_services": incident.affected_services,
        }

        steps: List[dict] = playbook.get("steps", [])
        step_results = []
        aborted = False

        incident.add_timeline_entry(
            actor="playbook-executor",
            action="playbook_started",
            details=f"playbook={playbook_name} steps={len(steps)}",
        )

        for i, step in enumerate(steps):
            action = step.get("action", f"step_{i}")
            on_failure = step.get("on_failure", "abort")

            logger.info("Executing step %d/%d: %s", i + 1, len(steps), action)
            result = await self.execute_step(step, context, incident)
            step_results.append(result)

            if result.get("status") == "error":
                if on_failure == "abort":
                    logger.error("Step '%s' failed with on_failure=abort, stopping playbook", action)
                    aborted = True
                    break
                elif on_failure == "retry":
                    logger.warning("Step '%s' exhausted retries, continuing", action)
                elif on_failure == "continue":
                    logger.info("Step '%s' failed, continuing as configured", action)

        final_status = "aborted" if aborted else "completed"
        incident.add_timeline_entry(
            actor="playbook-executor",
            action=f"playbook_{final_status}",
            details=f"playbook={playbook_name} steps_run={len(step_results)}",
        )

        logger.info(
            "Playbook '%s' %s for incident %s (%d/%d steps executed)",
            playbook_name,
            final_status,
            incident.short_id(),
            len(step_results),
            len(steps),
        )

        return {
            "incident_id": str(incident.id),
            "playbook": playbook_name,
            "status": final_status,
            "steps_total": len(steps),
            "steps_executed": len(step_results),
            "results": step_results,
            "context": {k: v for k, v in context.items() if not k.startswith("_secret")},
        }

# _r 20260610151205-6ec30769
