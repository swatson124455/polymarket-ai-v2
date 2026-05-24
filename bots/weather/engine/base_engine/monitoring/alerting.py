"""
Automated Alerting System - Real-time alerts for critical issues.

Provides alerting for:
- Critical errors
- Performance degradation
- API failures
- Risk limit breaches
- System health issues
"""
import asyncio
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timezone, timedelta
from enum import Enum
from structlog import get_logger
from bots.weather.engine.base_engine.monitoring.health_monitor import HealthMonitor, HealthStatus

logger = get_logger()


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Alert:
    """Represents a single alert."""
    
    def __init__(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        source: str = "system",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.title = title
        self.message = message
        self.severity = severity
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc)
        self.acknowledged = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "message": self.message,
            "severity": self.severity.value,
            "source": self.source,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged
        }


class AlertingSystem:
    """
    Automated alerting system with multiple channels.
    
    Supports:
    - Logging (always)
    - Dashboard notifications
    - Email (configurable)
    - Slack/Discord webhooks (configurable)
    - SMS (configurable, for critical only)
    """
    
    def __init__(
        self,
        health_monitor: Optional[HealthMonitor] = None,
        email_enabled: bool = False,
        slack_webhook: Optional[str] = None,
        discord_webhook: Optional[str] = None,
        sms_enabled: bool = False
    ):
        self.health_monitor = health_monitor
        self.email_enabled = email_enabled
        self.slack_webhook = slack_webhook
        self.discord_webhook = discord_webhook
        self.sms_enabled = sms_enabled
        
        self.alerts: List[Alert] = []
        self.max_alerts = 10000
        self.alert_callbacks: List[Callable[[Alert], None]] = []
        
        # Rate limiting: don't spam same alert
        self.alert_cooldown: Dict[str, datetime] = {}
        self.cooldown_seconds = 300  # 5 minutes between same alerts
    
    async def send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        source: str = "system",
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Send an alert through all configured channels.
        
        Args:
            title: Alert title
            message: Alert message
            severity: Alert severity level
            source: Source of the alert
            metadata: Additional metadata
        """
        # Check cooldown
        alert_key = f"{source}:{title}"
        if alert_key in self.alert_cooldown:
            last_sent = self.alert_cooldown[alert_key]
            if datetime.now(timezone.utc) - last_sent < timedelta(seconds=self.cooldown_seconds):
                logger.debug(f"Alert {alert_key} in cooldown, skipping")
                return
        
        alert = Alert(title, message, severity, source, metadata)
        
        # Store alert
        self.alerts.append(alert)
        if len(self.alerts) > self.max_alerts:
            self.alerts.pop(0)
        
        # Update cooldown
        self.alert_cooldown[alert_key] = datetime.now(timezone.utc)
        
        # Log alert
        log_level = {
            AlertSeverity.INFO: logger.info,
            AlertSeverity.WARNING: logger.warning,
            AlertSeverity.ERROR: logger.error,
            AlertSeverity.CRITICAL: logger.critical
        }.get(severity, logger.warning)
        
        log_level(
            f"ALERT [{severity.value.upper()}]: {title}",
            message=message,
            source=source,
            metadata=metadata
        )
        
        # Send through channels
        await self._send_to_channels(alert)
        
        # Call registered callbacks
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.warning(f"Alert callback failed: {str(e)}")
    
    async def _send_to_channels(self, alert: Alert):
        """Send alert through configured channels."""
        # Email (for critical/error)
        if self.email_enabled and alert.severity in [AlertSeverity.ERROR, AlertSeverity.CRITICAL]:
            await self._send_email(alert)
        
        # Slack webhook
        if self.slack_webhook:
            await self._send_slack(alert)
        
        # Discord webhook
        if self.discord_webhook:
            await self._send_discord(alert)
        
        # SMS (critical only)
        if self.sms_enabled and alert.severity == AlertSeverity.CRITICAL:
            await self._send_sms(alert)
    
    async def _send_email(self, alert: Alert):
        """
        Send alert via email using SMTP.

        Env vars (all optional — if SMTP_HOST is unset, this is a no-op):
          SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
          ALERT_EMAIL_FROM, ALERT_EMAIL_TO (comma-separated recipients)
        """
        import os
        host = os.getenv("SMTP_HOST")
        if not host:
            logger.debug("Email alert skipped (SMTP_HOST not set): %s", alert.title)
            return
        try:
            import smtplib
            from email.mime.text import MIMEText
            port = int(os.getenv("SMTP_PORT", "587"))
            user = os.getenv("SMTP_USER", "")
            password = os.getenv("SMTP_PASSWORD", "")
            from_addr = os.getenv("ALERT_EMAIL_FROM", user)
            to_addrs = [a.strip() for a in os.getenv("ALERT_EMAIL_TO", "").split(",") if a.strip()]
            if not to_addrs:
                logger.debug("Email alert skipped (no ALERT_EMAIL_TO)")
                return

            subject = f"[{alert.severity.value.upper()}] {alert.title}"
            body = f"{alert.message}\n\nSource: {alert.source}\nTime: {alert.timestamp.isoformat()}"
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = ", ".join(to_addrs)

            # Run blocking SMTP in executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            def _smtp_send():
                with smtplib.SMTP(host, port, timeout=10) as s:
                    s.ehlo()
                    if port == 587:
                        s.starttls()
                    if user and password:
                        s.login(user, password)
                    s.sendmail(from_addr, to_addrs, msg.as_string())
            await loop.run_in_executor(None, _smtp_send)
            logger.info("Email alert sent: %s", alert.title)
        except Exception as e:
            logger.warning("Failed to send email alert: %s", e)
    
    async def _send_slack(self, alert: Alert):
        """Send alert to Slack webhook."""
        if not self.slack_webhook:
            return
        
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                payload = {
                    "text": f"*[{alert.severity.value.upper()}]* {alert.title}",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{alert.title}*\n{alert.message}"
                            }
                        }
                    ]
                }
                await client.post(self.slack_webhook, json=payload, timeout=5.0)
        except Exception as e:
            logger.warning(f"Failed to send Slack alert: {str(e)}")
    
    async def _send_discord(self, alert: Alert):
        """Send alert to Discord webhook."""
        if not self.discord_webhook:
            return
        
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                # Discord color mapping
                color_map = {
                    AlertSeverity.INFO: 3447003,  # Blue
                    AlertSeverity.WARNING: 16776960,  # Yellow
                    AlertSeverity.ERROR: 16711680,  # Red
                    AlertSeverity.CRITICAL: 9109504  # Dark red
                }
                
                payload = {
                    "embeds": [{
                        "title": alert.title,
                        "description": alert.message,
                        "color": color_map.get(alert.severity, 3447003),
                        "timestamp": alert.timestamp.isoformat(),
                        "fields": [
                            {"name": "Severity", "value": alert.severity.value.upper(), "inline": True},
                            {"name": "Source", "value": alert.source, "inline": True}
                        ]
                    }]
                }
                await client.post(self.discord_webhook, json=payload, timeout=5.0)
        except Exception as e:
            logger.warning(f"Failed to send Discord alert: {str(e)}")
    
    async def _send_sms(self, alert: Alert):
        """
        Send critical alert via Twilio SMS.

        Env vars (all optional — if TWILIO_ACCOUNT_SID is unset, this is a no-op):
          TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_SMS_TO
        """
        import os
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        if not account_sid:
            logger.debug("SMS alert skipped (TWILIO_ACCOUNT_SID not set): %s", alert.title)
            return
        try:
            import httpx
            auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
            from_number = os.getenv("TWILIO_FROM_NUMBER", "")
            to_number = os.getenv("ALERT_SMS_TO", "")
            if not all([auth_token, from_number, to_number]):
                logger.debug("SMS alert skipped (incomplete Twilio config)")
                return

            body = f"[{alert.severity.value.upper()}] {alert.title}: {alert.message}"[:1600]
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    data={"To": to_number, "From": from_number, "Body": body},
                    auth=(account_sid, auth_token),
                    timeout=10.0,
                )
                if resp.status_code < 300:
                    logger.info("SMS alert sent: %s", alert.title)
                else:
                    logger.warning("SMS alert failed (HTTP %d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("Failed to send SMS alert: %s", e)
    
    def add_callback(self, callback: Callable[[Alert], None]):
        """Register a callback function to be called when alerts are sent."""
        self.alert_callbacks.append(callback)
    
    def get_recent_alerts(
        self,
        limit: int = 100,
        severity: Optional[AlertSeverity] = None,
        source: Optional[str] = None
    ) -> List[Alert]:
        """Get recent alerts with optional filtering."""
        filtered = self.alerts
        
        if severity:
            filtered = [a for a in filtered if a.severity == severity]
        
        if source:
            filtered = [a for a in filtered if a.source == source]
        
        return filtered[-limit:]
    
    async def monitor_health(self, interval_seconds: int = 60):
        """
        Continuously monitor health and send alerts for issues.
        
        Args:
            interval_seconds: How often to check health
        """
        if not self.health_monitor:
            logger.warning("Health monitor not configured, cannot monitor health")
            return
        
        while True:
            try:
                health = await self.health_monitor.check_all_services()
                
                # Check overall status
                if health["overall"] == "unhealthy":
                    await self.send_alert(
                        title="System Unhealthy",
                        message="One or more system components are unhealthy",
                        severity=AlertSeverity.CRITICAL,
                        source="health_monitor",
                        metadata=health
                    )
                elif health["overall"] == "degraded":
                    await self.send_alert(
                        title="System Degraded",
                        message="System performance is degraded",
                        severity=AlertSeverity.WARNING,
                        source="health_monitor",
                        metadata=health
                    )
                
                # Check individual components
                for comp_name, comp_data in health["components"].items():
                    if comp_data["status"] == "unhealthy":
                        await self.send_alert(
                            title=f"{comp_name.capitalize()} Unhealthy",
                            message=comp_data.get("message", "Component check failed"),
                            severity=AlertSeverity.ERROR,
                            source=f"health_monitor.{comp_name}",
                            metadata=comp_data
                        )
                
                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logger.error(f"Health monitoring error: {str(e)}", exc_info=True)
                await asyncio.sleep(interval_seconds)

    async def check_model_metrics(
        self,
        brier_score: Optional[float] = None,
        sharpe_ratio: Optional[float] = None,
        win_rate: Optional[float] = None,
        bot_name: str = "system",
    ) -> None:
        """
        Check model performance metrics against thresholds and send alerts.
        Called periodically from the learning scheduler or main loop.
        """
        if brier_score is not None and brier_score > 0.25:
            await self.send_alert(
                title="High Brier score",
                message=f"Brier score {brier_score:.4f} exceeds 0.25 threshold — models may be miscalibrated",
                severity=AlertSeverity.WARNING,
                source=f"model_metrics.{bot_name}",
                metadata={"brier_score": brier_score, "threshold": 0.25},
            )

        if sharpe_ratio is not None and sharpe_ratio < 0:
            _sev = AlertSeverity.CRITICAL if sharpe_ratio < -1.0 else AlertSeverity.WARNING
            await self.send_alert(
                title="Negative Sharpe ratio",
                message=f"Sharpe ratio {sharpe_ratio:.2f} is negative — strategy losing money risk-adjusted",
                severity=_sev,
                source=f"model_metrics.{bot_name}",
                metadata={"sharpe_ratio": sharpe_ratio},
            )

        if win_rate is not None and win_rate < 0.40:
            await self.send_alert(
                title="Low win rate",
                message=f"Win rate {win_rate:.1%} below 40% threshold",
                severity=AlertSeverity.WARNING,
                source=f"model_metrics.{bot_name}",
                metadata={"win_rate": win_rate, "threshold": 0.40},
            )

    async def check_drift_status(self, drift_status: Dict[str, Any]) -> None:
        """Alert on concept drift detection from CalibrationTracker."""
        if drift_status.get("ddm_drift") or drift_status.get("eddm_drift"):
            await self.send_alert(
                title="Concept drift detected",
                message=f"Model accuracy drifting — error rate {drift_status.get('error_rate', 0):.4f}. Retrain recommended.",
                severity=AlertSeverity.ERROR,
                source="drift_detector",
                metadata=drift_status,
            )

    async def check_daily_pnl_summary(self, db) -> None:
        """Query today's resolved paper_trades and send a daily PnL summary alert.

        Aggregates per bot_name for trades resolved (realized_pnl IS NOT NULL)
        today (UTC). Sends a single Slack/Discord/Email message. Called once per
        calendar day by LearningScheduler._retrain_cycle_inner().
        """
        from datetime import date
        from sqlalchemy import text as _text
        try:
            async with db.get_session() as session:
                rows = await session.execute(_text("""
                    SELECT bot_name,
                           COUNT(*) AS trades,
                           COALESCE(SUM(realized_pnl), 0) AS total_pnl,
                           COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins
                    FROM trade_events
                    WHERE event_time >= CURRENT_DATE
                      AND realized_pnl IS NOT NULL
                      AND event_type IN ('EXIT', 'RESOLUTION')
                    GROUP BY bot_name
                    ORDER BY bot_name
                """))
                data = rows.fetchall()
        except Exception as e:
            logger.warning("daily_pnl_summary_query_failed", error=str(e))
            return
        if not data:
            return
        lines = [f"Daily PnL Summary ({date.today().isoformat()})"]
        total_pnl = 0.0
        for row in data:
            wr = round(row.wins / row.trades * 100, 1) if row.trades else 0.0
            lines.append(f"• {row.bot_name}: {row.trades} trades, ${row.total_pnl:+.2f}, {wr}% win")
            total_pnl += row.total_pnl
        lines.append(f"Total: ${total_pnl:+.2f}")
        await self.send_alert(
            title="Daily PnL Summary",
            message="\n".join(lines),
            severity=AlertSeverity.INFO,
            source="daily_pnl_summary",
        )
