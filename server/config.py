import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Risk management
MAX_DAILY_DRAWDOWN_PCT: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "3.0"))
MAX_OPEN_TRADES: int = int(os.getenv("MAX_OPEN_TRADES", "2"))

# API authentication — MT5 EA must send this in X-API-Key header
# Leave empty to disable authentication (not recommended for production)
API_KEY: str = os.getenv("API_KEY", "")

# Active trading pairs — comma-separated list
# Each pair needs: profile in pair_profiles.py + EA attached to chart in MT5
ACTIVE_PAIRS: list[str] = [
    p.strip() for p in os.getenv("ACTIVE_PAIRS", "GBPJPY").split(",") if p.strip()
]

# Analysis model — switch between Opus and Sonnet 4.5 for A/B testing
# Default: Opus. Set to "claude-sonnet-4-5-20250929" to test with Sonnet.
ANALYSIS_MODEL: str = os.getenv("ANALYSIS_MODEL", "claude-opus-4-20250514")
