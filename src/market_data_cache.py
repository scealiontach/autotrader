import pandas as pd
from sqlalchemy import text
from database import Session


class MarketDataCache:

    def __init__(self, earliest_date="1901-01-01") -> None:
        self.cache = {}
        self.set_earliest_date(earliest_date)

    def set_earliest_date(self, earliest_date):
        self.earliest_date = earliest_date

    def load_data(self, product_id):
        """
        Load and cache data for the given product_id starting from the earliest_date.
        """
        with Session() as session:
            if product_id not in self.cache:
                query = """
                    SELECT Date, ClosingPrice
                    FROM MarketData
                    WHERE ProductID = :product_id AND Date >= :after_date
                    ORDER BY Date ASC;
                """
                statement = text(query)
                df = pd.read_sql(statement, session.bind, params={"product_id":product_id, "after_date": self.earliest_date})  # type: ignore
                self.cache[product_id] = df
        # Else: Data for this product_id is already loaded

    def get_data(self, product_id, start_date, end_date):
        """
        Retrieve data for a specific date range from the cache.
        """
        if product_id in self.cache:
            df = self.cache[product_id]
            # Filter the DataFrame for the specified date range
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            return df.loc[mask]
        else:
            # This should not happen if load_data is called appropriately
            raise ValueError(f"Data for {product_id} not loaded into cache.")


CACHE = MarketDataCache()
