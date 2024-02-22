from decimal import Decimal

import wtforms
from dateutil.utils import today
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DecimalField,
    IntegerField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import InputRequired, Length

from recommender import STRATEGIES


class AddPortfolioForm(FlaskForm):
    name = StringField("Portfolio Name", validators=[InputRequired(), Length(5, 40)])
    description = TextAreaField(
        "Description", validators=[InputRequired(), Length(10, 200)]
    )
    reserve_cash_percent = DecimalField(
        "Reserve Cash %", validators=[InputRequired()], places=0, default=Decimal(5)
    )
    reinvest_period = IntegerField(
        "Reinvest Period", validators=[InputRequired()], default=7
    )
    reinvest_amt = DecimalField(
        "Reinvest Amount", validators=[InputRequired()], places=0, default=Decimal(100)
    )
    bank_threshold = DecimalField(
        "Bank Threshold", validators=[InputRequired()], places=0, default=Decimal(1000)
    )
    bank_pc = IntegerField("Bank %", validators=[InputRequired()], default=33)
    crypto_allowed = BooleanField("Crypto Allowed", default=False)
    is_active = BooleanField("Simulated", default=False)
    max_exposure = IntegerField(
        "Max Exposure %", validators=[InputRequired()], default=20
    )
    strategy = wtforms.SelectField(
        "Strategy",
        choices=[(index, strategy) for index, strategy in enumerate(STRATEGIES)],
        validators=[InputRequired()],
    )
    submit = SubmitField("Submit")


class EditPortfolioForm(FlaskForm):
    name = StringField("Portfolio Name", validators=[InputRequired(), Length(5, 40)])
    description = TextAreaField(
        "Description", validators=[InputRequired(), Length(10, 200)]
    )
    reserve_cash_percent = DecimalField(
        "Reserve Cash %", validators=[InputRequired()], places=0, default=Decimal(5)
    )
    reinvest_period = IntegerField(
        "Reinvest Period", validators=[InputRequired()], default=7
    )
    reinvest_amt = DecimalField(
        "Reinvest Amount", validators=[InputRequired()], places=0, default=Decimal(100)
    )
    bank_threshold = DecimalField(
        "Bank Threshold", validators=[InputRequired()], places=0, default=Decimal(1000)
    )
    bank_pc = IntegerField("Bank %", validators=[InputRequired()], default=33)
    crypto_allowed = BooleanField("Crypto Allowed", default=False)
    is_active = BooleanField("Simulated", default=False)
    max_exposure = IntegerField(
        "Max Exposure %", validators=[InputRequired()], default=20
    )
    strategy = wtforms.SelectField(
        "Strategy",
        choices=[(index, strategy) for index, strategy in enumerate(STRATEGIES)],
        validators=[InputRequired()],
    )
    submit = SubmitField("Submit")


class DeletePortfolioForm(FlaskForm):
    confirm = BooleanField(
        "Confirm Deletion?", default=False, validators=[InputRequired()]
    )
    submit = SubmitField("Delete")


class SimulatePortfolioForm(FlaskForm):
    submit = SubmitField("Simulate")


class ResetPortfolioForm(FlaskForm):
    submit = SubmitField("Reset")


class StepPortfolioForm(FlaskForm):
    submit = SubmitField("Step")


class InvestPortfolioForm(FlaskForm):
    amount = DecimalField(
        "Amount", validators=[InputRequired()], places=2, default=Decimal(0)
    )
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    description = TextAreaField(
        "Description", validators=[InputRequired(), Length(3, 200)]
    )
    submit = SubmitField("Invest")


class CashTransactionForm(FlaskForm):
    amount = DecimalField(
        "Amount", validators=[InputRequired()], places=2, default=Decimal(0)
    )
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    description = TextAreaField(
        "Description", validators=[InputRequired(), Length(3, 200)]
    )
    submit = SubmitField("Submit")


class OrderForm(FlaskForm):
    symbol = StringField("Symbol", validators=[InputRequired(), Length(3, 10)])
    quantity = DecimalField(
        "Quantity", validators=[InputRequired()], default=Decimal(100)
    )
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    price = wtforms.DecimalField("Price", validators=[InputRequired()], places=2)
    buysell = wtforms.SelectField(
        "Buy/Sell", choices=[("BUY", "BUY"), ("SELL", "SELL")]
    )
    submit = SubmitField("Submit")


class EditHolding(FlaskForm):
    symbol = StringField("Symbol", validators=[InputRequired(), Length(3, 10)])
    quantity = DecimalField(
        "Quantity", validators=[InputRequired()], default=Decimal(0)
    )
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    buysell = wtforms.SelectField(
        "Buy/Sell", choices=[("BUY", "BUY"), ("SELL", "SELL")]
    )
    submit = SubmitField("Submit")


class BuyOrderForm(FlaskForm):
    symbol = StringField("Symbol", validators=[InputRequired(), Length(3, 10)])
    quantity = DecimalField(
        "Quantity", validators=[InputRequired()], default=Decimal(100)
    )
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    price = wtforms.DecimalField("Price", validators=[InputRequired()], places=2)
    submit = SubmitField("Submit")


class SellOrderForm(FlaskForm):
    symbol = StringField("Symbol", validators=[InputRequired(), Length(3, 10)])
    quantity = IntegerField("Quantity", validators=[InputRequired()], default=100)
    date = wtforms.DateField("Date", validators=[InputRequired()], default=today())
    price = wtforms.DecimalField("Price", validators=[InputRequired()], places=2)
    submit = SubmitField("Sell")


class UpdateMarketDataForm(FlaskForm):
    submit = SubmitField("Update Market Data")
