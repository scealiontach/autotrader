# from constants import DATABASE_URL
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from flask_material import Material
from flask_sqlalchemy import SQLAlchemy
from models import (
    Base,
    CashTransaction,
    Lot,
    Portfolio,
    PortfolioPosition,
    Product,
    TradingRecommendation,
    Transaction,
)
import portfolio
import constants
from utils import get_database_connection

app = Flask(__name__, template_folder="./templates")
Material(app)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    "postgresql://myuser:mypassword@localhost/mydatabase"  # Update as needed
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

constants.DATABASE_URL = app.config["SQLALCHEMY_DATABASE_URI"]


db = SQLAlchemy(app, model_class=Base)


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/portfolios", methods=["POST"])
def create_portfolio():
    data = request.json
    if data:
        new_portfolio = Portfolio(name=data["name"], owner=data.get("owner"))
        db.session.add(new_portfolio)
        db.session.commit()
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
    portfolios_data = db.session.query(Portfolio).all()
    portfolios = [
        {"id": p.id, "name": p.name, "owner": p.owner, "createddate": p.createddate}
        for p in portfolios_data
    ]
    if ("fmt" in request.args) and (request.args["fmt"] == "json"):
        return jsonify(portfolios)
    else:
        rows = []
        for p in portfolios:
            _p = portfolio.portfolio_for_name(p["name"])
            as_of = datetime.today()
            rows.append(
                {
                    "id": _p.portfolio_id,
                    "name": p["name"],
                    "cash": _p.wallet.cash_balance(as_of),
                    "invest": _p.wallet.invest_balance(as_of),
                    "value": _p.value(as_of),
                    "bank": _p.wallet.bank_balance(as_of),
                }
            )
        return render_template("portfolios.html", portfolios=rows)


@app.route("/portfolios/<int:portfolio_id>", methods=["GET"])
def portfolio_detail(portfolio_id):
    _portfolio = db.session.query(Portfolio).get(portfolio_id)
    if ("fmt" in request.args) and (request.args["fmt"] == "json"):
        if _portfolio:
            return jsonify(
                {
                    "id": _portfolio.id,
                    "name": _portfolio.name,
                    "owner": _portfolio.owner,
                    "createddate": _portfolio.createddate,
                }
            )
        return jsonify({"message": "Portfolio not found"}), 404
    else:
        if _portfolio:

            _p = portfolio.portfolio_for_name(_portfolio.name)
            as_of = datetime.today()
            data = {
                "id": _p.portfolio_id,
                "name": _portfolio.name,
                "cash": _p.wallet.cash_balance(as_of),
                "invest": _p.wallet.invest_balance(as_of),
                "value": _p.value(as_of),
                "bank": _p.wallet.bank_balance(as_of),
            }
            return render_template("portfolio_detail.html", portfolio=data, as_of=as_of)
        return jsonify({"message": "Portfolio not found"}), 404


@app.route("/api/portfolios/<int:portfolio_id>/recommendations", methods=["GET"])
def get_portfolio_recommendations(portfolio_id):
    result = (
        db.session.query(TradingRecommendation)
        .join(Portfolio)
        .where(Portfolio.id == portfolio_id)
        .all()
    )
    result_data = [
        {
            "id": r.id,
            "portfolio_id": r.portfolio_id,
            "product_id": r.product_id,
            "recommendation_date": r.recommendation_date,
            "action": r.action,
        }
        for r in result
    ]
    return jsonify(result_data)


@app.route("/api/portfolios/<int:portfolio_id>", methods=["PUT"])
def update_portfolio(portfolio_id):
    portfolio = db.session.query(Portfolio).get(portfolio_id)
    if portfolio:
        data = request.json
        if data is None:
            return jsonify({"message": "Invalid input"}), 400
        portfolio.name = data["name"]
        portfolio.owner = data.get("owner")
        db.session.commit()
        return jsonify({"message": "Portfolio updated successfully"})
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/api/portfolios/<int:portfolio_id>", methods=["DELETE"])
def delete_portfolio(portfolio_id):
    portfolio = db.session.query(Portfolio).get(portfolio_id)
    if portfolio:
        db.session.delete(portfolio)
        db.session.commit()
        return jsonify({"message": "Portfolio deleted successfully"})
    return jsonify({"message": "Portfolio not found"}), 404


@app.route("/api/portfolios/<int:portfolio_id>/transactions", methods=["GET"])
def get_portfolio_transactions(portfolio_id):
    transactions = (
        db.session.query(Transaction)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )
    transactions_data = [
        {
            "id": t.id,
            "product_id": t.product_id,
            "portfolio_id": t.portfolio_id,
            "quantity": t.quantity,
            "price": t.price,
            "transaction_date": t.transaction_date,
            "transaction_type": t.transaction_type,
        }
        for t in transactions
    ]
    return jsonify(transactions_data)


