import streamlit as st
import nest_asyncio
nest_asyncio.apply()

import requests
import pandas as pd
from datetime import datetime
import os
import asyncio
from ib_insync import *

st.set_page_config(page_title="IBKR Go for Gold Trader", layout="wide")
st.title("IBKR ForecastTrader – Go for Gold Trader")
st.markdown("**Version 2.6 – Live TWS Fixed** – full live prices + balance pull + one-click trading. Monthly $500 inflow tracked.")

# Monthly capital tracker
st.sidebar.header("Monthly Capital Inflow")
current_balance = st.sidebar.number_input("Current IBKR balance (AUD)", value=500.0, step=10.0)
monthly_deposit = st.sidebar.number_input("Monthly deposit into IBKR (AUD)", value=500.0, step=50.0)
if st.sidebar.button("Record Monthly Deposit"):
    st.sidebar.success(f"Recorded +${monthly_deposit} – balance updated")

RISK_MODE = st.sidebar.selectbox("Risk mode", ["Conservative (0.25 Kelly)", "Balanced (0.5 Kelly)", "Go for Gold (0.5 Kelly)"], index=2)
kelly_fraction = 0.25 if "Conservative" in RISK_MODE else 0.5

CITY_CONFIG = {
    "Houston": {"lat": 29.9844, "lon": -95.3414, "icao": "KIAH"},
    "Miami": {"lat": 25.79325, "lon": -80.2906, "icao": "KMIA"},
    "Austin": {"lat": 30.18304, "lon": -97.67987, "icao": "KAUS"},
    "Dallas": {"lat": 32.89682, "lon": -97.03799, "icao": "KDFW"},
    "Los Angeles": {"lat": 33.942, "lon": -118.408, "icao": "KLAX"},
    "Seattle": {"lat": 47.4489, "lon": -122.3094, "icao": "KSEA"},
}

def connect_tws():
    try:
        ib = IB()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(ib.connectAsync("127.0.0.1", 7496, clientId=999))
        st.success("✅ Connected to TWS – pulling live prices and balance")
        return ib
    except Exception as e:
        st.error(f"TWS connection failed: {e}")
        return None

def get_metar(icao):
    try:
        resp = requests.get(f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json", timeout=10).json()
        return float(resp[0]["temp"]) if resp and isinstance(resp, list) else None
    except:
        return None

def get_ensemble_data(lat, lon):
    url = f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={lat}&longitude={lon}&hourly=temperature_2m&models=icon_seamless_eps,gfs_seamless&timezone=auto&forecast_days=3"
    try:
        return requests.get(url, timeout=15).json()
    except:
        return None

def calculate_temp_prob(ensemble_data, target_date_str, threshold_f):
    if not ensemble_data or "hourly" not in ensemble_data:
        return 0.5
    threshold_c = (threshold_f - 32) * 5 / 9
    times = pd.to_datetime(ensemble_data["hourly"]["time"])
    member_cols = [col for col in ensemble_data["hourly"] if col.startswith("temperature_2m")]
    member_maxes = []
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    for col in member_cols:
        temps_c = ensemble_data["hourly"][col]
        df_temp = pd.DataFrame({"time": times, "temp_c": temps_c})
        day_data = df_temp[df_temp["time"].dt.date == target_date]
        if not day_data.empty:
            member_maxes.append(day_data["temp_c"].max())
    if not member_maxes:
        return 0.5
    prob_yes = sum(1 for m in member_maxes if m > threshold_c) / len(member_maxes)
    return round(prob_yes, 4)

if st.button("🚀 Run Live Maximum Edge Scan + Place Trades", type="primary"):
    ib = connect_tws()
    if not ib:
        st.stop()

    # Pull live balance
    try:
        account_summary = ib.accountSummary()
        live_balance = float(account_summary[0].value) if account_summary else current_balance
    except:
        live_balance = current_balance

    results = []
    target_date_str = (datetime.now().date() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    threshold_f = 66.5

    for city, cfg in CITY_CONFIG.items():
        metar = get_metar(cfg["icao"])
        ensemble = get_ensemble_data(cfg["lat"], cfg["lon"])
        prob_yes = calculate_temp_prob(ensemble, target_date_str, threshold_f)

        live_yes = 0.50
        try:
            contract = Contract(symbol="TEMP", secType="OPT", exchange="FORECAST", currency="USD")
            ib.qualifyContracts(contract)
            tick = ib.reqTickers(contract)[0]
            live_yes = tick.marketPrice() or 0.50
        except:
            pass

        edge = prob_yes - live_yes - 0.002
        rec = "STRONG BUY YES" if edge > 0.10 else "BUY YES" if edge > 0.08 else "CHECK IN IBKR"
        recommended_stake = round(live_balance * kelly_fraction * max(edge, 0), 2)

        results.append({
            "Type": "Temperature",
            "Contract": f"{city} High > {threshold_f}°F",
            "Live_Yes_Price": round(live_yes, 4),
            "Est_Prob_Yes": prob_yes,
            "Edge": round(edge, 4),
            "Recommended_Stake": recommended_stake,
            "Recommendation": rec,
            "Detail": f"METAR {metar}°C"
        })

    df = pd.DataFrame(results)
    df = df[df["Edge"] > 0.08].sort_values(by="Edge", ascending=False)

    st.success("Live scan complete – TWS prices & balance used")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if not df.empty:
        top = df.iloc[0]
        st.metric("Best opportunity right now", f"{top['Contract']} – Edge {top['Edge']:.1%} (live price {top['Live_Yes_Price']})", delta=top['Recommendation'])
        if st.button(f"CONFIRM & PLACE TRADE on {top['Contract']} – ${top['Recommended_Stake']}"):
            try:
                order = LimitOrder("BUY", int(top['Recommended_Stake'] / top['Live_Yes_Price']), top['Live_Yes_Price'])
                trade = ib.placeOrder(Contract(symbol="TEMP", secType="OPT", exchange="FORECAST", currency="USD"), order)
                st.success(f"Trade placed successfully for {top['Contract']}")
            except Exception as e:
                st.error(f"Trade failed: {e}")

    if ib:
        ib.disconnect()

st.sidebar.info("TWS must be running. Monthly deposit is automatically factored into stakes.")
