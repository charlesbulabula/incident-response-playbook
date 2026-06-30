import logging
from typing import Optional

import pdpyras

logger = logging.getLogger(__name__)

URGENCY_MAP = {
    "P1": "high",
    "P2": "high",
    "P3": "low",
    "P4": "low",
}


class PagerDutyClient:
    def __init__(self, api_token: str, from_email: str):
        self.from_email = from_email
        self._session = pdpyras.APISession(api_token)
        self._session.headers["From"] = from_email
        logger.info("PagerDutyClient initialized for %s", from_email)

    def create_incident(
        self,
        title: str,
        severity: str,
        service_id: str,
        body: Optional[str] = None,
        escalation_policy_id: Optional[str] = None,
    ) -> str:
        urgency = URGENCY_MAP.get(severity, "high")
        payload = {
            "incident": {
                "type": "incident",
                "title": title,
                "service": {"id": service_id, "type": "service_reference"},
                "urgency": urgency,
                "body": {"type": "incident_body", "details": body or title},
            }
        }
        if escalation_policy_id:
            payload["incident"]["escalation_policy"] = {
                "id": escalation_policy_id,
                "type": "escalation_policy_reference",
            }

        try:
            resp = self._session.post("/incidents", json=payload)
            resp.raise_for_status()
            incident_id = resp.json()["incident"]["id"]
            logger.info("Created PD incident: id=%s title=%s severity=%s", incident_id, title, severity)
            return incident_id
        except pdpyras.PDClientError as exc:
            logger.error("Failed to create PD incident: %s", exc)
            raise

    def acknowledge(self, incident_id: str, user_email: str) -> bool:
        payload = {
            "incident": {
                "type": "incident",
                "status": "acknowledged",
            }
        }
        headers = {"From": user_email}
        try:
            resp = self._session.put(f"/incidents/{incident_id}", json=payload, headers=headers)
            resp.raise_for_status()
            logger.info("Acknowledged PD incident %s by %s", incident_id, user_email)
            return True
        except pdpyras.PDClientError as exc:
            logger.error("Failed to acknowledge PD incident %s: %s", incident_id, exc)
            raise

    def resolve(self, incident_id: str, resolution_note: Optional[str] = None) -> bool:
        payload = {
            "incident": {
                "type": "incident",
                "status": "resolved",
            }
        }
        try:
            resp = self._session.put(f"/incidents/{incident_id}", json=payload)
            resp.raise_for_status()
            if resolution_note:
                self.add_note(incident_id, resolution_note)
            logger.info("Resolved PD incident %s", incident_id)
            return True
        except pdpyras.PDClientError as exc:
            logger.error("Failed to resolve PD incident %s: %s", incident_id, exc)
            raise

    def add_note(self, incident_id: str, content: str) -> Optional[str]:
        payload = {
            "note": {
                "content": content[:25000],
            }
        }
        try:
            resp = self._session.post(f"/incidents/{incident_id}/notes", json=payload)
            resp.raise_for_status()
            note_id = resp.json()["note"]["id"]
            logger.debug("Added note to PD incident %s: note_id=%s", incident_id, note_id)
            return note_id
        except pdpyras.PDClientError as exc:
            logger.error("Failed to add note to PD incident %s: %s", incident_id, exc)
            raise

    def get_on_call(self, schedule_id: str) -> Optional[str]:
        try:
            resp = self._session.get(
                f"/schedules/{schedule_id}/users",
                params={"since": "now", "until": "now"},
            )
            resp.raise_for_status()
            users = resp.json().get("users", [])
            if users:
                email = users[0].get("email")
                logger.info("On-call for schedule %s: %s", schedule_id, email)
                return email
            logger.warning("No on-call user found for schedule %s", schedule_id)
            return None
        except pdpyras.PDClientError as exc:
            logger.error("Failed to get on-call for schedule %s: %s", schedule_id, exc)
            raise

    def get_incident(self, incident_id: str) -> Optional[dict]:
        try:
            resp = self._session.get(f"/incidents/{incident_id}")
            resp.raise_for_status()
            return resp.json().get("incident")
        except pdpyras.PDClientError as exc:
            logger.error("Failed to get PD incident %s: %s", incident_id, exc)
            return None

    def snooze(self, incident_id: str, duration_seconds: int) -> bool:
        payload = {"duration": duration_seconds}
        try:
            resp = self._session.post(f"/incidents/{incident_id}/snooze", json=payload)
            resp.raise_for_status()
            logger.info("Snoozed PD incident %s for %ds", incident_id, duration_seconds)
            return True
        except pdpyras.PDClientError as exc:
            logger.error("Failed to snooze PD incident %s: %s", incident_id, exc)
            raise

# _r 20260630104512-d17b747e
