# app.py
import os
from flask import Flask, request, jsonify, send_file
from dotenv import load_dotenv
from db import SessionLocal, engine, Base
from models import Country, Metadata
from utils import fetch_countries, fetch_exchange_rates, utcnow
from image_gen import generate_summary_image
from sqlalchemy import select, func, update
from sqlalchemy.exc import SQLAlchemyError
import random
from datetime import datetime
import traceback

load_dotenv()
CACHE_DIR = os.getenv("CACHE_DIR", "cache")

# Create DB tables if not exist (for quick start). For production, use migrations.
Base.metadata.create_all(bind=engine)

app = Flask(__name__)

# Helper: JSON error responses
def json_error(message, details=None, status=400):
    payload = {"error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status

@app.route("/countries/refresh", methods=["POST"])
def refresh_countries():
    session = SessionLocal()
    try:
        # 1) fetch external APIs first (fail fast)
        try:
            countries_data = fetch_countries()
        except Exception as e:
            return json_error("External data source unavailable", f"Could not fetch data from restcountries: {str(e)}", status=503)
        try:
            rates = fetch_exchange_rates()
        except Exception as e:
            return json_error("External data source unavailable", f"Could not fetch data from exchange rates: {str(e)}", status=503)

        # 2) Begin a DB transaction. We'll perform upserts.
        # If any unexpected error occurs during DB write, rollback and return 500.
        with session.begin():
            # Upsert each country
            processed = 0
            for c in countries_data:
                name = c.get("name")
                # Skip if name or population missing: name is required
                if not name:
                    # skip this record (no name)
                    continue
                population = c.get("population")
                if population is None:
                    # Skip or set to 0? Spec says population required; but external missing is unlikely.
                    # We'll set to 0 and still store (safer)
                    population = 0

                capital = c.get("capital")
                region = c.get("region")
                flag_url = c.get("flag")

                # currencies could be None, empty, or array of objects. We should extract first currency code if present.
                currency_code = None
                currencies = c.get("currencies") or []
                if isinstance(currencies, list) and len(currencies) > 0:
                    first = currencies[0]
                    # some objects: {"code":"NGN", "name":"Nigerian naira", "symbol":"₦"}
                    currency_code = first.get("code")

                # exchange_rate matching (rates dict where keys are currency codes)
                exchange_rate = None
                if currency_code:
                    # rates keys are e.g., "NGN": 1600.23
                    # Some rates might be strings or numbers; safe conversion.
                    r = rates.get(currency_code)
                    if r is not None:
                        try:
                            exchange_rate = float(r)
                        except Exception:
                            exchange_rate = None

                # estimated_gdp rules:
                estimated_gdp = None
                if not currency_code:
                    # currencies empty => estimated_gdp = 0
                    estimated_gdp = 0.0
                else:
                    if exchange_rate is None:
                        estimated_gdp = None
                    else:
                        multiplier = random.randint(1000, 2000)
                        # avoid division by zero
                        if exchange_rate == 0:
                            estimated_gdp = None
                        else:
                            estimated_gdp = (population * multiplier) / exchange_rate

                # Case-insensitive match by name
                # Try to find existing
                existing = session.execute(
                    select(Country).where(func.lower(Country.name) == name.lower())
                ).scalar_one_or_none()

                if existing:
                    # update fields
                    existing.capital = capital
                    existing.region = region
                    existing.population = population
                    existing.currency_code = currency_code
                    existing.exchange_rate = exchange_rate
                    existing.estimated_gdp = estimated_gdp
                    existing.flag_url = flag_url
                    existing.last_refreshed_at = utcnow()
                else:
                    new = Country(
                        name=name,
                        capital=capital,
                        region=region,
                        population=population,
                        currency_code=currency_code,
                        exchange_rate=exchange_rate,
                        estimated_gdp=estimated_gdp,
                        flag_url=flag_url,
                        last_refreshed_at=utcnow()
                    )
                    session.add(new)
                processed += 1

            # Update Metadata last_refreshed_at
            ts = utcnow().isoformat()
            meta = session.get(Metadata, "last_refreshed_at")
            if meta:
                meta.value = ts
            else:
                session.add(Metadata(key="last_refreshed_at", value=ts))

        # End transaction successfully: now generate image using DB data.
        # Query total and top 5
        try:
            with SessionLocal() as s2:
                total = s2.execute(select(func.count(Country.id))).scalar_one()
                top = s2.execute(
                    select(Country.name, Country.estimated_gdp).order_by(Country.estimated_gdp.desc().nullslast()).limit(5)
                ).all()
                top_list = [{"name": row[0], "estimated_gdp": (row[1] if row[1] is not None else None)} for row in top]
                last_ts = ts
                os.makedirs(CACHE_DIR, exist_ok=True)
                image_path = generate_summary_image(CACHE_DIR, total, top_list, last_ts)
        except Exception as e:
            # Image generation should not make refresh fail; just log and continue
            print("Image generation error:", e)
            image_path = None

        return jsonify({
            "message": "Refresh successful",
            "processed_countries": processed,
            "summary_image": image_path if image_path else None,
            "last_refreshed_at": ts
        }), 200

    except Exception as e:
        # rollback already done by session context manager, but be explicit
        session.rollback()
        traceback.print_exc()
        return json_error("Internal server error", status=500)
    finally:
        session.close()

@app.route("/countries", methods=["GET"])
def get_countries():
    region = request.args.get("region")
    currency = request.args.get("currency")
    sort = request.args.get("sort")  # 'gdp_desc' or 'gdp_asc'

    session = SessionLocal()
    try:
        q = select(Country)
        if region:
            q = q.where(func.lower(Country.region) == region.lower())
        if currency:
            q = q.where(func.lower(Country.currency_code) == currency.lower())

        if sort == "gdp_desc":
            q = q.order_by(Country.estimated_gdp.desc().nullslast())
        elif sort == "gdp_asc":
            q = q.order_by(Country.estimated_gdp.asc().nullsfirst())
        else:
            q = q.order_by(Country.name.asc())

        rows = session.execute(q).scalars().all()

        def to_dict(c):
            return {
                "id": c.id,
                "name": c.name,
                "capital": c.capital,
                "region": c.region,
                "population": c.population,
                "currency_code": c.currency_code,
                "exchange_rate": c.exchange_rate,
                "estimated_gdp": c.estimated_gdp,
                "flag_url": c.flag_url,
                "last_refreshed_at": c.last_refreshed_at.isoformat() if c.last_refreshed_at else None
            }

        return jsonify([to_dict(r) for r in rows]), 200
    except Exception as e:
        traceback.print_exc()
        return json_error("Internal server error", status=500)
    finally:
        session.close()

@app.route("/countries/<string:name>", methods=["GET"])
def get_country(name):
    session = SessionLocal()
    try:
        row = session.execute(select(Country).where(func.lower(Country.name) == name.lower())).scalar_one_or_none()
        if not row:
            return json_error("Country not found", status=404)
        c = row
        data = {
            "id": c.id,
            "name": c.name,
            "capital": c.capital,
            "region": c.region,
            "population": c.population,
            "currency_code": c.currency_code,
            "exchange_rate": c.exchange_rate,
            "estimated_gdp": c.estimated_gdp,
            "flag_url": c.flag_url,
            "last_refreshed_at": c.last_refreshed_at.isoformat() if c.last_refreshed_at else None
        }
        return jsonify(data), 200
    except Exception as e:
        traceback.print_exc()
        return json_error("Internal server error", status=500)
    finally:
        session.close()

@app.route("/countries/<string:name>", methods=["DELETE"])
def delete_country(name):
    session = SessionLocal()
    try:
        row = session.execute(select(Country).where(func.lower(Country.name) == name.lower())).scalar_one_or_none()
        if not row:
            return json_error("Country not found", status=404)
        session.delete(row)
        session.commit()
        return jsonify({"message": f"Country '{name}' deleted"}), 200
    except Exception as e:
        session.rollback()
        traceback.print_exc()
        return json_error("Internal server error", status=500)
    finally:
        session.close()

@app.route("/status", methods=["GET"])
def status():
    session = SessionLocal()
    try:
        total = session.execute(select(func.count(Country.id))).scalar_one()
        meta = session.get(Metadata, "last_refreshed_at")
        last = meta.value if meta else None
        return jsonify({"total_countries": total, "last_refreshed_at": last}), 200
    except Exception as e:
        traceback.print_exc()
        return json_error("Internal server error", status=500)
    finally:
        session.close()

@app.route("/countries/image", methods=["GET"])
def serve_image():
    path = os.path.join(CACHE_DIR, "summary.png")
    if not os.path.exists(path):
        return json_error("Summary image not found", status=404)
    return send_file(path, mimetype="image/png")

# Consistent error handlers
@app.errorhandler(404)
def not_found(e):
    return json_error("Not found", status=404)

@app.errorhandler(500)
def internal(e):
    return json_error("Internal server error", status=500)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
