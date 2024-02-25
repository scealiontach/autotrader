import itertools
import logging as log
import math
import subprocess
import sys
import time
import warnings
from datetime import datetime, timedelta
from decimal import Decimal

import psutil
from pyinstrument import Profiler
from sqlalchemy import text, update
from sqlalchemy.orm import object_session

from analyzer import cumulative_return
from constants import BUY, HOLD, REQUIRED_HOLDING_DAYS, SELL
from database import Session
from market_data_cache import CACHE
from portfolio import Portfolio, portfolio_for_name
from product import Product
from recommender import STRATEGIES, Action, Recommendation
from reporting import csv_log
from utils import round_down

warnings.filterwarnings("ignore")

INITIAL_DEPOSIT_DESCRIPTION = "Initial deposit"


def make_recommendations(portfolio: Portfolio, as_of_eod=None) -> list[Recommendation]:
    """
    Make trading recommendations based on the given portfolio and the market conditions
    as of a given date.
    """
    recommendations = []
    products = portfolio.eligible_products(as_of_date=as_of_eod)

    last_recommender = None
    for p in products:
        recommender = portfolio.recommender_for(p.symbol, as_of_eod)
        last_recommender = recommender
        rec = recommender.recommend()
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
    cash = portfolio.cash_balance(trade_date)
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
        product = Product.from_symbol(rec.symbol)
        last_price = product.fetch_last_closing_price(trade_date) or Decimal(0)
        position = portfolio.find_position(product.symbol)

        if position:
            current_quantity = position.fetch_current_quantity(trade_date)
            current_investment = current_quantity * last_price
            holding_met = position.meets_holding_period(
                trade_date, REQUIRED_HOLDING_DAYS
            )
        else:
            current_quantity = Decimal(0)
            current_investment = Decimal(0)
            holding_met = True

        if rec.last is None:
            continue

        if rec.action in (HOLD, BUY) and current_quantity > 0:
            if not holding_met:
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

        position = portfolio.find_position(symbol)
        if position:
            current_quantity = position.fetch_current_quantity(trade_date)
            met_holding = position.meets_holding_period(
                trade_date, REQUIRED_HOLDING_DAYS
            )
        else:
            current_quantity = Decimal(0)
            met_holding = True

        if rec.last is None:
            log.warning(f"Last price is None for {symbol}")
            continue
        if rec.action == SELL and current_quantity > 0:
            if not met_holding:
                log.debug(f"Position {symbol} does not meet holding period")
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
        product = Product.from_symbol(rec.symbol)
        position = portfolio.find_position(product.symbol)
        last_price = product.fetch_last_closing_price(trade_date) or 0
        if position:
            current_quantity = position.fetch_current_quantity(trade_date)
            current_investment = current_quantity * last_price
        else:
            current_quantity = 0
            current_investment = 0

        if rec.last is None:
            continue

        if product and product.sector and product.sector in ("Cryptocurrency"):
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


def sim_started(portfolio: Portfolio):
    with Session() as session:
        if object_session(portfolio) is None:
            session.add(portfolio)
        simulation_tracker_stmt = text(
            """
            SELECT first_date, run_length_days, last_sim_date FROM simulation_tracker WHERE Portfolio_ID = :portfolio_id
            """
        )
        row = session.execute(
            simulation_tracker_stmt, {"portfolio_id": portfolio.id}
        ).first()
        if row:
            return True
        else:
            return False


def get_trading_dates(portfolio: Portfolio, max_days=1260):
    with Session() as session:
        if object_session(portfolio) is None:
            session.add(portfolio)

        simulation_tracker_stmt = text(
            """
            SELECT first_date, run_length_days, last_sim_date FROM simulation_tracker WHERE Portfolio_ID = :portfolio_id
            """
        )
        row = session.execute(
            simulation_tracker_stmt, {"portfolio_id": portfolio.id}
        ).first()
        if row:
            first_date, run_length_days, last_sim_date = row
        else:
            first_date = portfolio.first_deposit()
            if first_date is None:
                raise ValueError("No first deposit date found")
            run_length_days = max_days
            last_sim_date = first_date - timedelta(days=1)
            session.expire_on_commit = False

        statement = text(
            """
            SELECT DISTINCT m.Date FROM MarketData m, products p
            where m.Date > :last_sim_date
            and m.Date <= :max_date
            and p.ProductID = m.ProductID
            and p.sector not in ('Cryptocurrency')
            ORDER BY m.Date ASC
            """
        )
        result = session.execute(
            statement,
            {
                "max_date": first_date + timedelta(days=run_length_days),
                "last_sim_date": last_sim_date,
            },
        )
        if result is None:
            return []
        trade_dates = list([row[0] for row in result])
        return trade_dates


