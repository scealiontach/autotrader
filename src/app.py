from decimal import Decimal
import json
import logging as log
import secrets
from datetime import date, timedelta

import download_products
import pandas as pd
import plotly
import plotly.express as px
import psutil
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_bootstrap import Bootstrap5
from flask_material import Material
from flask_wtf import CSRFProtect
from pytz import utc

import update_eod_data
from constants import ALL_INDEXES, DATABASE_URL
from database import Session
from driver import exercise_strategy, initialize_portfolio, make_recommendations
from forms import (
    AddPortfolioForm,
    CashTransactionForm,
    DeletePortfolioForm,
    EditHolding,
    EditPortfolioForm,
    InvestPortfolioForm,
    OrderForm,
    ResetPortfolioForm,
    SimulatePortfolioForm,
    StepPortfolioForm,
    UpdateMarketDataForm,
)
from market_data_cache import CACHE
from models import CashTransaction, TradingRecommendation, Transaction
from portfolio import Lot, Portfolio, Position
from product import Product
from recommender import STRATEGIES, Recommendation


max_processes = psutil.cpu_count(logical=False) or 1
max_processes = max_processes - 1
max_processes = max(max_processes, 1)

jobstores = {"default": SQLAlchemyJobStore(url=DATABASE_URL)}
executors = {
    "default": ThreadPoolExecutor(9),
    "external": ThreadPoolExecutor(1),
}
job_defaults = {"coalesce": False, "max_instances": 1}
scheduler = BackgroundScheduler(
    jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone=utc
)
scheduler.start()
app = Flask(__name__, template_folder="./templates")

token = secrets.token_urlsafe(16)
app.secret_key = token

Material(app)
bootstrap = Bootstrap5(app)
csrf = CSRFProtect(app)


def is_api_request(request):
    return "fmt" in request.args and request.args["fmt"] == "json"


@app.before_request
def before_request():
    g.db_session = Session()


@app.teardown_request
def shutdown_session(exception=None):
    Session.remove()


@app.route("/", methods=["GET"])
def home():
    form = UpdateMarketDataForm()
    return render_template("home.html", update_market_form=form)


def update_market_data_job():
    log.info("Downloading product information begins")
    download_products.download_products()
    log.info("Downloading product information complete")
    log.info("Market data update begins")
    update_eod_data.update()
    log.info("Market data update complete")


@app.route("/update", methods=["POST"])
def update_market_data():
    scheduler.add_job(update_market_data_job, id="update_market_data")
    return redirect(url_for("home"))


COL_CUMULATIVE_RETURN = "cum_ret"
COL_PCT_CHANGE_DAILY = "pct_change_daily"
COL_CLOSE = "closingprice"
COL_TOTAL = "total"

COL_ROW_INDEX = "row_index"
COL_DATE = "date"
COL_VOLUME = "volume"


def filter_portfolios_strategy(request):
    def f(portfolio: Portfolio):
        arg_filter = request.args.get("strategy", None, type=str)
        if arg_filter:
            if "," in arg_filter:
                filter_list = arg_filter.split(",")
            else:
                filter_list = [arg_filter]
            if portfolio.strategy not in filter_list:
                return False
            return True
        else:
            return True

    return f


