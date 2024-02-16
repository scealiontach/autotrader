from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from types import NoneType
from typing import Union

from cachetools import LFUCache

product_cache = LFUCache(maxsize=1024)


class Product:
    def __init__(
        self,
        connection,
        product_id: Union[int, NoneType] = None,
        symbol: Union[str, NoneType] = None,
    ):
        self.connection = connection
        self.closing_cache_hits = 0
        self.quantity_cache_hits = 0
        self.symbol: str
        if not product_id and not symbol:
            raise ValueError("Either product_id or symbol must be provided")
        if product_id:
            self.product_id = product_id
            self.refresh_by_product_id()
        elif symbol:
            self.symbol = symbol
            self.refresh_by_symbol()

    @staticmethod
    @lru_cache(maxsize=1024)
    def from_id(connection, product_id: int):
        return Product(connection, product_id=product_id)

    @staticmethod
    @lru_cache(maxsize=1024)
    def from_symbol(connection, symbol: str):
        return Product(connection, symbol=symbol)

    def refresh_by_product_id(self):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT Symbol, CompanyName, Sector, Market, IsActive, CreatedDate, Dividend_Rate, info, ProductId
                from Products where ProductID = %s
                """,
                (self.product_id,),
            )
            result = cur.fetchone()
            self.symbol = result[0]
            self.company_name = result[1]
            self.sector = result[2]
            self.market = result[3]
            self.is_active = result[4]
            self.created_date = result[5]
            self.dividend_rate = result[6]
            self.info = result[7]

    def refresh_by_symbol(self):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT Symbol, CompanyName, Sector, Market, IsActive, CreatedDate, Dividend_Rate, info, ProductId
                from Products where Symbol = %s
                """,
                (self.symbol,),
            )
            result = cur.fetchone()
            self.company_name = result[1]
            self.sector = result[2]
            self.market = result[3]
            self.is_active = result[4]
            self.created_date = result[5]
            self.dividend_rate = result[6]
            self.info = result[7]
            self.product_id = result[8]

    def fetch_last_closing_price(self, as_of_date) -> Union[Decimal, NoneType]:
        """
        Fetch the last closing price for a given product as of or before a given day.

        :param product_id: The ID of the product.
        :param as_of_date: The date before which to find the last closing price (datetime.date object).
        :return: The last closing price as a float, or None if not found.
        """
        cache_key = f"{self.product_id}-closing-{as_of_date}"
        if cache_key in product_cache:
            self.closing_cache_hits += 1
            return product_cache[cache_key]

        closing_price = None
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT ClosingPrice
                FROM MarketData
                WHERE ProductID = %s AND Date > %s AND Date <= %s
                ORDER BY Date DESC
                LIMIT 1
            """,
                (self.product_id, as_of_date - timedelta(days=4), as_of_date),
            )
            result = cur.fetchone()
            if result and result[0]:
                closing_price = result[0]
        product_cache[cache_key] = closing_price
        return closing_price

    def fetch_current_quantity(self, as_of_date) -> Decimal:
        """
        Fetch the current quantity of shares owned for a given stock symbol.

        :param symbol: The stock symbol to query.
        :return: The quantity of shares currently owned for the symbol.
        """
        cache_key = f"{self.product_id}-quantity-{as_of_date}"
        if cache_key in product_cache:
            self.quantity_cache_hits += 1
            print(f"Cache hit for {cache_key} {self.quantity_cache_hits}")
            return product_cache[f"{self.product_id}-quantity"]
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT sum(pp.Quantity)
                FROM PortfolioPositions pp
                WHERE pp.ProductId = %s;
            """,
                (self.product_id,),
            )
            result = cur.fetchone()
            if result and result[0]:
                product_cache[cache_key] = result[0]
                return result[0]
            else:
                product_cache[cache_key] = 0
                return Decimal(
                    0
                )  # Return 0 if the symbol is not found in the portfolio
