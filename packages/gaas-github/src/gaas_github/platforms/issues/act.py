"""Stub for github.issues.act — actions not yet implemented.

This handler logs what actions would be taken without executing them.
The SIMPLE_ACTIONS allowlist and reversibility tiers will be defined
here alongside the actual action implementations.
"""
from __future__ import annotations

import logging

from gaas_sdk.task import TaskRecord

log = logging.getLogger(__name__)


def handle(task: TaskRecord):
    org = task["payload"]["org"]
    repo = task["payload"]["repo"]
    number = task["payload"]["number"]
    actions = task["payload"].get("actions", [])
    provenance = task.get("provenance", "unknown")

    log.info(
        "github.issues.act: %s/%s#%d — actions=%s provenance=%s (not yet implemented)",
        org, repo, number, actions, provenance,
    )
