import logging as log
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import NoneType
from typing import Optional, Union

import pandas as pd
from cachetools import LFUCache, TTLCache
from numpy import divide
from sqlalchemy import (
    DECIMAL,
    JSON,
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    insert,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from analyzer import cumulative_return
from constants import BUY, BUY_TX_FEE, SELL, SELL_TX_FEE
from database import Session
from market import Market
from models import Base, TradingRecommendation
from product import Product
from recommender import Recommendation, Recommender
from reporting import csv_log
from utils import round_down, round_up

position_cache = LFUCache(maxsize=4096)
recommendation_cache = TTLCache(maxsize=4096, ttl=30)


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[int] = mapped_column("lotid", Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        "portfolioid", ForeignKey("portfolios.portfolioid"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        "productid", ForeignKey("products.productid"), nullable=False
    )
    quantity: Mapped[DECIMAL] = mapped_column(
        DECIMAL(14, 6), unique=False, nullable=False
    )
    purchaseprice: Mapped[DECIMAL] = mapped_column(
        "purchaseprice", DECIMAL(10, 2), unique=False, nullable=False
    )
    purchasedate: Mapped[Date] = mapped_column(
        "purchasedate", Date, unique=False, nullable=False
    )

    def __init__(self, quantity: Decimal, price: Decimal, purchasedate) -> NoneType:
        self.quantity = quantity  # type: ignore
        self.purchaseprice = price  # type: ignore
        self.purchasedate = purchasedate

    def as_dict(self):
        return {
            "id": self.id,
            "portfolio_id": self.portfolio_id,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "purchasedate": self.purchasedate,
            "purchaseprice": self.purchaseprice,
        }


class Position(Base):
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
        "last", DECIMAL(14, 6), unique=False, nullable=True
    )
    invest: Mapped[DECIMAL] = mapped_column(
        "invest", DECIMAL(14, 6), unique=False, nullable=True
    )

    def __repr__(self):
        return f"<Position {self.id}, {self.product_id}, {self.portfolio_id}>"

    def __init__(self, portfolio, product):
        self.portfolio_id = portfolio.id
        self.product_id = product.id
        self.quantity = 0  # type: ignore

    def as_dict(self):
        portfolio = Portfolio.from_id(self.portfolio_id)
        recommendation = self.recommendation(portfolio.last_active())
        if recommendation:
            recommendation = recommendation.action
        else:
            recommendation = "None"
        return {
            "id": self.id,
            "portfolio_id": self.portfolio_id,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "purchasedate": self.purchasedate,
            "last_updated": self.last_updated,
            "last": self.last,
            "invest": self.invest,
            "lots": [l.id for l in self.get_lots()],
            "recommendation": recommendation,
        }

    def meets_holding_period(self, as_of_date, required_days=3):
        """
        Check if the oldest holding of a given symbol meets the required holding period.

        :param symbol: Stock symbol to check.
        :param required_days: Required holding period in days.
        :return: True if the holding period is met, False otherwise.
        """
        with Session() as session:
            statement = text(
                """
                SELECT MAX(TransactionDate)
                FROM Transactions
                WHERE PortfolioID = :portfolio_id AND ProductID = :product_id;
            """
            )
            result = session.execute(
                statement,
                {
                    "portfolio_id": self.portfolio_id,
                    "product_id": self.product_id,
                },
            ).first()
            if result and result[0]:
                latest_purchase_date = result[0]
                threshold_date = (
                    latest_purchase_date + timedelta(days=required_days)
                ).date()

                if as_of_date >= threshold_date:
                    return True
            else:
                return True
        return False

    def get_lots(self) -> list[Lot]:
        lots = []
        with Session() as session:
            stmt = (
                select(Lot)
                .where(
                    Lot.portfolio_id == self.portfolio_id,
                    Lot.product_id == self.product_id,
                )
                .order_by(Lot.purchasedate.asc())
            )
            result = session.execute(stmt).all()
            for row in result:
                lots.append(row[0])
        return lots

    def recommendation(self, as_of_date):
        with Session() as session:
            row = (
                session.query(TradingRecommendation)
                .where(
                    TradingRecommendation.portfolio_id == self.portfolio_id,
                    TradingRecommendation.product_id == self.product_id,
                    TradingRecommendation.recommendation_date <= as_of_date,
                )
                .order_by(TradingRecommendation.recommendation_date.desc())
                .first()
            )
            return row

    def fetch_current_quantity(self, as_of_date) -> Decimal:
        """
        Fetch the current quantity of shares owned for a given stock symbol.

        :param symbol: The stock symbol to query.
        :return: The quantity of shares currently owned for the symbol.
        """
        cache_key = f"{self.product_id}-{self.portfolio_id}-quantity-{as_of_date}"
        if cache_key in position_cache:
            return position_cache[cache_key]
        with Session() as session:
            statement = text(
                """
                SELECT sum(pp.Quantity)
                FROM PortfolioPositions pp
                WHERE pp.ProductId = :product_id and pp.PortfolioID = :portfolio_id;
            """
            )
            result = session.execute(
                statement,
                {
                    "product_id": self.product_id,
                    "portfolio_id": self.portfolio_id,
                },
            ).first()
            if result and result[0]:
                position_cache[cache_key] = result[0]
                return result[0]
            else:
                position_cache[cache_key] = 0
                return Decimal(
                    0
                )  # Return 0 if the symbol is not found in the portfolio


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column("portfolioid", Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    owner: Mapped[Optional[str]] = mapped_column(
        String(255), unique=False, nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, unique=False, nullable=True
    )
    createddate: Mapped[Date] = mapped_column(
        Date, unique=False, nullable=False, default=datetime.today
    )
    is_active = mapped_column(
        "isactive", Boolean, unique=False, nullable=False, default=True
    )
    reserve_cash_percent: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), unique=False, nullable=False, default=Decimal(5)
    )
    reinvest_period: Mapped[int] = mapped_column(
        Integer, unique=False, nullable=False, default=7
    )
    reinvest_amt: Mapped[Decimal] = mapped_column(
        DECIMAL(15, 2), unique=False, nullable=False, default=Decimal(0)
    )
    bank_threshold: Mapped[Decimal] = mapped_column(
        DECIMAL(8, 2), unique=False, nullable=False, default=Decimal(10000)
    )
    bank_pc: Mapped[int] = mapped_column(
        Integer, unique=False, nullable=False, default=33
    )
    rebalance_months: Mapped[JSON] = mapped_column(
        JSON, unique=False, nullable=False, default=[1]
    )
    dividend_only: Mapped[bool] = mapped_column(
        Boolean, unique=False, nullable=False, default=False
    )
    sectors_allowed: Mapped[JSON] = mapped_column(
        JSON, unique=False, nullable=False, default=[]
    )
    sectors_forbidden: Mapped[JSON] = mapped_column(
        JSON, unique=False, nullable=False, default=[]
    )
    max_exposure: Mapped[int] = mapped_column(
        Integer, unique=False, nullable=False, default=20
    )
    strategy: Mapped[str] = mapped_column(
        String(32), unique=False, nullable=False, default="advanced"
    )

    def __repr__(self):
        return "<Portfolio %r>" % self.name

    def __init__(
        self,
        name="",
        owner="",
        description="",
    ):
        self.name = name
        self.owner = owner
        self.description = description

    @staticmethod
    def from_id(portfolio_id: int):
        with Session() as session:
            portfolio = session.query(Portfolio).get(portfolio_id)
            if portfolio is None:
                raise ValueError(f"Portfolio with ID {portfolio_id} not found")
            return portfolio

    def as_dict_fast(self):
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "description": self.description,
            "createddate": self.createddate,
            "is_active": self.is_active,
            "reserve_cash_percent": self.reserve_cash_percent,
            "reinvest_period": self.reinvest_period,
            "reinvest_amt": self.reinvest_amt,
            "bank_threshold": self.bank_threshold,
            "bank_pc": self.bank_pc,
            "rebalance_months": self.rebalance_months,
            "dividend_only": self.dividend_only,
            "sectors_allowed": self.sectors_allowed,
            "sectors_forbidden": self.sectors_forbidden,
            "max_exposure": self.max_exposure,
            "strategy": self.strategy,
            "cash": self.cash_balance(self.last_active()),
            "bank": self.bank_balance(self.last_active()),
            "invest": self.invest_balance(self.last_active()),
            "value": self.value(self.last_active()),
            "roi": self.roi(self.last_active()),
            "last_active": self.last_active(),
        }

    def as_dict(self):
        ret = self.as_dict_fast()
        ret["active_recommendations"] = [
            r.as_dict() for r in self.active_recommendations()
        ]
        return ret

    def find_position(self, symbol: str) -> Optional[Position]:
        with Session() as session:
            position = (
                session.query(Position)
                .join(Product)
                .filter(Position.portfolio_id == self.id)
                .where(Product.symbol == symbol)
                .first()
            )
            return position

    def recommender_for(self, symbol: str, target_date) -> Recommender:
        product = Product.from_symbol(symbol)
        return Recommender(
            self.id,
            product,
            target_date,
            strategy=self.strategy,
        )

    def market_as_of(self, as_of_date) -> Market:
        return Market(as_of_date)

    def is_rebalance_month(self, as_of_date):
        return as_of_date.month in (self.rebalance_months)

    def get_recommendation(self, symbol):
        with Session() as session:
            statement = text(
                """
                SELECT Action
                FROM TradingRecommendations
                JOIN Products ON TradingRecommendations.ProductID = Products.ProductID
                WHERE Symbol = :symbol
                AND PortfolioID = :portfolio_id
                ORDER BY RecommendationDate DESC
                LIMIT 1;
            """
            )
            result = session.execute(
                statement, {"symbol": symbol, "portfolio_id": self.id}
            ).first()
            if result:
                return result[0]
        return "None"

    def eligible_products(self):
        with Session() as session:
            query = """
                SELECT p.ProductID, p.Symbol
                FROM Products p
                WHERE p.isactive = true
            """
            sectors_allowed: list = self.sectors_allowed  # type: ignore
            sectors_forbidden: list = self.sectors_forbidden  # type: ignore
            if self.dividend_only:
                query += " AND p.dividend_rate IS NOT NULL"
            if len(sectors_allowed) > 0:
                sectors = ",".join(sectors_allowed)
                sectors = f"('{sectors}')"
                query += f" AND p.sector in {sectors}"
            if len(sectors_forbidden) > 0:
                sectors = ",".join(sectors_forbidden)
                sectors = f"('{sectors}')"
                query += f" AND p.sector not in {sectors}"

            statement = text(query)
            result = session.execute(statement).all()
            return result

    def positions(self) -> list[Position]:
        """Fetch the current portfolio from the database."""
        poslist = []
        with Session() as session:
            statement = select(Position).where(Position.portfolio_id == self.id)

            sectors_allowed: list = self.sectors_allowed  # type: ignore
            sectors_forbidden: list = self.sectors_forbidden  # type: ignore
            if (
                self.dividend_only
                or len(sectors_allowed) > 0
                or len(sectors_forbidden) > 0
            ):
                statement = statement.join(Product)
                if self.dividend_only:
                    statement = statement.where(Product.dividend_rate.isnot(None))
                if len(sectors_allowed) > 0:
                    statement = statement.where(Product.sector.in_(sectors_allowed))
                if len(sectors_forbidden) > 0:
                    statement = statement.where(
                        Product.sector.notin_(sectors_forbidden)
                    )

            result = session.execute(statement).all()

            poslist = [row[0] for row in result]
        return poslist

    def update_positions(self, as_of_d):
        with Session() as session:
            session.expire_on_commit = False
            del_statement = text(
                """
                UPDATE PortfolioPositions SET Quantity=0 WHERE PortfolioID = :portfolio_id
            """
            )
            session.execute(del_statement, {"portfolio_id": self.id})

            ins_statement = text(
                """
            INSERT INTO PortfolioPositions (PortfolioID, ProductID, Quantity, purchasedate, lastupdated, invest )
            SELECT PortfolioID, ProductID, SUM(Quantity), max(purchasedate), now(), sum(quantity * purchaseprice)
            FROM Lots
            where PortfolioID = :portfolio_id
            GROUP BY PortfolioID, ProductID
            ON CONFLICT (PortfolioID, ProductID) DO UPDATE
            SET Quantity = EXCLUDED.Quantity, purchasedate=EXCLUDED.purchasedate, lastupdated=EXCLUDED.lastupdated, invest=EXCLUDED.invest
            """
            )
            session.execute(ins_statement, {"portfolio_id": self.id})
            session.commit()

            sel_statement = text(
                """
                select pp.productid from portfoliopositions pp where portfolioID = :portfolio_id
                """
            )
            result = session.execute(sel_statement, {"portfolio_id": self.id}).all()

            for row in result:
                product = Product.from_id(row[0])
                last = product.fetch_last_closing_price(as_of_d)

                update_stmt = text(
                    """
                        UPDATE PortfolioPositions SET last = :last
                        WHERE PortfolioID = :portfolio_id AND ProductID = :product_id
                    """
                )
                session.execute(
                    update_stmt,
                    {
                        "last": last,
                        "portfolio_id": self.id,
                        "product_id": product.id,
                    },
                )
                session.commit()
            del_statement = text(
                """
                DELETE from PortfolioPositions where Quantity=0 and PortfolioID = :portfolio_id
            """
            )
            session.execute(del_statement, {"portfolio_id": self.id})
            session.commit()

    def value(self, as_of_date):
        """
        Calculates the value of the portfolio as of a given closing date.

        :param as_of_date: The closing date for the calculation (datetime.date object).
        :return: The total value of the portfolio as a float.
        """
        total_value = 0
        with Session() as session:
            # Fetch portfolio positions
            statement = text(
                """
                SELECT ProductID, sum(Quantity)
                FROM PortfolioPositions
                where PortfolioID = :portfolio_id
                group by ProductID
                order by ProductID;
            """
            )
            positions = session.execute(statement, {"portfolio_id": self.id}).all()
            closing_prices = {}
            for product_id, quantity in positions:
                # Fetch closing price for each position as of the given date
                statement = text(
                    """
                    SELECT ClosingPrice
                    FROM MarketData
                    WHERE ProductID = :product_id AND Date <= :as_of_date
                    ORDER BY Date DESC
                    LIMIT 1;
                """
                )
                result = session.execute(
                    statement, {"product_id": product_id, "as_of_date": as_of_date}
                ).first()
                if result:
                    quantity = Decimal(quantity)
                    closing_price = Decimal(result[0])
                    closing_prices[product_id] = closing_price
                    total_value += quantity * closing_price
        return total_value

    def take_profit(self, bank_pc: int, total_cash: Decimal, roi: Decimal) -> Decimal:
        if roi > self.bank_threshold:
            withdrawal = ((total_cash * bank_pc) / 100) - (
                ((total_cash * bank_pc) / 100) % 10
            )
            return withdrawal
        else:
            return Decimal(0)

    def report_status(self, report_date, roi, first_day, report=True):
        bank = self.bank_balance(report_date)
        total_cash = self.cash_balance(report_date)
        total_value = self.value(report_date)
        total_invest = self.invest_balance(report_date)
        market = Market(report_date)
        signal = 1
        if market.adline():
            signal = -1

        perf = market.rate_performance(first_day, roi)
        csv_log(
            report_date,
            "REPORT",
            [
                signal,
                perf,
                f"{bank:.2f}",
                f"{total_cash:.2f}",
                f"{total_value:.2f}",
                f"{total_invest:.2f}",
                f"{roi:.2f}",
            ],
            report=report,
        )

    def record_performance(self, report_date):
        with Session() as session:
            bank = self.bank_balance(report_date)
            total_cash = self.cash_balance(report_date)
            total_value = self.value(report_date)
            total_invest = self.invest_balance(report_date)

            statement = text(
                """
                INSERT INTO Portfolio_Performance (Portfolio_ID, Date, stock_value, invested, cash, bank)
                VALUES (:portfolio_id, :date, :stock_value, :invested, :cash, :bank);
            """
            )
            session.execute(
                statement,
                {
                    "portfolio_id": self.id,
                    "date": report_date,
                    "stock_value": total_value,
                    "invested": total_invest,
                    "cash": total_cash,
                    "bank": bank,
                },
            )
            session.commit()

    def sell(self, product_id, quantity, price, transaction_date, report=True):
        self._execute_trade(
            product_id, SELL, quantity, price, transaction_date, report=report
        )

    def buy(self, product_id, quantity, price, transaction_date, report=True):
        self._execute_trade(
            product_id, BUY, quantity, price, transaction_date, report=report
        )

    def roi(self, trade_date=None):
        if trade_date is None:
            trade_date = self.last_active()
        total_value = self.value(trade_date)
        cash = self.cash_balance(trade_date)
        bank = self.bank_balance(trade_date)
        total_invested = self.invest_balance(trade_date)
        return cumulative_return(total_invested, total_value + cash + bank) * 100

    def reinvest_or_bank(self, trade_date, report=True):
        last_reinvestment_date = self.last_transaction_like("%Reinvest/Bank%")
        total_value = self.value(trade_date)
        cash = self.cash_balance(trade_date)
        total_invested = self.invest_balance(trade_date)
        banking_roi = cumulative_return(total_invested, total_value + cash) * 100
        if last_reinvestment_date is None or trade_date > (
            last_reinvestment_date + timedelta(days=self.reinvest_period)
        ):
            withdrawal = self.take_profit(self.bank_pc, cash, banking_roi)
            self.sweep(
                withdrawal,
                self.reinvest_amt,
                trade_date,
                f"Reinvest/Bank @ {banking_roi:.2f}% ROI",
                report=report,
            )
            return trade_date
        else:
            return last_reinvestment_date

    def result(self):
        total_invest = Decimal(0)
        investment_value = Decimal(0)
        for position in self.positions():
            if position and position.quantity > 0:  # type: ignore
                lots = position.get_lots()
                invested = Decimal(0)
                for l in lots:
                    invested += l.quantity * l.purchaseprice  # type: ignore
                if position.last is None or position.last <= 0:  # type: ignore
                    value = Decimal(0)
                else:
                    value = Decimal(position.quantity) * Decimal(position.last)  # type: ignore
                total_invest += invested
                investment_value += value

        cash, bank, invest = self.current_balances()
        if total_invest > 0:
            final_roi = ((investment_value + cash) / invest - 1) * 100
        else:
            final_roi = 0

        total = investment_value + cash + bank
        return total, final_roi

    def view(self):
        print(("=" * 9) + f" {p.name}:{p.id} " + ("=" * 9))

        total_invest = Decimal(0)
        investment_value = self.value(self.last_active())
        for position in self.positions():
            if position.quantity > 0:  # type: ignore
                product = Product.from_id(position.product_id)
                recommendation = self.get_recommendation(product.symbol)
                lots = position.get_lots()
                invested = Decimal(0)
                lot_count = len(lots)
                for l in lots:
                    invested += l.quantity * l.purchaseprice  # type: ignore

                if position.last is None or position.last <= 0:  # type: ignore
                    value = Decimal(0)
                else:
                    value = Decimal(position.quantity) * Decimal(position.last)  # type: ignore
                unrealized_gain = value - invested

                print(
                    f"{product.symbol:<6s}: {str(product.sector)[0:14]:<14s} {recommendation:4s} {position.quantity:> 10.4f} @ {position.last:> 10.2f} Invested: {invested:>10.2f}({lot_count:3d}) Value: {(value):>10.2f} Unrealized: {unrealized_gain:>10.2f}"
                )
                total_invest += invested

        cash, bank, invest = self.current_balances()
        total_unrealized = investment_value - total_invest
        if total_invest > 0:
            potential_roi = ((investment_value / total_invest) - 1) * 100
        else:
            potential_roi = 0

        if total_invest > 0:
            final_roi = ((investment_value + cash) / invest - 1) * 100
        else:
            final_roi = 0
        total = investment_value + cash + bank

        active_range = self.last_active()

        print()
        if active_range:
            print(f"Last Active: {active_range}")
        print(
            f"Total Value: {total:>13.2f} Investment: {investment_value:>10.2f} Unrealized: {total_unrealized:>13.2f} Potential ROI: {potential_roi:>10.2f}%"
        )
        print(
            f"Total Invested: {invest:>10.2f} Cash: {cash:>16.2f} Bank: {bank:>19.2f} Final ROI: {final_roi:>14.2f}%"
        )
        print()

    def _execute_trade(
        self,
        product_id: int,
        transaction_type: str,
        quantity: Decimal,
        price,
        transaction_date,
        report=True,
    ):
        if not product_id:
            raise ValueError("Product ID is required")
        product = Product.from_id(product_id=product_id)
        if not product:
            raise ValueError("Product not found")
        with Session() as session:
            statement = text(
                """
                INSERT INTO Transactions (PortfolioID, ProductID, TransactionType, Quantity, Price, TransactionDate)
                VALUES (:portfolio_id, :product_id, :transaction_type, :quantity, :price, :transaction_date);
            """
            )
            session.execute(
                statement,
                {
                    "portfolio_id": self.id,
                    "product_id": product_id,
                    "transaction_type": transaction_type,
                    "quantity": quantity,
                    "price": price,
                    "transaction_date": transaction_date,
                },
            )
            self.manage_lots(
                product, quantity, transaction_type, price, transaction_date
            )
            if transaction_type == BUY:
                self.add_debit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Buy {quantity} shares of {product.symbol}",
                    report=report,
                )
                self.add_debit(
                    BUY_TX_FEE,
                    transaction_date,
                    f"Buy TX FEE {quantity} shares of {product.symbol}",
                    report=report,
                )
            elif transaction_type == SELL:
                self.add_deposit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Sell {quantity} shares of {product.id}",
                    report=report,
                )
                self.add_debit(
                    SELL_TX_FEE,
                    transaction_date,
                    f"Sell TX FEE {quantity} shares of {product.symbol}",
                    report=report,
                )

            session.commit()

    def last_active(self):
        with Session() as session:
            statement = text(
                """
                SELECT MAX(recommendationdate)
                FROM tradingrecommendations
                WHERE PortfolioID = :portfolio_id;
            """
            )
            result = session.execute(statement, {"portfolio_id": self.id}).first()
            if result and result[0]:
                return result[0]
        return datetime(1975, 5, 1).date()

    def manage_lots(
        self, product: Product, quantity, transaction_type, price=None, as_of_date=None
    ):
        """
        Adds to the Lots table for a purchase or removes lots in FIFO order for a sale.

        :param product: The product.
        :param quantity: The quantity to buy/sell.
        :param transaction_type: 'buy' for purchase, 'sell' for sale.
        :param price: The purchase price per unit (required for 'buy').
        :param as_of_date: The date of the purchase (required for 'buy').
        """
        try:
            with Session() as session:
                if transaction_type == BUY:
                    if price is None or as_of_date is None:
                        raise ValueError(
                            "Purchase price and date must be provided for buys."
                        )
                    # Insert new lot for a purchase
                    statement = text(
                        """
                        INSERT INTO Lots (PortfolioID, ProductID, Quantity, PurchasePrice, PurchaseDate)
                        VALUES (:portfolio_id, :product_id, :quantity, :price, :as_of_date);
                    """
                    )
                    session.execute(
                        statement,
                        {
                            "portfolio_id": self.id,
                            "product_id": product.id,
                            "quantity": quantity,
                            "price": price,
                            "as_of_date": as_of_date,
                        },
                    )
                elif transaction_type == SELL:
                    statement = text(
                        """
                        SELECT LotID, Quantity FROM Lots
                        WHERE PortfolioID = :portfolio_id AND ProductID = :product_id
                        ORDER BY PurchaseDate ASC, LotID ASC;
                    """
                    )
                    lots = session.execute(
                        statement,
                        {
                            "portfolio_id": self.id,
                            "product_id": product.id,
                        },
                    ).all()

                    for lot_id, lot_quantity in lots:
                        if quantity <= 0:
                            break  # Exit loop if all shares have been sold

                        if quantity >= lot_quantity:
                            # Delete the entire lot
                            statement = text(
                                """
                                DELETE FROM Lots WHERE LotID = :lot_id;
                            """
                            )
                            session.execute(statement, {"lot_id": lot_id})
                            quantity -= (
                                lot_quantity  # Reduce the remaining quantity to sell
                            )
                        else:
                            # Partially sell from the current lot
                            statement = text(
                                """
                                UPDATE Lots SET Quantity = Quantity - :quantity WHERE LotID = :lot_id;
                            """
                            )
                            session.execute(
                                statement, {"quantity": quantity, "lot_id": lot_id}
                            )
                            quantity = 0  # All shares sold
                    statement = text(
                        """
                        DELETE FROM Lots WHERE Quantity = 0;
                    """
                    )
                    session.execute(statement)  # Remove empty lots
                else:
                    raise ValueError("Transaction type must be 'buy' or 'sell'.")

                session.commit()  # Commit the transaction
        except Exception as e:
            log.error(f"An error occurred: {e}")

    def current_balances(self):
        cash = self.cash_balance(self.last_active())
        bank = self.bank_balance(self.last_active())
        invest = self.invest_balance(self.last_active())
        return cash, bank, invest

    def cash_balance(self, as_of_date) -> Decimal:
        """
        Calculate the current cash balance for a given portfolio as of a specified date.

        :param portfolio_id: The ID of the portfolio.
        :param as_of_date: The date up to which to calculate the balance.
        :return: The cash balance as a float.
        """
        with Session() as session:
            statement = text(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = :portfolio_id AND TransactionDate <= :as_of_date;
            """
            )
            result = session.execute(
                statement, {"portfolio_id": self.id, "as_of_date": as_of_date}
            ).first()
            if result:
                balance = result[0] if result[0] is not None else 0.0
            else:
                balance = 0
        return Decimal(balance)

    def bank_balance(self, as_of_date) -> Decimal:
        """
        Calculate the current cash balance for a given portfolio as of a specified date.

        :param portfolio_id: The ID of the portfolio.
        :param as_of_date: The date up to which to calculate the balance.
        :return: The cash balance as a float.
        """
        with Session() as session:
            statement = text(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = :portfolio_id AND TransactionDate <= :as_of_date AND TransactionType in ('BANK');
            """
            )
            result = session.execute(
                statement, {"portfolio_id": self.id, "as_of_date": as_of_date}
            ).first()
            if result:
                balance = result[0] if result[0] is not None else 0.0
            else:
                balance = 0
        return Decimal(abs(balance))

    def invest_balance(self, as_of_date) -> Decimal:
        """
        Calculate the current cash balance for a given portfolio as of a specified date.

        :param portfolio_id: The ID of the portfolio.
        :param as_of_date: The date up to which to calculate the balance.
        :return: The cash balance as a float.
        """
        with Session() as session:
            statement = text(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = :portfolio_id AND TransactionDate <= :as_of_date AND TransactionType in ('INVEST');
            """
            )
            result = session.execute(
                statement, {"portfolio_id": self.id, "as_of_date": as_of_date}
            ).first()

            if result:
                balance = result[0] if result[0] is not None else 0.0
            else:
                balance = 0
        return Decimal(balance)

    def add_debit(
        self,
        amount: Decimal,
        transaction_date,
        description="",
        transaction_type="DEBIT",
        report=True,
    ) -> None:
        """
        Record a debit (withdrawal or payment) transaction for a given portfolio.

        :param portfolio_id: The ID of the portfolio.
        :param amount: The amount to withdraw (positive float).
        :param transaction_date: The date of the transaction.
        :param description: Optional description of the transaction.
        """
        if amount == 0:
            return
        with Session() as session:
            statement = text(
                """
                INSERT INTO CashTransactions (PortfolioID, TransactionType, Amount, TransactionDate, Description)
                VALUES (:portfolio_id, :transaction_type, :amount, :transaction_date, :description);
            """
            )
            session.execute(
                statement,
                {
                    "portfolio_id": self.id,
                    "transaction_type": transaction_type,
                    "amount": -round_up(amount, d=2),
                    "transaction_date": transaction_date,
                    "description": description,
                },
            )
            session.commit()
            csv_log(
                transaction_date,
                transaction_type,
                [f"{round_up(amount,d=2):.2f}", description],
                report=report,
            )

    def add_deposit(
        self,
        amount: Decimal,
        transaction_date,
        description="",
        transaction_type="DEPOSIT",
        report=True,
    ) -> None:
        """
        Record a deposit (credit) transaction for a given portfolio.

        :param portfolio_id: The ID of the portfolio.
        :param amount: The amount to deposit (positive float).
        :param transaction_date: The date of the transaction.
        :param description: Optional description of the transaction.
        """
        if amount == 0:
            return
        with Session() as session:
            session.expire_on_commit = False
            session.add(self)
            session.refresh(self)
            statement = text(
                """
                INSERT INTO CashTransactions (PortfolioID, TransactionType, Amount, TransactionDate, Description)
                VALUES (:portfolio_id, :transaction_type, :amount, :transaction_date, :description);
            """
            )
            session.execute(
                statement,
                {
                    "portfolio_id": self.id,
                    "transaction_type": transaction_type,
                    "amount": round_down(amount, d=2),
                    "transaction_date": transaction_date,
                    "description": description,
                },
            )
            session.commit()
            csv_log(
                transaction_date,
                transaction_type,
                [f"{round_down(amount,d=2):.2f}", description],
                report=report,
            )

    def bank(
        self, amount: Decimal, transaction_date, description="", report=True
    ) -> None:
        self.add_debit(
            amount,
            transaction_date,
            description=description,
            transaction_type="BANK",
            report=report,
        )

    def invest(
        self, amount: Decimal, transaction_date, description="", report=True
    ) -> None:
        self.add_deposit(
            amount,
            transaction_date,
            description=description,
            transaction_type="INVEST",
            report=report,
        )

    def sweep(
        self,
        withdraw: Decimal,
        invest: Decimal,
        transaction_date,
        description="",
        report=True,
    ) -> None:
        if withdraw > invest:
            to_bank = withdraw - invest
            self.bank(to_bank, transaction_date, description=description, report=report)
        elif invest > withdraw:
            to_invest = invest - withdraw
            if self.bank_balance(transaction_date) > to_invest:
                self.bank(
                    -to_invest, transaction_date, description=description, report=report
                )
            else:
                self.invest(
                    to_invest, transaction_date, description=description, report=report
                )

    def last_transaction_like(self, description):
        with Session() as session:
            statement = text(
                """
                SELECT TransactionDate
                FROM CashTransactions
                WHERE PortfolioID = :portfolio_id AND Description LIKE :description
                ORDER BY TransactionDate DESC
                LIMIT 1;
            """
            )
            result = session.execute(
                statement,
                {"portfolio_id": self.id, "description": f"%{description}%"},
            ).first()
            if result:
                return result[0]
        return None

    def first_transaction_like(self, description):
        with Session() as session:
            statement = text(
                """
                SELECT TransactionDate
                FROM CashTransactions
                WHERE PortfolioID = :portfolio_id AND Description LIKE :description
                ORDER BY TransactionDate ASC
                LIMIT 1;
            """
            )
            result = session.execute(
                statement,
                {"portfolio_id": self.id, "description": f"%{description}%"},
            ).first()
            if result:
                return result[0]
        return None

    def get_performance(self, end_date=None):
        with Session() as session:
            if end_date is None:
                end_date = self.last_active()
            statement = text(
                """
                SELECT Date, stock_value, invested, cash, bank, stock_value + cash + bank as total
                FROM Portfolio_Performance
                WHERE Portfolio_ID = :portfolio_id AND Date <= :end_date
                ORDER BY Date ASC;
            """
            )
            df = pd.read_sql(statement, session.bind, params={"portfolio_id": self.id, "end_date": end_date})  # type: ignore
            return df

    def active_recommendations(
        self, as_of_eod: Union[date, None] = None
    ) -> list[Recommendation]:
        """
        Make trading recommendations based on the given portfolio and the market conditions
        as of a given date.
        """
        if as_of_eod is None:
            if self.is_active:
                target_date = self.last_active()
            else:
                target_date = datetime.today().date()
        else:
            target_date = as_of_eod

        cache_key = f"active_recommendations-{self.id}-{target_date}"
        if cache_key in recommendation_cache:
            return recommendation_cache[cache_key]

        recommendations: list[Recommendation] = []
        products = self.eligible_products()

        for p in products:
            recommender = self.recommender_for(p.symbol, target_date)
            rec = recommender.recommend()
            rec.as_of = target_date
            recommendations.append(rec)
        recommendations.sort(key=lambda x: x.strength, reverse=True)
        positions = self.positions()
        held_symbols = list(
            map(lambda x: Product.from_id(x.product_id).symbol, positions)
        )
        sell_recommendations = list(
            filter(
                lambda x: x.action == "SELL" and x.symbol in held_symbols,
                recommendations,
            )
        )
        buy_recommendations = list(
            filter(lambda x: x.action == "BUY", recommendations)
        )[:5]
        ret_recommendations = sell_recommendations + buy_recommendations
        ret_recommendations.sort(key=lambda x: x.strength, reverse=True)
        recommendation_cache[cache_key] = ret_recommendations
        return ret_recommendations


# get all the portfolios in the database
def get_portfolios() -> list[Portfolio]:
    portfolios = []
    with Session() as session:
        portfolios = session.query(Portfolio).all()
    return portfolios


def new_portfolio(name, owner="admin", description=""):
    with Session() as session:
        portfolio = Portfolio(name=name, owner=owner)
        portfolio.description = description
        session.add(portfolio)
        session.commit()
        return portfolio


def portfolio_for_name(name) -> Portfolio:

    with Session() as session:
        result = session.query(Portfolio).filter_by(name=name).first()
        if result:
            return result
        else:
            return new_portfolio(name)


if __name__ == "__main__":
    portfolios = get_portfolios()
    portfolios.sort(key=lambda x: x.result()[0], reverse=True)

    count = 0
    max_count = 5
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        max_count = len(portfolios)
    for p in portfolios:
        if count < max_count:
            result = p.view()
        count += 1
