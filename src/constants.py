from decimal import Decimal
import os

# The index symbols: "^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX", "SPY"
ALL_INDEXES = ["^GSPC", "^DJI", "^IXIC", "^RUT", "^VIX"]
INDEX_SYMBOLS = ["^GSPC"]

# Example: "dbname=mydatabase user=myuser password=mypassword host=localhost"
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set")

# Environment variables for database connection and API key
API_KEY = os.getenv("EOD_HISTORICAL_DATA_API_KEY")

SELL_TX_FEE = Decimal(0)
BUY_TX_FEE = Decimal(0)
REQUIRED_HOLDING_DAYS = 1

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"
