import logging as log
import warnings
from datetime import datetime, timedelta

import yfinance as yf
from sqlalchemy import text

from database import Session

warnings.filterwarnings("ignore")


def fetch_products_from_db():
    """Fetch product symbols from the database."""
    products = []
    with Session() as session:
        statement = text(
            """
            SELECT ProductID, Symbol FROM Products;
            """
        )

        result = session.execute(statement)
        products = result.fetchall()
    return products


def update_eod_data(product_id, symbol):
    end_date = (datetime.now() + timedelta(days=1)).date()

    last_recorded_date = get_last_recorded_date(product_id)
    if last_recorded_date:
        # if the last_recorded_date is the same as the end_date then we don't need to fetch any data
        if last_recorded_date.strftime("%Y-%m-%d") == end_date.strftime("%Y-%m-%d"):
            return (last_recorded_date, last_recorded_date)
        start_date = last_recorded_date
    else:
        start_date = end_date - timedelta(
            days=5 * 365
        )  # Default to last 365 days if no record exists

    stock = yf.Ticker(symbol)
    hist = stock.history(start=start_date, end=end_date)

    first_date = None
    last_date = None
    with Session() as session:
        for index, row in hist.iterrows():
            date = index.date()  # type: ignore
            if first_date is None:
                first_date = date
                last_date = date
            if date < first_date:
                first_date = date
            if date > last_date:
                last_date = date
            open_price = row["Open"]
            high_price = row["High"]
            low_price = row["Low"]
            close_price = row["Close"]
            volume = row["Volume"]

            # Here you would insert or update the data in your database
            # Example insert statement (you need to adjust according to your schema):
            statement = text(
                """
                INSERT INTO MarketData (ProductID, Date, OpeningPrice, HighPrice, LowPrice, ClosingPrice, Volume)
                VALUES (:product_id, :date, :open_price, :high_price, :low_price, :close_price, :volume)
                ON CONFLICT (ProductID, Date) DO UPDATE
                SET OpeningPrice = EXCLUDED.OpeningPrice,
                    HighPrice = EXCLUDED.HighPrice,
                    LowPrice = EXCLUDED.LowPrice,
                    ClosingPrice = EXCLUDED.ClosingPrice,
                    Volume = EXCLUDED.Volume;
            """
            )
            session.execute(
                statement,
                {
                    "product_id": product_id,
                    "date": date,
                    "open_price": open_price,
                    "high_price": high_price,
                    "low_price": low_price,
                    "close_price": close_price,
                    "volume": volume,
                },
            )
            session.commit()
    return first_date, last_date


def compute_advance_decline_table():
    """
    Compute a table showing the date, the number of products that have advanced,
    and the number that have declined since the previous trading day.
    """
    query = """
    WITH RankedPrices AS (
        SELECT
            Date,
            ProductID,
            ClosingPrice,
            LAG(ClosingPrice) OVER (PARTITION BY ProductID ORDER BY Date) AS PreviousClosingPrice
        FROM MarketData
    ),
    DailyChanges AS (
        SELECT
            Date,
            SUM(CASE WHEN ClosingPrice > PreviousClosingPrice THEN 1 ELSE 0 END) AS Advanced,
            SUM(CASE WHEN ClosingPrice < PreviousClosingPrice THEN 1 ELSE 0 END) AS Declined
        FROM RankedPrices
        WHERE PreviousClosingPrice IS NOT NULL  -- Exclude the first day for each product
        GROUP BY Date
        ORDER BY Date
    )
    insert into MarketMovement SELECT * FROM DailyChanges;
    """

    try:
        with Session() as session:
            statement = text(
                """
                DELETE from MarketMovement;
                """
            )
            session.execute(statement)
            statement = text(query)
            session.execute(statement)
            session.commit()
    except Exception as e:
        log.error(f"(E06) An error occurred: {e}")


def get_last_recorded_date(product_id):
    with Session() as session:
        statement = text(
            """
            SELECT MAX(Date) FROM MarketData WHERE ProductID = :product_id;
        """
        )
        result = session.execute(statement, {"product_id": product_id}).first()
        if result and result[0]:
            return result[0]
        else:
            return None


def update():
    log.info("Updating market data...")
    products = list(fetch_products_from_db())
    # sort products by symbol
    products.sort(key=lambda x: x[1])
    earliest = None
    latest = None
    for product_id, symbol in products:
        first, last = update_eod_data(product_id, symbol)
        if earliest is None:
            earliest = first
            latest = last
        else:
            if first and first < earliest:
                earliest = first
            if last and last > latest:
                latest = last
    log.info(
        f"Equity market data fetched Earliest date: {earliest} Latest date: {latest}"
    )

    compute_advance_decline_table()


if __name__ == "__main__":
    update()
