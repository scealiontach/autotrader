import itertools
import math
import sys
import warnings
from datetime import date, timedelta
from decimal import Decimal

from analyzer import cumulative_return
from constants import BUY, HOLD, REQUIRED_HOLDING_DAYS, SELL
from market_data_cache import CACHE
from portfolio import Portfolio, get_portfolios
from recommender import Action, Recommendation
from reporting import csv_log
from utils import round_down

warnings.filterwarnings("ignore")

INITIAL_DEPOSIT_DESCRIPTION = "Initial deposit"


def make_recommendations(portfolio: Portfolio, as_of_eod) -> list[Recommendation]:
    """
    Make trading recommendations based on the given portfolio and the market conditions
    as of a given date.
    """
    recommendations = []
    positions = portfolio.positions()

    market = portfolio.market_as_of(as_of_eod)
    last_recommender = None
    for position in positions:
        symbol = position["symbol"]
        recommender = portfolio.recommender_for(symbol, as_of_eod)
        last_recommender = recommender
        if market.adline():
            rec = recommender.mean_reversion(period=200)
        else:
            rec = recommender.macd()
        rec.as_of = as_of_eod
        recommendations.append(rec)
    if last_recommender:
        last_recommender.record_recommendations(recommendations)
    return recommendations


def make_plan(portfolio: Portfolio, recommendations: list, trade_date) -> list[Action]:
    """
    Given a portfolio and a list of recommendations make a plan of actions
    to execute the recommendations.

    Args:
        portfolio (Portfolio): The portfolio to make the plan for
        recommendations (list): A list of recommendations
        trade_date (date): The date to make the plan for
    returns:
        list: A list of actions to execute
    """
    portfolio_value = portfolio.value(trade_date)
    cash = portfolio.wallet.cash_balance(trade_date)
    reserve_cash = (portfolio.reserve_cash_percent / 100) * (cash + portfolio_value)
    max_exposure_percentage = portfolio.max_exposure

    investable_cash = cash - reserve_cash

    actions = []
    total_value = portfolio.value(trade_date) + cash

    # sort the recommendations by the strength of the signal
    recommendations.sort(key=lambda x: x.strength, reverse=True)

    if total_value > 0:
        min_shares = Decimal(math.pow(10, math.floor(math.log10(total_value) / 2)))
        min_reinvest_shares = Decimal(max(math.floor(min_shares / 10), 1))
    else:
        min_shares = Decimal(1)
        min_reinvest_shares = Decimal(1)

    max_investment_for_stock = Decimal((total_value * max_exposure_percentage) / 100)

    proceeds_collector = []
    actions.extend(
        _process_hold_recommendations(
            portfolio,
            recommendations,
            trade_date,
            min_reinvest_shares,
            max_investment_for_stock,
            investable_cash,
            proceeds_collector,
        )
    )
    investable_cash = Decimal(sum(proceeds_collector))

    proceeds_collector = []
    actions.extend(
        _process_sell_recommendations(
            portfolio, recommendations, trade_date, investable_cash, proceeds_collector
        )
    )
    investable_cash = Decimal(sum(proceeds_collector))

    proceeds_collector = []
    actions.extend(
        _process_buy_recommendations(
            portfolio,
            recommendations,
            trade_date,
            min_shares,
            min_reinvest_shares,
            max_investment_for_stock,
            investable_cash,
            proceeds_collector,
        )
    )

    return actions


def _process_hold_recommendations(
    portfolio: Portfolio,
    recommendations: list[Recommendation],
    trade_date,
    min_reinvest_shares: Decimal,
    max_investment_for_stock,
    investable_cash: Decimal,
    proceeds_collector: list,
) -> list[Action]:
    actions = []
    hold_recommendations = [rec for rec in recommendations if rec.action == HOLD]

    for rec in hold_recommendations:
        product = portfolio.product_for(rec.symbol)
        last_price = product.fetch_last_closing_price(trade_date) or Decimal(0)
        position = portfolio.position_for(product.symbol)
        current_quantity = product.fetch_current_quantity(trade_date)
        current_investment = current_quantity * last_price

        if rec.last is None:
            continue

        if rec.action in (HOLD, BUY) and current_quantity > 0:
            if not position.meets_holding_period(trade_date, REQUIRED_HOLDING_DAYS):
                continue
            over_investment = current_investment - max_investment_for_stock
            if over_investment > 0 and portfolio.is_rebalance_month(trade_date):
                shares_to_sell = calculate_shares_to_sell(
                    over_investment, last_price, min_reinvest_shares
                )
                if shares_to_sell > 0 and shares_to_sell <= current_quantity:
                    action = Action(rec, "divest", shares_to_sell)
                    action.action = SELL
                    actions.append(action)
                    investable_cash += Decimal(shares_to_sell * rec.last)
    proceeds_collector.append(investable_cash)
    return actions