@app.route("/portfolios/chart", methods=["GET"])
def portfolios_chart():
    portfolios: list[Portfolio] = (
        g.db_session.query(Portfolio).order_by(Portfolio.id.asc()).all()
    )
    portfolios = list(filter(filter_portfolios_strategy(request), portfolios))

    sim_filter = request.args.get("simulated", 0, type=int)
    if sim_filter > 0:
        portfolios = [p for p in portfolios if p.is_active]
    elif sim_filter < 0:
        portfolios = [p for p in portfolios if not p.is_active]

    df = None

    active_filter = request.args.get("active", 0, type=bool)
    if active_filter:
        portfolios = [p for p in portfolios if p.is_active == active_filter]

    graph_col = COL_CUMULATIVE_RETURN

    y_cols = []
    df = None
    select_cols = [COL_TOTAL]
    portfolio_ids = []
    for p in portfolios:
        pf_df = p.get_performance()
        if pf_df is None or len(pf_df) <= 0 or pf_df.empty:
            continue

        portfolio_ids.append(p.id)
        col_prefix = str(p.id) + "."

        y_cols.append(col_prefix + graph_col)

        for col in pf_df.columns:
            if col == COL_DATE:
                continue
            if col in select_cols:
                pf_df.rename(columns={col: col_prefix + col}, inplace=True)
            else:
                del pf_df[col]

        pf_df[col_prefix + COL_PCT_CHANGE_DAILY] = pf_df[
            col_prefix + COL_TOTAL
        ].pct_change()
        pf_df[col_prefix + COL_CUMULATIVE_RETURN] = (
            1 + pf_df[col_prefix + COL_PCT_CHANGE_DAILY]
        ).cumprod() - 1
        del pf_df[col_prefix + COL_TOTAL]
        del pf_df[col_prefix + COL_PCT_CHANGE_DAILY]

        pf_df[COL_ROW_INDEX] = pf_df.index + 1
        pf_df.set_index(COL_ROW_INDEX)
        del pf_df[COL_DATE]

        if df is None:
            df = pf_df
        else:
            df = pd.merge(df, pf_df, on=COL_ROW_INDEX, how="outer")

    if df is not None and not df.empty:
        for p in portfolios:
            if p.id not in portfolio_ids:
                continue
            col_prefix = str(p.id) + "."

    fig = px.line(df, x=COL_ROW_INDEX, y=y_cols, title="Comparative")
    graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

    return render_template("portfolios_chart.html", graphJSON=graph_json)


@app.route("/portfolios/add", methods=["POST"])
def portfolio_add():
    form = AddPortfolioForm()
    if form.validate_on_submit():
        if form.crypto_allowed.data:
            sectors_allowed = ["Cryptocurrency"]
            sectors_forbidden = []
        else:
            sectors_allowed = []
            sectors_forbidden = ["Cryptocurrency"]
        portfolio = Portfolio(
            name=form.name.data,  # type: ignore
            owner="admin",
            description=form.description.data or "",
        )
        portfolio.is_active = True
        portfolio.reserve_cash_percent = form.reserve_cash_percent.data  # type: ignore
        portfolio.reinvest_period = form.reinvest_period.data  # type: ignore
        portfolio.reinvest_amt = form.reinvest_amt.data  # type: ignore
        portfolio.bank_threshold = form.bank_threshold.data  # type: ignore
        portfolio.bank_pc = form.bank_pc.data  # type: ignore
        portfolio.max_exposure = form.max_exposure.data  # type: ignore
        portfolio.strategy = STRATEGIES[int(form.strategy.data)]  # type: ignore
        portfolio.sectors_allowed = sectors_allowed  # type: ignore
        portfolio.sectors_forbidden = sectors_forbidden  # type: ignore
        portfolio.dividend_only = False
        portfolio.is_active = form.is_active.data
        log.info(f"Adding portfolio {portfolio.name}")
        g.db_session.add(portfolio)
        g.db_session.commit()
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio.id))
    else:
        return jsonify({"message": "Invalid request"}), 400


@app.route("/portfolios", methods=["GET"])
def portfolios():
    add_form = AddPortfolioForm()
    sim_form = SimulatePortfolioForm()
    reset_form = ResetPortfolioForm()
    invest_form = InvestPortfolioForm()

    portfolios: list[Portfolio] = (
        g.db_session.query(Portfolio).order_by(Portfolio.id.asc()).all()
    )

    portfolios = list(filter(filter_portfolios_strategy(request), portfolios))

    sim_filter = request.args.get("simulated", 0, type=int)
    if sim_filter > 0:
        portfolios = [p for p in portfolios if p.is_active]
    elif sim_filter < 0:
        portfolios = [p for p in portfolios if not p.is_active]

    data = []
    for p in portfolios:
        data.append(p.as_dict_fast())
    if is_api_request(request):
        return jsonify(data)
    else:
        return render_template(
            "portfolios.html",
            data=data,
            add_form=add_form,
            sim_form=sim_form,
            reset_form=reset_form,
            invest_form=invest_form,
        )


