from __future__ import annotations

import os
from typing import Any

import requests


class TelegramNotifier:
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
        message = self.build_message(event)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception:
            return False
