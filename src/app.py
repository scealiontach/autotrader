# from constants import DATABASE_URL
import threading
from decimal import Decimal

from flask import Flask, g, jsonify, redirect, render_template, request
from flask_material import Material
from sqlalchemy.sql.operators import as_

import update_eod_data
from database import Session
from driver import INITIAL_DEPOSIT_DESCRIPTION, exercise_strategy, initialize_portfolio
from models import CashTransaction, TradingRecommendation, Transaction
from portfolio import Lot, Portfolio, Position
from product import Product

app = Flask(__name__, template_folder="./templates")
Material(app)


@app.before_request
def before_request():
    g.db_session = Session()


@app.teardown_appcontext
def shutdown_session(exception=None):
    # Remove and close the session at the end of the request
    g.db_session.close()
    Session.remove()


@app.route("/")
def home():
    return render_template("home.html")


def is_api_request(request):
    return "fmt" in request.args and request.args["fmt"] == "json"


@app.route("/update", methods=["POST"])
def update():
    bg_thread = threading.Thread(target=update_eod_data.update)
    bg_thread.start()
    return redirect(f"/")


@app.route("/portfolios", methods=["POST"])
def create_portfolio():
    data = request.json
    if data:
        new_portfolio = Portfolio(name=data["name"], owner=data.get("owner"))
        g.db_session.add(new_portfolio)
        g.db_session.commit()
        return (
            jsonify(
                {"message": "Portfolio created successfully", "id": new_portfolio.id}
            ),
            201,
        )
    else:
        return jsonify({"message": "Invalid input"}), 400


@app.route("/portfolios", methods=["GET"])
def portfolios():
    portfolios: list[Portfolio] = (
        g.db_session.query(Portfolio).order_by(Portfolio.id.asc()).all()
    )
    data = []
    for p in portfolios:
        data.append(p.as_dict())
    if is_api_request(request):
        return jsonify(data)
    else:
        return render_template("portfolios.html", data=data)


@app.route("/portfolios/<int:portfolio_id>", methods=["GET"])
def portfolio_detail(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if is_api_request(request):
        if portfolio:
            return jsonify(portfolio.as_dict())
        return jsonify({"message": "Portfolio not found"}), 404
    else:
        if portfolio:
            positions = [p.as_dict() for p in portfolio.positions()]
            for p in positions:
                p["symbol"] = Product.from_id(p["product_id"]).symbol
            return render_template(
                "portfolio_detail.html",
                portfolio=portfolio.as_dict(),
                positions=positions,
            )

        return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/simulate", methods=["POST"])
def portfolio_simulate(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        bg_thread = threading.Thread(
            target=exercise_strategy, args=(portfolio,), kwargs={"report": False}
        )
        bg_thread.start()
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/reset", methods=["POST"])
def portfolio_reset(portfolio_id):
    portfolio: Portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        initialize_portfolio(portfolio)
        return redirect(request.referrer)
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>/invest", methods=["POST"])
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


@app.route("/portfolios/<int:portfolio_id>", methods=["PUT"])
def update_portfolio(portfolio_id):
    portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        data = request.json
        if data is None:
            return jsonify({"message": "Invalid input"}), 400
        portfolio.name = data["name"]
        portfolio.owner = data.get("owner")
        g.db_session.commit()
        return jsonify({"message": "Portfolio updated successfully"})
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/portfolios/<int:portfolio_id>", methods=["DELETE"])
def delete_portfolio(portfolio_id):
    portfolio = g.db_session.query(Portfolio).get(portfolio_id)
    if portfolio:
        g.db_session.delete(portfolio)
        g.db_session.commit()
        return jsonify({"message": "Portfolio deleted successfully"})
    return jsonify({"message": "Portfolio not found"}), 404


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
