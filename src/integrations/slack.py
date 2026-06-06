import logging
from datetime import timezone
from typing import List, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.models.incident import Incident, SeverityLevel

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    SeverityLevel.P1: "#FF0000",
    SeverityLevel.P2: "#FF6600",
    SeverityLevel.P3: "#FFCC00",
    SeverityLevel.P4: "#36A64F",
}

ONCALL_TEAM_EMAILS_DEFAULT = []


class SlackClient:
    def __init__(self, bot_token: str, oncall_team_user_ids: Optional[List[str]] = None):
        self._client = WebClient(token=bot_token)
        self.oncall_team_user_ids = oncall_team_user_ids or []
        logger.info("SlackClient initialized")

    def _get_user_id_by_email(self, email: str) -> Optional[str]:
        try:
            resp = self._client.users_lookupByEmail(email=email)
            return resp["user"]["id"]
        except SlackApiError as exc:
            logger.warning("Could not find Slack user for email %s: %s", email, exc)
            return None

    def create_war_room(self, incident: Incident, extra_user_ids: Optional[List[str]] = None) -> Optional[str]:
        date_str = incident.created_at.strftime("%Y%m%d")
        channel_name = f"inc-{date_str}-{incident.short_id()}".lower()

        try:
            create_resp = self._client.conversations_create(name=channel_name, is_private=False)
            channel_id = create_resp["channel"]["id"]
            logger.info("Created Slack war room: %s (id=%s)", channel_name, channel_id)
        except SlackApiError as exc:
            logger.error("Failed to create Slack channel %s: %s", channel_name, exc)
            return None

        invite_ids = list(set(self.oncall_team_user_ids + (extra_user_ids or [])))
        if invite_ids:
            try:
                self._client.conversations_invite(channel=channel_id, users=",".join(invite_ids))
            except SlackApiError as exc:
                logger.warning("Failed to invite users to %s: %s", channel_name, exc)

        color = SEVERITY_COLORS.get(incident.severity, "#AAAAAA")
        attachment = {
            "color": color,
            "title": f"[{incident.severity.value}] {incident.title}",
            "fields": [
                {"title": "Severity", "value": incident.severity.value, "short": True},
                {"title": "Status", "value": incident.status.value, "short": True},
                {"title": "Incident ID", "value": str(incident.id), "short": False},
                {"title": "Affected Services", "value": ", ".join(incident.affected_services) or "Unknown", "short": False},
            ],
            "footer": "Incident Response Platform",
            "ts": int(incident.created_at.timestamp()),
        }
        if incident.runbook_url:
            attachment["fields"].append({"title": "Runbook", "value": incident.runbook_url, "short": False})

        try:
            self._client.chat_postMessage(
                channel=channel_id,
                text=f":rotating_light: *Incident War Room: {incident.title}*",
                attachments=[attachment],
            )
        except SlackApiError as exc:
            logger.error("Failed to post incident details to %s: %s", channel_id, exc)

        return channel_id

    def post_update(self, channel_id: str, message: str, is_milestone: bool = False) -> Optional[str]:
        prefix = ":checkered_flag: *Milestone:* " if is_milestone else ":information_source: "
        try:
            resp = self._client.chat_postMessage(channel=channel_id, text=f"{prefix}{message}")
            return resp["ts"]
        except SlackApiError as exc:
            logger.error("Failed to post update to channel %s: %s", channel_id, exc)
            return None

    def pin_runbook(self, channel_id: str, runbook_url: str) -> bool:
        try:
            post_resp = self._client.chat_postMessage(
                channel=channel_id,
                text=f":book: *Runbook:* <{runbook_url}|Click here to view the runbook>",
            )
            ts = post_resp["ts"]
            self._client.pins_add(channel=channel_id, timestamp=ts)
            logger.info("Pinned runbook in channel %s", channel_id)
            return True
        except SlackApiError as exc:
            logger.error("Failed to pin runbook in channel %s: %s", channel_id, exc)
            return False

    def post_summary(self, channel_id: str, incident: Incident) -> Optional[str]:
        duration_str = "N/A"
        if incident.resolved_at and incident.created_at:
            duration = incident.resolved_at - incident.created_at
            hours, rem = divmod(int(duration.total_seconds()), 3600)
            minutes = rem // 60
            duration_str = f"{hours}h {minutes}m"

        timeline_text = "\n".join(
            f"• [{entry.ts.strftime('%H:%M UTC')}] *{entry.action}* by {entry.actor}"
            + (f": {entry.details}" if entry.details else "")
            for entry in incident.timeline[-10:]
        )

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"Incident Resolved: {incident.title}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Severity:* {incident.severity.value}"},
                {"type": "mrkdwn", "text": f"*Duration:* {duration_str}"},
                {"type": "mrkdwn", "text": f"*Assignee:* {incident.assignee or 'Unassigned'}"},
                {"type": "mrkdwn", "text": f"*Status:* {incident.status.value}"},
            ]},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Timeline (last 10 events):*\n{timeline_text or 'No entries'}"}},
            {"type": "divider"},
        ]

        try:
            resp = self._client.chat_postMessage(
                channel=channel_id,
                text=f"Incident resolved: {incident.title}",
                blocks=blocks,
            )
            return resp["ts"]
        except SlackApiError as exc:
            logger.error("Failed to post summary to %s: %s", channel_id, exc)
            return None

    def send_to_oncall(self, user_email: str, message: str) -> bool:
        user_id = self._get_user_id_by_email(user_email)
        if not user_id:
            return False
        try:
            dm = self._client.conversations_open(users=user_id)
            dm_channel = dm["channel"]["id"]
            self._client.chat_postMessage(channel=dm_channel, text=message)
            logger.info("Sent DM to on-call user %s", user_email)
            return True
        except SlackApiError as exc:
            logger.error("Failed to send DM to %s: %s", user_email, exc)
            return False

# _r 20260606090404-380e1477
