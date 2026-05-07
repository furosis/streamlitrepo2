import base64
import hashlib
import os
import sqlite3
from datetime import date, datetime
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


# ==========================================================
# BAZA DANYCH
# ==========================================================

def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL
        )
        """
    )

    # Migracja na wypadek, gdyby wcześniej istniała tabela bez user_id
    columns = pd.read_sql_query("PRAGMA table_info(transactions)", conn)
    if "user_id" not in columns["name"].tolist():
        conn.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER DEFAULT 1")

    conn.commit()
    conn.close()


# ==========================================================
# HASŁA I UŻYTKOWNICY
# ==========================================================

def hash_password(password: str) -> str:
    salt = os.urandom(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        120_000,
    )

    return (
        base64.b64encode(salt).decode("utf-8")
        + ":"
        + base64.b64encode(password_hash).decode("utf-8")
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_b64, hash_b64 = stored_hash.split(":")
        salt = base64.b64decode(salt_b64)
        correct_hash = base64.b64decode(hash_b64)

        test_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            120_000,
        )

        return test_hash == correct_hash

    except Exception:
        return False


def register_user(username: str, password: str):
    username = username.strip().lower()

    if len(username) < 3:
        raise ValueError("Nazwa użytkownika musi mieć co najmniej 3 znaki.")

    if len(password) < 5:
        raise ValueError("Hasło musi mieć co najmniej 5 znaków.")

    conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (
                username,
                hash_password(password),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()

    except sqlite3.IntegrityError:
        raise ValueError("Taki użytkownik już istnieje.")

    finally:
        conn.close()


def login_user(username: str, password: str):
    username = username.strip().lower()

    conn = get_connection()

    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    conn.close()

    if row is None:
        return None

    user_id, username_db, password_hash = row

    if verify_password(password, password_hash):
        return {
            "id": user_id,
            "username": username_db,
        }

    return None


def show_login_screen():
    st.title("📈 Aplikacja wspomagająca inwestycje giełdowe")
    st.caption(
        "System analityczny do obsługi portfela inwestycyjnego, transakcji, raportów i wykresów."
    )

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Logowanie")

        with st.form("login_form"):
            username = st.text_input("Nazwa użytkownika")
            password = st.text_input("Hasło", type="password")

            submitted = st.form_submit_button("Zaloguj")

            if submitted:
                user = login_user(username, password)

                if user:
                    st.session_state["user_id"] = user["id"]
                    st.session_state["username"] = user["username"]
                    st.success("Zalogowano poprawnie.")
                    st.rerun()
                else:
                    st.error("Nieprawidłowy login lub hasło.")

    with right:
        st.subheader("Rejestracja")

        with st.form("register_form"):
            new_username = st.text_input("Nowa nazwa użytkownika")
            new_password = st.text_input("Nowe hasło", type="password")

            submitted = st.form_submit_button("Utwórz konto")

            if submitted:
                try:
                    register_user(new_username, new_password)
                    st.success("Konto zostało utworzone. Możesz się teraz zalogować.")
                except Exception as exc:
                    st.error(str(exc))

    st.divider()

    st.info(
        "Hasła są zapisywane w bazie SQLite jako hash PBKDF2-HMAC-SHA256 z losową solą. "
        "Aplikacja nie przechowuje haseł w postaci jawnej."
    )


# ==========================================================
# DANE RYNKOWE — YFINANCE
# ==========================================================

@st.cache_data(ttl=1800)
def load_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """
    Pobiera dane historyczne z Yahoo Finance za pomocą biblioteki yfinance.
    Dane są wykorzystywane do wykresów, porównań oraz wyceny portfela.
    """

    try:
        ticker = ticker.upper().strip()

        data = yf.Ticker(ticker).history(
            period=period,
            auto_adjust=True,
        )

        if data.empty:
            return pd.DataFrame()

        data = data.reset_index()

        if "Date" in data.columns:
            data["Date"] = pd.to_datetime(data["Date"]).dt.tz_localize(None)
        elif "Datetime" in data.columns:
            data["Date"] = pd.to_datetime(data["Datetime"]).dt.tz_localize(None)

        return data

    except Exception:
        return pd.DataFrame()


# ==========================================================
# TRANSAKCJE
# ==========================================================

def normalize_transaction_type(value: str) -> str:
    value = str(value).lower().strip()

    buy_values = ["buy", "kupno", "zakup", "k"]
    sell_values = ["sell", "sprzedaż", "sprzedaz", "s"]

    if value in buy_values:
        return "buy"

    if value in sell_values:
        return "sell"

    raise ValueError("Typ transakcji musi być: buy/sell albo kupno/sprzedaż.")


def insert_transaction(user_id, trade_date, ticker, transaction_type, quantity, price, fee):
    ticker = str(ticker).upper().strip()
    transaction_type = normalize_transaction_type(transaction_type)

    if not ticker:
        raise ValueError("Ticker nie może być pusty.")

    if float(quantity) <= 0:
        raise ValueError("Liczba akcji musi być większa od zera.")

    if float(price) <= 0:
        raise ValueError("Cena musi być większa od zera.")

    if float(fee) < 0:
        raise ValueError("Prowizja nie może być ujemna.")

    conn = get_connection()

    conn.execute(
        """
        INSERT INTO transactions
        (user_id, trade_date, ticker, transaction_type, quantity, price, fee)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            str(trade_date),
            ticker,
            transaction_type,
            float(quantity),
            float(price),
            float(fee),
        ),
    )

    conn.commit()
    conn.close()


