import warnings
from datetime import datetime, timedelta

import psycopg2
import requests
import yfinance as yf
from constants import API_KEY, DATABASE_URL

warnings.filterwarnings("ignore")


def fetch_products_from_db():
    """Fetch product symbols from the database."""
    products = []
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT ProductID, Symbol FROM Products where sector not in ('Cryptocurrency');"
        )
        products = cur.fetchall()
        cur.close()
    except Exception as error:
        print(f"E1: {error}")
    finally:
        if conn is not None:
            conn.close()
    return products


def fetch_crypto_from_db():
    """Fetch product symbols from the database."""
    products = []
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT ProductID, info FROM Products where sector in ('Cryptocurrency');"
        )
        products = cur.fetchall()
        cur.close()
    except Exception as error:
        print(f"E2: {error}")
    finally:
        if conn is not None:
            conn.close()
    return products


def fetch_crypto_historical_data(crypto_id, vs_currency="usd", weeks=520):
    """
    Fetch historical data for a given cryptocurrency from the CoinGecko API.

    :param crypto_id: ID of the cryptocurrency (e.g., 'bitcoin', 'ethereum')
    :param vs_currency: The target currency of market data (e.g., 'usd', 'eur')
    :param days: The number of days to fetch historical data for (max 90 days)
    :return: A list of daily prices and market caps.
    """
    url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart/range"
    from_date = datetime.today() - timedelta(weeks=weeks)
    to_date = datetime.today()
    params = {
        "vs_currency": vs_currency,
        "from": from_date.timestamp(),
        "to": to_date.timestamp(),
    }
    response = requests.get(url, params=params)
    data = response.json()

    # Extracting prices and market_caps
    prices = data.get("prices", [])
    market_caps = data.get("market_caps", [])
    total_volumes = data.get("total_volumes", [])

    # Converting timestamps to readable dates
    historical_data = []
    for price, market_cap, volume in zip(prices, market_caps, total_volumes):
        date = datetime.utcfromtimestamp(price[0] / 1000).strftime("%Y-%m-%d")
        historical_data.append(
            {
                "date": date,
                "price": price[1],
                "market_cap": market_cap[1],
                "volume": volume[1],
            }
        )
    historical_data.sort(key=lambda x: x["date"])

    previous_datum = None
    new_historical_data = []
    for datum in historical_data:
        if previous_datum:
            new_datum = {
                "date": datum["date"],
                "closingprice": datum["price"],
                "market_cap": datum["market_cap"],
                "volume": datum["volume"],
                "openingprice": previous_datum["price"],
            }
            new_historical_data.append(new_datum)
        else:
            new_datum = {
                "date": datum["date"],
                "closingprice": datum["price"],
                "market_cap": datum["market_cap"],
                "volume": datum["volume"],
                "openingprice": datum["price"],
            }

            new_historical_data.append(new_datum)
        previous_datum = datum

    return new_historical_data


def insert_crypto_market_data_to_db(product_id, data):
    """Insert market data into the MarketData table."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        for entry in data:
            cur.execute(
                """
                INSERT INTO MarketData (ProductID, Date, OpeningPrice, ClosingPrice, Volume)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ProductID, Date) DO NOTHING;
            """,
                (
                    product_id,
                    entry["date"],
                    entry["openingprice"],
                    entry["closingprice"],
                    entry["volume"],
                ),
            )
        conn.commit()
        cur.close()
    finally:
        if conn is not None:
            conn.close()


def update_eod_data(product_id, symbol):
    end_date = datetime.now().date()
    # if the end_date is a saturday or sunday then we need to fetch the data for the previous Friday
    # if end_date.weekday() == 5:
    #     end_date -= timedelta(days=1)
    # elif end_date.weekday() == 6:
    #     end_date -= timedelta(days=2)

    # end_date + timedelta(days=1)

    last_recorded_date = get_last_recorded_date(product_id)
    if last_recorded_date:
        # if the last_recorded_date is the same as the end_date then we don't need to fetch any data
        if last_recorded_date.strftime("%Y-%m-%d") == end_date.strftime("%Y-%m-%d"):
            return
        start_date = last_recorded_date
    else:
        start_date = end_date - timedelta(
            days=5 * 365
        )  # Default to last 365 days if no record exists

    print(f"Fetching data for {symbol} {start_date} to {end_date}...")
    stock = yf.Ticker(symbol)
    hist = stock.history(start=start_date, end=end_date)

    with psycopg2.connect(DATABASE_URL) as conn:
        for index, row in hist.iterrows():
            date = index.date()
            open_price = row["Open"]
            high_price = row["High"]
            low_price = row["Low"]
            close_price = row["Close"]
            volume = row["Volume"]

            # Here you would insert or update the data in your database
            # Example insert statement (you need to adjust according to your schema):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO MarketData (ProductId, Date, OpeningPrice, HighPrice, LowPrice, ClosingPrice, Volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ProductId, Date) DO UPDATE
                    SET OpeningPrice = EXCLUDED.OpeningPrice,
                        HighPrice = EXCLUDED.HighPrice,
                        LowPrice = EXCLUDED.LowPrice,
                        ClosingPrice = EXCLUDED.ClosingPrice,
                        Volume = EXCLUDED.Volume;
                """,
                    (
                        product_id,
                        date,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ),
                )
                conn.commit()


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
        with psycopg2.connect(DATABASE_URL) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                           DELETE from MarketMovement;
                           """
                )
                cur.execute(query)
                # Fetch and return the result
                connection.commit()
    except Exception as e:
        print(f"(E06) An error occurred: {e}")


def get_last_recorded_date(product_id):
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(Date) FROM MarketData WHERE ProductID = %s;
            """,
                (product_id,),
            )
            result = cur.fetchone()
            if result and result[0]:
                return result[0]
            else:
                return None


def update():
    products = fetch_products_from_db()
    # sort products by symbol
    products.sort(key=lambda x: x[1])
    for product_id, symbol in products:
        update_eod_data(product_id, symbol)

    for product_id, info in fetch_crypto_from_db():
        crypto_id = info["id"]
        data = fetch_crypto_historical_data(crypto_id)
        insert_crypto_market_data_to_db(product_id, data)

    compute_advance_decline_table()


if __name__ == "__main__":
    update()
