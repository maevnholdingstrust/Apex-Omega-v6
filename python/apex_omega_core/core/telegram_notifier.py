from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class EventNotifierBase(ABC):
    """Pluggable interface for execution-lifecycle notifications.

    Concrete implementations (e.g. :class:`TelegramNotifier`) are injected
    into :class:`~.contract_invoker.ContractInvoker` so that the trading
    path is never hard-coupled to a specific outbound transport.  Deployments
    that do not require a particular transport should use :class:`NullNotifier`.
    """

    @abstractmethod
    def send_event(self, event: dict[str, Any]) -> bool:
        """Dispatch a lifecycle event notification.

        Returns ``True`` on success, ``False`` on failure or when disabled.
        Implementations *must not* raise — any error should be swallowed and
        logged at WARNING level.
        """


class NullNotifier(EventNotifierBase):
    """No-op notifier used when no outbound transport is configured."""

    def send_event(self, event: dict[str, Any]) -> bool:
        return False


class TelegramNotifier(EventNotifierBase):
    """Sends execution-lifecycle events to a Telegram chat.

    Requires the ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` environment
    variables (or explicit constructor arguments) to be set.  When either
    value is absent the notifier silently disables itself so that callers
    never need to guard against a missing configuration.

    .. note::
        This implementation makes an outbound HTTP call to
        ``api.telegram.org``.  If that domain is not listed in the deployment
        network allowlist, substitute a different :class:`EventNotifierBase`
        implementation instead.
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)

    def build_message(self, event: dict[str, Any]) -> str:
        status = str(event.get("status") or "").lower()
        chain_name = event.get("chain_name") or f"Chain-{event.get('chain_id')}"
        token_pair = event.get("token_pair") or "unknown"
        explorer_url = event.get("explorer_url") or "n/a"
        expected_profit = event.get("expected_profit_usd")
        gas_used = event.get("gas_used")
        reasons = event.get("rejection_reasons") or []

        if status == "submitted":
            return (
                "TX SUBMITTED\n"
                f"Chain: {chain_name}\n"
                f"Pair: {token_pair}\n"
                f"Expected Profit: ${expected_profit}\n"
                f"Tx: {explorer_url}"
            )
        if status == "confirmed":
            return (
                "TX CONFIRMED\n"
                f"Chain: {chain_name}\n"
                f"Pair: {token_pair}\n"
                f"Gas Used: {gas_used}\n"
                f"Tx: {explorer_url}"
            )
        if status == "reverted":
            return (
                "TX REVERTED\n"
                f"Chain: {chain_name}\n"
                f"Pair: {token_pair}\n"
                f"Tx: {explorer_url}"
            )
        if status == "dry_run":
            return (
                "TX DRY-RUN PAYLOAD BUILT\n"
                f"Chain: {chain_name}\n"
                f"Pair: {token_pair}"
            )
        return (
            "TX REJECTED\n"
            f"Chain: {chain_name}\n"
            f"Pair: {token_pair}\n"
            f"Reason: {', '.join(str(x) for x in reasons) if reasons else 'unknown'}"
        )

    def send_event(self, event: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        try:
            import requests  # imported lazily so the module is usable without requests

            message = self.build_message(event)
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message}
            resp = requests.post(url, json=payload, timeout=3)
            return resp.ok
        except Exception as exc:
            logger.warning("TelegramNotifier.send_event failed: %s", exc)
            return False


def build_notifier() -> EventNotifierBase:
    """Factory that returns a :class:`TelegramNotifier` when credentials are
    present in the environment, otherwise a :class:`NullNotifier`.

    Callers that want a different transport should construct their own
    :class:`EventNotifierBase` subclass and pass it directly to
    :class:`~.contract_invoker.ContractInvoker`.
    """
    notifier = TelegramNotifier()
    if notifier.enabled:
        return notifier
    return NullNotifier()
