import datetime
import json

import psycopg2
import requests
import update_eod_data
import yfinance as yf
from bs4 import BeautifulSoup, Tag
from constants import DATABASE_URL, INDEX_SYMBOLS

HTML_PARSER = "html.parser"
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKIPEDIA_DJIA_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"
WIKIPEDIA_NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
SLICKCHARTS_SP500_URL = "https://www.slickcharts.com/sp500"

COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"

VENMO_SUPPORTED_CURRENCY_IDS = [
    "paypal-usd",
    "bitcoin",
    "ethereum",
    "litecoin",
    "bitcoin-cash",
]


def fetch_crypto_list():
    try:
        response = requests.get(COINGECKO_COINS_LIST_URL)
        response.raise_for_status()  # Raises a HTTPError if the status is 4xx, 5xx
        cryptocurrencies = response.json()
        return cryptocurrencies
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return []


def fetch_sp500_symbols(url=SLICKCHARTS_SP500_URL):
    try:
        # Fetch the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad responses

        # Parse the HTML content
        soup = BeautifulSoup(response.text, HTML_PARSER)

        # Find all rows in the table containing the symbols
        # This depends on the specific structure of the webpage
        # As of my last update, symbols are in a table with rows <tr> and the symbol is in the second column <td>
        symbols = []
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 2:  # Ensure the row has enough columns
                symbol = cols[
                    2
                ].text.strip()  # Symbol is assumed to be in the third column
                symbols.append(symbol)

        return symbols
    except requests.RequestException as e:
        print(f"Request failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

    return []  # Return an empty list in case of failure


def fetch_sp500_symbols_wikipedia(
    url=WIKIPEDIA_SP500_URL,
):
    try:
        # Fetch the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad responses

        # Parse the HTML content
        soup = BeautifulSoup(response.text, HTML_PARSER)

        # The symbols are typically in the first table of the page under the 'Symbol' column
        # Find the table
        table = soup.find("table", {"id": "constituents"})

        # if table is not of type Tag raise an error
        if not isinstance(table, Tag):
            raise ValueError(f"Expected a Tag, but got {type(table)}")

        # Find all rows in the table, skip the header
        symbols = []
        for row in table.find_all("tr")[1:]:  # Skip the header row
            cols = row.find_all("td")
            if cols:  # If cols is not empty
                symbol = cols[
                    0
                ].text.strip()  # Symbol is assumed to be in the first column
                symbols.append(symbol.replace(".", "-"))

        return symbols
    except requests.RequestException as e:
        print(f"Request failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

    return []  # Return an empty list in case of failure


def fetch_dji_symbols_wikipedia(
    url=WIKIPEDIA_DJIA_URL,
):
    try:
        # Fetch the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad responses

        # Parse the HTML content
        soup = BeautifulSoup(response.text, HTML_PARSER)

        # The symbols are typically in the first table of the page under the 'Symbol' column
        # Find the table
        table = soup.find("table", {"id": "constituents"})
        # if table is not a Tag raise an error
        if not isinstance(table, Tag):
            raise ValueError(f"Expected a Tag, but got {type(table)}")

        # Find all rows in the table, skip the header
        symbols = []
        for row in table.find_all("tr")[1:]:  # Skip the header row
            cols = row.find_all("td")
            if cols:  # If cols is not empty
                symbol = cols[
                    1
                ].text.strip()  # Symbol is assumed to be in the first column
                symbols.append(symbol.replace(".", "-"))

        return symbols
    except requests.RequestException as e:
        print(f"Request failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

    return []  # Return an empty list in case of failure


def fetch_nasdaq100_symbols_wikipedia(
    url=WIKIPEDIA_NASDAQ100_URL,
):
    try:
        # Fetch the webpage content
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad responses

        # Parse the HTML content
        soup = BeautifulSoup(response.text, HTML_PARSER)

        # The symbols are typically in the first table of the page under the 'Symbol' column
        # Find the table
        table = soup.find("table", {"id": "constituents"})
        # if table is not a Tag raise an error
        if not isinstance(table, Tag):
            raise ValueError(f"Expected a Tag, but got {type(table)}")
        # Find all rows in the table, skip the header
        symbols = []
        for row in table.find_all("tr")[1:]:  # Skip the header row
            cols = row.find_all("td")
            if cols:  # If cols is not empty
                symbol = cols[
                    1
                ].text.strip()  # Symbol is assumed to be in the first column
                symbols.append(symbol.replace(".", "-"))

        return symbols
    except requests.RequestException as e:
        print(f"Request failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

    return []  # Return an empty list in case of failure


def fetch_stock_info(symbol):
    """Fetch product information from Yahoo Finance."""
    stock = yf.Ticker(symbol)
    info = stock.info

    return {
        "symbol": symbol,
        "company_name": info.get("longName"),
        "sector": info.get("sector"),
        "market": info.get("market"),
        "info": json.dumps(info),
    }


def fetch_crypto_info(coin_id):
    info = requests.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}").json()
    print(coin_id)
    product = {
        "symbol": info["symbol"],
        "company_name": info["name"],
        "sector": "Cryptocurrency",
        "market": "Cryptocurrency",
        "info": json.dumps(info),
    }
    return product


def insert_product_into_db(product_info, active=True):
    """Insert product information into the PostgreSQL database."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # Insert into the database, avoiding duplicates
        info_data = json.loads(product_info["info"])
        cur.execute(
            """
            INSERT INTO Products (Symbol, CompanyName, Sector, Market, IsActive, dividend_rate, info, createddate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (Symbol) do update set IsActive = EXCLUDED.IsActive, dividend_rate=EXCLUDED.dividend_rate, info=EXCLUDED.info;
        """,
            (
                product_info["symbol"],
                product_info["company_name"],
                product_info["sector"],
                product_info["market"],
                active,
                info_data["dividendRate"] if "dividendRate" in info_data else None,
                product_info["info"],
                datetime.datetime.today(),
            ),
        )
        conn.commit()
        cur.close()
        print(f"Inserted {product_info['symbol']} into the database.")
    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()


def insert_crypto_into_db(coins, active=True):
    """Insert product information into the PostgreSQL database."""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        for coin in coins:
            cur.execute(
                """
                INSERT INTO Products (Symbol, CompanyName, Sector, Market, IsActive, createddate, info)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (Symbol) do update set IsActive = EXCLUDED.IsActive;
            """,
                (
                    coin["symbol"],
                    coin["company_name"],
                    coin["sector"],
                    coin["market"],
                    active,
                    datetime.datetime.today(),
                    coin["info"],
                ),
            )
            print(f"Inserted {coin['symbol']} into the database.")
        conn.commit()
        cur.close()

    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()


# get the the row from the products table corresponding to the symbol
def get_product(symbol):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM Products WHERE Symbol = %s
        """,
            (symbol,),
        )
        product = cur.fetchone()
        cur.close()
        return product
    except psycopg2.DatabaseError as error:
        print(error)
    finally:
        if conn is not None:
            conn.close()


def download_products():
    # Check if symbols are provided as command line arguments
    # if len(sys.argv) < 2:
    #     print("Usage: python script.py SYMBOL1 SYMBOL2 ...")
    #     sys.exit(1)
    coins = []
    for id in VENMO_SUPPORTED_CURRENCY_IDS:
        coin = fetch_crypto_info(id)
        symbol = coin["symbol"]
        coins.append(coin)

    insert_crypto_into_db(coins)

    symbols = []
    symbols += fetch_nasdaq100_symbols_wikipedia()
    symbols += fetch_sp500_symbols_wikipedia()
    symbols += fetch_dji_symbols_wikipedia()
    symbols = list(set(symbols))
    symbols.sort()
    for symbol in symbols:
        product = get_product(symbol)
        if (
            product
            and product[6].date()
            >= (datetime.datetime.today() - datetime.timedelta(days=5)).date()
        ):
            continue
        product_info = fetch_stock_info(symbol)
        if product_info:
            insert_product_into_db(product_info)

    for index in INDEX_SYMBOLS:
        product = get_product(symbol)
        if (
            product
            and product[6].date()
            >= (datetime.datetime.today() - datetime.timedelta(days=5)).date()
        ):
            continue
        product_info = fetch_stock_info(index)
        if product_info:
            insert_product_into_db(product_info, False)
    # if the end_date is before 7pm we need to fetch the data for the previous day
    update_eod_data.update()


if __name__ == "__main__":
    download_products()
