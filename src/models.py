from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    DECIMAL,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column("portfolioid", Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    owner: Mapped[Optional[str]] = mapped_column(
        String(255), unique=False, nullable=True
    )
    createddate: Mapped[Date] = mapped_column(
        Date, unique=False, nullable=False, default=datetime.today
    )

    def __repr__(self):
        return "<Portfolio %r>" % self.name


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

    def __repr__(self):
        return "<Product %r>" % self.symbol


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column("transactionid", Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        "productid", ForeignKey("products.productid")
    )
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid")
    )
    quantity: Mapped[DECIMAL] = mapped_column(
        DECIMAL(14, 6), unique=False, nullable=False
    )
    price: Mapped[DECIMAL] = mapped_column(DECIMAL(10, 2), unique=False, nullable=False)
    transaction_date: Mapped[Date] = mapped_column(
        "transactiondate", Date, unique=False, nullable=False, default=datetime.today
    )
    transaction_type: Mapped[str] = mapped_column(
        "transactiontype", String(4), unique=False, nullable=False
    )

    def __repr__(self):
        return "<Transaction %r>" % self.id


class PortfolioPosition(Base):
    __tablename__ = "portfoliopositions"
    id: Mapped[int] = mapped_column("positionid", Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid")
    )
    product_id: Mapped[int] = mapped_column(
        "productid", ForeignKey("products.productid")
    )
    quantity: Mapped[DECIMAL] = mapped_column(
        DECIMAL(14, 6), unique=False, nullable=False
    )
    purchasedate: Mapped[Date] = mapped_column(
        "purchasedate", Date, unique=False, nullable=True
    )
    last_updated: Mapped[Date] = mapped_column(
        "lastupdated", Date, unique=False, nullable=False, default=datetime.today
    )
    last: Mapped[DECIMAL] = mapped_column(
        "last", DECIMAL(14, 6), unique=False, nullable=False
    )
    invest: Mapped[DECIMAL] = mapped_column(
        "invest", DECIMAL(14, 6), unique=False, nullable=False
    )

    def __repr__(self):
        return "<PortfolioPosition %r>" % self.id


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[int] = mapped_column("lotid", Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid")
    )
    product_id: Mapped[int] = mapped_column(
        "productid", ForeignKey("products.productid")
    )
    quantity: Mapped[DECIMAL] = mapped_column(
        DECIMAL(14, 6), unique=False, nullable=False
    )
    purchasprice: Mapped[DECIMAL] = mapped_column(
        "purchaseprice", DECIMAL(10, 2), unique=False, nullable=False
    )
    purchasedate: Mapped[Date] = mapped_column(
        "purchasedate", Date, unique=False, nullable=False
    )


class TradingRecommendation(Base):
    __tablename__ = "tradingrecommendations"
    id: Mapped[int] = mapped_column("recommendationid", Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid")
    )
    product_id: Mapped[int] = mapped_column(
        "productid", ForeignKey("products.productid")
    )
    recommendation_date: Mapped[Date] = mapped_column(
        "recommendationdate", Date, unique=False, nullable=False
    )
    action: Mapped[str] = mapped_column(String(10), unique=False, nullable=False)


class CashTransaction(Base):
    __tablename__ = "cashtransactions"
    id: Mapped[int] = mapped_column("transactionid", Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid")
    )
    transaction_type: Mapped[str] = mapped_column(
        "transactiontype", String(4), unique=False, nullable=False
    )
    amount: Mapped[DECIMAL] = mapped_column(
        DECIMAL(15, 2), unique=False, nullable=False
    )
    transaction_date: Mapped[Date] = mapped_column(
        "transactiondate", Date, unique=False, nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, unique=False, nullable=True
    )
