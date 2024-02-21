import json
import secrets
from datetime import date
from decimal import Decimal
import logging as log

import pandas as pd
import plotly
import plotly.express as px
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.sql.functions import now
import psutil
from dateutil.utils import today
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from flask_bootstrap import Bootstrap5
from flask_material import Material
from flask_wtf import CSRFProtect
from pytz import utc

import update_eod_data
from constants import ALL_INDEXES, DATABASE_URL
from database import Session
from driver import INITIAL_DEPOSIT_DESCRIPTION, exercise_strategy, initialize_portfolio
from forms import AddPortfolioForm, EditPortfolioForm
from market_data_cache import CACHE
from models import CashTransaction, TradingRecommendation, Transaction
from portfolio import Lot, Portfolio, Position
from product import Product
from recommender import STRATEGIES

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


@scheduler.scheduled_job(
    "cron", executor="external", max_instances=1, coalesce=True, minute="*/10"
)
def update_market_data():
    log.info("Market data update begins")
    update_eod_data.update()
    log.info("Market data update complete")


@app.before_request
def before_request():
    g.db_session = Session()


@app.teardown_request
def shutdown_session(exception=None):
    Session.remove()


@app.route("/")
def home():
    return render_template("home.html")


COL_CUMULATIVE_RETURN = ".cum_ret"
COL_PCT_CHANGE_DAILY = ".pct_change_daily"
COL_CLOSE = ".close"


@app.route("/portfolios/chart", methods=["GET"])
def portfolios_chart():
    portfolios: list[Portfolio] = (
        g.db_session.query(Portfolio).order_by(Portfolio.id.asc()).all()
    )
    df = None

    graph_col = COL_CUMULATIVE_RETURN
    last_active = None
    y_cols = []
    for p in portfolios:
        pf_df = p.get_performance()
        if len(pf_df) <= 0:
            continue
        y_cols.append(str(p.id) + graph_col)
        pf_df[str(p.id) + COL_PCT_CHANGE_DAILY] = pf_df["total"].pct_change()
        pf_df[str(p.id) + COL_CUMULATIVE_RETURN] = (
            1 + pf_df[str(p.id) + COL_PCT_CHANGE_DAILY]
        ).cumprod() - 1
        pf_df = pf_df[
            [
                "date",
                str(p.id) + COL_CUMULATIVE_RETURN,
                str(p.id) + COL_PCT_CHANGE_DAILY,
            ]
        ]
        if df is None:
            df = pf_df
        else:
            df = pd.merge(df, pf_df, on="date", how="left")
        if last_active is None or p.last_active() > last_active:
            last_active = p.last_active()

    if last_active is None or p.last_active() > last_active:
        last_active = today()
    for index in ALL_INDEXES:
        product = Product.from_symbol(index)
        CACHE.load_data(product.id)
        y_cols.append(product.symbol + graph_col)
        index_df = CACHE.get_data(product.id, date(1901, 1, 1), last_active)
        index_df.rename(
            columns={"closingprice": product.symbol + COL_CLOSE}, inplace=True
        )
        index_df[product.symbol + COL_PCT_CHANGE_DAILY] = index_df[
            product.symbol + COL_CLOSE
        ].pct_change()
        index_df[product.symbol + COL_CUMULATIVE_RETURN] = (
            1 + index_df[product.symbol + COL_PCT_CHANGE_DAILY]
        ).cumprod() - 1
        if df is None:
            df = index_df
        else:
            df = pd.merge(df, index_df, on="date", how="left")

    fig = px.line(df, x="date", y=y_cols, title="vs INDEX")
    graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

    return render_template("portfolios_chart.html", graphJSON=graph_json)


@app.route("/portfolios", methods=["GET", "POST"])
def portfolios():
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

    portfolios: list[Portfolio] = (
        g.db_session.query(Portfolio).order_by(Portfolio.id.asc()).all()
    )
    data = []
    for p in portfolios:
        data.append(p.as_dict_fast())
    if is_api_request(request):
        return jsonify(data)
    else:
        return render_template("portfolios.html", data=data, form=form)


