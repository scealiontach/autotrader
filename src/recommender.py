import logging as log
from datetime import date, timedelta
from decimal import Decimal
import math
from types import NoneType
from typing import Union

from sqlalchemy import text

from analyzer import ProductAnalyzer
from constants import BUY, HOLD, SELL
from database import Session
from market import Market
from market_data_cache import CACHE
from product import Product


class Recommendation:
    def __init__(
        self,
        symbol: str,
        action: str,
        last: Union[Decimal, NoneType],
        strategy: str,
        strength: Decimal,
        info=None,
    ) -> None:
        self.symbol = symbol
        self.action = action
        self.last = last
        self.strategy = strategy
        self.strength = strength
        self.info = info
        self.as_of: Union[date, None] = None

    def __repr__(self):
        return f"Recommendation({self.symbol}:{self.symbol} strategy={self.strategy}, strength={self.strength})"

    def as_dict(self):
        return {
            "symbol": self.symbol,
            "action": self.action,
            "last": self.last,
            "strategy": self.strategy,
            "strength": self.strength,
            "info": self.info,
            "as_of": self.as_of,
        }


class Action(Recommendation):
    def __init__(
        self, recommendation: Recommendation, portfolio_move: str, shares: Decimal
    ):
        super().__init__(
            recommendation.symbol,
            recommendation.action,
            recommendation.last,
            recommendation.strategy,
            recommendation.strength,
            recommendation.info,
        )
        self.portfolio_move = portfolio_move
        self.shares = shares