def read_transactions(user_id: int) -> pd.DataFrame:
    conn = get_connection()

    df = pd.read_sql_query(
        """
        SELECT id, trade_date, ticker, transaction_type, quantity, price, fee
        FROM transactions
        WHERE user_id = ?
        ORDER BY trade_date, id
        """,
        conn,
        params=(int(user_id),),
    )

    conn.close()

    if df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "trade_date",
                "ticker",
                "transaction_type",
                "quantity",
                "price",
                "fee",
            ]
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()

    return df


def delete_transaction(user_id: int, row_id: int):
    conn = get_connection()

    conn.execute(
        "DELETE FROM transactions WHERE id = ? AND user_id = ?",
        (
            int(row_id),
            int(user_id),
        ),
    )

    conn.commit()
    conn.close()


def delete_all_transactions(user_id: int):
    conn = get_connection()

    conn.execute(
        "DELETE FROM transactions WHERE user_id = ?",
        (int(user_id),),
    )

    conn.commit()
    conn.close()


def import_csv_transactions(user_id: int, df: pd.DataFrame):
    required = {
        "trade_date",
        "ticker",
        "transaction_type",
        "quantity",
        "price",
        "fee",
    }

    if not required.issubset(df.columns):
        raise ValueError(f"CSV musi zawierać kolumny: {', '.join(sorted(required))}")

    clean = df.copy()

    clean["trade_date"] = pd.to_datetime(clean["trade_date"]).dt.date
    clean["ticker"] = clean["ticker"].astype(str).str.upper().str.strip()
    clean["transaction_type"] = clean["transaction_type"].apply(normalize_transaction_type)
    clean["quantity"] = pd.to_numeric(clean["quantity"])
    clean["price"] = pd.to_numeric(clean["price"])
    clean["fee"] = pd.to_numeric(clean["fee"]).fillna(0)

    added = 0

    for _, row in clean.iterrows():
        insert_transaction(
            user_id=user_id,
            trade_date=row["trade_date"],
            ticker=row["ticker"],
            transaction_type=row["transaction_type"],
            quantity=row["quantity"],
            price=row["price"],
            fee=row["fee"],
        )

        added += 1

    return added


# ==========================================================
# WIĘKSZA PRZYKŁADOWA BAZA TRANSAKCJI
# ==========================================================

