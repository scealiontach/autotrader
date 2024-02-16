from sqlalchemy import (
    JSON,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    Text,
    TIMESTAMP,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Portfolios(Base):
    __tablename__ = "portfolios"
    PortfolioID = Column(Integer, primary_key=True, autoincrement=True)
    Name = Column(String(255), nullable=False)
    Owner = Column(String(255))
    Description = Column(Text)
    CreatedDate = Column(TIMESTAMP, server_default=func.now())


class Products(Base):
    __tablename__ = "products"
    ProductID = Column(Integer, primary_key=True, autoincrement=True)
    Symbol = Column(String(10), nullable=False, unique=True)
    CompanyName = Column(String(255), nullable=False)
    Sector = Column(String(255))
    Market = Column(String(255))
    IsActive = Column(Boolean, server_default="true")
    CreatedDate = Column(TIMESTAMP, server_default=func.now())
    DividendRate = Column(Float)
    Info = Column(JSON)


class Transactions(Base):
    __tablename__ = "transactions"
    TransactionID = Column(Integer, primary_key=True, autoincrement=True)
    PortfolioID = Column(Integer, ForeignKey("portfolios.PortfolioID"), nullable=False)
    ProductID = Column(Integer, ForeignKey("products.ProductID"), nullable=False)
    TransactionType = Column(String(10), nullable=False)
    Price = Column(Float, nullable=False)
    TransactionDate = Column(TIMESTAMP, server_default=func.now())


class PortfolioPositions(Base):
    __tablename__ = "portfoliopositions"
    PositionID = Column(Integer, primary_key=True, autoincrement=True)
    PortfolioID = Column(Integer, ForeignKey("portfolios.PortfolioID"), nullable=False)
    ProductID = Column(Integer, ForeignKey("products.ProductID"), nullable=False)
    Quantity = Column(Float, nullable=False)
    PurchaseDate = Column(TIMESTAMP, server_default=func.now())
    Last = Column(Float)
    Invest = Column(Float)
    __table_args__ = (
        UniqueConstraint("PortfolioID", "ProductID", name="unique_portfolio_product"),
    )


class MarketData(Base):
    __tablename__ = "marketdata"
    DataID = Column(Integer, primary_key=True, autoincrement=True)
    ProductID = Column(Integer, ForeignKey("products.ProductID"), nullable=False)
    Date = Column(TIMESTAMP, server_default=func.now())
    OpeningPrice = Column(Float)
    ClosingPrice = Column(Float)
    HighPrice = Column(Float)
    LowPrice = Column(Float)
    Volume = Column(Integer)
    __table_args__ = (
        UniqueConstraint("ProductID", "Date", name="unique_product_date"),
    )


class TradingRecommendations(Base):
    __tablename__ = "tradingrecommendations"
    RecommendationID = Column(Integer, primary_key=True, autoincrement=True)
    ProductID = Column(Integer, ForeignKey("products.ProductID"), nullable=False)
    RecommendationDate = Column(TIMESTAMP, server_default=func.now())
    Action = Column(String(10), nullable=False)
    ClosingPrice = Column(Float)
    Info = Column(Text)
    __table_args__ = (
        UniqueConstraint(
            "ProductID", "RecommendationDate", name="unique_product_recommendation"
        ),
    )


class CashTransactions(Base):
    __tablename__ = "cashtransactions"
    TransactionID = Column(Integer, primary_key=True, autoincrement=True)
    PortfolioID = Column(Integer, ForeignKey("portfolios.PortfolioID"), nullable=False)
    TransactionType = Column(String(10), nullable=False)
    Amount = Column(Float, nullable=False)
    TransactionDate = Column(TIMESTAMP, server_default=func.now())
    Description = Column(Text)


class MarketMovement(Base):
    __tablename__ = "marketmovement"
    Date = Column(TIMESTAMP, primary_key=True)
    Advancing = Column(Integer, nullable=False)
    Declining = Column(Integer, nullable=False)


# Setup for connecting to the database, replace with your actual database URI
# engine = create_engine('postgresql://username:password@localhost/portfolio_db')
# Base.metadata.create_all(engine)
