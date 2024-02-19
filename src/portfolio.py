import sys
from datetime import datetime, timedelta
from decimal import Decimal
from types import NoneType
from typing import Union

from cachetools import LFUCache
from models import Base
from sqlalchemy import DECIMAL, Date, ForeignKey, Integer, select, text
from sqlalchemy.orm import Mapped, mapped_column

from analyzer import cumulative_return
from constants import BUY, BUY_TX_FEE, SELL, SELL_TX_FEE
from database import Session
from market import Market
from product import Product
from recommender import Recommender
from reporting import csv_log
from utils import round_down, round_up

position_cache = LFUCache(maxsize=4096)


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
    purchasprice: Mapped[DECIMAL] = mapped_column(
        "purchaseprice", DECIMAL(10, 2), unique=False, nullable=False
    )
    purchasedate: Mapped[Date] = mapped_column(
        "purchasedate", Date, unique=False, nullable=False
    )

    def __init__(self, quantity: Decimal, price: Decimal, purchasedate) -> NoneType:
        self.quantity = quantity  # type: ignore
        self.purchasprice = price  # type: ignore
        self.purchasedate = purchasedate


class Position:
    def __init__(self, portfolio, product):
        self.portfolio = portfolio
        self.symbol = product.symbol
        self.product = product

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
                    "portfolio_id": self.portfolio.portfolio_id,
                    "product_id": self.product.id,
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

    def get_lots(self, portfolio_id: int) -> list[Lot]:
        lots = []
        with Session() as session:
            stmt = (
                select(Lot)
                .where(
                    Lot.portfolio_id == portfolio_id, Lot.product_id == self.product.id
                )
                .order_by(Lot.purchasedate.asc())
            )
            result = session.execute(stmt).all()
            for row in result:
                lots.append(row[0])
        return lots

    def fetch_current_quantity(self, as_of_date) -> Decimal:
        """
        Fetch the current quantity of shares owned for a given stock symbol.

        :param symbol: The stock symbol to query.
        :return: The quantity of shares currently owned for the symbol.
        """
        cache_key = (
            f"{self.product.id}-{self.portfolio.portfolio_id}-quantity-{as_of_date}"
        )
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
                    "product_id": self.product.id,
                    "portfolio_id": self.portfolio.portfolio_id,
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