DEMO_TRANSACTIONS = [
    ("2025-01-08", "AAPL", "buy", 10, 182.40, 1.99),
    ("2025-01-14", "MSFT", "buy", 6, 404.20, 1.99),
    ("2025-01-22", "NVDA", "buy", 12, 118.60, 1.99),
    ("2025-02-03", "GOOGL", "buy", 8, 144.30, 1.99),
    ("2025-02-10", "AMZN", "buy", 7, 168.50, 1.99),
    ("2025-02-19", "META", "buy", 4, 486.10, 1.99),
    ("2025-03-04", "TSLA", "buy", 5, 201.80, 1.99),
    ("2025-03-11", "AAPL", "buy", 5, 175.20, 1.99),
    ("2025-03-18", "MSFT", "buy", 2, 389.70, 1.99),
    ("2025-03-27", "NVDA", "sell", 4, 126.40, 1.99),
    ("2025-04-02", "PKO.WA", "buy", 30, 59.20, 3.50),
    ("2025-04-09", "PKN.WA", "buy", 20, 66.40, 3.50),
    ("2025-04-17", "CDR.WA", "buy", 12, 118.30, 3.50),
    ("2025-05-06", "AAPL", "sell", 3, 194.10, 1.99),
    ("2025-05-13", "GOOGL", "buy", 4, 151.20, 1.99),
    ("2025-05-21", "AMZN", "sell", 2, 181.90, 1.99),
    ("2025-06-03", "META", "buy", 2, 502.30, 1.99),
    ("2025-06-17", "TSLA", "buy", 4, 184.60, 1.99),
    ("2025-07-01", "NVDA", "buy", 8, 132.70, 1.99),
    ("2025-07-15", "MSFT", "sell", 2, 425.50, 1.99),
    ("2025-08-05", "PKO.WA", "buy", 20, 61.10, 3.50),
    ("2025-08-19", "PKN.WA", "sell", 5, 70.20, 3.50),
    ("2025-09-02", "CDR.WA", "buy", 6, 126.80, 3.50),
    ("2025-09-16", "AAPL", "buy", 4, 203.40, 1.99),
    ("2025-10-01", "GOOGL", "sell", 3, 164.20, 1.99),
    ("2025-10-15", "AMZN", "buy", 5, 187.30, 1.99),
    ("2025-11-03", "META", "sell", 1, 548.60, 1.99),
    ("2025-11-18", "TSLA", "sell", 3, 231.20, 1.99),
    ("2025-12-04", "NVDA", "buy", 6, 141.90, 1.99),
    ("2025-12-17", "MSFT", "buy", 3, 438.40, 1.99),
    ("2026-01-09", "PKO.WA", "sell", 15, 66.80, 3.50),
    ("2026-01-21", "PKN.WA", "buy", 10, 73.10, 3.50),
    ("2026-02-04", "CDR.WA", "sell", 5, 139.40, 3.50),
    ("2026-02-18", "AAPL", "buy", 3, 211.60, 1.99),
    ("2026-03-05", "NVDA", "sell", 5, 155.20, 1.99),
    ("2026-03-19", "AMZN", "sell", 4, 198.70, 1.99),
    ("2026-04-02", "GOOGL", "buy", 5, 172.40, 1.99),
    ("2026-04-16", "META", "buy", 2, 566.30, 1.99),
]


def load_demo_transactions(user_id: int):
    for item in DEMO_TRANSACTIONS:
        insert_transaction(
            user_id=user_id,
            trade_date=item[0],
            ticker=item[1],
            transaction_type=item[2],
            quantity=item[3],
            price=item[4],
            fee=item[5],
        )


# ==========================================================
# OBLICZENIA PORTFELA
# ==========================================================

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

        history = load_price_history(ticker, period="5d")

        if not history.empty and "Close" in history.columns:
            market_price = float(history["Close"].iloc[-1])
            market_value = current_qty * market_price
            unrealized_pl = market_value - (current_qty * avg_buy_price)
            total_pl = realized_pl + unrealized_pl

            profit_percent = (
                total_pl / (gross_buy_value + buy_fees) * 100
                if gross_buy_value > 0
                else 0
            )
        else:
            market_price = np.nan
            market_value = np.nan
            unrealized_pl = np.nan
            total_pl = realized_pl
            profit_percent = np.nan

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
                "Stopa zwrotu [%]": profit_percent,
            }
        )

    return pd.DataFrame(rows).sort_values("Łączny P/L", ascending=False)


