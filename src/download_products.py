import json
from datetime import datetime, timedelta

import requests
import yfinance as yf
from bs4 import BeautifulSoup, Tag
from sqlalchemy import text

import update_eod_data
from constants import INDEX_SYMBOLS
from database import Session

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
    with Session() as session:
        # Insert into the database, avoiding duplicates
        info_data = json.loads(product_info["info"])
        statement = text(
            """
        INSERT INTO Products (Symbol, CompanyName, Sector, Market, IsActive, dividend_rate, info, createddate)
        VALUES (:symbol, :company_name, :sector, :market, :is_active, :dividend_rate, :info, :createddate)
        ON CONFLICT (Symbol) do update set IsActive = EXCLUDED.IsActive, dividend_rate=EXCLUDED.dividend_rate, info=EXCLUDED.info,
        companyname=EXCLUDED.companyname, sector=EXCLUDED.sector, market=EXCLUDED.market;

        """
        )
        if "exchange" in info_data:
            exchange = info_data["exchange"]
            if exchange == "NMS":
                market = "NASDAQ"
            elif exchange == "NYQ":
                market = "NYSE"
            elif exchange == "PCX":
                market = "NYSEARCA"
            elif exchange == "BTS":
                market = "BATS"
            elif exchange == "NGM":
                market = "NASDAQ"
            elif exchange == "CCC":
                market = "Cryptocurrency"
            else:
                market = exchange
        else:
            market = "Unknown"

        if market == "Cryptocurrency":
            sector = "Cryptocurrency"
        else:
            sector = product_info["sector"]
        session.execute(
            statement,
            {
                "symbol": product_info["symbol"],
                "company_name": product_info["company_name"],
                "sector": sector,
                "market": market,
                "is_active": active,
                "dividend_rate": (
                    info_data["dividendRate"] if "dividendRate" in info_data else None
                ),
                "info": product_info["info"],
                "createddate": datetime.today(),
            },
        )
        session.commit()
        print(f"Inserted {product_info['symbol']} into the database.")


def insert_crypto_into_db(coins, active=True):
    """Insert product information into the PostgreSQL database."""
    with Session() as session:
        for coin in coins:
            statement = text(
                """
                INSERT INTO Products (Symbol, CompanyName, Sector, Market, IsActive, info, createddate)
                VALUES (:symbol, :company_name, :sector, :market, :is_active, :info, :createddate)
                ON CONFLICT (Symbol) do update set IsActive = EXCLUDED.IsActive, info=EXCLUDED.info,
                companyname=EXCLUDED.companyname, sector=EXCLUDED.sector, market=EXCLUDED.market;
            """
            )
            session.execute(
                statement,
                {
                    "symbol": coin["symbol"],
                    "company_name": coin["company_name"],
                    "sector": coin["sector"],
                    "market": coin["market"],
                    "is_active": active,
                    "info": coin["info"],
                    "createddate": datetime.today(),
                },
            )
            session.commit()
            print(f"Inserted {coin['symbol']} into the database.")


# get the the row from the products table corresponding to the symbol
def get_product(symbol):
    with Session() as session:
        statement = text(
            """
            SELECT * FROM Products WHERE Symbol = :symbol
        """
        )
        product = session.execute(statement, {"symbol": symbol}).first()
        return product


def download_products():
    # Check if symbols are provided as command line arguments
    # if len(sys.argv) < 2:
    #     print("Usage: python script.py SYMBOL1 SYMBOL2 ...")
    #     sys.exit(1)
    # coins = []
    # for id in VENMO_SUPPORTED_CURRENCY_IDS:
    #     coin = fetch_crypto_info(id)
    #     symbol = coin["symbol"]
    #     coins.append(coin)

    # insert_crypto_into_db(coins)

    symbols = []
    symbols += fetch_nasdaq100_symbols_wikipedia()
    symbols += fetch_sp500_symbols_wikipedia()
    symbols += fetch_dji_symbols_wikipedia()
    symbols += ["pyusd-usd", "eth-usd", "ltc-usd", "bch-usd", "btc-usd"]
    symbols = list(set(symbols))
    symbols.sort()
    for symbol in symbols:
        product = get_product(symbol)
        if (
            product
            and product[6].date() >= (datetime.today() - timedelta(days=5)).date()
        ):
            continue
        product_info = fetch_stock_info(symbol)
        if product_info:
            insert_product_into_db(product_info)

    for index in INDEX_SYMBOLS:
        product = get_product(symbol)
        if (
            product
            and product[6].date() >= (datetime.today() - timedelta(days=5)).date()
        ):
            continue
        product_info = fetch_stock_info(index)
        if product_info:
            insert_product_into_db(product_info, False)
    # if the end_date is before 7pm we need to fetch the data for the previous day


if __name__ == "__main__":
    download_products()
