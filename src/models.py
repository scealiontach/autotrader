from datetime import datetime
from typing import Optional

from sqlalchemy import (
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

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "portfolio_id": self.portfolio_id,
            "quantity": self.quantity,
            "price": self.price,
            "transaction_date": self.transaction_date,
            "transaction_type": self.transaction_type,
        }


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

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "portfolio_id": self.portfolio_id,
            "product_id": self.product_id,
            "recommendation_date": self.recommendation_date,
            "action": self.action,
        }


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

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "portfolio_id": self.portfolio_id,
            "transaction_type": self.transaction_type,
            "amount": self.amount,
            "transaction_date": self.transaction_date,
            "description": self.description,
        }