# ==========================================================
# WYKRESY
# ==========================================================

def price_chart(history_df: pd.DataFrame, ticker: str):
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

        if history.empty or "Close" not in history.columns:
            continue

        normalized = history["Close"] / history["Close"].iloc[0] * 100

        fig.add_trace(
            go.Scatter(
                x=history["Date"],
                y=normalized,
                mode="lines",
                name=ticker,
            )
        )

    fig.update_layout(
        title="Porównanie historyczne spółek: start = 100",
        xaxis_title="Data",
        yaxis_title="Indeks bazowy 100",
        height=500,
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


def allocation_chart(portfolio: pd.DataFrame):
    active = portfolio[portfolio["Aktualnie [szt.]"] > 0].copy()

    if active.empty:
        return None

    fig = go.Figure(
        data=[
            go.Pie(
                labels=active["Ticker"],
                values=active["Wartość rynkowa"].fillna(0),
                hole=0.45,
            )
        ]
    )

    fig.update_layout(
        title="Struktura portfela według wartości rynkowej",
        height=430,
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


def profit_chart(portfolio: pd.DataFrame):
    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=portfolio["Ticker"],
            y=portfolio["Łączny P/L"],
            name="Łączny P/L",
        )
    )

    fig.update_layout(
        title="Zysk / strata według spółek",
        xaxis_title="Ticker",
        yaxis_title="Zysk / strata",
        height=430,
        margin=dict(l=20, r=20, t=50, b=20),
    )

    return fig


# ==========================================================
# RAPORTY
# ==========================================================

def build_report_text(username: str, portfolio: pd.DataFrame, transactions: pd.DataFrame) -> str:
    if portfolio.empty:
        return "Brak danych do wygenerowania raportu."

    total_cost = portfolio["Koszt zakupu"].sum()
    total_market_value = np.nansum(portfolio["Wartość rynkowa"])
    total_realized = portfolio["Zrealizowany P/L"].sum()
    total_unrealized = np.nansum(portfolio["Niezrealizowany P/L"])
    total_pl = np.nansum(portfolio["Łączny P/L"])

    best_row = portfolio.iloc[0]
    worst_row = portfolio.iloc[-1]

    report = f"""
RAPORT ANALIZY PORTFELA INWESTYCYJNEGO

Użytkownik: {username}
Data wygenerowania raportu: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Źródło danych rynkowych: yfinance / Yahoo Finance

1. PODSUMOWANIE OGÓLNE

Łączny koszt zakupu: {total_cost:,.2f}
Aktualna wartość rynkowa: {total_market_value:,.2f}
Zrealizowany zysk/strata: {total_realized:,.2f}
Niezrealizowany zysk/strata: {total_unrealized:,.2f}
Łączny wynik portfela: {total_pl:,.2f}

2. NAJBARDZIEJ OPŁACALNA SPÓŁKA

Ticker: {best_row["Ticker"]}
Łączny P/L: {best_row["Łączny P/L"]:,.2f}
Stopa zwrotu: {best_row["Stopa zwrotu [%]"]:,.2f}%

3. NAJSŁABSZA SPÓŁKA W PORTFELU

Ticker: {worst_row["Ticker"]}
Łączny P/L: {worst_row["Łączny P/L"]:,.2f}
Stopa zwrotu: {worst_row["Stopa zwrotu [%]"]:,.2f}%

4. LICZBA TRANSAKCJI

Liczba wszystkich transakcji: {len(transactions)}
Liczba instrumentów w portfelu: {portfolio["Ticker"].nunique()}

5. UWAGA

Niniejszy raport ma charakter informacyjny i edukacyjny.
Aplikacja nie stanowi systemu doradztwa inwestycyjnego ani rekomendacji kupna lub sprzedaży instrumentów finansowych.
"""

    return report.strip()


