-- Portfolios Table
CREATE TABLE Portfolios (
    PortfolioID SERIAL PRIMARY KEY,
    Name VARCHAR(255) NOT NULL,
    Owner VARCHAR(255),
    Description TEXT,
    CreatedDate TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    IsActive BOOLEAN DEFAULT TRUE,
    reserve_cash_percent DECIMAL(5, 2) DEFAULT 5,
    reinvest_period INTEGER DEFAULT 7,
    reinvest_amt DECIMAL(15, 2) DEFAULT 0,
    bank_threshold DECIMAL(8, 2) DEFAULT 10000,
    bank_pc INTEGER DEFAULT 33,
    rebalance_months JSON default '[1]',
    dividend_only BOOLEAN DEFAULT FALSE,
    sectors_allowed JSON default '[]',
    sectors_forbidden JSON default '[]',
    max_exposure INTEGER DEFAULT 20,
    strategy VARCHAR(32) DEFAULT 'advanced'
);

-- Products (Equities) Table
CREATE TABLE Products (
    ProductID SERIAL PRIMARY KEY,
    Symbol VARCHAR(10) NOT NULL UNIQUE,
    CompanyName VARCHAR(255) NOT NULL,
    Sector VARCHAR(255),
    Market VARCHAR(255),
    IsActive BOOLEAN DEFAULT TRUE,
    CreatedDate TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

alter table products add column dividend_rate NUMERIC
(10,2);
alter table products add column info json;



-- Transactions Table
CREATE TABLE Transactions (
    TransactionID SERIAL PRIMARY KEY,
    PortfolioID INT NOT NULL,
    ProductID INT NOT NULL,
    TransactionType VARCHAR(4) CHECK (TransactionType IN ('BUY', 'SELL')),
    Quantity NUMERIC(14, 6) NOT NULL,
    Price NUMERIC(10, 2) NOT NULL,
    TransactionDate TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    FOREIGN KEY (PortfolioID) REFERENCES Portfolios(PortfolioID),
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- PortfolioPositions Table
CREATE TABLE PortfolioPositions (
    PositionID SERIAL PRIMARY KEY,
    PortfolioID INT NOT NULL,
    ProductID INT NOT NULL,
    Quantity NUMERIC(14, 6) NOT NULL,
    PurchaseDate DATE,
    LastUpdated TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    last NUMERIC(14,6),
    invest NUMERIC(14,6),
    FOREIGN KEY (PortfolioID) REFERENCES Portfolios(PortfolioID),
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);


ALTER TABLE PortfolioPositions
ADD CONSTRAINT portfolio_product_unique UNIQUE (PortfolioID, ProductID);


-- MarketData Table
CREATE TABLE MarketData (
    DataID SERIAL PRIMARY KEY,
    ProductID INT NOT NULL,
    Date DATE NOT NULL,
    OpeningPrice NUMERIC(10, 2),
    ClosingPrice NUMERIC(10, 2),
    HighPrice NUMERIC(10, 2),
    LowPrice NUMERIC(10, 2),
    Volume BIGINT,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
    UNIQUE (ProductID, Date)
);

CREATE TABLE TradingRecommendations (
    RecommendationID SERIAL PRIMARY KEY,
    PortfolioID INT NOT NULL,
    ProductID INT NOT NULL,
    RecommendationDate DATE NOT NULL,
    Action VARCHAR(10),
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
    FOREIGN KEY (PortfolioID) REFERENCES Portfolios(PortfolioID),
    UNIQUE (PortfolioID, ProductID)
);


CREATE TABLE CashTransactions
(
  TransactionID SERIAL PRIMARY KEY,
  PortfolioID INT NOT NULL,
  TransactionType VARCHAR(50),
  -- e.g., 'DEPOSIT', 'WITHDRAWAL', 'BUY', 'SELL'
  Amount NUMERIC(15, 2),
  -- Positive for deposits/incoming, negative for withdrawals/outgoing
  TransactionDate DATE NOT NULL,
  Description TEXT,
  -- Optional description of the transaction
  FOREIGN KEY (PortfolioID) REFERENCES Portfolios(PortfolioID)
);

CREATE TABLE MarketMovement
(
  date DATE PRIMARY KEY,
  advancing INT NOT NULL,
  declining INT NOT NULL
);

CREATE TABLE Lots
(
  LotID SERIAL PRIMARY KEY,
  PortfolioID INT NOT NULL,
  ProductID INT NOT NULL,
  Quantity DECIMAL(14, 6) NOT NULL,
  PurchasePrice DECIMAL(10, 2) NOT NULL,
  PurchaseDate DATE NOT NULL,
  FOREIGN KEY (PortfolioID) REFERENCES Portfolios(PortfolioID),
  FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

CREATE TABLE portfolio_performance
(
  id SERIAL PRIMARY KEY,
  portfolio_id INTEGER NOT NULL,
  date DATE NOT NULL,
  stock_value NUMERIC(15, 2) NOT NULL,
  invested NUMERIC(15, 2) NOT NULL,
  cash NUMERIC(15, 2) NOT NULL,
  bank NUMERIC(15, 2) NOT NULL,
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolioid)
);

CREATE TABLE simulation_tracker
(
  id SERIAL PRIMARY KEY,
  portfolio_id INTEGER NOT NULL unique,
  first_date DATE NOT NULL,
  run_length_days INTEGER NOT NULL DEFAULT 365,
  last_sim_date DATE NOT NULL,
  FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolioid)
);
