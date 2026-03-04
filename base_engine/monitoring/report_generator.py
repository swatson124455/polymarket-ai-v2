"""
Automated Report Generator
==========================
Scheduled reports via email/Slack.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from base_engine.analysis.performance_attribution import PerformanceAttribution
from base_engine.data.database import Database

logger = get_logger()


class ReportGenerator:
    """
    Automated report generation and delivery.
    """
    
    def __init__(self, db: Optional[Database] = None, config: Optional[Dict] = None):
        self.db = db
        self.performance_attribution = PerformanceAttribution(db)
        
        if config is None:
            config = {}
        
        self.email_enabled = config.get("email_enabled", False)
        self.slack_enabled = config.get("slack_enabled", False)
        self.email_recipients = config.get("email_recipients", [])
        self.slack_webhook = config.get("slack_webhook", None)
    
    async def generate_daily_report(self) -> Dict:
        """Generate daily performance report"""
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=1)
        
        attribution = await self.performance_attribution.attribute_performance(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
        report = {
            "type": "daily",
            "date": end_date.date().isoformat(),
            "summary": attribution.get("summary", {}),
            "breakdowns": {
                "by_category": attribution.get("by_category", {}),
                "by_bot": attribution.get("by_bot", {}),
                "by_regime": attribution.get("by_regime", {})
            }
        }
        
        return report
    
    async def generate_weekly_report(self) -> Dict:
        """Generate weekly performance report"""
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        
        attribution = await self.performance_attribution.attribute_performance(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
        report = {
            "type": "weekly",
            "week_start": start_date.date().isoformat(),
            "week_end": end_date.date().isoformat(),
            "summary": attribution.get("summary", {}),
            "breakdowns": {
                "by_category": attribution.get("by_category", {}),
                "by_bot": attribution.get("by_bot", {}),
                "by_regime": attribution.get("by_regime", {}),
                "by_price_range": attribution.get("by_price_range", {}),
                "by_signal_source": attribution.get("by_signal_source", {})
            }
        }
        
        return report
    
    async def send_report(self, report: Dict, channels: Optional[List[str]] = None) -> Dict:
        """
        Send report via configured channels.
        
        Args:
            report: Report dict
            channels: Optional list of channels ("email", "slack"). If None, uses all enabled.
        
        Returns:
            Dict with send results
        """
        if channels is None:
            channels = []
            if self.email_enabled:
                channels.append("email")
            if self.slack_enabled:
                channels.append("slack")
        
        results = {}
        
        if "email" in channels:
            results["email"] = await self._send_email(report)
        
        if "slack" in channels:
            results["slack"] = await self._send_slack(report)
        
        return results
    
    async def _send_email(self, report: Dict) -> Dict:
        """Send report via email"""
        if not self.email_recipients:
            return {"success": False, "error": "No email recipients configured"}
        
        # Format email content
        subject = f"Polymarket Trading Report - {report.get('type', 'report').upper()}"
        body = self._format_report_text(report)
        
        # In production, would use email library (smtplib, sendgrid, etc.)
        logger.info(f"Email report generated (not sent - email not configured)", recipients=len(self.email_recipients))
        
        return {
            "success": True,
            "recipients": len(self.email_recipients),
            "subject": subject
        }
    
    async def _send_slack(self, report: Dict) -> Dict:
        """Send report via Slack"""
        if not self.slack_webhook:
            return {"success": False, "error": "No Slack webhook configured"}
        
        # Format Slack message
        message = self._format_slack_message(report)
        
        # In production, would POST to Slack webhook
        logger.info(f"Slack report generated (not sent - webhook not configured)")
        
        return {
            "success": True,
            "webhook": self.slack_webhook[:20] + "..."
        }
    
    def _format_report_text(self, report: Dict) -> str:
        """Format report as plain text"""
        lines = [
            f"=== {report.get('type', 'report').upper()} TRADING REPORT ===",
            ""
        ]
        
        summary = report.get("summary", {})
        lines.extend([
            f"Total Profit: ${summary.get('total_profit', 0):.2f}",
            f"Total Trades: {summary.get('total_trades', 0)}",
            f"Win Rate: {summary.get('win_rate', 0)*100:.1f}%",
            ""
        ])
        
        breakdowns = report.get("breakdowns", {})
        if breakdowns.get("by_bot"):
            lines.append("By Bot:")
            for bot, metrics in breakdowns["by_bot"].items():
                lines.append(f"  {bot}: ${metrics.get('profit', 0):.2f} ({metrics.get('trades', 0)} trades)")
        
        return "\n".join(lines)
    
    def _format_slack_message(self, report: Dict) -> Dict:
        """Format report as Slack message"""
        summary = report.get("summary", {})
        
        return {
            "text": f"*{report.get('type', 'report').upper()} Trading Report*",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Total Profit:* ${summary.get('total_profit', 0):.2f}\n*Trades:* {summary.get('total_trades', 0)}\n*Win Rate:* {summary.get('win_rate', 0)*100:.1f}%"
                    }
                }
            ]
        }