def save_report(user_id: int, title: str, content: str):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO reports (user_id, created_at, title, content)
        VALUES (?, ?, ?, ?)
        """,
        (
            int(user_id),
            datetime.now().isoformat(timespec="seconds"),
            title,
            content,
        ),
    )

    conn.commit()
    conn.close()


def read_reports(user_id: int) -> pd.DataFrame:
    conn = get_connection()

    df = pd.read_sql_query(
        """
        SELECT id, created_at, title, content
        FROM reports
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        conn,
        params=(int(user_id),),
    )

    conn.close()

    return df


# ==========================================================
# START APLIKACJI
# ==========================================================

init_db()

if "user_id" not in st.session_state:
    show_login_screen()
    st.stop()

user_id = st.session_state["user_id"]
username = st.session_state["username"]

st.title("📈 Aplikacja wspomagająca inwestycje giełdowe")
st.caption(f"Zalogowany użytkownik: **{username}**")

with st.sidebar:
    st.header("Panel użytkownika")

    st.info(
        "Aplikacja pobiera dane rynkowe online za pomocą biblioteki yfinance. "
        "Dane z innych źródeł, np. analizy.pl, mogą być wprowadzane przez plik CSV."
    )

    if st.button("Wyloguj"):
        st.session_state.clear()
        st.rerun()

    st.divider()

    st.header("Dodaj transakcję")

    with st.form("transaction_form", clear_on_submit=True):
        trade_date = st.date_input("Data transakcji", value=date.today())
        ticker = st.text_input("Ticker", value="AAPL")
        transaction_type = st.selectbox("Typ transakcji", ["buy", "sell"])
        quantity = st.number_input("Liczba akcji", min_value=0.0, value=1.0, step=1.0)
        price = st.number_input("Cena za akcję", min_value=0.0, value=100.0, step=0.01)
        fee = st.number_input("Prowizja", min_value=0.0, value=0.0, step=0.01)

        submitted = st.form_submit_button("Zapisz transakcję")

        if submitted:
            try:
                insert_transaction(
                    user_id=user_id,
                    trade_date=trade_date,
                    ticker=ticker,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    price=price,
                    fee=fee,
                )

                st.success("Transakcja została dodana.")
                st.rerun()

            except Exception as exc:
                st.error(str(exc))

    st.divider()

    st.header("Import CSV")

    uploaded = st.file_uploader("Wgraj plik CSV z transakcjami", type=["csv"])

    if uploaded is not None:
        if st.button("Importuj CSV"):
            try:
                csv_df = pd.read_csv(uploaded)
                count = import_csv_transactions(user_id, csv_df)

                st.success(f"Zaimportowano transakcje: {count}")
                st.rerun()

            except Exception as exc:
                st.error(str(exc))

    st.markdown(
        """
        **Wymagany format CSV:**

        `trade_date,ticker,transaction_type,quantity,price,fee`
        """
    )

    st.divider()

    st.header("Dane przykładowe")

    if st.button("Załaduj większą przykładową bazę"):
        try:
            load_demo_transactions(user_id)

            st.success("Przykładowa baza została załadowana.")
            st.rerun()

        except Exception as exc:
            st.error(str(exc))

    if st.button("Usuń wszystkie moje transakcje"):
        delete_all_transactions(user_id)

        st.warning("Usunięto wszystkie transakcje użytkownika.")
        st.rerun()


transactions = read_transactions(user_id)
portfolio = calculate_portfolio(transactions)


if transactions.empty:
    st.info(
        "Dodaj pierwszą transakcję z panelu po lewej, zaimportuj CSV albo załaduj większą przykładową bazę."
    )
    st.stop()


# ==========================================================
# METRYKI GÓRNE
# ==========================================================

