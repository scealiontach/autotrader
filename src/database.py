from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
from constants import DATABASE_URL


engine = create_engine(DATABASE_URL)  # type: ignore
Session = scoped_session(sessionmaker(bind=engine))