@app.route("/portfolios/<int:portfolio_id>", methods=["GET", "POST"])
def portfolio_detail(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    form = EditPortfolioForm()
    if request.method == "GET":
        form.bank_pc.data = portfolio.bank_pc
        form.bank_threshold.data = portfolio.bank_threshold
        form.crypto_allowed.data = "Cryptocurrency" in portfolio.sectors_allowed  # type: ignore
        form.max_exposure.data = portfolio.max_exposure
        form.reserve_cash_percent.data = portfolio.reserve_cash_percent
        form.reinvest_amt.data = portfolio.reinvest_amt
        form.reinvest_period.data = portfolio.reinvest_period
        form.strategy.process_data(STRATEGIES.index(portfolio.strategy))
        form.name.data = portfolio.name
        form.description.data = portfolio.description
        form.is_active.data = portfolio.is_active

    if form.validate_on_submit():
        if form.crypto_allowed.data:
            sectors_allowed = ["Cryptocurrency"]
            sectors_forbidden = []
        else:
            sectors_allowed = []
            sectors_forbidden = ["Cryptocurrency"]
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
        log.info(f"Updating portfolio {portfolio.id}/{portfolio.name}")
        g.db_session.add(portfolio)
        g.db_session.commit()
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio.id))

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
            if len(pf_df) > 0:
                pf_df[str(portfolio.id) + COL_PCT_CHANGE_DAILY] = pf_df[
                    "total"
                ].pct_change()
                pf_df[str(portfolio.id) + COL_CUMULATIVE_RETURN] = (
                    1 + pf_df[str(portfolio.id) + COL_PCT_CHANGE_DAILY]
                ).cumprod() - 1
                pf_df = pf_df[
                    [
                        "date",
                        str(portfolio.id) + COL_CUMULATIVE_RETURN,
                        str(portfolio.id) + COL_PCT_CHANGE_DAILY,
                    ]
                ]
                y_cols.append(str(portfolio.id) + graph_col)
                df = pf_df

            for index in ALL_INDEXES:
                product = Product.from_symbol(index)
                CACHE.load_data(product.id)
                index_df = CACHE.get_data(
                    product.id, date(1901, 1, 1), portfolio.last_active()
                )
                if len(index_df) <= 0:
                    continue
                index_df.rename(
                    columns={"closingprice": product.symbol + COL_CLOSE}, inplace=True
                )
                index_df[product.symbol + COL_PCT_CHANGE_DAILY] = index_df[
                    product.symbol + COL_CLOSE
                ].pct_change()
                index_df[product.symbol + COL_CUMULATIVE_RETURN] = (
                    1 + index_df[product.symbol + COL_PCT_CHANGE_DAILY]
                ).cumprod() - 1
                if df is None:
                    df = index_df
                else:
                    df = pd.merge(df, index_df, on="date", how="left")
                y_cols.append(product.symbol + graph_col)

            if df is not None:
                fig = px.line(df, x="date", y=y_cols, title="vs INDEX")

                graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
            else:
                graph_json = None
            return render_template(
                "portfolio_detail.html",
                portfolio=portfolio.as_dict(),
                positions=positions,
                graphJSON=graph_json,
                form=form,
            )

        return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>", methods=["DELETE"])
def delete_portfolio(portfolio_id):
    portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        g.db_session.delete(portfolio)
        g.db_session.commit()
        return jsonify({"message": "Portfolio deleted successfully"})
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/simulate", methods=["POST"])
def portfolio_simulate(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if not portfolio.is_active:
        return redirect(request.referrer)
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
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/reset", methods=["POST"])
def portfolio_reset(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        initialize_portfolio(portfolio, full=True)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/invest", methods=["GET"])
def portfolio_invest(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        portfolio.invest(Decimal(1000), "1975-04-24", INITIAL_DEPOSIT_DESCRIPTION)
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
    return render_template("tx_register.html", transactions=transactions_data)


@app.route("/portfolios/<int:portfolio_id>/cash", methods=["GET"])
def get_portfolio_cashtransactions(portfolio_id):
    transactions = (
        g.db_session.query(CashTransaction)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(CashTransaction.transaction_date.desc())
        .all()
    )
    transactions_data = [t.as_dict() for t in transactions]
    if is_api_request(request):
        return jsonify(transactions_data)
    return render_template("cash_register.html", transactions=transactions_data)


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
    app.run(debug=True, port=6000)