total_cost = portfolio["Koszt zakupu"].sum() if not portfolio.empty else 0
total_market_value = np.nansum(portfolio["Wartość rynkowa"]) if not portfolio.empty else 0
total_pl = np.nansum(portfolio["Łączny P/L"]) if not portfolio.empty else 0
best_ticker = portfolio.iloc[0]["Ticker"] if not portfolio.empty else "-"

c1, c2, c3, c4 = st.columns(4)

c1.metric("Łączny koszt zakupu", f"{total_cost:,.2f}")
c2.metric("Aktualna wartość", f"{total_market_value:,.2f}")
c3.metric("Łączny P/L", f"{total_pl:,.2f}")
c4.metric("Najbardziej opłacalna spółka", best_ticker)


# ==========================================================
# ZAKŁADKI
# ==========================================================

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "Portfel",
        "Analiza spółki",
        "Porównanie spółek",
        "Historia transakcji",
        "Raporty",
        "Eksport i informacje",
    ]
)


with tab1:
    st.subheader("Podsumowanie portfela")

    display_cols = [
        "Ticker",
        "Kupiono [szt.]",
        "Sprzedano [szt.]",
        "Aktualnie [szt.]",
        "Średnia cena zakupu",
        "Aktualny kurs",
        "Wartość rynkowa",
        "Łączny P/L",
        "Stopa zwrotu [%]",
    ]

    st.dataframe(portfolio[display_cols], use_container_width=True)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.plotly_chart(profit_chart(portfolio), use_container_width=True)

    with chart_col2:
        fig = allocation_chart(portfolio)

        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Brak aktywnych pozycji do pokazania struktury portfela.")

    st.subheader("Ranking opłacalności")

    ranking = portfolio[
        [
            "Ticker",
            "Łączny P/L",
            "Stopa zwrotu [%]",
            "Wartość rynkowa",
        ]
    ].copy()

    st.dataframe(ranking, use_container_width=True)

    st.subheader("Kalkulator sprzedaży")

    selected_ticker = st.selectbox(
        "Wybierz ticker",
        portfolio["Ticker"].tolist(),
    )

    selected_row = portfolio[portfolio["Ticker"] == selected_ticker].iloc[0]

    target_price = st.number_input(
        "Założona cena sprzedaży",
        min_value=0.0,
        value=float(selected_row["Aktualny kurs"])
        if pd.notna(selected_row["Aktualny kurs"])
        else 100.0,
        step=0.01,
    )

    quantity_to_sell = st.number_input(
        "Liczba akcji do sprzedaży",
        min_value=0.0,
        value=float(max(selected_row["Aktualnie [szt.]"], 0.0)),
        step=1.0,
    )

    estimated_pl = (target_price - selected_row["Średnia cena zakupu"]) * quantity_to_sell

    st.metric("Szacowany zysk/strata", f"{estimated_pl:,.2f}")


with tab2:
    st.subheader("Szczegółowa analiza spółki")

    all_tickers = sorted(transactions["ticker"].unique().tolist())

    ticker_for_analysis = st.selectbox(
        "Spółka",
        all_tickers,
        key="analysis_ticker",
    )

    period = st.selectbox(
        "Zakres danych",
        ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
        index=3,
    )

    history = load_price_history(ticker_for_analysis, period=period)

    if history.empty:
        st.warning("Brak danych dla wybranego tickera. Sprawdź symbol spółki.")
    else:
        st.plotly_chart(
            price_chart(history, ticker_for_analysis),
            use_container_width=True,
        )

        close = history["Close"]

        s1, s2, s3, s4 = st.columns(4)

        s1.metric("Średnia cena", f"{close.mean():,.2f}")
        s2.metric("Minimum", f"{close.min():,.2f}")
        s3.metric("Maksimum", f"{close.max():,.2f}")
        s4.metric("Odchylenie standardowe", f"{close.std():,.2f}")

        change = close.iloc[-1] - close.iloc[0]
        change_percent = change / close.iloc[0] * 100

        st.metric(
            "Zmiana w analizowanym okresie",
            f"{change:,.2f}",
            f"{change_percent:,.2f}%",
        )


