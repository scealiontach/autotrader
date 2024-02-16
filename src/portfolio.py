from datetime import timedelta
from decimal import Decimal
from types import NoneType
from typing import Union
from constants import BUY, BUY_TX_FEE, SELL, SELL_TX_FEE

from market import Market
from product import Product
from recommender import Recommender
from reporting import csv_log
from utils import get_database_connection, round_down, round_up


class Position:
    def __init__(self, connection, product):
        self.connection = connection
        self.symbol = product.symbol
        self.product = product

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
                SELECT MAX(PurchaseDate)
                FROM PortfolioPositions
                JOIN Products ON PortfolioPositions.ProductID = Products.ProductID
                WHERE Symbol = %s;
            """,
                (self.symbol,),
            )
            result = cur.fetchone()
            if result and result[0]:
                earliest_purchase_date = result[0]
                if as_of_date - earliest_purchase_date >= timedelta(days=required_days):
                    return True
        return False


class Wallet:
    def __init__(self, connection, portfolio_id):
        self.connection = connection
        self.portfolio_id = portfolio_id

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
            )

    def add_deposit(
        self,
        amount: Decimal,
        transaction_date,
        description="",
        transaction_type="DEPOSIT",
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
            )

    def bank(self, amount: Decimal, transaction_date, description="") -> None:
        self.add_debit(
            amount, transaction_date, description=description, transaction_type="BANK"
        )

    def invest(self, amount: Decimal, transaction_date, description="") -> None:
        self.add_deposit(
            amount, transaction_date, description=description, transaction_type="INVEST"
        )

    def sweep(
        self, withdraw: Decimal, invest: Decimal, transaction_date, description=""
    ) -> None:
        if withdraw > invest:
            to_bank = withdraw - invest
            self.bank(to_bank, transaction_date, description=description)
        elif invest > withdraw:
            to_invest = invest - withdraw
            if self.bank_balance(transaction_date) > to_invest:
                self.bank(-to_invest, transaction_date, description=description)
            else:
                self.invest(to_invest, transaction_date, description=description)


CRYPTO_ALLOWED = 0
CRYPTO_FORBIDDEN = 1
CRYPTO_ONLY = 2


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
        rebalance_months=None,
        dividend_only=False,
        sectors_allowed=None,
        sectors_forbidden=None,
        max_exposure: int = 20,
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
            rebalance_months=new_rebalance_months,
            dividend_only=dividend_only,
            sectors_allowed=sectors_allowed,
            sectors_forbidden=sectors_forbidden,
            max_exposure=max_exposure,
        )

    def set_options(
        self,
        reserve_cash_percent: Union[Decimal, NoneType] = None,
        reinvest_period: Union[int, NoneType] = None,
        reinvest_amt: Union[Decimal, NoneType] = None,
        bank_threshold: Union[int, NoneType] = None,
        rebalance_months: Union[list[int], NoneType] = None,
        dividend_only: Union[bool, NoneType] = None,
        sectors_allowed: Union[list[str], NoneType] = None,
        sectors_forbidden: Union[list[str], NoneType] = None,
        max_exposure: Union[int, NoneType] = None,
    ):
        if reserve_cash_percent is not None:
            self.reserve_cash_percent = reserve_cash_percent
        if reinvest_period is not None:
            self.reinvest_period = reinvest_period
        if reinvest_amt is not None:
            self.reinvest_amt = reinvest_amt
        if bank_threshold is not None:
            self.bank_threshold = bank_threshold
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

    def position_for(self, symbol) -> Position:
        return Position(self.connection, self.product_for(symbol))

    def product_for(self, symbol) -> Product:
        return Product.from_symbol(self.connection, symbol)

    def recommender_for(self, symbol: str, target_date) -> Recommender:
        product = self.product_for(symbol)
        return Recommender(self.connection, self.portfolio_id, product, target_date)

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
                ORDER BY RecommendationDate DESC
                LIMIT 1;
            """,
                (symbol,),
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
            # Step 1: Aggregate transactions to calculate current positions
            cur.execute(
                """
                SELECT ProductID,
                        SUM(CASE WHEN TransactionType = 'BUY' THEN Quantity ELSE -Quantity END) AS NetQuantity,
                        SUM(CASE WHEN TransactionType = 'BUY' THEN (Quantity*price) ELSE -(price*quantity) END) AS NetCost,
                        MAX(TransactionDate) AS LastTransactionDate
                FROM Transactions
                GROUP BY ProductID
            """
            )

            positions = cur.fetchall()

            # Step 2: Update the PortfolioPositions table
            for product_id, net_quantity, net_cost, last_transaction_date in positions:
                product = Product.from_id(self.connection, product_id)
                last_price = product.fetch_last_closing_price(as_of_d)
                if net_quantity > 0:
                    # Update existing position or insert a new one
                    cur.execute(
                        """
                        INSERT INTO PortfolioPositions (PortfolioID, ProductID, Quantity, purchaseDate, last, invest)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (PortfolioID, ProductID) DO UPDATE SET Quantity = EXCLUDED.Quantity, purchaseDate = EXCLUDED.purchaseDate, last=EXCLUDED.last
                    """,
                        (
                            self.portfolio_id,
                            product_id,
                            net_quantity,
                            last_transaction_date,
                            last_price,
                            net_cost,
                        ),
                    )
                else:
                    # Delete the position if the net quantity is zero or negative
                    cur.execute(
                        """
                        DELETE FROM PortfolioPositions WHERE ProductID = %s
                    """,
                        (product_id,),
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

    def report_status(self, report_date, roi, first_day):
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
        )

    def sell(self, product_id, quantity, price, transaction_date):
        self._execute_trade(product_id, SELL, quantity, price, transaction_date)

    def buy(self, product_id, quantity, price, transaction_date):
        self._execute_trade(product_id, BUY, quantity, price, transaction_date)

    def view(self):
        for position in self.positions():
            if position["quantity"] and position["quantity"] > 0:
                recommendation = self.get_recommendation(position["symbol"])
                product = Product.from_id(self.connection, position["product_id"])
                print(
                    f"{position['symbol']:<6s}: {product.sector[0:14]:<14s} {recommendation:4s} {position['quantity']:> 10.4f} @ {position['last']:> 10.2f} Invested: {position['invest']:>10.2f} Value: {(position['quantity']*position['last']):>10.2f}"
                )

    def _execute_trade(
        self,
        product_id: int,
        transaction_type: str,
        quantity: Decimal,
        price,
        transaction_date,
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
            if transaction_type == BUY:
                self.wallet.add_debit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Buy {quantity} shares of {product.symbol}",
                )
                self.wallet.add_debit(
                    BUY_TX_FEE,
                    transaction_date,
                    f"Buy TX FEE {quantity} shares of {product.symbol}",
                )
            elif transaction_type == SELL:
                self.wallet.add_deposit(
                    Decimal(quantity) * price,
                    transaction_date,
                    f"Sell {quantity} shares of {product.product_id}",
                )
                self.wallet.add_debit(
                    SELL_TX_FEE,
                    transaction_date,
                    f"Sell TX FEE {quantity} shares of {product.symbol}",
                )

            self.connection.commit()


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


if __name__ == "__main__":
    for p in get_portfolios():
        print(("=" * 9) + f" {p.name}:{p.portfolio_id} " + ("=" * 9))
        p.view()
