import datetime
import sys
from datetime import timedelta
from decimal import Decimal
from types import NoneType
from typing import Union

from cachetools import LFUCache

from analyzer import cumulative_return
from constants import BUY, BUY_TX_FEE, SELL, SELL_TX_FEE
from market import Market
from product import Product
from recommender import Recommender
from reporting import csv_log
from utils import get_database_connection, round_down, round_up

position_cache = LFUCache(maxsize=4096)


class Lot:

    def __init__(
        self, lot_id: int, quantity: Decimal, price: Decimal, purchase_date
    ) -> NoneType:
        self.lot_id = lot_id
        self.quantity = quantity
        self.price = price
        self.purchase_date = purchase_date


class Position:
    def __init__(self, portfolio, product):
        self.connection = portfolio.connection
        self.portfolio = portfolio
        self.symbol = product.symbol
        self.product = product

    def __getstate__(self):
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["connection"]
        return state

    def __setstate__(self, state):
        # Restore instance attributes (i.e., filename and lineno).
        self.__dict__.update(state)
        self.connection = get_database_connection()

    def meets_holding_period(self, as_of_date, required_days=3):
        """
        Check if the oldest holding of a given symbol meets the required holding period.

        :param symbol: Stock symbol to check.
        :param required_days: Required holding period in days.
        :return: True if the holding period is met, False otherwise.
        """
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(pp.PurchaseDate)
                FROM PortfolioPositions pp
                WHERE pp.productid = %s and pp.PortfolioID = %s
            """,
                (self.product.product_id, self.portfolio.portfolio_id),
            )
            result = cur.fetchone()
            if result and result[0]:
                earliest_purchase_date = result[0]
                if as_of_date - earliest_purchase_date >= timedelta(days=required_days):
                    return True
            else:
                return True
        return False

    def get_lots(self, portfolio_id: int) -> list[Lot]:
        lots = []
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT LotID, Quantity, PurchasePrice, PurchaseDate
                FROM Lots
                WHERE ProductID = %s and PortfolioID = %s
                ORDER BY PurchaseDate ASC;
            """,
                (self.product.product_id, portfolio_id),
            )
            for row in cur.fetchall():
                lots.append(Lot(row[0], row[1], row[2], row[3]))
        return lots

    def fetch_current_quantity(self, as_of_date) -> Decimal:
        """
        Fetch the current quantity of shares owned for a given stock symbol.

        :param symbol: The stock symbol to query.
        :return: The quantity of shares currently owned for the symbol.
        """
        cache_key = f"{self.product.product_id}-{self.portfolio.portfolio_id}-quantity-{as_of_date}"
        if cache_key in position_cache:
            return position_cache[cache_key]
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT sum(pp.Quantity)
                FROM PortfolioPositions pp
                WHERE pp.ProductId = %s and pp.PortfolioID = %s;
            """,
                (self.product.product_id, self.portfolio.portfolio_id),
            )
            result = cur.fetchone()
            if result and result[0]:
                position_cache[cache_key] = result[0]
                return result[0]
            else:
                position_cache[cache_key] = 0
                return Decimal(
                    0
                )  # Return 0 if the symbol is not found in the portfolio


class Wallet:
    def __init__(self, connection, portfolio_id):
        self.connection = connection
        self.portfolio_id = portfolio_id

    def __getstate__(self):
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["connection"]
        return state

    def __setstate__(self, state):
        # Restore instance attributes (i.e., filename and lineno).
        self.__dict__.update(state)
        self.connection = get_database_connection()

    def current_balances(self):
        cash = self.cash_balance(datetime.datetime.now())
        bank = self.bank_balance(datetime.datetime.now())
        invest = self.invest_balance(datetime.datetime.now())
        return cash, bank, invest

    def cash_balance(self, as_of_date) -> Decimal:
        """
        Calculate the current cash balance for a given portfolio as of a specified date.

        :param portfolio_id: The ID of the portfolio.
        :param as_of_date: The date up to which to calculate the balance.
        :return: The cash balance as a float.
        """
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = %s AND TransactionDate <= %s;
            """,
                (self.portfolio_id, as_of_date),
            )
            result = cur.fetchone()
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
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = %s AND TransactionDate <= %s AND TransactionType in ('BANK');
            """,
                (self.portfolio_id, as_of_date),
            )
            result = cur.fetchone()
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
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(Amount)
                FROM CashTransactions
                WHERE PortfolioID = %s AND TransactionDate <= %s AND TransactionType in ('INVEST');
            """,
                (self.portfolio_id, as_of_date),
            )
            result = cur.fetchone()
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
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO CashTransactions (PortfolioID, TransactionType, Amount, TransactionDate, Description)
                VALUES (%s, %s, %s, %s, %s);
            """,
                (
                    self.portfolio_id,
                    transaction_type,
                    -round_up(amount, d=2),
                    transaction_date,
                    description,
                ),
            )
            self.connection.commit()
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
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO CashTransactions (PortfolioID, TransactionType, Amount, TransactionDate, Description)
                VALUES (%s, %s, %s, %s, %s);
            """,
                (
                    self.portfolio_id,
                    transaction_type,
                    round_down(amount, d=2),
                    transaction_date,
                    description,
                ),
            )
            self.connection.commit()
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
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT TransactionDate
                FROM CashTransactions
                WHERE PortfolioID = %s AND Description LIKE %s
                ORDER BY TransactionDate DESC
                LIMIT 1;
            """,
                (self.portfolio_id, f"%{description}%"),
            )
            result = cur.fetchone()
            if result:
                return result[0]
        return None

    def first_transaction_like(self, description):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT TransactionDate
                FROM CashTransactions
                WHERE PortfolioID = %s AND Description LIKE %s
                ORDER BY TransactionDate ASC
                LIMIT 1;
            """,
                (self.portfolio_id, f"%{description}%"),
            )
            result = cur.fetchone()
            if result:
                return result[0]
        return None