with tab3:
    st.subheader("Porównanie historyczne spółek")

    compare_tickers = st.multiselect(
        "Wybierz tickery",
        sorted(transactions["ticker"].unique().tolist()),
        default=sorted(transactions["ticker"].unique().tolist())[:3],
    )

    compare_period = st.selectbox(
        "Zakres porównania",
        ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
        index=3,
        key="compare_period",
    )

    if compare_tickers:
        st.plotly_chart(
            comparison_chart(compare_tickers, compare_period),
            use_container_width=True,
        )
    else:
        st.info("Wybierz co najmniej jeden ticker.")


with tab4:
    st.subheader("Historia transakcji")

    show_df = transactions.copy()

    show_df["transaction_type"] = show_df["transaction_type"].replace(
        {
            "buy": "kupno",
            "sell": "sprzedaż",
        }
    )

    st.dataframe(show_df, use_container_width=True)

    st.subheader("Usuwanie transakcji")

    transaction_ids = transactions["id"].tolist()

    selected_id = st.selectbox(
        "Wybierz ID transakcji do usunięcia",
        transaction_ids,
    )

    if st.button("Usuń wybraną transakcję"):
        delete_transaction(user_id, selected_id)

        st.success("Transakcja została usunięta.")
        st.rerun()


with tab5:
    st.subheader("Raporty")

    report_content = build_report_text(
        username=username,
        portfolio=portfolio,
        transactions=transactions,
    )

    st.text_area(
        "Podgląd raportu",
        report_content,
        height=420,
    )

    col_a, col_b = st.columns(2)

    with col_a:
        if st.button("Zapisz raport do historii"):
            title = f"Raport portfela - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            save_report(
                user_id,
                title,
                report_content,
            )

            st.success("Raport został zapisany.")
            st.rerun()

    with col_b:
        st.download_button(
            label="Pobierz raport TXT",
            data=report_content,
            file_name="raport_portfela.txt",
            mime="text/plain",
        )

    st.divider()

    st.subheader("Historia zapisanych raportów")

    reports = read_reports(user_id)

    if reports.empty:
        st.info("Nie zapisano jeszcze żadnego raportu.")
    else:
        for _, row in reports.iterrows():
            with st.expander(f'{row["created_at"]} — {row["title"]}'):
                st.text(row["content"])


with tab6:
    st.subheader("Eksport danych")

    st.download_button(
        label="Pobierz transakcje CSV",
        data=transactions.to_csv(index=False).encode("utf-8"),
        file_name="transactions_export.csv",
        mime="text/csv",
    )

    st.download_button(
        label="Pobierz podsumowanie portfela CSV",
        data=portfolio.to_csv(index=False).encode("utf-8"),
        file_name="portfolio_summary.csv",
        mime="text/csv",
    )

    st.divider()

    st.subheader("Informacje o projekcie")

    st.markdown(
        """
        Aplikacja została przygotowana jako prototyp systemu wspomagającego analizę inwestycji giełdowych.

        **Zakres zgodny z SRS:**
        - rejestracja i logowanie użytkownika,
        - bezpieczne przechowywanie haseł w postaci hasha,
        - zapis transakcji kupna i sprzedaży,
        - import danych z pliku CSV,
        - analiza średniej ceny zakupu,
        - obliczanie zysku lub straty,
        - ranking najbardziej opłacalnych walorów,
        - wykresy historyczne,
        - porównanie spółek,
        - generowanie raportów,
        - zapis historii raportów,
        - eksport danych.

        **Źródło danych:**
        Aplikacja korzysta z danych online pobieranych za pomocą biblioteki `yfinance`.
        Dane z serwisów zewnętrznych, takich jak analizy.pl, mogą być wprowadzane ręcznie przez plik CSV.

        **Zastrzeżenie:**
        System ma charakter informacyjny i edukacyjny.
        Nie jest narzędziem doradztwa inwestycyjnego.
        """
    )