@app.route("/api/portfolios/<int:portfolio_id>/cash", methods=["GET"])
def get_portfolio_cashtransactions(portfolio_id):
    transactions = (
        db.session.query(CashTransaction)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(CashTransaction.transaction_date.desc())
        .all()
    )
    transactions_data = [
        {
            "id": t.id,
            "portfolio_id": t.portfolio_id,
            "portfolio_id": t.portfolio_id,
            "transaction_type": t.transaction_type,
            "amount": t.amount,
            "transaction_date": t.transaction_date,
            "description": t.description,
        }
        for t in transactions
    ]
    return jsonify(transactions_data)


@app.route("/api/portfolios/<int:portfolio_id>/positions", methods=["GET"])
def get_portfolio_positions(portfolio_id):
    positions = (
        db.session.query(PortfolioPosition)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(PortfolioPosition.purchasedate.desc())
        .all()
    )
    positions_data = [
        {
            "id": p.id,
            "product_id": p.product_id,
            "quantity": p.quantity,
            "purchasedate": p.purchasedate,
            "last_updated": p.last_updated,
            "last": p.last,
            "invest": p.invest,
        }
        for p in positions
    ]
    return jsonify(positions_data)


@app.route("/api/positions", methods=["GET"])
def get_positions():
    positions = (
        db.session.query(PortfolioPosition)
        .order_by(PortfolioPosition.purchasedate.desc())
        .all()
    )
    positions_data = [
        {
            "id": p.id,
            "portfolio_id": p.portfolio_id,
            "product_id": p.product_id,
            "quantity": p.quantity,
            "purchasedate": p.purchasedate,
            "last_updated": p.last_updated,
            "last": p.last,
            "invest": p.invest,
        }
        for p in positions
    ]
    return jsonify(positions_data)


@app.route("/api/positions/<int:position_id>", methods=["GET"])
def get_position(position_id):
    position = db.session.query(PortfolioPosition).get(position_id)
    if position:
        return jsonify(
            {
                "id": position.id,
                "portfolio_id": position.portfolio_id,
                "product_id": position.product_id,
                "quantity": position.quantity,
                "purchasedate": position.purchasedate,
                "last_updated": position.last_updated,
                "last": position.last,
                "invest": position.invest,
            }
        )
    return jsonify({"message": "Position not found"}), 404


@app.route("/api/portfolios/<int:portfolio_id>/lots", methods=["GET"])
def get_position_lots(portfolio_id):
    lots = (
        db.session.query(Lot)
        .filter_by(portfolio_id=portfolio_id)
        .order_by(Lot.id, Lot.purchasedate.desc())
        .all()
    )
    lots_data = [
        {
            "id": l.id,
            "portfolio_id": l.portfolio_id,
            "product_id": l.product_id,
            "quantity": l.quantity,
            "purchaseprice": l.purchasprice,
            "purchasedate": l.purchasedate,
        }
        for l in lots
    ]
    return jsonify(lots_data)


@app.route("/api/products", methods=["GET"])
def get_products():
    products = db.session.query(Product).order_by(Product.symbol).all()
    products_data = [
        {
            "id": p.id,
            "symbol": p.symbol,
            "company_name": p.company_name,
            "sector": p.sector,
            "market": p.market,
            "is_active": p.is_active,
            "dividend_rate": p.dividend_rate,
            "info": p.info,
            "createddate": p.createddate,
        }
        for p in products
    ]
    return jsonify(products_data)


@app.route("/api/products/id/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = db.session.query(Product).get(product_id)
    if product:
        return jsonify(
            {
                "id": product.id,
                "symbol": product.symbol,
                "company_name": product.company_name,
                "sector": product.sector,
                "market": product.market,
                "is_active": product.is_active,
                "dividend_rate": product.dividend_rate,
                "info": product.info,
                "createddate": product.createddate,
            }
        )
    return jsonify({"message": "Product not found"}), 404


@app.route("/api/products/sym/<string:symbol>", methods=["GET"])
def get_product_by_symbol(symbol):
    product = db.session.query(Product).filter_by(symbol=symbol).first()
    if product:
        return jsonify(
            {
                "id": product.id,
                "symbol": product.symbol,
                "company_name": product.company_name,
                "sector": product.sector,
                "market": product.market,
                "is_active": product.is_active,
                "dividend_rate": product.dividend_rate,
                "info": product.info,
                "createddate": product.createddate,
            }
        )
    return jsonify({"message": "Product not found"}), 404


if __name__ == "__main__":
    app.run(debug=True, port=6000)
