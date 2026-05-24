"""
Webhook dispatcher (#39) - push events to configurable URLs.

Load webhook configs from DB (webhook_config table) and POST JSON on events.
"""
import asyncio
import hmac
import hashlib
from typing import Any, Dict, List, Optional
from structlog import get_logger
import httpx

logger = get_logger()


class WebhookDispatcher:
    """
    Dispatch event payloads to configured webhook URLs.

    Events: market_resolved, big_trade, anomaly_detected, sync_failed, etc.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _sign_payload(self, payload: str, secret: Optional[str]) -> Optional[str]:
        if not secret:
            return None
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    async def dispatch(
        self,
        event_type: str,
        payload: Dict[str, Any],
        url_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Send event to all webhooks configured for this event_type.

        If url_override is set, send only to that URL (for testing or one-off).
        Returns list of { "url": str, "status_code": int or None, "error": str or None }.
        """
        results: List[Dict[str, Any]] = []
        if url_override:
            configs = [{"id": 0, "event_type": event_type, "url": url_override, "secret": None, "active": True}]
        elif self.db:
            configs = await self.db.get_webhook_configs(event_type=event_type, active_only=True)
        else:
            return results

        if not configs:
            return results

        body = {"event": event_type, "payload": payload}
        import json
        body_str = json.dumps(body, default=str)

        client = await self._get_client()
        for cfg in configs:
            url = cfg.get("url") or url_override
            if not url:
                continue
            secret = cfg.get("secret")
            headers = {"Content-Type": "application/json"}
            sig = self._sign_payload(body_str, secret)
            if sig:
                headers["X-Webhook-Signature"] = f"sha256={sig}"
            try:
                resp = await client.post(url, content=body_str, headers=headers)
                results.append({"url": url, "status_code": resp.status_code, "error": None})
                if resp.status_code >= 400:
                    logger.warning("webhook delivery failed", url=url, status=resp.status_code, event=event_type)
            except Exception as err:
                logger.warning("webhook request failed", url=url, error=str(err), event=event_type)
                results.append({"url": url, "status_code": None, "error": str(err)})

        return results

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
