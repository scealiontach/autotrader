from datetime import date
import logging as log
from decimal import Decimal

import pandas as pd
from cachetools import LFUCache
from sqlalchemy import text

from analyzer import ProductAnalyzer
from constants import INDEX_SYMBOLS
from database import Session
from product import Product

market_cache = LFUCache(maxsize=1024)


class Market:
    def __init__(self, end_date, indexes=INDEX_SYMBOLS) -> None:
        self.end_date = end_date
        self.indexes = indexes

    def adline(self, period=50):
        """
        Checks if the A-D Line moves below its moving average on a particular date.
        True = A-D Line below its moving average (bearish signal)
        False = A-D Line above its moving average (bullish signal)

        :param target_date: The date to check (YYYY-MM-DD format).
        :param period: The period over which to calculate the moving average.
        :return: Boolean indicating if the A-D Line was below its moving average on the target date.
        """
        cache_key = f"adline_{self.end_date}_{period}"
        if cache_key in market_cache:
            return market_cache[cache_key]
        try:
            with Session() as session:
                # Fetch market movement data up to the target date
                query = """
                    SELECT date, (advancing - declining) AS net_advancing
                    FROM MarketMovement
                    WHERE date <= :before_date
                    ORDER BY date ASC;
                """
                statement = text(query)
                df = pd.read_sql(statement, session.bind, params={"before_date": self.end_date})  # type: ignore

                # Calculate the A-D Line as a cumulative sum of net advancing
                df["ad_line"] = df["net_advancing"].cumsum()

                # Calculate the moving average of the A-D Line
                df["ma"] = df["ad_line"].rolling(window=period).mean()

                # Check if the A-D Line was below its moving average on the target date
                target_row = df[df["date"] == self.end_date]
                if not target_row.empty and (
                    target_row["ad_line"].iloc[0] < target_row["ma"].iloc[0]
                ):
                    market_cache[cache_key] = True
                    return True
                market_cache[cache_key] = False
                return False
        except Exception as e:
            log.error(f"(E08) An error occurred: {e}")
            return False

    def rate_performance(self, first_day: date, roi: Decimal) -> int:
        score = 0
        for idx in self.indexes:
            product = Product.from_symbol(idx)
            if product is None:
                raise ValueError(
                    f"Product with symbol {idx} not found in the database."
                )
            analyzer = ProductAnalyzer(product, self.end_date)
            cum_ret = analyzer.cum_return(first_day)
            if cum_ret is None:
                continue
            index_ret = cum_ret * 100
            if index_ret > roi:
                score -= 1
            elif index_ret < roi:
                score += 1

        if score == len(self.indexes):
            rating = 1
        elif score > 0 and score < len(self.indexes):
            rating = 0
        else:
            rating = -1
        return rating
