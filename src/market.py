from decimal import Decimal
from functools import lru_cache
import pandas as pd
from product import Product

from recommender import ProductAnalyzer
from constants import INDEX_SYMBOLS


class Market:
    def __init__(self, connection, end_date, indexes=INDEX_SYMBOLS) -> None:
        self.connection = connection
        self.end_date = end_date
        self.indexes = indexes

    @lru_cache(maxsize=1024)
    def adline(self, period=50):
        """
        Checks if the A-D Line moves below its moving average on a particular date.

        :param target_date: The date to check (YYYY-MM-DD format).
        :param period: The period over which to calculate the moving average.
        :return: Boolean indicating if the A-D Line was below its moving average on the target date.
        """
        try:
            # Fetch market movement data up to the target date
            query = """
                SELECT date, (advancing - declining) AS net_advancing
                FROM MarketMovement
                WHERE date <= %s
                ORDER BY date ASC;
            """
            df = pd.read_sql(query, self.connection, params=[self.end_date])

            # Calculate the A-D Line as a cumulative sum of net advancing
            df["ad_line"] = df["net_advancing"].cumsum()

            # Calculate the moving average of the A-D Line
            df["ma"] = df["ad_line"].rolling(window=period).mean()

            # Check if the A-D Line was below its moving average on the target date
            target_row = df[df["date"] == self.end_date]
            if not target_row.empty and (
                target_row["ad_line"].iloc[0] < target_row["ma"].iloc[0]
            ):
                return True
            return False
        except Exception as e:
            print(f"(E08) An error occurred: {e}")
            return False

    def rate_performance(self, first_day, roi: Decimal) -> int:
        score = 0
        for idx in self.indexes:
            product = Product.from_symbol(self.connection, idx)
            analyzer = ProductAnalyzer(self.connection, product, self.end_date)
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