class Portfolio:
    def __init__(
        self,
        portfolio_id: int,
        name="",
        reserve_cash_percent: Decimal = Decimal(5),
        reinvest_period: int = 7,
        reinvest_amt: Decimal = Decimal(0),
        bank_threshold: int = 10000,
        bank_pc=33,
        rebalance_months=None,
        dividend_only=False,
        sectors_allowed=None,
        sectors_forbidden=None,
        max_exposure: int = 20,
        strategy="advanced",
    ):
        self.portfolio_id = portfolio_id
        self.name = name

        if rebalance_months is None:
            new_rebalance_months = [1]
        else:
            new_rebalance_months = rebalance_months
        if sectors_allowed is None:
            sectors_allowed = []
        if sectors_forbidden is None:
            sectors_forbidden = []
        self.set_options(
            reserve_cash_percent=reserve_cash_percent,
            reinvest_period=reinvest_period,
            reinvest_amt=reinvest_amt,
            bank_threshold=bank_threshold,
            bank_pc=bank_pc,
            rebalance_months=new_rebalance_months,
            dividend_only=dividend_only,
            sectors_allowed=sectors_allowed,
            sectors_forbidden=sectors_forbidden,
            max_exposure=max_exposure,
            strategy=strategy,
        )

    def set_options(
        self,
        reserve_cash_percent: Union[Decimal, NoneType] = None,
        reinvest_period: Union[int, NoneType] = None,
        reinvest_amt: Union[Decimal, NoneType] = None,
        bank_threshold: Union[int, NoneType] = None,
        bank_pc: Union[int, NoneType] = None,
        rebalance_months: Union[list[int], NoneType] = None,
        dividend_only: Union[bool, NoneType] = None,
        sectors_allowed: Union[list[str], NoneType] = None,
        sectors_forbidden: Union[list[str], NoneType] = None,
        max_exposure: Union[int, NoneType] = None,
        strategy: Union[str, NoneType] = None,
    ):
        if reserve_cash_percent is not None:
            self.reserve_cash_percent = reserve_cash_percent
        if reinvest_period is not None:
            self.reinvest_period = reinvest_period
        if reinvest_amt is not None:
            self.reinvest_amt = reinvest_amt
        if bank_threshold is not None:
            self.bank_threshold = bank_threshold
        if bank_pc is not None:
            self.bank_pc = bank_pc
        if rebalance_months is not None:
            self.rebalance_months = rebalance_months
        if dividend_only is not None:
            self.dividend_only = dividend_only
        if sectors_allowed is not None:
            self.sectors_allowed = sectors_allowed
        if sectors_forbidden is not None:
            self.sectors_forbidden = sectors_forbidden
        if max_exposure is not None:
            self.max_exposure = max_exposure
        if strategy is not None:
            self.strategy = strategy

    def position_for(self, symbol) -> Position:
        return Position(self, self.product_for(symbol))

    def product_for(self, symbol) -> Product:
        product = Product.from_symbol(symbol)
        if product is None:
            raise ValueError(f"Product with symbol {symbol} not found in the database.")
        return product

    def recommender_for(self, symbol: str, target_date) -> Recommender:
        product = self.product_for(symbol)
        return Recommender(
            self.portfolio_id,
            product,
            target_date,
            strategy=self.strategy,
        )

    def market_as_of(self, as_of_date) -> Market:
        return Market(as_of_date)

    def is_rebalance_month(self, as_of_date):
        return as_of_date.month in self.rebalance_months

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
                statement, {"symbol": symbol, "portfolio_id": self.portfolio_id}
            ).first()
            if result:
                return result[0]
        return "None"

    def positions(self) -> list[dict]:
        """Fetch the current portfolio from the database."""
        poslist = []
        with Session() as session:
            query = """
                    SELECT p.ProductID, p.Symbol, pp.Quantity, pp.Last, pp.Invest
                    FROM Products p
                    LEFT JOIN PortfolioPositions pp ON (p.ProductID = pp.ProductID AND pp.PortfolioID = :portfolio_id)
                    WHERE p.isactive = true
            """
            if self.dividend_only:
                query += " AND p.dividend_rate IS NOT NULL"
            if len(self.sectors_allowed) > 0:
                sectors = ",".join(self.sectors_allowed)
                sectors = f"('{sectors}')"
                query += f" AND p.sector in {sectors}"
            if len(self.sectors_forbidden) > 0:
                sectors = ",".join(self.sectors_forbidden)
                sectors = f"('{sectors}')"
                query += f" AND p.sector not in {sectors}"

            statement = text(query)
            result = session.execute(
                statement, {"portfolio_id": self.portfolio_id}
            ).all()

            poslist = [
                {
                    "product_id": row[0],
                    "symbol": row[1],
                    "quantity": row[2],
                    "last": row[3],
                    "invest": row[4],
                }
                for row in result
            ]
        return poslist

    def update_positions(self, as_of_d):
        with Session() as session:
            del_statement = text(
                """
                UPDATE PortfolioPositions SET Quantity=0 WHERE PortfolioID = :portfolio_id
            """
            )
            session.execute(del_statement, {"portfolio_id": self.portfolio_id})
            session.commit()

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
            session.execute(ins_statement, {"portfolio_id": self.portfolio_id})
            session.commit()

            sel_statement = text(
                """
                select pp.productid from portfoliopositions pp where portfolioID = :portfolio_id
                """
            )
            result = session.execute(
                sel_statement, {"portfolio_id": self.portfolio_id}
            ).all()
            session.commit()

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
                        "portfolio_id": self.portfolio_id,
                        "product_id": product.id,
                    },
                )
                session.commit()
            del_statement = text(
                """
                DELETE from PortfolioPositions where Quantity=0 and PortfolioID = :portfolio_id
            """
            )
            session.execute(del_statement, {"portfolio_id": self.portfolio_id})
            session.commit()

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
            positions = session.execute(
                statement, {"portfolio_id": self.portfolio_id}
            ).all()
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
                    closing_price = result[0]
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

    def sell(self, product_id, quantity, price, transaction_date, report=True):
        self._execute_trade(
            product_id, SELL, quantity, price, transaction_date, report=report
        )

    def buy(self, product_id, quantity, price, transaction_date, report=True):
        self._execute_trade(
            product_id, BUY, quantity, price, transaction_date, report=report
        )

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
            if position["quantity"] and position["quantity"] > 0:
                lots = self.position_for(position["symbol"]).get_lots(self.portfolio_id)
                invested = Decimal(0)
                for l in lots:
                    invested += l.quantity * l.purchasprice  # type: ignore
                value = Decimal(position["quantity"]) * Decimal(position["last"])
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
        print(("=" * 9) + f" {p.name}:{p.portfolio_id} " + ("=" * 9))

        total_invest = Decimal(0)
        investment_value = Decimal(0)
        for position in self.positions():
            if position["quantity"] and position["quantity"] > 0:
                recommendation = self.get_recommendation(position["symbol"])
                product = Product.from_id(position["product_id"])
                lots = self.position_for(position["symbol"]).get_lots(self.portfolio_id)
                invested = Decimal(0)
                lot_count = len(lots)
                for l in lots:
                    invested += l.quantity * l.purchasprice  # type: ignore
                value = Decimal(position["quantity"]) * Decimal(position["last"])
                unrealized_gain = value - invested
                print(
                    f"{position['symbol']:<6s}: {str(product.sector)[0:14]:<14s} {recommendation:4s} {position['quantity']:> 10.4f} @ {position['last']:> 10.2f} Invested: {invested:>10.2f}({lot_count:3d}) Value: {(value):>10.2f} Unrealized: {unrealized_gain:>10.2f}"
                )
                total_invest += invested
                investment_value += value
        total_unrealized = investment_value - total_invest
        if total_invest > 0:
            potential_roi = ((investment_value / total_invest) - 1) * 100
        else:
            potential_roi = 0

        cash, bank, invest = self.current_balances()
        if total_invest > 0:
            final_roi = ((investment_value + cash) / invest - 1) * 100
        else:
            final_roi = 0
        total = investment_value + cash + bank

        active_range = self.last_active()

        print()
        if active_range:
            print(f"Last Active: {active_range[0]}")
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
                    "portfolio_id": self.portfolio_id,
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
            result = session.execute(
                statement, {"portfolio_id": self.portfolio_id}
            ).first()
            if result:
                return result
        return None

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
                            "portfolio_id": self.portfolio_id,
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
                            "portfolio_id": self.portfolio_id,
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
            print(f"An error occurred: {e}")

    def current_balances(self):
        cash = self.cash_balance(datetime.now())
        bank = self.bank_balance(datetime.now())
        invest = self.invest_balance(datetime.now())
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
                statement, {"portfolio_id": self.portfolio_id, "as_of_date": as_of_date}
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
                statement, {"portfolio_id": self.portfolio_id, "as_of_date": as_of_date}
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
                statement, {"portfolio_id": self.portfolio_id, "as_of_date": as_of_date}
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
                    "portfolio_id": self.portfolio_id,
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
            statement = text(
                """
                INSERT INTO CashTransactions (PortfolioID, TransactionType, Amount, TransactionDate, Description)
                VALUES (:portfolio_id, :transaction_type, :amount, :transaction_date, :description);
            """
            )
            session.execute(
                statement,
                {
                    "portfolio_id": self.portfolio_id,
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
                {"portfolio_id": self.portfolio_id, "description": f"%{description}%"},
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
                {"portfolio_id": self.portfolio_id, "description": f"%{description}%"},
            ).first()
            if result:
                return result[0]
        return None


# get all the portfolios in the database
def get_portfolios() -> list[Portfolio]:
    portfolios = []
    with Session() as session:
        statement = text(
            """
            SELECT PortfolioID, Name
            FROM Portfolios;
        """
        )
        result = session.execute(statement).all()
        portfolios = [Portfolio(row[0], name=row[1]) for row in result]
    return portfolios


def new_portfolio(name, owner="admin", description=""):
    with Session() as session:
        statement = text(
            """
            INSERT INTO Portfolios (Name, Owner, Description)
            VALUES (:name, :owner, :description)
            RETURNING PortfolioID;
        """
        )
        result = session.execute(
            statement, {"name": name, "owner": owner, "description": description}
        ).first()
        if result:
            portfolio_id = result[0]
        else:
            raise ValueError("Portfolio not created")
        session.commit()
    return Portfolio(portfolio_id, name=name)


def portfolio_for_name(name) -> Portfolio:

    with Session() as session:
        statement = text(
            """
            SELECT PortfolioID
            FROM Portfolios
            WHERE Name = :name;
        """
        )
        result = session.execute(statement, {"name": name}).first()
        if result:
            portfolio_id = result[0]
        else:
            return new_portfolio(name)
    return Portfolio(portfolio_id, name=name)


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