@app.route("/portfolios/<int:portfolio_id>/edit", methods=["POST"])
def portfolio_detail_edit(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    edit_form = EditPortfolioForm()
    if edit_form.validate_on_submit():
        if edit_form.crypto_allowed.data:
            sectors_allowed = ["Cryptocurrency"]
            sectors_forbidden = []
        else:
            sectors_allowed = []
            sectors_forbidden = ["Cryptocurrency"]
        portfolio.is_active = True
        portfolio.name = edit_form.name.data  # type: ignore
        portfolio.description = edit_form.description.data  # type: ignore
        portfolio.reserve_cash_percent = edit_form.reserve_cash_percent.data  # type: ignore
        portfolio.reinvest_period = edit_form.reinvest_period.data  # type: ignore
        portfolio.reinvest_amt = edit_form.reinvest_amt.data  # type: ignore
        portfolio.bank_threshold = edit_form.bank_threshold.data  # type: ignore
        portfolio.bank_pc = edit_form.bank_pc.data  # type: ignore
        portfolio.max_exposure = edit_form.max_exposure.data  # type: ignore
        portfolio.strategy = STRATEGIES[int(edit_form.strategy.data)]  # type: ignore
        portfolio.sectors_allowed = sectors_allowed  # type: ignore
        portfolio.sectors_forbidden = sectors_forbidden  # type: ignore
        portfolio.dividend_only = False
        portfolio.is_active = edit_form.is_active.data
        log.info(f"Updating portfolio {portfolio.id}/{portfolio.name}")
        g.db_session.add(portfolio)
        g.db_session.commit()
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio.id))
    else:
        edit_form.errors
        return jsonify({"message": "Invalid request", "errors": edit_form.errors}), 400


