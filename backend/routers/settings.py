from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from backend.database import PROJECT_DIR

router = APIRouter(prefix="/api/settings", tags=["settings"])
CONFIG_PATH = PROJECT_DIR / "dashboard.config.json"
DEFAULT_STOCKS = ["NASDAQ:NVDA", "NASDAQ:AMD", "NYSE:TSM", "NASDAQ:ASML", "AMEX:SPY", "NASDAQ:QQQ", "BINANCE:BTCUSDT", "BINANCE:ETHUSDT"]


class SettingsUpdate(BaseModel):
    title: str | None = None
    theme: str | None = None
    accent: str | None = None
    stocks: list[str] | None = None


def _config() -> dict:
    if not CONFIG_PATH.exists():
        return {"title": "lab.local", "theme": "dark", "accent": "#ffffff", "agents": [], "newsletter_sources": [], "stocks": DEFAULT_STOCKS}
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("stocks", DEFAULT_STOCKS)
    return cfg


def _write(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


@router.get("")
def get_settings() -> dict:
    cfg = _config()
    return {
        "title": cfg.get("title", "lab.local"),
        "theme": cfg.get("theme", "dark"),
        "accent": cfg.get("accent", "#ffffff"),
        "stocks": cfg.get("stocks", DEFAULT_STOCKS),
        "newsletter_sources": cfg.get("newsletter_sources", []),
        "agents": cfg.get("agents", []),
    }


@router.put("")
def update_settings(payload: SettingsUpdate) -> dict:
    cfg = _config()
    if payload.title is not None:
        cfg["title"] = payload.title.strip() or "lab.local"
    if payload.theme is not None:
        cfg["theme"] = payload.theme
    if payload.accent is not None:
        cfg["accent"] = payload.accent
    if payload.stocks is not None:
        cfg["stocks"] = [ticker.strip().upper() for ticker in payload.stocks if ticker.strip()]
    _write(cfg)
    return {"ok": True, "settings": get_settings()}


@router.get("/stocks")
def get_stocks() -> dict:
    cfg = _config()
    return {"symbols": cfg.get("stocks", DEFAULT_STOCKS)}