def update_sim_date(portfolio: Portfolio, run_length_days, trade_date):
    with Session() as session:
        first_date = portfolio.first_deposit()
        update_stmt = text(
            """
            INSERT INTO simulation_tracker (Portfolio_ID, first_date, run_length_days, last_sim_date)
            VALUES (:portfolio_id, :first_date, :run_length_days, :trade_date)
            ON CONFLICT (Portfolio_ID) DO UPDATE SET
              run_length_days = :run_length_days, last_sim_date = :trade_date
            """
        )
        session.execute(
            update_stmt,
            {
                "trade_date": trade_date,
                "first_date": first_date,
                "run_length_days": run_length_days,
                "portfolio_id": portfolio.id,
            },
        )
        session.commit()


def reset_portfolio(portfolio: Portfolio, full=False):
    with Session() as session:
        session.add(portfolio)
        session.refresh(portfolio)
        del_portfoliopositions = text(
            """
            DELETE FROM PortfolioPositions where PortfolioID = :portfolio_id
            """
        )
        del_transactions = text(
            """
            DELETE FROM Transactions where PortfolioID = :portfolio_id
            """
        )
        if full:
            del_cashtransactions = text(
                """
                DELETE FROM CashTransactions where PortfolioID = :portfolio_id
                """
            )
        else:
            del_cashtransactions = text(
                """
                DELETE FROM CashTransactions where PortfolioID = :portfolio_id and Description != :initial_deposit_description
                """
            )
        del_tradingrecommendations = text(
            """
            DELETE FROM TradingRecommendations where PortfolioID = :portfolio_id
            """
        )
        del_lots = text(
            """
            DELETE FROM Lots where PortfolioID = :portfolio_id
            """
        )
        del_portfolio_performance = text(
            """
            DELETE FROM portfolio_performance where Portfolio_ID = :portfolio_id
            """
        )
        session.execute(del_portfoliopositions, {"portfolio_id": portfolio.id})
        session.execute(del_transactions, {"portfolio_id": portfolio.id})
        session.execute(
            del_cashtransactions,
            {
                "portfolio_id": portfolio.id,
                "initial_deposit_description": INITIAL_DEPOSIT_DESCRIPTION,
            },
        )
        session.execute(del_tradingrecommendations, {"portfolio_id": portfolio.id})
        session.execute(del_lots, {"portfolio_id": portfolio.id})
        session.execute(del_portfolio_performance, {"portfolio_id": portfolio.id})

        session.commit()


def reset_all_portfolios():
    with Session() as session:
        del_sim_tracker = text(
            """
            DELETE FROM simulation_tracker
            """
        )
        del_portfoliopositions = text(
            """
            DELETE FROM PortfolioPositions
            """
        )
        del_transactions = text(
            """
            DELETE FROM Transactions
            """
        )
        del_cashtransactions = text(
            """
            DELETE FROM CashTransactions
            """
        )
        del_tradingrecommendations = text(
            """
            DELETE FROM TradingRecommendations
            """
        )
        del_lots = text(
            """
            DELETE FROM Lots
            """
        )
        session.execute(del_sim_tracker)
        session.execute(del_portfoliopositions)
        session.execute(del_transactions)
        session.execute(del_cashtransactions)
        session.execute(del_tradingrecommendations)
        session.execute(del_lots)
        session.commit()


def exercise_strategy(portfolio: Portfolio, report=True, iteration_count=10):
    max_days = 5 * 365
    trading_dates = get_trading_dates(portfolio, max_days=max_days)
    if len(trading_dates) <= 0:
        return Decimal(0)

    cache = CACHE
    cache.set_earliest_date(trading_dates[0])

    with Session() as session:
        # with Profiler(interval=0.001) as profiler:
        count = 0
        for trade_date in trading_dates:
            session.add(portfolio)
            session.refresh(portfolio)
            log.info(
                f"Processing {trade_date} {portfolio.strategy} for id={portfolio.id}: {portfolio.name}"
            )
            last_trade_date = trade_date
            run_day(portfolio, trade_date, report=report)
            update_sim_date(portfolio, max_days, trade_date)
            if count < iteration_count:
                count += 1
            else:
                break

    # profiler.print()
    banked_cash = portfolio.bank_balance(last_trade_date)
    total_invested = portfolio.invest_balance(last_trade_date)
    total_value = portfolio.value(last_trade_date)
    cash = portfolio.cash_balance(last_trade_date)
    roi = ((banked_cash + total_value + cash - total_invested) / total_invested) * 100
    return roi