class Portfolio:
    def __init__(
        self,
        portfolio_id: int,
        connection,
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
        self.connection = connection
        self.name = name
        self.wallet = Wallet(self.connection, self.portfolio_id)

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

    def __getstate__(self):
        state = self.__dict__.copy()
        # Remove the unpicklable entries.
        del state["connection"]
        return state

    def __setstate__(self, state):
        # Restore instance attributes (i.e., filename and lineno).
        self.__dict__.update(state)
        self.connection = get_database_connection()

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
        return Product.from_symbol(self.connection, symbol)

    def recommender_for(self, symbol: str, target_date) -> Recommender:
        product = self.product_for(symbol)
        return Recommender(
            self.connection,
            self.portfolio_id,
            product,
            target_date,
            strategy=self.strategy,
        )

    def market_as_of(self, as_of_date) -> Market:
        return Market(self.connection, as_of_date)

    def is_rebalance_month(self, as_of_date):
        return as_of_date.month in self.rebalance_months

    def get_recommendation(self, symbol):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT Action
                FROM TradingRecommendations
                JOIN Products ON TradingRecommendations.ProductID = Products.ProductID
                WHERE Symbol = %s
                AND PortfolioID = %s
                ORDER BY RecommendationDate DESC
                LIMIT 1;
            """,
                (symbol, self.portfolio_id),
            )
            result = cur.fetchone()
            if result:
                return result[0]
        return "None"

    def positions(self) -> list[dict]:
        """Fetch the current portfolio from the database."""
        poslist = []
        with self.connection.cursor() as cur:
            query = """
                    SELECT p.ProductID, p.Symbol, pp.Quantity, pp.Last, pp.Invest
                    FROM Products p
                    LEFT JOIN PortfolioPositions pp ON (p.ProductID = pp.ProductID AND pp.PortfolioID = %s)
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

            cur.execute(
                query,
                (self.portfolio_id,),
            )
            poslist = [
                {
                    "product_id": row[0],
                    "symbol": row[1],
                    "quantity": row[2],
                    "last": row[3],
                    "invest": row[4],
                }
                for row in cur.fetchall()
            ]
        return poslist

    def update_positions(self, as_of_d):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                DELETE FROM PortfolioPositions WHERE PortfolioID = %s
            """,
                (self.portfolio_id,),
            )
            cur.execute(
                """
            INSERT INTO PortfolioPositions (PortfolioID, ProductID, Quantity, purchasedate, lastupdated, invest )
            SELECT PortfolioID, ProductID, SUM(Quantity), max(purchasedate), now(), sum(quantity * purchaseprice)
            FROM Lots
            where PortfolioID = %s
            GROUP BY PortfolioID, ProductID
            ON CONFLICT (PortfolioID, ProductID) DO UPDATE
            SET Quantity = EXCLUDED.Quantity;
            """,
                (self.portfolio_id,),
            )
            cur.execute(
                """
                        select pp.productid from portfoliopositions pp where portfolioID = %s
                        """,
                (self.portfolio_id,),
            )
            for row in cur.fetchall():
                product = Product.from_id(self.connection, row[0])
                last = product.fetch_last_closing_price(as_of_d)
                cur.execute(
                    """
                    UPDATE PortfolioPositions SET last = %s
                    WHERE PortfolioID = %s AND ProductID = %s
                """,
                    (last, self.portfolio_id, row[0]),
                )
            self.connection.commit()

    def value(self, as_of_date):
        """
        Calculates the value of the portfolio as of a given closing date.

        :param as_of_date: The closing date for the calculation (datetime.date object).
        :return: The total value of the portfolio as a float.
        """
        total_value = 0

        with self.connection.cursor() as cur:
            # Fetch portfolio positions
            cur.execute(
                """
                SELECT ProductID, sum(Quantity)
                FROM PortfolioPositions
                where PortfolioID = %s
                group by ProductID
                order by ProductID;
            """,
                (self.portfolio_id,),
            )
            positions = cur.fetchall()
            closing_prices = {}
            for product_id, quantity in positions:
                # Fetch closing price for each position as of the given date
                cur.execute(
                    """
                    SELECT ClosingPrice
                    FROM MarketData
                    WHERE ProductID = %s AND Date <= %s
                    ORDER BY Date DESC
                    LIMIT 1;
                """,
                    (product_id, as_of_date),
                )
                result = cur.fetchone()

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
        bank = self.wallet.bank_balance(report_date)
        total_cash = self.wallet.cash_balance(report_date)
        total_value = self.value(report_date)
        total_invest = self.wallet.invest_balance(report_date)
        market = Market(self.connection, report_date)
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
        last_reinvestment_date = self.wallet.last_transaction_like("%Reinvest/Bank%")
        total_value = self.value(trade_date)
        wallet = self.wallet
        cash = wallet.cash_balance(trade_date)
        total_invested = wallet.invest_balance(trade_date)
        banking_roi = cumulative_return(total_invested, total_value + cash) * 100
        if last_reinvestment_date is None or trade_date > (
            last_reinvestment_date + timedelta(days=self.reinvest_period)
        ):
            withdrawal = self.take_profit(self.bank_pc, cash, banking_roi)
            wallet.sweep(
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
                    invested += l.quantity * l.price
                value = Decimal(position["quantity"]) * Decimal(position["last"])
                total_invest += invested
                investment_value += value

        cash, bank, invest = self.wallet.current_balances()
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
                product = Product.from_id(self.connection, position["product_id"])
                lots = self.position_for(position["symbol"]).get_lots(self.portfolio_id)
                invested = Decimal(0)
                lot_count = len(lots)
                for l in lots:
                    invested += l.quantity * l.price
                value = Decimal(position["quantity"]) * Decimal(position["last"])
                unrealized_gain = value - invested
                print(
                    f"{position['symbol']:<6s}: {product.sector[0:14]:<14s} {recommendation:4s} {position['quantity']:> 10.4f} @ {position['last']:> 10.2f} Invested: {invested:>10.2f}({lot_count:3d}) Value: {(value):>10.2f} Unrealized: {unrealized_gain:>10.2f}"
                )
                total_invest += invested
                investment_value += value
        total_unrealized = investment_value - total_invest
        if total_invest > 0:
            potential_roi = ((investment_value / total_invest) - 1) * 100
        else:
            potential_roi = 0

        cash, bank, invest = self.wallet.current_balances()
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
        product = Product.from_id(self.connection, product_id=product_id)
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO Transactions (PortfolioID, ProductID, TransactionType, Quantity, Price, TransactionDate)
                VALUES (%s, %s, %s, %s, %s, %s)
            """,
                (
                    self.portfolio_id,
                    product_id,
                    transaction_type,
                    quantity,
                    price,
                    transaction_date,
                ),
            )
            self.manage_lots(
                product, quantity, transaction_type, price, transaction_date
            )
            if transaction_type == BUY:
                self.wallet.add_debit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Buy {quantity} shares of {product.symbol}",
                    report=report,
                )
                self.wallet.add_debit(
                    BUY_TX_FEE,
                    transaction_date,
                    f"Buy TX FEE {quantity} shares of {product.symbol}",
                    report=report,
                )
            elif transaction_type == SELL:
                self.wallet.add_deposit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Sell {quantity} shares of {product.product_id}",
                    report=report,
                )
                self.wallet.add_debit(
                    SELL_TX_FEE,
                    transaction_date,
                    f"Sell TX FEE {quantity} shares of {product.symbol}",
                    report=report,
                )

            self.connection.commit()

    def last_active(self):
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(recommendationdate)
                FROM TradingRecommendations
                WHERE PortfolioID = %s;
            """,
                (self.portfolio_id,),
            )
            result = cur.fetchone()
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
            with self.connection.cursor() as cur:
                if transaction_type == BUY:
                    if price is None or as_of_date is None:
                        raise ValueError(
                            "Purchase price and date must be provided for buys."
                        )
                    # Insert new lot for a purchase
                    cur.execute(
                        """
                        INSERT INTO Lots (PortfolioID, ProductID, Quantity, PurchasePrice, PurchaseDate)
                        VALUES (%s, %s, %s, %s, %s);
                    """,
                        (
                            self.portfolio_id,
                            product.product_id,
                            quantity,
                            price,
                            as_of_date,
                        ),
                    )
                elif transaction_type == SELL:
                    # Retrieve existing lots for the product in FIFO order
                    cur.execute(
                        """
                        SELECT LotID, Quantity FROM Lots
                        WHERE PortfolioID = %s AND ProductID = %s
                        ORDER BY PurchaseDate ASC, LotID ASC;
                    """,
                        (self.portfolio_id, product.product_id),
                    )
                    lots = cur.fetchall()

                    for lot_id, lot_quantity in lots:
                        if quantity <= 0:
                            break  # Exit loop if all shares have been sold

                        if quantity >= lot_quantity:
                            # Delete the entire lot
                            cur.execute("DELETE FROM Lots WHERE LotID = %s;", (lot_id,))
                            quantity -= (
                                lot_quantity  # Reduce the remaining quantity to sell
                            )
                        else:
                            # Partially sell from the current lot
                            cur.execute(
                                """
                                UPDATE Lots SET Quantity = Quantity - %s WHERE LotID = %s;
                            """,
                                (quantity, lot_id),
                            )
                            quantity = 0  # All shares sold
                    cur.execute(
                        "DELETE FROM Lots WHERE Quantity = 0;"
                    )  # Remove empty lots
                else:
                    raise ValueError("Transaction type must be 'buy' or 'sell'.")

                self.connection.commit()  # Commit the transaction
        except Exception as e:
            print(f"An error occurred: {e}")


# get all the portfolios in the database
def get_portfolios() -> list[Portfolio]:
    connection = get_database_connection()
    answer = []
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT distinct PortfolioID, Name
            FROM Portfolios;
        """
        )
        portfolios = cur.fetchall()
        for p in portfolios:
            portfolio = Portfolio(p[0], connection, name=p[1])
            answer.append(portfolio)
    return answer


def new_portfolio(name, owner="admin", description=""):
    connection = get_database_connection()
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO Portfolios (Name, Owner, Description)
            VALUES (%s,%s, %s)
            RETURNING PortfolioID;
        """,
            (name, owner, description),
        )
        row = cur.fetchone()
        if row:
            portfolio_id = row[0]
        connection.commit()
    return Portfolio(portfolio_id, connection, name=name)


def portfolio_for_name(name) -> Portfolio:
    connection = get_database_connection()

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT PortfolioID
            FROM Portfolios
            WHERE Name = %s;
        """,
            (name,),
        )
        row = cur.fetchone()
        if row:
            portfolio_id = row[0]
        else:
            return new_portfolio(name)
    return Portfolio(portfolio_id, connection, name=name)


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
