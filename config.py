import os

POLL_INTERVAL      = int(os.getenv("POLL_INTERVAL", 3600))
MARKET_LIMIT       = int(os.getenv("MARKET_LIMIT", 100))
TRADE_WINDOW_HOURS = int(os.getenv("TRADE_WINDOW_HOURS", 1))
MIN_TRADE_SIZE     = float(os.getenv("MIN_TRADE_SIZE", 5000))
OLLAMA_ENDPOINT    = os.getenv("OLLAMA_ENDPOINT", "http://sunils-mac-studio:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")
DB_PATH            = os.getenv("DB_PATH", "./polymarket.db")