def run_day(portfolio: Portfolio, trade_date, execute=True, report=True):
    recommendations = make_recommendations(portfolio, trade_date)
    recommendations.sort(key=lambda x: x.strength, reverse=True)
    planned_actions = make_plan(portfolio, recommendations, trade_date)

    active_recs = [action for action in planned_actions if action.action != HOLD]
    if len(active_recs) == 0 and len(recommendations) > 0:
        cash = portfolio.cash_balance(trade_date)
        log.info(
            f"pid={portfolio.id} No planned actions for {trade_date} had {len(active_recs)} recommendations, cash={cash}"
        )

    if execute:
        execute_plan(portfolio, trade_date, planned_actions, report=report)

    portfolio.update_positions(trade_date)

    portfolio.reinvest_or_bank(trade_date, report=report)

    total_value = portfolio.value(trade_date)
    cash = portfolio.cash_balance(trade_date)
    banked_cash = portfolio.bank_balance(trade_date)
    total_invested = portfolio.invest_balance(trade_date)
    complete_roi = (
        cumulative_return(total_invested, banked_cash + total_value + cash) * 100
    )
    first_date = portfolio.first_deposit()
    if first_date is not None:
        portfolio.record_performance(trade_date)
        portfolio.report_status(trade_date, complete_roi, first_date, report=report)
    return first_date


def execute_plan(
    portfolio: Portfolio, trade_date, planned_actions: list[Action], report=True
):
    for action in planned_actions:
        product = Product.from_symbol(action.symbol)
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
            report=report,
        )
        portfolio._execute_trade(
            product.id,
            action.action,
            action.shares,
            action.last,
            trade_date,
            report=report,
        )


INITIAL_WALLET_RANGE = [900]
REINVEST_PERIOD_RANGE = [3650]
RESERVE_CASH_RANGE = [1]
REINVEST_AMT_RANGE = [100]
BANK_THRESHOLD = [1000]
WITH_CRYPTO = ["only", "no"]
MAX_EXPOSURE = [75]
YEARS_BACK = [5, 10, 15, 20, 25, 30, 35, 40]

LOCAL_STRATEGIES = [
    "bollinger",
    "breakout",
    "sma_buy_hold",
    "rsi",
    "vwap",
    "mean_reversion",
    "macd",
    "buy_sma_sell_rsi",
    "buy_sma_sell_vwap",
    "sma_rsi",
    "upmacd_downmr",
    "advanced",
]
# LOCAL_STRATEGIES = ["vwap", "sma_rsi","buy_sma_sell_rsi"]
# LOCAL_STRATEGIES = STRATEGIES


def parameter_search():
    # with Profiler(interval=0.1) as profiler:
    log.info("Executing parameter search")
    initial_combinations = itertools.product(
        RESERVE_CASH_RANGE,
        REINVEST_PERIOD_RANGE,
        REINVEST_AMT_RANGE,
        BANK_THRESHOLD,
        WITH_CRYPTO,
        MAX_EXPOSURE,
        LOCAL_STRATEGIES,
        YEARS_BACK,
    )

    initial_combinations = list(initial_combinations)
    portfolios_to_test: list[Portfolio] = []
    for combination in initial_combinations:
        name = f"Parameter Search {combination}"
        with Session() as session:
            session.expire_on_commit = False
            portfolio = portfolio_for_name(name)

            if not sim_started(portfolio):
                initialize_portfolio(portfolio, full=True)
                initialwallet_date = datetime.today() - timedelta(
                    weeks=52 * combination[7]
                )
                portfolio.invest(
                    Decimal(INITIAL_WALLET_RANGE[0]),
                    initialwallet_date,
                    INITIAL_DEPOSIT_DESCRIPTION,
                    report=False,
                )
            portfolio.reserve_cash_percent = combination[0]
            portfolio.reinvest_period = combination[1]
            portfolio.reinvest_amt = combination[2]
            portfolio.bank_threshold = combination[3]
            portfolio.max_exposure = combination[5]
            portfolio.strategy = combination[6]
            if combination[4] == "no":
                portfolio.sectors_forbidden = ["Cryptocurrency"]  # type: ignore
            elif combination[4] == "only":  # only crypto
                portfolio.sectors_allowed = ["Cryptocurrency"]  # type: ignore

            session.add(portfolio)
            session.commit()
            portfolios_to_test.append(portfolio)

    portfolios_to_test.sort(key=lambda x: x.name)

    max_processes = psutil.cpu_count(logical=False) or 1
    max_processes = max_processes - 1
    max_processes = max(max_processes, 1)

    pipes = []
    for p in portfolios_to_test:

        log.info(f"Testing parameters {combination}")

        pipe = subprocess.Popen(
            [
                sys.executable,
                __file__,
                str(p.id),
            ]
        )
        pipes.append(pipe)

        while len(pipes) >= max_processes:
            for p in pipes:
                exit_code = p.poll()
                if exit_code is not None:
                    pipes.remove(p)
            time.sleep(0.5)
    for p in pipes:
        p.wait()


def initialize_portfolio(portfolio: Portfolio, full=False):
    reset_portfolio(portfolio, full)


def main():
    # download_products.download_products()
    if len(sys.argv) >= 2:
        portfolio_id = int(sys.argv[1])
        portfolio = Portfolio.from_id(portfolio_id)

        # with Profiler(interval=0.0001) as profile:

        exercise_strategy(portfolio, report=False)
        # profile.print()
    else:
        parameter_search()


if __name__ == "__main__":
    log.basicConfig(
        level=log.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[log.StreamHandler()],
    )
    main()
