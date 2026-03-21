import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

DB_PATH = Path("portfolio.db")

st.set_page_config(
    page_title="Aplikacja wspomagająca inwestycje giełdowe",
    page_icon="📈",
    layout="wide",
)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


@st.cache_data(ttl=1800)
def load_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    data = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if data.empty:
        return pd.DataFrame()
    data = data.reset_index()
    data["Date"] = pd.to_datetime(data["Date"]).dt.tz_localize(None)
    return data


@st.cache_data(ttl=600)
def load_company_name(ticker: str) -> str:
    try:
        info = yf.Ticker(ticker).fast_info
        return info.get("shortName") or ticker
    except Exception:
        return ticker


def read_transactions() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM transactions ORDER BY trade_date, id", conn)
    conn.close()
    if df.empty:
        return pd.DataFrame(columns=["id", "trade_date", "ticker", "transaction_type", "quantity", "price", "fee"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ticker"] = df["ticker"].str.upper().str.strip()
    return df


def insert_transaction(trade_date, ticker, transaction_type, quantity, price, fee):
    conn = get_connection()
    conn.execute(
        "INSERT INTO transactions (trade_date, ticker, transaction_type, quantity, price, fee) VALUES (?, ?, ?, ?, ?, ?)",
        (str(trade_date), ticker.upper().strip(), transaction_type, float(quantity), float(price), float(fee)),
    )
    conn.commit()
    conn.close()



def delete_transaction(row_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM transactions WHERE id = ?", (int(row_id),))
    conn.commit()
    conn.close()



def import_csv(df: pd.DataFrame):
    required = {"trade_date", "ticker", "transaction_type", "quantity", "price", "fee"}
    if not required.issubset(df.columns):
        raise ValueError(f"CSV musi zawierać kolumny: {', '.join(sorted(required))}")

    clean = df.copy()
    clean["trade_date"] = pd.to_datetime(clean["trade_date"]).dt.date
    clean["ticker"] = clean["ticker"].astype(str).str.upper().str.strip()
    clean["transaction_type"] = clean["transaction_type"].astype(str).str.lower().str.strip()

    for _, row in clean.iterrows():
        insert_transaction(
            row["trade_date"],
            row["ticker"],
            row["transaction_type"],
            row["quantity"],
            row["price"],
            row.get("fee", 0),
        )



def calculate_portfolio(transactions: pd.DataFrame) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame()

    rows = []
    for ticker, group in transactions.groupby("ticker"):
        buys = group[group["transaction_type"] == "buy"].copy()
        sells = group[group["transaction_type"] == "sell"].copy()

        bought_qty = buys["quantity"].sum()
        sold_qty = sells["quantity"].sum()
        current_qty = bought_qty - sold_qty

        gross_buy_value = (buys["quantity"] * buys["price"]).sum()
        buy_fees = buys["fee"].sum()
        gross_sell_value = (sells["quantity"] * sells["price"]).sum()
        sell_fees = sells["fee"].sum()

        avg_buy_price = (gross_buy_value + buy_fees) / bought_qty if bought_qty > 0 else 0
        realized_pl = gross_sell_value - sell_fees - (avg_buy_price * sold_qty)

        market_price = np.nan
        market_value = np.nan
        unrealized_pl = np.nan
        total_pl = realized_pl

        history = load_price_history(ticker, period="5d")
        if not history.empty:
            market_price = float(history["Close"].iloc[-1])
            market_value = current_qty * market_price
            unrealized_pl = market_value - (current_qty * avg_buy_price)
            total_pl = realized_pl + unrealized_pl

        rows.append(
            {
                "Ticker": ticker,
                "Kupiono [szt.]": bought_qty,
                "Sprzedano [szt.]": sold_qty,
                "Aktualnie [szt.]": current_qty,
                "Średnia cena zakupu": avg_buy_price,
                "Koszt zakupu": gross_buy_value + buy_fees,
                "Przychód ze sprzedaży": gross_sell_value - sell_fees,
                "Zrealizowany P/L": realized_pl,
                "Aktualny kurs": market_price,
                "Wartość rynkowa": market_value,
                "Niezrealizowany P/L": unrealized_pl,
                "Łączny P/L": total_pl,
            }
        )

    result = pd.DataFrame(rows).sort_values("Łączny P/L", ascending=False)
    return result



def portfolio_chart(history_df: pd.DataFrame, ticker: str):
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=history_df["Date"],
            y=history_df["Close"],
            mode="lines",
            name="Cena zamknięcia",
        )
    )
    fig.update_layout(
        title=f"Notowania {ticker}",
        xaxis_title="Data",
        yaxis_title="Cena",
        height=450,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig



def comparison_chart(tickers: list[str], period: str):
    fig = go.Figure()
    for ticker in tickers:
        history = load_price_history(ticker, period=period)
        if history.empty:
            continue
        normalized = history["Close"] / history["Close"].iloc[0] * 100
        fig.add_trace(go.Scatter(x=history["Date"], y=normalized, mode="lines", name=ticker))
    fig.update_layout(
        title="Porównanie historyczne (start = 100)",
        xaxis_title="Data",
        yaxis_title="Indeks bazowy 100",
        height=500,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    return fig


st.title("📈 Aplikacja wspomagająca inwestycje giełdowe")
st.caption("")

with st.sidebar:
    st.header("Dodaj transakcję")
    with st.form("transaction_form", clear_on_submit=True):
        trade_date = st.date_input("Data", value=date.today())
        ticker = st.text_input("Ticker", value="AAPL")
        transaction_type = st.selectbox("Typ transakcji", ["buy", "sell"])
        quantity = st.number_input("Liczba akcji", min_value=0.0, value=1.0, step=1.0)
        price = st.number_input("Cena za akcję", min_value=0.0, value=100.0, step=0.01)
        fee = st.number_input("Prowizja", min_value=0.0, value=0.0, step=0.01)
        submitted = st.form_submit_button("Zapisz")
        if submitted:
            if not ticker.strip():
                st.error("Podaj ticker.")
            else:
                insert_transaction(trade_date, ticker, transaction_type, quantity, price, fee)
                st.success("Transakcja została dodana.")

    st.divider()
    st.subheader("Import CSV")
    uploaded = st.file_uploader("Wgraj plik CSV", type=["csv"])
    if uploaded is not None:
        try:
            import_csv(pd.read_csv(uploaded))
            st.success("Dane zostały zaimportowane.")
        except Exception as exc:
            st.error(str(exc))

    st.divider()
    st.markdown(
        """
        **Format CSV:**
        - `trade_date`
        - `ticker`
        - `transaction_type` (`buy` / `sell`)
        - `quantity`
        - `price`
        - `fee`
        """
    )

transactions = read_transactions()
portfolio = calculate_portfolio(transactions)

if transactions.empty:
    st.info("Dodaj pierwszą transakcję z panelu po lewej albo zaimportuj CSV.")
else:
    total_cost = portfolio["Koszt zakupu"].sum() if not portfolio.empty else 0
    total_market_value = np.nansum(portfolio["Wartość rynkowa"]) if not portfolio.empty else 0
    total_pl = np.nansum(portfolio["Łączny P/L"]) if not portfolio.empty else 0
    best_ticker = portfolio.iloc[0]["Ticker"] if not portfolio.empty else "-"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Łączny koszt zakupu", f"{total_cost:,.2f}")
    c2.metric("Aktualna wartość", f"{total_market_value:,.2f}")
    c3.metric("Łączny P/L", f"{total_pl:,.2f}")
    c4.metric("Najbardziej opłacalna spółka", best_ticker)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Portfel",
        "Analiza spółki",
        "Porównanie spółek",
        "Historia transakcji",
    ])

    with tab1:
        st.subheader("Podsumowanie portfela")
        display_cols = [
            "Ticker", "Kupiono [szt.]", "Sprzedano [szt.]", "Aktualnie [szt.]",
            "Średnia cena zakupu", "Aktualny kurs", "Wartość rynkowa", "Łączny P/L"
        ]
        st.dataframe(portfolio[display_cols], use_container_width=True)

        st.subheader("Kalkulator sprzedaży")
        selected_ticker = st.selectbox("Wybierz ticker", portfolio["Ticker"].tolist())
        selected_row = portfolio[portfolio["Ticker"] == selected_ticker].iloc[0]
        target_price = st.number_input("Założona cena sprzedaży", min_value=0.0, value=float(selected_row["Aktualny kurs"]) if pd.notna(selected_row["Aktualny kurs"]) else 100.0, step=0.01)
        quantity_to_sell = st.number_input("Liczba akcji do sprzedaży", min_value=0.0, value=float(max(selected_row["Aktualnie [szt.]"], 0.0)), step=1.0)
        estimated_pl = (target_price - selected_row["Średnia cena zakupu"]) * quantity_to_sell
        st.metric("Szacowany zysk/strata", f"{estimated_pl:,.2f}")

    with tab2:
        st.subheader("Szczegółowa analiza spółki")
        all_tickers = sorted(transactions["ticker"].unique().tolist())
        ticker_for_analysis = st.selectbox("Spółka", all_tickers, key="analysis_ticker")
        period = st.selectbox("Zakres danych", ["1mo", "3mo", "6mo", "1y", "2y", "5y"], index=3)
        history = load_price_history(ticker_for_analysis, period=period)

        if history.empty:
            st.warning("Brak danych dla wybranego tickera.")
        else:
            st.plotly_chart(portfolio_chart(history, ticker_for_analysis), use_container_width=True)
            close = history["Close"]
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Średnia cena", f"{close.mean():,.2f}")
            s2.metric("Min", f"{close.min():,.2f}")
            s3.metric("Max", f"{close.max():,.2f}")
            s4.metric("Odchylenie standardowe", f"{close.std():,.2f}")

    with tab3:
        st.subheader("Porównanie historyczne akcji")
        compare_tickers = st.multiselect(
            "Wybierz tickery",
            sorted(transactions["ticker"].unique().tolist()),
            default=sorted(transactions["ticker"].unique().tolist())[:3],
        )
        compare_period = st.selectbox("Zakres porównania", ["1mo", "3mo", "6mo", "1y", "2y", "5y"], index=3, key="compare_period")
        if compare_tickers:
            st.plotly_chart(comparison_chart(compare_tickers, compare_period), use_container_width=True)
        else:
            st.info("Wybierz co najmniej jeden ticker.")

    with tab4:
        st.subheader("Wszystkie transakcje")
        st.dataframe(transactions, use_container_width=True)
        delete_id = st.number_input("Usuń rekord o ID", min_value=0, step=1)
        if st.button("Usuń wskazany rekord"):
            delete_transaction(delete_id)
            st.success("Rekord został usunięty. Odśwież widok jeśli trzeba.")

st.divider()
with st.expander("Jak uruchomić lokalnie i wrzucić na Streamlit Cloud"):
    st.code(
        """
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
        """.strip(),
        language="bash",
    )
    st.markdown(
        "1. Wrzuć pliki `app.py` i `requirements.txt` do repozytorium GitHub.\n"
        "2. Wejdź na Streamlit Cloud.\n"
        "3. Wskaż repozytorium i plik startowy `app.py`.\n"
        "4. Kliknij **Deploy**."
    )