STRATEGIES = [
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


class Recommender:
    def __init__(
        self,
        portfolio_id: int,
        product: Product,
        end_date,
        strategy="advanced",
    ) -> None:
        self.strategy = strategy
        self.portfolio_id = portfolio_id
        self.end_date = end_date
        self.product = product
        self.analyzer = ProductAnalyzer(self.product, end_date)
        self.data_cache = CACHE
        self.data_cache.load_data(self.product.id)

    def _make_recommendation(
        self, action: str, strategy: str, strength: Decimal, info=None
    ):
        last_price = self.product.fetch_last_closing_price(self.end_date)
        return Recommendation(
            self.product.symbol, action, last_price, strategy, strength, info
        )

    def sma_buy_hold(self, window=50):
        sma = self.analyzer.sma(window)
        strategy = "sma_buy_hold"
        last_price = self.product.fetch_last_closing_price(self.end_date)
        strength = Decimal(0)
        if (
            sma is None
            or last_price is None
            or math.isnan(sma)
            or math.isnan(last_price)
        ):
            return self._make_recommendation(
                HOLD, strategy, strength, info={"sma": sma}
            )
        if last_price > sma:
            strength = (last_price - sma) / sma
            return self._make_recommendation(BUY, strategy, strength, info={"sma": sma})
        else:
            return self._make_recommendation(
                HOLD, strategy, strength, info={"sma": sma}
            )

    def rsi(self, window=14, high=70, low=30):
        rsi = self.analyzer.rsi(window=window)
        strategy = "rsi"
        if rsi is None:
            strength = Decimal(0)
            return self._make_recommendation(
                HOLD, strategy, strength, info={"rsi": rsi}
            )
        if rsi > high:
            strength = (rsi - high) / high
            return self._make_recommendation(
                SELL, strategy, strength, info={"rsi": rsi}
            )
        elif rsi < low:
            strength = (low - rsi) / low
            return self._make_recommendation(BUY, strategy, strength, info={"rsi": rsi})
        else:
            strength = Decimal(0)
            return self._make_recommendation(
                HOLD, strategy, strength, info={"rsi": rsi}
            )

    def vwap(self, high=1.02, low=0.98):
        vwap = self.analyzer.vwap()
        strategy = "vwap"
        last_price = self.product.fetch_last_closing_price(self.end_date)
        if not vwap:
            return self._make_recommendation(
                HOLD, strategy, Decimal(0), info={"vwap": vwap}
            )
        if last_price and last_price < vwap * Decimal(low):
            strength = abs(last_price - vwap) / vwap
            return self._make_recommendation(
                BUY, strategy, strength, info={"vwap": vwap}
            )
        elif last_price and last_price > vwap * Decimal(high):
            strength = abs(last_price - vwap) / vwap
            return self._make_recommendation(
                SELL, strategy, strength, info={"vwap": vwap}
            )
        elif last_price:
            strength = abs(last_price - vwap) / vwap
            return self._make_recommendation(
                HOLD, strategy, strength, info={"vwap": vwap}
            )
        else:
            strength = Decimal(9)
            return self._make_recommendation(
                HOLD, strategy, strength, info={"vwap": vwap}
            )

    def mean_reversion(self, period=50):
        """
        Make a mean reversion action recommendation based on the current price relative to the SMA.

        :param product_id: The ID of the product/security.
        :param target_date: The target date for the analysis (YYYY-MM-DD format).
        :param period: The period over which to calculate the SMA.
        :return: A recommendation string (BUY, SELL, or HOLD).
        """
        strategy = "mean_reversion"
        try:
            with Session() as session:
                # Fetch the closing prices for the specified period ending on the target date
                statement = text(
                    """
                    SELECT ClosingPrice
                    FROM MarketData
                    WHERE ProductID = :product_id AND Date <= :end_date
                    ORDER BY Date DESC
                    LIMIT :period;
                """
                )
                prices = session.execute(
                    statement,
                    {
                        "product_id": self.product.id,
                        "end_date": self.end_date,
                        "period": period,
                    },
                ).fetchall()

                if len(prices) < period:
                    return self._make_recommendation(HOLD, strategy, Decimal(0))

                # Calculate the SMA and compare the current price to the SMA
                sma = Decimal(sum((price[0] for price in prices), start=0) / period)
                current_price = prices[0][0]

                # Define thresholds for decision making (e.g., 5% deviation from the SMA)
                threshold = Decimal(0.05) * sma
                high_threshold = sma + threshold
                low_threshold = sma - threshold

                low_strength = abs(current_price - low_threshold) / low_threshold
                high_strength = abs(current_price - high_threshold) / high_threshold

                if math.isnan(current_price) or math.isnan(sma):
                    return self._make_recommendation(HOLD, strategy, Decimal(0))
                if current_price < low_threshold:
                    return self._make_recommendation(BUY, strategy, low_strength)
                elif current_price > high_threshold:
                    return self._make_recommendation(SELL, strategy, high_strength)
                else:
                    return self._make_recommendation(HOLD, strategy, Decimal(0))
        except Exception as e:
            log.error(f"(E03) An error occurred: {e}")
            raise e

    def macd(self, short_span=9, mid_span=12, long_span=26):
        """
        Calculate MACD for a given product_id and target_date, and make a trading recommendation.

        :param product_id: The ID of the product.
        :param target_date: The target date for the recommendation (YYYY-MM-DD format).
        :return: A trading recommendation (BUY, SELL, or HOLD).
        """
        strategy = "macd"
        try:
            start_date = self.end_date - timedelta(days=long_span * 3)
            # Fetch historical closing prices up to the target date
            df = self.data_cache.get_data(self.product.id, start_date, self.end_date)

            # Ensure there's enough data
            if df.empty or len(df) < long_span:
                return self._make_recommendation(HOLD, strategy, Decimal(0))

            # Calculate the MACD and signal line
            exp1 = df["closingprice"].ewm(span=mid_span, adjust=False).mean()
            exp2 = df["closingprice"].ewm(span=long_span, adjust=False).mean()
            macd = exp1 - exp2
            signal = macd.ewm(span=short_span, adjust=False).mean()

            macd_signal_difference = abs(macd.iloc[-1] - signal.iloc[-1])
            strength = Decimal(macd_signal_difference)

            # Determine the trading recommendation
            if macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]:
                return self._make_recommendation(BUY, strategy, strength)
            elif macd.iloc[-1] < signal.iloc[-1] and macd.iloc[-2] >= signal.iloc[-2]:
                return self._make_recommendation(SELL, strategy, strength)
            else:
                return self._make_recommendation(HOLD, strategy, Decimal(0))
        except Exception as e:
            log.error(f"(E04) An error occurred: {e}")
            raise e

    def buy_sma_sell_rsi(self, window=50, high=70, low=30):
        secondary_rec = self.rsi(window=window, high=high, low=low)
        primary_rec = self.sma_buy_hold(window=window)
        # filter sma_recs which have the HOLD action and which are also SELL action in rsi_recs
        if primary_rec.action == HOLD and secondary_rec.action == SELL:
            primary_rec.action = SELL
            primary_rec.strength = secondary_rec.strength
        primary_rec.strategy = "buy_sma_sell_rsi"
        return primary_rec

    def buy_sma_sell_vwap(self, window=50, high=1.02, low=0.98):
        primary_rec = self.sma_buy_hold(window=window)
        secondary_rec = self.vwap(high=high, low=low)
        # filter sma_recs which have the HOLD action and which are also SELL action in rsi_recs
        if primary_rec.action in (HOLD, BUY) and secondary_rec.action == SELL:
            primary_rec.action = SELL
            primary_rec.strength = secondary_rec.strength
        primary_rec.strategy = "buy_sma_sell_vwap"
        return primary_rec

    def sma_rsi(
        self,
        rsi_period=14,
        sma_short_period=50,
        sma_long_period=200,
        high=70,
        low=30,
        mid=50,
    ):
        strategy = "sma_rsi"
        rsi = self.analyzer.rsi(window=rsi_period)
        sma_short = self.analyzer.sma(sma_short_period)
        sma_long = self.analyzer.sma(sma_long_period)

        if (
            rsi is None
            or sma_short is None
            or sma_long is None
            or math.isnan(rsi)
            or math.isnan(sma_short)
            or math.isnan(sma_long)
        ):
            return self._make_recommendation(HOLD, strategy, Decimal(0))
        if rsi > high:
            strength = (rsi - high) / high
            return self._make_recommendation(
                SELL, strategy, strength, info={"rsi": rsi, "rsi_high": high}
            )
        elif rsi < low and rsi != 0:
            return self._make_recommendation(
                BUY, strategy, (low - rsi) / rsi, info={"rsi": rsi, "rsi_low": low}
            )
        elif sma_short > sma_long:
            # Uptrend condition
            if rsi >= mid:
                return self._make_recommendation(
                    BUY,
                    strategy,
                    (rsi - mid) / mid,
                    info={
                        "rsi": rsi,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "rsi_mid": mid,
                    },
                )
            else:
                return self._make_recommendation(
                    HOLD,
                    strategy,
                    (rsi - mid) / mid,
                    info={
                        "rsi": rsi,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "rsi_mid": mid,
                    },
                )
        elif sma_short < sma_long:
            # Downtrend condition
            strength = Decimal(0)
            if rsi != 0:
                strength = (mid - rsi) / rsi
            if rsi <= mid and rsi != 0:
                return self._make_recommendation(
                    SELL,
                    strategy,
                    (mid - rsi) / rsi,
                    info={
                        "rsi": rsi,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "rsi_mid": mid,
                    },
                )
            else:
                return self._make_recommendation(
                    HOLD,
                    strategy,
                    strength,
                    info={
                        "rsi": rsi,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "rsi_mid": mid,
                    },
                )
        else:
            return self._make_recommendation(
                HOLD,
                strategy,
                Decimal(0),
                info={
                    "rsi": rsi,
                    "sma_short": sma_short,
                    "sma_long": sma_long,
                },
            )

    def record_recommendations(
        self,
        recommendations: list[Recommendation],
    ):
        """
        Records daily trading recommendations into the TradingRecommendations table.

        Parameters:
        - recommendations: A list of dicts, each dict containing 'symbol', 'action', 'last', and 'vwap'.
        """
        with Session() as session:
            try:
                session.expire_on_commit = False
                for rec in recommendations:
                    product = Product.from_symbol(rec.symbol)
                    if product is None:
                        raise ValueError(
                            f"Product with symbol {rec.symbol} not found in the database."
                        )
                    # Insert the recommendation, skip if already exists for today
                    statement = text(
                        """
                        INSERT INTO TradingRecommendations (PortfolioID, ProductID, RecommendationDate, Action)
                        VALUES (:portfolio_id, :product_id, :recommendation_date, :action)
                        ON CONFLICT (PortfolioID, ProductID) do update set RecommendationDate = EXCLUDED.RecommendationDate, Action = EXCLUDED.Action;
                    """
                    )
                    session.execute(
                        statement,
                        {
                            "portfolio_id": self.portfolio_id,
                            "product_id": product.id,
                            "recommendation_date": rec.as_of,
                            "action": rec.action,
                        },
                    )
                    session.commit()
            except Exception as e:
                log.error(f"(E05) Error inserting recommendation for {rec.symbol}: {e}")
                session.rollback()

    def upmacd_downmr(self):
        market = Market(self.end_date)
        if market.adline():
            rec = self.mean_reversion(period=200)
        else:
            rec = self.macd()
        rec.strategy = "upmacd_downmr"
        return rec

    def breakout_recommendation(self, breakout_window=50):
        analyzer = ProductAnalyzer(self.product, self.end_date)
        signal = analyzer.breakout(breakout_window=breakout_window)
        if signal > 0:
            return self._make_recommendation(BUY, "breakout", Decimal(signal))
        elif signal < 0:
            return self._make_recommendation(SELL, "breakout", Decimal(signal))
        else:
            return self._make_recommendation(HOLD, "breakout", Decimal(signal))

    def bollinger_recommendation(self, window=20, num_std_dev=2):
        analyzer = ProductAnalyzer(self.product, self.end_date)
        signal = analyzer.bollinger_bands(window=window, num_std_dev=num_std_dev)
        if signal > 0:
            return self._make_recommendation(BUY, "bollinger", Decimal(signal))
        elif signal < 0:
            return self._make_recommendation(SELL, "bollinger", Decimal(signal))
        else:
            return self._make_recommendation(HOLD, "bollinger", Decimal(signal))

    def engulging_recommendation(self):
        analyzer = ProductAnalyzer(self.product, self.end_date)
        signal = analyzer.engulfing()
        if signal and signal > 0:
            return self._make_recommendation(BUY, "engulfing", Decimal(signal))
        elif signal and signal < 0:
            return self._make_recommendation(SELL, "engulfing", Decimal(signal))
        else:
            return self._make_recommendation(HOLD, "engulfing", Decimal(0))

    def advanced_recommendation(self, weights=None):
        score = 0

        if weights is None:
            indicator_weights = {
                "engulfing": 0.1,
                "macd": 0.1,
                "rsi": 0.15,
                "vwap": 0.2,
                "mean_reversion": 0.1,
                "breakout": 0.25,
                "bollinger": 0.25,
            }
        else:
            indicator_weights = weights

        total_possible_score = 0
        possible_count = 0
        for strategy in STRATEGIES:
            if strategy in indicator_weights:
                total_possible_score += indicator_weights[strategy]
                possible_count += 1

        if total_possible_score == 0:
            return self._make_recommendation(HOLD, "advanced", Decimal(0))

        threshold = (total_possible_score / possible_count) * 2

        score = 0
        for strategy in STRATEGIES:
            if strategy in indicator_weights:
                rec = self._strategy_byname(strategy)
                if rec.action == BUY:
                    score += indicator_weights[strategy]
                elif rec.action == SELL:
                    score -= indicator_weights[strategy]

        if score >= threshold:
            return self._make_recommendation(BUY, "advanced", Decimal(score))
        elif score <= -threshold:
            return self._make_recommendation(SELL, "advanced", Decimal(score))
        else:
            return self._make_recommendation(HOLD, "advanced", Decimal(score))

    def _strategy_byname(self, strategy: str) -> Recommendation:
        if strategy == "bollinger":
            return self.bollinger_recommendation()
        if strategy == "breakout":
            return self.breakout_recommendation()
        if strategy == "sma_buy_hold":
            return self.sma_buy_hold()
        elif strategy == "rsi":
            return self.rsi()
        elif strategy == "vwap":
            return self.vwap()
        elif strategy == "mean_reversion":
            return self.mean_reversion()
        elif strategy == "macd":
            return self.macd()
        elif strategy == "buy_sma_sell_rsi":
            return self.buy_sma_sell_rsi()
        elif strategy == "buy_sma_sell_vwap":
            return self.buy_sma_sell_vwap()
        elif strategy == "sma_rsi":
            return self.sma_rsi()
        elif strategy == "upmacd_downmr":
            return self.upmacd_downmr()
        elif strategy == "advanced":
            return self.advanced_recommendation()
        elif strategy == "engulfing":
            return self.engulging_recommendation()
        else:
            raise ValueError(f"Invalid strategy: {strategy}")

    def recommend(self):
        return self._strategy_byname(self.strategy)
