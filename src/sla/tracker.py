import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import redis
from prometheus_client import Gauge

from src.models.incident import Incident, SeverityLevel, SLA_TARGETS

logger = logging.getLogger(__name__)


class SLAStatus(str, Enum):
    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    BREACHED = "breached"


AT_RISK_THRESHOLD = 0.80

MTTD_GAUGE = Gauge(
    "incident_mttd_minutes",
    "Mean time to detect in minutes",
    ["severity"],
)
MTTA_GAUGE = Gauge(
    "incident_mtta_minutes",
    "Mean time to acknowledge in minutes",
    ["severity"],
)
MTTR_GAUGE = Gauge(
    "incident_mttr_minutes",
    "Mean time to resolve in minutes",
    ["severity"],
)
SLA_BREACH_GAUGE = Gauge(
    "incident_sla_breaches_total",
    "Total SLA breaches",
    ["severity", "metric"],
)


class SLATracker:
    KEY_PREFIX = "sla:"

    def __init__(self, redis_client: redis.Redis, ttl_seconds: int = 86400 * 30):
        self.redis = redis_client
        self.ttl = ttl_seconds
        logger.info("SLATracker initialized")

    def _key(self, incident_id: str, metric: str) -> str:
        return f"{self.KEY_PREFIX}{incident_id}:{metric}"

    def _store(self, incident: Incident, metric: str, value_minutes: float):
        key = self._key(str(incident.id), metric)
        self.redis.setex(key, self.ttl, str(round(value_minutes, 3)))
        logger.debug("Stored SLA metric: incident=%s %s=%.2f min", incident.short_id(), metric, value_minutes)

    def _get_minutes_since(self, since: Optional[datetime]) -> Optional[float]:
        if not since:
            return None
        now = datetime.now(timezone.utc)
        delta = now - (since if since.tzinfo else since.replace(tzinfo=timezone.utc))
        return delta.total_seconds() / 60

    def record_detected(self, incident: Incident) -> Optional[float]:
        incident.detected_at = datetime.now(timezone.utc)
        minutes = self._get_minutes_since(incident.created_at)
        if minutes is not None:
            self._store(incident, "mttd", minutes)
            MTTD_GAUGE.labels(severity=incident.severity.value).set(minutes)
        return minutes

    def record_acknowledged(self, incident: Incident) -> Optional[float]:
        if not incident.acknowledged_at:
            incident.acknowledged_at = datetime.now(timezone.utc)
        minutes = self._get_minutes_since(incident.created_at)
        if minutes is not None:
            self._store(incident, "mtta", minutes)
            MTTA_GAUGE.labels(severity=incident.severity.value).set(minutes)
        return minutes

    def record_resolved(self, incident: Incident) -> Optional[float]:
        if not incident.resolved_at:
            incident.resolved_at = datetime.now(timezone.utc)
        minutes = self._get_minutes_since(incident.created_at)
        if minutes is not None:
            self._store(incident, "mttr", minutes)
            MTTR_GAUGE.labels(severity=incident.severity.value).set(minutes)
        return minutes

    def check_breach(self, incident: Incident) -> SLAStatus:
        targets = SLA_TARGETS.get(incident.severity, {})
        if not targets:
            return SLAStatus.ON_TRACK

        now = datetime.now(timezone.utc)
        created = incident.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_minutes = (now - created).total_seconds() / 60

        mttr_target = targets.get("mttr_minutes", float("inf"))
        mtta_target = targets.get("mtta_minutes", float("inf"))
        mttd_target = targets.get("mttd_minutes", float("inf"))

        if incident.status.value not in ("RESOLVED", "MITIGATED"):
            if elapsed_minutes >= mttr_target:
                logger.warning(
                    "SLA BREACH: incident=%s severity=%s elapsed=%.1f mttr_target=%.1f",
                    incident.short_id(),
                    incident.severity.value,
                    elapsed_minutes,
                    mttr_target,
                )
                SLA_BREACH_GAUGE.labels(severity=incident.severity.value, metric="mttr").inc()
                return SLAStatus.BREACHED

            if elapsed_minutes >= mttr_target * AT_RISK_THRESHOLD:
                return SLAStatus.AT_RISK

        if incident.status.value == "OPEN":
            if elapsed_minutes >= mtta_target:
                logger.warning(
                    "SLA BREACH: incident=%s not acknowledged after %.1f min (target=%.1f)",
                    incident.short_id(),
                    elapsed_minutes,
                    mtta_target,
                )
                SLA_BREACH_GAUGE.labels(severity=incident.severity.value, metric="mtta").inc()
                return SLAStatus.BREACHED

            if elapsed_minutes >= mtta_target * AT_RISK_THRESHOLD:
                return SLAStatus.AT_RISK

        return SLAStatus.ON_TRACK

    def get_stored_metrics(self, incident_id: str) -> dict:
        metrics = {}
        for metric in ("mttd", "mtta", "mttr"):
            key = self._key(incident_id, metric)
            val = self.redis.get(key)
            if val:
                try:
                    metrics[metric] = float(val)
                except ValueError:
                    pass
        return metrics

    def get_sla_summary(self, incident: Incident) -> dict:
        targets = SLA_TARGETS.get(incident.severity, {})
        stored = self.get_stored_metrics(str(incident.id))
        breach_status = self.check_breach(incident)

        return {
            "incident_id": str(incident.id),
            "severity": incident.severity.value,
            "sla_status": breach_status.value,
            "targets": targets,
            "actuals": stored,
        }

# _r 20260624112006-750af504