def calculate_shares_to_sell(
    over_investment: Decimal, last_price: Decimal, min_reinvest_shares: Decimal
) -> Decimal:
    shares_to_sell = over_investment / last_price
    if min_reinvest_shares < 1:
        shares_to_sell = round_down(shares_to_sell, d=4)
    else:
        shares_to_sell = round_down(shares_to_sell, d=0)
    return shares_to_sell


def _process_sell_recommendations(
    portfolio: Portfolio,
    recommendations: list[Recommendation],
    trade_date,
    investable_cash: Decimal,
    proceeds_collector: list,
) -> list[Action]:
    actions = []
    sell_recommendations = [rec for rec in recommendations if rec.action == SELL]

    for rec in sell_recommendations:
        symbol = rec.symbol
        product = portfolio.product_for(symbol)
        position = portfolio.position_for(symbol)
        current_quantity = product.fetch_current_quantity(trade_date)
        if rec.last is None:
            continue
        if rec.action == SELL and current_quantity > 0:
            if not position.meets_holding_period(trade_date, REQUIRED_HOLDING_DAYS):
                continue
            shares_to_sell = current_quantity
            if shares_to_sell > 0 and shares_to_sell <= current_quantity:
                actions.append(Action(rec, "exit", shares_to_sell))
                investable_cash += Decimal(shares_to_sell * rec.last)

    proceeds_collector.append(investable_cash)
    return actions


def _process_buy_recommendations(
    portfolio: Portfolio,
    recommendations: list[Recommendation],
    trade_date,
    suggested_min_shares: Decimal,
    suggested_min_reinvest_shares: Decimal,
    max_investment_for_stock: Decimal,
    investable_cash: Decimal,
    proceeds_collector: list,
) -> list[Action]:
    actions = []
    buy_recommendations = [rec for rec in recommendations if rec.action == BUY]

    for rec in buy_recommendations:
        product = portfolio.product_for(rec.symbol)
        last_price = product.fetch_last_closing_price(trade_date) or 0
        current_quantity = product.fetch_current_quantity(trade_date)
        current_investment = current_quantity * last_price

        if rec.last is None:
            continue

        if product.sector in ("Cryptocurrency"):
            min_shares = 0.01
            min_reinvest_shares = 0.0001
        else:
            min_shares = suggested_min_shares
            min_reinvest_shares = suggested_min_reinvest_shares
        over_investment = current_investment - max_investment_for_stock
        if over_investment > 0:
            continue

        if rec.action == BUY and investable_cash > 0:
            additional_investment_allowed = (
                max_investment_for_stock - current_investment
            )
            additional_investment_allowed = min(
                additional_investment_allowed, investable_cash
            )
            max_additional_shares = additional_investment_allowed / rec.last
            if min_reinvest_shares < 1:
                max_additional_shares = round_down(max_additional_shares, d=4)
            else:
                max_additional_shares = round_down(max_additional_shares, d=0)
            portfolio_move = "invest"
            if current_investment == 0:
                portfolio_move = "enter"
                if max_additional_shares < min_shares:
                    continue

            if max_additional_shares > 0:
                actions.append(Action(rec, portfolio_move, max_additional_shares))
                investable_cash -= max_additional_shares * rec.last
    proceeds_collector.append(investable_cash)
    return actions


