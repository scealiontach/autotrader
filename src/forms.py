from decimal import Decimal
from os import close
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
from wtforms.validators import DataRequired, Length
import wtforms

from recommender import STRATEGIES


class AddPortfolioForm(FlaskForm):
    name = StringField("Portfolio Name", validators=[DataRequired(), Length(5, 40)])
    description = TextAreaField(
        "Description", validators=[DataRequired(), Length(10, 200)]
    )
    reserve_cash_percent = DecimalField(
        "Reserve Cash %", validators=[DataRequired()], places=0, default=Decimal(5)
    )
    reinvest_period = IntegerField(
        "Reinvest Period", validators=[DataRequired()], default=7
    )
    reinvest_amt = DecimalField(
        "Reinvest Amount", validators=[DataRequired()], places=0, default=Decimal(100)
    )
    bank_threshold = DecimalField(
        "Bank Threshold", validators=[DataRequired()], places=0, default=Decimal(1000)
    )
    bank_pc = IntegerField("Bank %", validators=[DataRequired()], default=33)
    crypto_allowed = BooleanField("Crypto Allowed", default=False)
    is_active = BooleanField("Simulated", default=False)
    max_exposure = IntegerField(
        "Max Exposure %", validators=[DataRequired()], default=20
    )
    strategy = wtforms.SelectField(
        "Strategy",
        choices=[(index, strategy) for index, strategy in enumerate(STRATEGIES)],
        validators=[DataRequired()],
    )
    submit = SubmitField("Submit")


class EditPortfolioForm(FlaskForm):
    name = StringField("Portfolio Name", validators=[DataRequired(), Length(5, 40)])
    description = TextAreaField(
        "Description", validators=[DataRequired(), Length(10, 200)]
    )
    reserve_cash_percent = DecimalField(
        "Reserve Cash %", validators=[DataRequired()], places=0, default=Decimal(5)
    )
    reinvest_period = IntegerField(
        "Reinvest Period", validators=[DataRequired()], default=7
    )
    reinvest_amt = DecimalField(
        "Reinvest Amount", validators=[DataRequired()], places=0, default=Decimal(100)
    )
    bank_threshold = DecimalField(
        "Bank Threshold", validators=[DataRequired()], places=0, default=Decimal(1000)
    )
    bank_pc = IntegerField("Bank %", validators=[DataRequired()], default=33)
    crypto_allowed = BooleanField("Crypto Allowed", default=False)
    is_active = BooleanField("Simulated", default=False)
    max_exposure = IntegerField(
        "Max Exposure %", validators=[DataRequired()], default=20
    )
    strategy = wtforms.SelectField(
        "Strategy",
        choices=[(index, strategy) for index, strategy in enumerate(STRATEGIES)],
        validators=[DataRequired()],
    )
    submit = SubmitField("Submit")


class CashTransactionForm(FlaskForm):
    amount = DecimalField(
        "Amount", validators=[DataRequired()], places=2, default=Decimal(0)
    )
    date = wtforms.DateField("Date", validators=[DataRequired()], default=today())
    description = TextAreaField(
        "Description", validators=[DataRequired(), Length(3, 200)]
    )
    submit = SubmitField("Submit")