@app.route("/portfolios/<int:portfolio_id>", methods=["GET"])
def portfolio_detail(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    edit_form = EditPortfolioForm()
    delete_form = DeletePortfolioForm()
    simulate_form = SimulatePortfolioForm()
    reset_form = ResetPortfolioForm()
    invest_form = InvestPortfolioForm()
    order_form = OrderForm()
    edit_holding_form = EditHolding()
    step_form = StepPortfolioForm()
    edit_form.bank_pc.data = portfolio.bank_pc
    edit_form.bank_threshold.data = portfolio.bank_threshold
    edit_form.crypto_allowed.data = "Cryptocurrency" in portfolio.sectors_allowed  # type: ignore
    edit_form.max_exposure.data = portfolio.max_exposure
    edit_form.reserve_cash_percent.data = portfolio.reserve_cash_percent
    edit_form.reinvest_amt.data = portfolio.reinvest_amt
    edit_form.reinvest_period.data = portfolio.reinvest_period
    edit_form.strategy.process_data(STRATEGIES.index(portfolio.strategy))
    edit_form.name.data = portfolio.name
    edit_form.description.data = portfolio.description
    edit_form.is_active.data = portfolio.is_active

    invest_form.amount.data = portfolio.reinvest_amt
    invest_form.date.data = portfolio.last_active()

    if is_api_request(request):
        if portfolio:
            return jsonify(portfolio.as_dict())
        return jsonify({"message": "Portfolio not found"}), 404
    else:
        if portfolio:
            positions = [p.as_dict() for p in portfolio.positions()]
            for p in positions:
                p["symbol"] = Product.from_id(p["product_id"]).symbol

            df = None
            pf_df = portfolio.get_performance()
            graph_col = COL_CUMULATIVE_RETURN
            y_cols = []
            col_prefix = str(portfolio.id) + "."
            if len(pf_df) > 0:
                pf_df[col_prefix + COL_PCT_CHANGE_DAILY] = pf_df["total"].pct_change()
                pf_df[col_prefix + COL_CUMULATIVE_RETURN] = (
                    1 + pf_df[col_prefix + COL_PCT_CHANGE_DAILY]
                ).cumprod() - 1
                pf_df = pf_df[
                    [
                        COL_DATE,
                        col_prefix + COL_CUMULATIVE_RETURN,
                        col_prefix + COL_PCT_CHANGE_DAILY,
                    ]
                ]
                y_cols.append(col_prefix + graph_col)
                df = pf_df

            for index in ALL_INDEXES:
                product = Product.from_symbol(index)
                CACHE.load_data(product.id)
                index_df = CACHE.get_data(
                    product.id, portfolio.first_deposit(), portfolio.last_active()
                )
                if len(index_df) <= 0:
                    continue
                col_prefix = product.symbol + "."
                for col in index_df.columns:
                    if col == COL_DATE:
                        continue
                    index_df[col_prefix + col] = index_df[col]
                    del index_df[col]

                index_df[col_prefix + COL_PCT_CHANGE_DAILY] = index_df[
                    col_prefix + COL_CLOSE
                ].pct_change()
                index_df[col_prefix + COL_CUMULATIVE_RETURN] = (
                    1 + index_df[col_prefix + COL_PCT_CHANGE_DAILY]
                ).cumprod() - 1
                if df is None:
                    df = index_df
                else:

                    df = pd.merge(df, index_df, on=COL_DATE, how="outer")
                y_cols.append(col_prefix + graph_col)

            if df is not None:
                fig = px.line(df, x=COL_DATE, y=y_cols, title="vs INDEX")
                graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
            else:
                graph_json = None

            strat_recos: dict[str, dict[str, str]] = {}
            recos: list[Recommendation] = []
            if not portfolio.is_active:
                for strategy in STRATEGIES:
                    recos.extend(portfolio.strategy_recommendation(strategy))
                for r in recos:
                    if r.symbol not in strat_recos:
                        strat_recos[r.symbol] = {}
                    strat_recos[r.symbol][r.strategy] = f"{r.action}/{r.strength:2.2f}"

            return render_template(
                "portfolio_detail.html",
                portfolio=portfolio.as_dict(),
                positions=positions,
                graphJSON=graph_json,
                edit_form=edit_form,
                delete_form=delete_form,
                simulate_form=simulate_form,
                reset_form=reset_form,
                invest_form=invest_form,
                order_form=order_form,
                edit_holding_form=edit_holding_form,
                step_form=step_form,
                strat_recos=strat_recos,
                strategies=STRATEGIES,
            )

        return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/delete", methods=["POST"])
def portfolio_delete(portfolio_id):
    portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        form = DeletePortfolioForm()
        if form.validate_on_submit():
            if form.confirm.data:
                initialize_portfolio(portfolio, full=True)
                g.db_session.delete(portfolio)
                g.db_session.commit()
            return redirect(url_for("portfolios"))
        else:
            return jsonify({"message": "Invalid request"}), 400
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/step", methods=["POST"])
def portfolio_step(portfolio_id):
    form = StepPortfolioForm()
    if not form.validate_on_submit():
        return jsonify({"message": "Invalid request"}), 400

    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        if not portfolio.is_active:
            la = portfolio.last_active()
            while la < date.today():
                make_recommendations(portfolio, la)
                portfolio.record_performance(la)
                la = la + timedelta(days=1)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/simulate", methods=["POST"])
def portfolio_simulate(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        if not portfolio.is_active:
            return redirect(request.referrer)
        form = SimulatePortfolioForm()
        if form.validate_on_submit():
            job_id = f"simulate_{portfolio_id}"
            job = scheduler.get_job(job_id)
            if job:
                log.warning(f"Job {job_id} already exists")
            else:
                scheduler.add_job(
                    exercise_strategy,
                    args=[portfolio],
                    kwargs={"report": False},
                    id=f"simulate_{portfolio_id}",
                )
            return redirect(request.referrer)
        else:
            return jsonify({"message": "Invalid request"}), 400
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/reset", methods=["POST"])
def portfolio_reset(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        form = ResetPortfolioForm()
        if form.validate_on_submit():
            initialize_portfolio(portfolio, full=True)
            return redirect(request.referrer)
        else:
            return jsonify({"message": "Invalid request"}), 400
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/invest", methods=["POST"])
def portfolio_invest(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        form = InvestPortfolioForm()
        if form.validate_on_submit():
            amount = form.amount.data
            date = form.date.data
            description = form.description.data
            if amount is None or date is None or description is None:
                return jsonify({"message": "Invalid request"}), 400
            portfolio.invest(amount, date, description)
            return redirect(request.referrer)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/order", methods=["POST"])
def portfolio_order(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        form = OrderForm()
        if form.validate_on_submit():
            symbol = form.symbol.data
            quantity = form.quantity.data
            date = form.date.data
            action = form.buysell.data
            price = form.price.data
            buysell = form.buysell.data
            if (
                action is None
                or symbol is None
                or quantity is None
                or date is None
                or price is None
                or buysell is None
            ):
                return jsonify({"message": "Invalid request"}), 400
            product = Product.from_symbol(symbol)
            if product is None:
                return jsonify({"message": "Invalid request"}), 400
            if action == "BUY":
                portfolio.buy(product.id, quantity, price, date)
            elif action == "SELL":
                portfolio.sell(product.id, quantity, price, date)
            return redirect(request.referrer)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/edit_holding", methods=["POST"])
def edit_holding(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        form = EditHolding()
        if form.validate_on_submit():
            symbol = form.symbol.data
            quantity = form.quantity.data
            date = form.date.data
            buysell = form.buysell.data
            if symbol is None or quantity is None or date is None or buysell is None:
                return jsonify({"message": "Invalid request"}), 400

            product = Product.from_symbol(symbol)
            price = product.fetch_last_closing_price(date)
            if price is None:
                return jsonify({"message": "Invalid request"}), 400

            total = quantity * price
            if buysell == "SELL":
                portfolio.add_debit(
                    total,
                    date,
                    f"Journal Entry for Sell {quantity} of {symbol}",
                    transaction_type="INVEST",
                )
                portfolio.sell(product.id, quantity, price, date)
            elif buysell == "BUY":
                portfolio.add_deposit(
                    total,
                    date,
                    f"Journal Entry for Buy {quantity} of {symbol}",
                    transaction_type="INVEST",
                )
                portfolio.buy(product.id, quantity, price, date)
            return redirect(request.referrer)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/recommendations", methods=["GET"])
def get_portfolio_recommendations(portfolio_id):
    result = (
        g.db_session.query(TradingRecommendation)
        .join(Portfolio)
        .where(Portfolio.id == portfolio_id)
        .all()
    )
    result_data = [r.as_dict() for r in result]
    return jsonify(result_data)


@app.route("/portfolios/<int:portfolio_id>/transactions", methods=["GET"])
def get_portfolio_transactions(portfolio_id):
    order_form = OrderForm()
    transactions = (
        g.db_session.query(Transaction)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )
    transactions_data = [t.as_dict() for t in transactions]
    for tx in transactions_data:
        tx["symbol"] = Product.from_id(tx["product_id"]).symbol
    if is_api_request(request):
        return jsonify(transactions_data)
    return render_template(
        "tx_register.html", transactions=transactions_data, order_form=order_form
    )


@app.route("/portfolios/<int:portfolio_id>/cash", methods=["GET"])
def get_portfolio_cashtransactions(portfolio_id):
    form = CashTransactionForm()
    transactions = (
        g.db_session.query(CashTransaction)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(CashTransaction.transaction_date.desc())
        .all()
    )
    transactions_data = [t.as_dict() for t in transactions]
    if is_api_request(request):
        return jsonify(transactions_data)
    return render_template(
        "cash_register.html", transactions=transactions_data, form=form
    )


@app.route("/portfolios/<int:portfolio_id>/positions", methods=["GET"])
def get_portfolio_positions(portfolio_id):
    positions = (
        g.db_session.query(Position)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(Position.purchasedate.desc())
        .all()
    )
    positions_data = [p.as_dict() for p in positions]
    return jsonify(positions_data)


@app.route("/positions", methods=["GET"])
def positions():
    positions = (
        g.db_session.query(Position).order_by(Position.purchasedate.desc()).all()
    )
    positions_data = [p.as_dict() for p in positions]
    return jsonify(positions_data)


@app.route("/positions/<int:position_id>", methods=["GET"])
def position_detail(position_id):
    position = g.db_session.query(Position).get(position_id)
    if position:
        if is_api_request(request):
            return jsonify(position.as_dict())
        p = position.as_dict()
        p["symbol"] = Product.from_id(p["product_id"]).symbol
        lots = [l.as_dict() for l in position.get_lots()]
        for l in lots:
            l["symbol"] = p["symbol"]
        return render_template("position_detail.html", position=p, lots=lots)
    return jsonify({"message": "Position not found"}), 404


@app.route("/lots", methods=["GET"])
def lots():
    lots = g.db_session.query(Lot).order_by(Lot.purchasedate.desc()).all()
    lots_data = [l.as_dict() for l in lots]
    for l in lots_data:
        l["symbol"] = Product.from_id(l["product_id"]).symbol
    return jsonify(lots_data)


@app.route("/lots/<int:lot_id>", methods=["GET"])
def lot_detail(lot_id):
    lot = g.db_session.query(Lot).get(lot_id)
    if lot:
        l = lot.as_dict()
        l["symbol"] = Product.from_id(l["product_id"]).symbol
        return jsonify(l)
    return jsonify({"message": "Lot not found"}), 404


@app.route("/positions/<int:position_id>/lots", methods=["GET"])
def get_position_lots(position_id):
    lots = (
        g.db_session.query(Lot)
        .join(Position)
        .where(Position.id == position_id)
        .order_by(Lot.id, Lot.purchasedate.desc())
        .all()
    )
    lots_data = [l.as_dict() for l in lots]
    for l in lots_data:
        l["symbol"] = Product.from_id(l["product_id"]).symbol
    return jsonify(lots_data)


@app.route("/products", methods=["GET"])
def products():
    products = g.db_session.query(Product).order_by(Product.symbol).all()
    products_data = [p.as_dict() for p in products]
    return jsonify(products_data)


@app.route("/products/id/<int:product_id>", methods=["GET"])
def product_detail(product_id):
    product: Product = g.db_session.query(Product).get(product_id)
    if product:
        stock_data = product.as_dict()
        if is_api_request(request):
            return jsonify(stock_data)
        if stock_data["sector"] == "Cryptocurrency":
            return render_template("product_detail-crypto.html", stock_data=stock_data)
        return render_template("product_detail.html", stock_data=stock_data)
    return jsonify({"message": "Product not found"}), 404


@app.route("/products/sym/<string:symbol>", methods=["GET"])
def product_by_symbol(symbol):
    product = g.db_session.query(Product).filter_by(symbol=symbol).first()
    if product:
        return redirect(f"/products/id/{product.id}")
    return jsonify({"message": "Product not found"}), 404


if __name__ == "__main__":
    log.basicConfig(
        level=log.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[log.StreamHandler()],
    )
    app.run(debug=True, port=6000)
