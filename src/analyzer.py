import logging as log
import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Union
from market_data_cache import CACHE
from sqlalchemy import text

from database import Session
from product import Product


def cumulative_return(initial_value: Decimal, final_value: Decimal) -> Decimal:
    """
    Calculate the cumulative return of an investment.

    :param initial_value: The initial value or price of the investment.
    :param final_value: The final value or price of the investment.
    :return: The cumulative return as a float.
    """
    if initial_value <= 0:
        return Decimal(0)

    cumulative_return = (final_value / initial_value) - 1
    return cumulative_return


class ProductAnalyzer:

    def __init__(self, product: Product, end_date) -> None:
        self.product = product
        self.end_date = end_date

    def cum_return(self, start_date: date) -> Union[Decimal, None]:
        in_price = self.product.fetch_last_closing_price(start_date)
        out_price = self.product.fetch_last_closing_price(self.end_date)
        if in_price is None or out_price is None:
            return None
        return cumulative_return(in_price, out_price)

    def sma(self, period: int) -> Union[Decimal, None]:
        """
        Calculate the Simple Moving Average (SMA) for a given product using the last 'period' closing prices
        before and including the target end_date.

        :param symbol: The symbol of the product.
        :param period: The number of closing prices to include in the SMA calculation.
        :param end_date: The end date for the SMA calculation (as a string in 'YYYY-MM-DD' format).
        :return: The SMA value as a float, or None if insufficient data is available.
        """
        try:
            with Session() as session:
                # Query to select the last 'period' closing prices before the end_date, then calculate average
                statement = text(
                    """
                    WITH OrderedPrices AS (
                        SELECT ClosingPrice
                        FROM MarketData
                        WHERE ProductID = :product_id AND Date <= :end_date
                        ORDER BY Date DESC
                        LIMIT :period
                    )
                    SELECT AVG(ClosingPrice) FROM OrderedPrices;
                """
                )
                result = session.execute(
                    statement,
                    {
                        "product_id": self.product.id,
                        "end_date": self.end_date,
                        "period": period,
                    },
                ).first()
                if result and result[0] is not None:
                    return result[0]
                else:
                    return None
        except Exception as e:
            log.error(f"(E01) An error occurred: {e}")
            return None

    def vwap(self, window=200) -> Union[Decimal, None]:
        """
        Calculate the VWAP for a given symbol on a specific date.

        :param symbol: The stock symbol.
        :param specific_date: The date for which to calculate the VWAP (datetime.date object).
        :return: The VWAP as a float, or None if data is not available.
        """
        start_date = self.end_date - timedelta(days=window)
        vwap = None
        # Filter the DataFrame for the desired date range
        df = CACHE.get_data(self.product.id, start_date, self.end_date)

        # Check if DataFrame is empty
        if df.empty:
            return None

        # Calculate the VWAP
        numerator = (df["closingprice"] * df["volume"]).sum()
        denominator = df["volume"].sum()

        # Check for divide by zero scenario
        if denominator == 0:
            return None

        vwap = numerator / denominator

        # Return the result as a Decimal
        return Decimal(vwap)

    def rsi(self, window=14) -> Union[Decimal, None]:
        """
        Calculate the RSI for a given symbol, window, and target day.

        :param symbol: The stock symbol.
        :param window: The window size for RSI calculation (e.g., 14 days).
        :param target_day: The target day for the calculation (YYYY-MM-DD).
        :return: The RSI value.
        """
        try:
            start_date = self.end_date - timedelta(
                days=window * 2
            )  # Fetch more days to ensure enough data
            end_date = self.end_date

            with Session() as session:
                statement = text(
                    """
                    SELECT Date, ClosingPrice
                    FROM MarketData
                    WHERE ProductID = :product_id AND Date BETWEEN :start_date AND :end_date
                    ORDER BY Date ASC;
                """
                )
                rows = session.execute(
                    statement,
                    {
                        "product_id": self.product.id,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                ).all()

                prices: list[Decimal] = [row[1] for row in rows]

                # Ensure we have enough data points
                if len(prices) < window + 1:
                    return None

                # Calculate daily changes
                changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

                # Separate gains and losses
                gains = [max(change, Decimal(0)) for change in changes]
                losses = [-min(change, Decimal(0)) for change in changes]

                gains = gains[-window:]
                losses = losses[-window:]

                sum_gains = Decimal(sum(gains))
                sum_losses = Decimal(sum(losses))
                # Calculate average gain and average loss
                avg_gain = sum_gains / window
                avg_loss = sum_losses / window

                if avg_loss == 0:
                    return Decimal(100)  # Prevent division by zero

                # Calculate RS and RSI
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))

                return rsi
        except Exception as e:
            log.error(f"(E02) An error occurred: {e}")
            return None

    def engulfing(self):
        """
        Detects if a Bearish Engulfing pattern occurred for a given product_id on a target date.

        :param product_id: The ID of the product.
        :param target_date: The target date to check for the pattern (YYYY-MM-DD format).
        :return: True if a Bearish Engulfing pattern is detected, False otherwise.
        """
        try:
            with Session() as session:
                statement = text(
                    """
                    SELECT Date, OpeningPrice, ClosingPrice
                    FROM MarketData
                    WHERE ProductID = :product_id AND Date <= :to_date AND Date >= :from_date
                    ORDER BY Date DESC
                    LIMIT 2;
                """
                )
                rows = session.execute(
                    statement,
                    {
                        "product_id": self.product.id,
                        "to_date": self.end_date,
                        "from_date": self.end_date - timedelta(days=7),
                    },
                ).all()

                if len(rows) < 2:
                    return False  # Not enough data to determine the pattern

                # Unpack rows
                _, open_today, close_today = rows[0]
                _, open_yesterday, close_yesterday = rows[1]

                # Check for Bearish Engulfing pattern
                if (
                    close_yesterday > open_yesterday
                    and close_today < open_today
                    and open_today >= close_yesterday
                    and close_today < open_yesterday
                ):
                    return -1  # Bearish Engulfing pattern detected
                else:
                    # Check for Bullish Engulfing pattern
                    if (
                        close_yesterday < open_yesterday
                        and close_today > open_today
                        and open_today <= close_yesterday
                        and close_today > open_yesterday
                    ):
                        return 1  # Bullish Engulfing pattern detected
                    else:
                        return 0  # No Engulfing pattern
        except Exception as e:
            log.error(f"An error occurred: {e}")
            return None

    def breakout(self, breakout_window=20):
        """
        Evaluates a breakout strategy signal for a specific day using SQL database.

        Parameters:
        - breakout_window (int): The number of days to consider for identifying the breakout range.

        Returns:
        - signal (int): The signal for the target date, where 1 represents a buy signal,
                        -1 represents a sell signal, and 0 represents no signal.
        """
        signal = 0
        with Session() as session:
            statement = text(
                """
                SELECT HighPrice, LowPrice, ClosingPrice
                FROM MarketData
                WHERE ProductID = :product_id AND Date <= :target_date
                ORDER BY Date DESC
                LIMIT :breakout_window;
                """
            )
            rows = session.execute(
                statement,
                {
                    "product_id": self.product.id,
                    "target_date": self.end_date,
                    "breakout_window": breakout_window,
                },
            ).all()

            # Ensure there's enough data to evaluate
            if len(rows) < breakout_window:
                return 0

            # Extract high, low, and close prices
            highs = [row[0] for row in rows]
            lows = [row[1] for row in rows]
            closing_price = rows[0][2]  # Most recent close price

            # Calculate highest high and lowest low
            highest_high = max(highs)
            lowest_low = min(lows)

            # Evaluate the signal
            if closing_price > highest_high:
                signal = 1  # Buy signal
            elif closing_price < lowest_low:
                signal = -1  # Sell signal
            else:
                signal = 0  # No signal
        return signal

    def bollinger_bands(self, window=20, num_std_dev=2):
        """
        Calculate Bollinger Bands signal for a given stock symbol and date.

        Parameters:
        - window (int): Number of days for the moving average.
        - num_std_dev (int): Number of standard deviations for the bands.

        Returns:
        - signal (str): 'BUY', 'SELL', or 'HOLD' based on Bollinger Bands.
        """

        # Connect to the SQLite database
        with Session() as session:
            statement = text(
                """
                SELECT Date, ClosingPrice
                FROM MarketData
                WHERE ProductID = :product_id AND Date <= :target_date
                ORDER BY Date DESC
                LIMIT :window;
                """
            )
            rows = session.execute(
                statement,
                {
                    "product_id": self.product.id,
                    "target_date": self.end_date,
                    "window": window,
                },
            ).all()

            close_prices = [row[1] for row in rows]

            # Ensure we have enough data points
            if len(close_prices) < window:
                return 0

            # Calculate the moving average
            avg_close = sum(close_prices) / len(close_prices)

            # Calculate the standard deviation
            std_dev = statistics.stdev(close_prices)

            # Calculate the upper and lower Bollinger Bands
            upper_band = avg_close + (num_std_dev * std_dev)
            lower_band = avg_close - (num_std_dev * std_dev)

            # Get the most recent close price
            recent_close = close_prices[0]

            # Determine the signal
            if recent_close > upper_band:
                signal = -1
            elif recent_close < lower_band:
                signal = 1
            else:
                signal = 0

            return signal


# Example usage
