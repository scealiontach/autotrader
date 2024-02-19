from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from types import NoneType
from typing import Optional, Union

from sqlalchemy.orm import Mapped, mapped_column

from cachetools import LFUCache
from models import Base
from sqlalchemy import DECIMAL, JSON, Boolean, Date, Integer, String, text

from database import Session

product_cache = LFUCache(maxsize=4096)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column("productid", Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(
        "companyname", String(255), unique=False, nullable=True
    )
    sector: Mapped[Optional[str]] = mapped_column(
        String(255), unique=False, nullable=True
    )
    market: Mapped[Optional[str]] = mapped_column(
        String(255), unique=False, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        "isactive", Boolean, unique=False, nullable=False, default=True
    )
    dividend_rate: Mapped[Optional[DECIMAL]] = mapped_column(
        DECIMAL(10, 2), unique=False, nullable=True
    )
    info: Mapped[Optional[JSON]] = mapped_column(JSON, unique=False, nullable=True)
    createddate: Mapped[Date] = mapped_column(
        Date, unique=False, nullable=False, default=datetime.today
    )

    @staticmethod
    def from_id(product_id: int):
        with Session() as session:
            product = session.query(Product).get(product_id)
            if product is None:
                raise ValueError(f"Product with ID {product_id} not found")
            return product

    @staticmethod
    def from_symbol(symbol: str):
        with Session() as session:
            return session.query(Product).filter(Product.symbol == symbol).first()

    @staticmethod
    def all_sectors() -> list[str]:
        with Session() as session:
            statement = text("SELECT DISTINCT Sector FROM Products")
            result = session.execute(statement)
            return [row[0] for row in result]

    def fetch_last_closing_price(self, as_of_date) -> Union[Decimal, NoneType]:
        """
        Fetch the last closing price for a given product as of or before a given day.

        :param product_id: The ID of the product.
        :param as_of_date: The date before which to find the last closing price (datetime.date object).
        :return: The last closing price as a float, or None if not found.
        """
        cache_key = f"{self.id}-closing-{as_of_date}"
        if cache_key in product_cache:
            return product_cache[cache_key]

        closing_price = None
        with Session() as session:
            statement = text(
                """
                            SELECT ClosingPrice
                            FROM MarketData
                            WHERE ProductID = :product_id AND Date > :from_date AND Date <= :to_date
                            ORDER BY Date DESC
                            LIMIT 1
                            """
            )
            result = session.execute(
                statement,
                {
                    "product_id": self.id,
                    "from_date": as_of_date - timedelta(days=4),
                    "to_date": as_of_date,
                },
            ).first()
            if result and result[0]:
                closing_price = result[0]
        product_cache[cache_key] = closing_price
        return closing_price