def get_trading_dates(connection, max_days=1460):
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT m.Date FROM MarketData m, products p
            where m.Date > date(now()) - %s and p.ProductID = m.ProductID
            and p.sector not in ('Cryptocurrency')
            ORDER BY m.Date
            """,
            (max_days,),
        )
        return [row[0] for row in cur.fetchall()]


def reset_data(connection):
    with connection.cursor() as cur:
        cur.execute("DELETE FROM PortfolioPositions")
        cur.execute("DELETE FROM Transactions")
        cur.execute("DELETE FROM CashTransactions")
        cur.execute("DELETE FROM TradingRecommendations")
        cur.execute("DELETE FROM MarketMovement")
        connection.commit()
    compute_advance_decline_table(connection)


def exercise_strategy(portfolio: Portfolio, report=True, bank_pc=33):

    wallet = portfolio.wallet
    trading_dates = get_trading_dates(portfolio.connection)
    if len(trading_dates) <= 0:
        return Decimal(0)

    cache = CACHE
    cache.set_earliest_date(trading_dates[0])

    first_date = trading_dates[0]
    last_reinvestment_date = trading_dates[0]
    print(f"First Date: {first_date}")
    # with Profiler(interval=0.001) as profiler:
    for trade_date in trading_dates:
        last_trade_date = trade_date
        recommendations = make_recommendations(portfolio, trade_date)
        planned_actions = make_plan(portfolio, recommendations, trade_date)

        execute_plan(portfolio, trade_date, planned_actions)
        portfolio.update_positions(trade_date)

        last_reinvestment_date = reinvest_or_bank(
            bank_pc, portfolio, last_reinvestment_date, trade_date
        )

        total_value = portfolio.value(trade_date)
        cash = wallet.cash_balance(trade_date)
        banked_cash = wallet.bank_balance(trade_date)
        total_invested = wallet.invest_balance(trade_date)
        complete_roi = (
            cumulative_return(total_invested, banked_cash + total_value + cash) * 100
        )
        portfolio.report_status(trade_date, complete_roi, first_date)

    # profiler.print()
    total_value = portfolio.value(last_trade_date)
    cash = portfolio.wallet.cash_balance(last_trade_date)
    roi = ((banked_cash + total_value + cash - total_invested) / total_invested) * 100
    if report:
        portfolio.report_status(last_trade_date, roi, first_date)
    return roi


def execute_plan(portfolio: Portfolio, trade_date, planned_actions: list[Action]):
    for action in planned_actions:
        product = portfolio.product_for(action.symbol)
        csv_log(
            trade_date,
            "TRADE",
            [
                action.symbol,
                product.sector,
                action.action,
                action.portfolio_move,
                action.shares,
                action.last,
            ],
        )
        portfolio._execute_trade(
            product.product_id,
            action.action,
            action.shares,
            action.last,
            trade_date,
        )


def reinvest_or_bank(bank_pc, portfolio: Portfolio, last_reinvestment_date, trade_date):
    total_value = portfolio.value(trade_date)
    wallet = portfolio.wallet
    cash = wallet.cash_balance(trade_date)
    total_invested = wallet.invest_balance(trade_date)
    banking_roi = cumulative_return(total_invested, total_value + cash) * 100
    if trade_date > (
        last_reinvestment_date + timedelta(days=portfolio.reinvest_period)
    ):
        withdrawal = portfolio.take_profit(bank_pc, cash, banking_roi)
        wallet.sweep(
            withdrawal,
            portfolio.reinvest_amt,
            trade_date,
            f"Reinvest/Bank @ {banking_roi:.2f}% ROI",
        )
        return trade_date
    else:
        return last_reinvestment_date


INITIAL_WALLET_RANGE = [0, 1000]
REINVEST_PERIOD_RANGE = [7, 15, 30]
RESERVE_CASH_RANGE = [2]
REINVEST_AMT_RANGE = [100]
BANK_THRESHOLD = [100, 200, 1000]
WITH_CRYPTO = ["yes", "no", "only"]


def parameter_search():
    # with Profiler(interval=0.1) as profiler:
    print("Executing parameter search")
    param_combinations = itertools.product(
        RESERVE_CASH_RANGE,
        INITIAL_WALLET_RANGE,
        REINVEST_PERIOD_RANGE,
        REINVEST_AMT_RANGE,
        BANK_THRESHOLD,
        WITH_CRYPTO,
    )

    optimals = []
    portfolio = get_portfolios()[0]
    for combination in param_combinations:
        print(f"Testing parameters {combination}")
        initialize(portfolio.connection)

        reserve_cash_percent = Decimal(combination[0])
        initial_wallet = Decimal(combination[1])
        reinvest_period = combination[2]
        reinvest_amt = Decimal(combination[3])
        bank_threshold = combination[4]
        if combination[5] == "no":
            portfolio.set_options(sectors_forbidden=["Cryptocurrency"])
        elif combination[5] == "only":  # only crypto
            portfolio.set_options(sectors_allowed=["Cryptocurrency"])

        portfolio.set_options(
            reserve_cash_percent=reserve_cash_percent,
            reinvest_period=reinvest_period,
            reinvest_amt=reinvest_amt,
            bank_threshold=bank_threshold,
            # sectors_forbidden=["Cryptocurrency"],
        )

        portfolio.wallet.invest(
            initial_wallet, "1975-04-24", INITIAL_DEPOSIT_DESCRIPTION
        )
        result = exercise_strategy(
            portfolio,
            report=False,
        )

        cash = portfolio.wallet.cash_balance(date.today())
        portfolio_value = portfolio.value(date.today())
        total_invested = portfolio.wallet.invest_balance(date.today())
        bank = portfolio.wallet.bank_balance(date.today())
        total = cash + portfolio_value + bank

        optimals.append(
            {
                "combination": {
                    "reserve_cash_percent": Decimal(combination[0]),
                    "initial_wallet": combination[1],
                    "reinvest_period": combination[2],
                    "reinvest_amt": Decimal(combination[3]),
                    "bank_threshold": combination[4],
                    "crypto": combination[5],
                },
                "roi": result,
                "invested": total_invested,
                "total": total,
            }
        )

    initialize(portfolio.connection)

    for result in optimals:
        params = result["combination"]
        roi = result["roi"]
        invested = result["invested"]
        total = result["total"]
        print(f"RESULT params={params}, roi={roi}, invested={invested}, total={total}")

    if len(optimals) > 0:
        optimals.sort(key=lambda x: x["roi"], reverse=True)
        result = optimals[0]
        params = result["combination"]
        roi = result["roi"]
        invested = result["invested"]
        total = result["total"]
        print(
            f"OPTIMAL BY ROI params={params}, roi={roi}, invested={invested}, total={total}"
        )

        optimals.sort(key=lambda x: x["total"], reverse=True)
        result = optimals[0]
        params = result["combination"]
        roi = result["roi"]
        invested = result["invested"]
        total = result["total"]
        print(
            f"OPTIMAL BY TOTAL params={params}, roi={roi}, invested={invested}, total={total}"
        )


def compute_advance_decline_table(connection):
    """
    Compute a table showing the date, the number of products that have advanced,
    and the number that have declined since the previous trading day.
    """
    query = """
    WITH RankedPrices AS (
        SELECT
            Date,
            ProductID,
            ClosingPrice,
            LAG(ClosingPrice) OVER (PARTITION BY ProductID ORDER BY Date) AS PreviousClosingPrice
        FROM MarketData
    ),
    DailyChanges AS (
        SELECT
            Date,
            SUM(CASE WHEN ClosingPrice > PreviousClosingPrice THEN 1 ELSE 0 END) AS Advanced,
            SUM(CASE WHEN ClosingPrice < PreviousClosingPrice THEN 1 ELSE 0 END) AS Declined
        FROM RankedPrices
        WHERE PreviousClosingPrice IS NOT NULL  -- Exclude the first day for each product
        GROUP BY Date
        ORDER BY Date
    )
    insert into MarketMovement SELECT * FROM DailyChanges;
    """

    try:
        with connection.cursor() as cur:
            cur.execute(query)
            # Fetch and return the result
            connection.commit()
    except Exception as e:
        print(f"(E06) An error occurred: {e}")


def initialize(connection):
    reset_data(connection)


def main():
    # download_products.download_products()
    if len(sys.argv) >= 5:
        reserve_cash_percent = Decimal(sys.argv[1])
        initial_wallet = Decimal(sys.argv[2])
        reinvest_period = int(sys.argv[3])
        reinvest_amt = Decimal(sys.argv[4])
        if len(sys.argv) == 6:
            bank_threshold = int(sys.argv[5])
        else:
            bank_threshold = 75

        portfolio = get_portfolios()[0]
        # with Profiler(interval=0.0001) as profile:

        initialize(portfolio.connection)
        portfolio.set_options(
            reserve_cash_percent=reserve_cash_percent,
            reinvest_period=reinvest_period,
            reinvest_amt=reinvest_amt,
            bank_threshold=bank_threshold,
        )

        portfolio.wallet.invest(
            initial_wallet, "1975-04-24", INITIAL_DEPOSIT_DESCRIPTION
        )
        exercise_strategy(
            portfolio,
            report=True,
        )
        # profile.print()
    else:
        parameter_search()


if __name__ == "__main__":
    main()
