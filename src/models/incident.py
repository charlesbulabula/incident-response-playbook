from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, UUID4


class SeverityLevel(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"


SLA_TARGETS = {
    SeverityLevel.P1: {"mttd_minutes": 5, "mtta_minutes": 15, "mttr_minutes": 60},
    SeverityLevel.P2: {"mttd_minutes": 15, "mtta_minutes": 30, "mttr_minutes": 240},
    SeverityLevel.P3: {"mttd_minutes": 60, "mtta_minutes": 120, "mttr_minutes": 1440},
    SeverityLevel.P4: {"mttd_minutes": 240, "mtta_minutes": 480, "mttr_minutes": 4320},
}


class TimelineEntry(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = Field(..., min_length=1, max_length=128)
    action: str = Field(..., min_length=1, max_length=256)
    details: Optional[str] = Field(default=None, max_length=2048)

    model_config = {"json_schema_extra": {"example": {
        "actor": "playbook-runner",
        "action": "slack_notification_sent",
        "details": "War room created: #inc-20260518-abc123",
    }}}


class Incident(BaseModel):
    id: UUID4 = Field(default_factory=uuid.uuid4)
    title: str = Field(..., min_length=1, max_length=512)
    severity: SeverityLevel
    status: IncidentStatus = Field(default=IncidentStatus.OPEN)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detected_at: Optional[datetime] = Field(default=None)
    acknowledged_at: Optional[datetime] = Field(default=None)
    mitigated_at: Optional[datetime] = Field(default=None)
    resolved_at: Optional[datetime] = Field(default=None)
    assignee: Optional[str] = Field(default=None, max_length=256)
    timeline: List[TimelineEntry] = Field(default_factory=list)
    affected_services: List[str] = Field(default_factory=list)
    runbook_url: Optional[str] = Field(default=None)
    pagerduty_incident_id: Optional[str] = Field(default=None)
    slack_channel_id: Optional[str] = Field(default=None)
    jira_ticket_key: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=4096)
    source_alert_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    def add_timeline_entry(
        self,
        actor: str,
        action: str,
        details: Optional[str] = None,
    ) -> TimelineEntry:
        entry = TimelineEntry(actor=actor, action=action, details=details)
        self.timeline.append(entry)
        self.updated_at = datetime.now(timezone.utc)
        return entry

    def acknowledge(self, assignee: str) -> None:
        self.status = IncidentStatus.ACKNOWLEDGED
        self.assignee = assignee
        self.acknowledged_at = datetime.now(timezone.utc)
        self.updated_at = self.acknowledged_at
        self.add_timeline_entry(actor=assignee, action="acknowledged", details="Incident acknowledged")

    def mitigate(self, actor: str, details: Optional[str] = None) -> None:
        self.status = IncidentStatus.MITIGATED
        self.mitigated_at = datetime.now(timezone.utc)
        self.updated_at = self.mitigated_at
        self.add_timeline_entry(actor=actor, action="mitigated", details=details)

    def resolve(self, actor: str, resolution_note: Optional[str] = None) -> None:
        self.status = IncidentStatus.RESOLVED
        self.resolved_at = datetime.now(timezone.utc)
        self.updated_at = self.resolved_at
        self.add_timeline_entry(actor=actor, action="resolved", details=resolution_note)

    def get_sla_targets(self) -> dict:
        return SLA_TARGETS.get(self.severity, {})

    def mttd_minutes(self) -> Optional[float]:
        if self.detected_at and self.created_at:
            delta = self.detected_at - self.created_at
            return delta.total_seconds() / 60
        return None

    def mtta_minutes(self) -> Optional[float]:
        if self.acknowledged_at and self.created_at:
            delta = self.acknowledged_at - self.created_at
            return delta.total_seconds() / 60
        return None

    def mttr_minutes(self) -> Optional[float]:
        if self.resolved_at and self.created_at:
            delta = self.resolved_at - self.created_at
            return delta.total_seconds() / 60
        return None

    def short_id(self) -> str:
        return str(self.id)[:8]

    def to_summary_dict(self) -> dict:
        return {
            "id": str(self.id),
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status.value,
            "assignee": self.assignee,
            "affected_services": self.affected_services,
            "created_at": self.created_at.isoformat(),
            "timeline_events": len(self.timeline),
            "mttd_minutes": self.mttd_minutes(),
            "mtta_minutes": self.mtta_minutes(),
            "mttr_minutes": self.mttr_minutes(),
        }

# _r 20260603103606-0fc40638
