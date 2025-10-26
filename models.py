# models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, func, Text
from sqlalchemy.sql import expression
from sqlalchemy.orm import Mapped, mapped_column
from db import Base
from sqlalchemy import Boolean

class Country(Base):
    __tablename__ = "countries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    capital = Column(String(255), nullable=True)
    region = Column(String(100), nullable=True)
    population = Column(Integer, nullable=False)
    currency_code = Column(String(10), nullable=True)      # nullable to allow countries with no currency
    exchange_rate = Column(Float, nullable=True)            # rate relative to USD (from API). null when not in rates
    estimated_gdp = Column(Float, nullable=True)            # null or 0 per rules
    flag_url = Column(Text, nullable=True)
    last_refreshed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Metadata(Base):
    __tablename__ = "metadata"
    # Single-row table (key/value)
    key = Column(String(100), primary_key=True)
    value = Column(String(500), nullable=True)
