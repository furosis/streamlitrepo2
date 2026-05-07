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

    # Migracja na wypadek starej bazy, w której tabela transactions nie miała user_id
    columns = pd.read_sql_query("PRAGMA table_info(transactions)", conn)

    if not columns.empty and "user_id" not in columns["name"].tolist():
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
        """
        SELECT id, username, password_hash
        FROM users
        WHERE username = ?
        """,
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


def delete_account(user_id: int):
    conn = get_connection()

    conn.execute(
        "DELETE FROM transactions WHERE user_id = ?",
        (int(user_id),),
    )

    conn.execute(
        "DELETE FROM reports WHERE user_id = ?",
        (int(user_id),),
    )

    conn.execute(
        "DELETE FROM users WHERE id = ?",
        (int(user_id),),
    )

    conn.commit()
    conn.close()


def show_login_screen():
    st.title("📈 Aplikacja wspomagająca inwestycje giełdowe")
    st.caption(
        "System do zapisu transakcji, analizy portfela, generowania raportów i prezentacji wykresów."
    )

    left, right = st.columns(2)

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
                    st.success("Konto zostało utworzone. Możesz się zalogować.")
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


@st.cache_data(ttl=300)
def get_latest_market_price(ticker: str):
    """
    Pobiera możliwie aktualną cenę instrumentu z yfinance.
    Najpierw próbuje pobrać last_price z fast_info,
    a jeśli to się nie uda, bierze ostatnią cenę Close z historii 5d.
    """

    ticker = ticker.upper().strip()

    if not ticker:
        return None, None, None

    try:
        fast_info = yf.Ticker(ticker).fast_info
        last_price = fast_info.get("last_price")

        if last_price is not None:
            return float(last_price), datetime.now(), "fast_info.last_price"

    except Exception:
        pass

    history = load_price_history(ticker, period="5d")

    if history.empty or "Close" not in history.columns:
        return None, None, None

    close_series = history["Close"].dropna()

    if close_series.empty:
        return None, None, None

    latest_price = float(close_series.iloc[-1])
    latest_date = history["Date"].iloc[-1]

    return latest_price, latest_date, "history.Close"


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
        """
        DELETE FROM transactions
        WHERE id = ? AND user_id = ?
        """,
        (
            int(row_id),
            int(user_id),
        ),
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
        raise ValueError(
            "CSV musi zawierać kolumny: "
            "trade_date, ticker, transaction_type, quantity, price, fee"
        )

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

        avg_buy_price = (
            (gross_buy_value + buy_fees) / bought_qty
            if bought_qty > 0
            else 0
        )

        realized_pl = gross_sell_value - sell_fees - (avg_buy_price * sold_qty)

        latest_price, latest_date, source = get_latest_market_price(ticker)

        if latest_price is not None:
            market_price = latest_price
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
            latest_date = None

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
                "Data kursu": latest_date,
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
            name="Cena",
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


# ==========================================================
# PANEL UŻYTKOWNIKA
# ==========================================================

with st.sidebar:
    st.header("Panel użytkownika")
    st.write("Zalogowano jako:")
    st.subheader(username)

    if st.button("Wyloguj"):
        st.session_state.clear()
        st.rerun()

    st.divider()

    st.subheader("Usuń konto")

    confirm_delete = st.checkbox(
        "Potwierdzam usunięcie konta i wszystkich moich danych"
    )

    if st.button("Usuń konto"):
        if confirm_delete:
            delete_account(user_id)
            st.session_state.clear()
            st.success("Konto zostało usunięte.")
            st.rerun()
        else:
            st.warning("Najpierw zaznacz potwierdzenie usunięcia konta.")


transactions = read_transactions(user_id)
portfolio = calculate_portfolio(transactions)


# ==========================================================
# METRYKI GÓRNE
# ==========================================================

if transactions.empty:
    st.info("Nie masz jeszcze żadnych transakcji. Przejdź do zakładki „Dodaj transakcję”.")
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


# ==========================================================
# ZAKŁADKI
# ==========================================================

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs(
    [
        "Portfel",
        "Dodaj transakcję",
        "Sprzedaż",
        "Analiza spółki",
        "Porównanie spółek",
        "Wykresy",
        "Historia transakcji",
        "Raporty",
        "Eksport i informacje",
    ]
)


# ==========================================================
# TAB 1 — PORTFEL
# ==========================================================

with tab1:
    st.subheader("Podsumowanie portfela")

    if transactions.empty:
        st.info("Brak danych do wyświetlenia. Dodaj pierwszą transakcję.")
    else:
        display_cols = [
            "Ticker",
            "Kupiono [szt.]",
            "Sprzedano [szt.]",
            "Aktualnie [szt.]",
            "Średnia cena zakupu",
            "Aktualny kurs",
            "Data kursu",
            "Wartość rynkowa",
            "Łączny P/L",
            "Stopa zwrotu [%]",
        ]

        st.dataframe(
            portfolio[display_cols],
            use_container_width=True,
        )

        st.subheader("Ranking opłacalności")

        ranking = portfolio[
            [
                "Ticker",
                "Łączny P/L",
                "Stopa zwrotu [%]",
                "Wartość rynkowa",
            ]
        ].copy()

        st.dataframe(
            ranking,
            use_container_width=True,
        )


# ==========================================================
# TAB 2 — DODAJ TRANSAKCJĘ
# ==========================================================

with tab2:
    st.subheader("Dodaj transakcję")

    st.markdown(
        """
        Wpisz ticker spółki, a aplikacja spróbuje automatycznie pobrać ostatni dostępny kurs z `yfinance`.
        Cenę możesz zostawić automatycznie pobraną albo poprawić ręcznie.
        """
    )

    if "transaction_ticker" not in st.session_state:
        st.session_state["transaction_ticker"] = "AAPL"

    if "transaction_price" not in st.session_state:
        st.session_state["transaction_price"] = 100.0

    if "last_price_ticker" not in st.session_state:
        st.session_state["last_price_ticker"] = ""

    ticker_input = st.text_input(
        "Ticker",
        key="transaction_ticker",
        help="Przykłady: AAPL, MSFT, NVDA, TSLA, PKO.WA, CDR.WA",
    )

    current_ticker = ticker_input.upper().strip()

    if current_ticker and current_ticker != st.session_state["last_price_ticker"]:
        latest_price, latest_date, source = get_latest_market_price(current_ticker)

        st.session_state["last_price_ticker"] = current_ticker

        if latest_price is not None:
            st.session_state["transaction_price"] = round(latest_price, 2)

            if latest_date is not None and hasattr(latest_date, "strftime"):
                date_text = latest_date.strftime("%Y-%m-%d")
            else:
                date_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            st.success(
                f"Pobrano kurs dla {current_ticker}: {latest_price:,.2f} "
                f"— źródło: {source}, data: {date_text}"
            )
        else:
            st.warning(
                "Nie udało się pobrać kursu dla tego tickera. "
                "Sprawdź symbol albo wpisz cenę ręcznie."
            )

    if st.button("Odśwież cenę z yfinance"):
        latest_price, latest_date, source = get_latest_market_price(current_ticker)

        if latest_price is not None:
            st.session_state["transaction_price"] = round(latest_price, 2)

            if latest_date is not None and hasattr(latest_date, "strftime"):
                date_text = latest_date.strftime("%Y-%m-%d")
            else:
                date_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            st.success(
                f"Zaktualizowano cenę: {latest_price:,.2f} "
                f"— źródło: {source}, data: {date_text}"
            )
        else:
            st.error("Nie udało się pobrać ceny dla podanego tickera.")

    with st.form("transaction_form", clear_on_submit=False):
        trade_date = st.date_input(
            "Data transakcji",
            value=date.today(),
        )

        transaction_type = st.selectbox(
            "Typ transakcji",
            ["buy", "sell"],
            format_func=lambda x: "Kupno" if x == "buy" else "Sprzedaż",
        )

        quantity = st.number_input(
            "Liczba akcji",
            min_value=0.0,
            value=1.0,
            step=1.0,
        )

        price = st.number_input(
            "Cena za akcję",
            min_value=0.0,
            step=0.01,
            key="transaction_price",
        )

        fee = st.number_input(
            "Prowizja",
            min_value=0.0,
            value=0.0,
            step=0.01,
        )

        submitted = st.form_submit_button("Zapisz transakcję")

        if submitted:
            try:
                insert_transaction(
                    user_id=user_id,
                    trade_date=trade_date,
                    ticker=current_ticker,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    price=price,
                    fee=fee,
                )

                st.success("Transakcja została dodana.")
                st.rerun()

            except Exception as exc:
                st.error(str(exc))


# ==========================================================
# TAB 3 — SPRZEDAŻ
# ==========================================================

with tab3:
    st.subheader("Sprzedaż akcji")

    if transactions.empty:
        st.info("Najpierw dodaj przynajmniej jedną transakcję kupna.")
    else:
        active_portfolio = portfolio[portfolio["Aktualnie [szt.]"] > 0].copy()

        if active_portfolio.empty:
            st.info("Nie masz aktualnie żadnych aktywnych pozycji do sprzedaży.")
        else:
            selected_ticker = st.selectbox(
                "Wybierz spółkę do sprzedaży",
                active_portfolio["Ticker"].tolist(),
                key="sell_selected_ticker",
            )

            selected_row = active_portfolio[
                active_portfolio["Ticker"] == selected_ticker
            ].iloc[0]

            current_quantity = float(selected_row["Aktualnie [szt.]"])
            avg_buy_price = float(selected_row["Średnia cena zakupu"])

            latest_price, latest_date, source = get_latest_market_price(selected_ticker)

            if latest_price is not None:
                current_price = float(latest_price)

                if latest_date is not None and hasattr(latest_date, "strftime"):
                    date_text = latest_date.strftime("%Y-%m-%d")
                else:
                    date_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                st.success(
                    f"Pobrano aktualny kurs dla {selected_ticker}: "
                    f"{current_price:,.2f} | źródło: {source} | data: {date_text}"
                )
            else:
                current_price = (
                    float(selected_row["Aktualny kurs"])
                    if pd.notna(selected_row["Aktualny kurs"])
                    else 100.0
                )

                st.warning(
                    "Nie udało się pobrać aktualnego kursu z yfinance. "
                    "Możesz wpisać cenę sprzedaży ręcznie."
                )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric("Ticker", selected_ticker)
            c2.metric("Posiadane akcje", f"{current_quantity:,.0f}")
            c3.metric("Średnia cena zakupu", f"{avg_buy_price:,.2f}")
            c4.metric("Aktualny kurs", f"{current_price:,.2f}")

            st.divider()

            sell_date = st.date_input(
                "Data sprzedaży",
                value=date.today(),
                key="sell_date",
            )

            sell_price = st.number_input(
                "Cena sprzedaży",
                min_value=0.0,
                value=round(current_price, 2),
                step=0.01,
                key=f"sell_price_{selected_ticker}",
            )

            quantity_to_sell = st.number_input(
                "Liczba akcji do sprzedaży",
                min_value=0.0,
                max_value=current_quantity,
                value=current_quantity,
                step=1.0,
                key=f"sell_quantity_{selected_ticker}",
            )

            sell_fee = st.number_input(
                "Prowizja sprzedaży",
                min_value=0.0,
                value=0.0,
                step=0.01,
                key=f"sell_fee_{selected_ticker}",
            )

            estimated_pl = (
                (sell_price - avg_buy_price) * quantity_to_sell
            ) - sell_fee

            st.metric(
                "Szacowany zysk/strata ze sprzedaży",
                f"{estimated_pl:,.2f}",
            )

            if st.button("Sprzedaj", key="sell_button"):
                try:
                    if quantity_to_sell <= 0:
                        st.error("Liczba akcji do sprzedaży musi być większa od zera.")
                    elif quantity_to_sell > current_quantity:
                        st.error("Nie możesz sprzedać więcej akcji niż posiadasz.")
                    else:
                        insert_transaction(
                            user_id=user_id,
                            trade_date=sell_date,
                            ticker=selected_ticker,
                            transaction_type="sell",
                            quantity=quantity_to_sell,
                            price=sell_price,
                            fee=sell_fee,
                        )

                        st.success(
                            f"Sprzedaż została zapisana: {selected_ticker}, "
                            f"{quantity_to_sell:,.0f} szt. po {sell_price:,.2f}"
                        )

                        st.rerun()

                except Exception as exc:
                    st.error(str(exc))


# ==========================================================
# TAB 4 — ANALIZA SPÓŁKI
# ==========================================================

with tab4:
    st.subheader("Szczegółowa analiza spółki")

    if transactions.empty:
        st.info("Najpierw dodaj przynajmniej jedną transakcję.")
    else:
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


# ==========================================================
# TAB 5 — PORÓWNANIE SPÓŁEK
# ==========================================================

with tab5:
    st.subheader("Porównanie historyczne spółek")

    if transactions.empty:
        st.info("Najpierw dodaj przynajmniej jedną transakcję.")
    else:
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


# ==========================================================
# TAB 6 — WYKRESY
# ==========================================================

with tab6:
    st.subheader("Wykresy portfela")

    if transactions.empty:
        st.info("Brak danych do wygenerowania wykresów.")
    else:
        col1, col2 = st.columns(2)

        with col1:
            st.plotly_chart(
                profit_chart(portfolio),
                use_container_width=True,
            )

        with col2:
            fig = allocation_chart(portfolio)

            if fig:
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                )
            else:
                st.info("Brak aktywnych pozycji do pokazania struktury portfela.")


# ==========================================================
# TAB 7 — HISTORIA TRANSAKCJI
# ==========================================================

with tab7:
    st.subheader("Historia transakcji")

    if transactions.empty:
        st.info("Brak zapisanych transakcji.")
    else:
        show_df = transactions.copy()

        show_df.insert(0, "Lp.", range(1, len(show_df) + 1))

        show_df["transaction_type"] = show_df["transaction_type"].replace(
            {
                "buy": "kupno",
                "sell": "sprzedaż",
            }
        )

        display_history = show_df[
            [
                "Lp.",
                "trade_date",
                "ticker",
                "transaction_type",
                "quantity",
                "price",
                "fee",
            ]
        ]

        st.dataframe(
            display_history,
            use_container_width=True,
        )

        st.subheader("Usuwanie transakcji")

        transaction_options = show_df[["Lp.", "id", "ticker", "trade_date"]].copy()

        transaction_options["opis"] = transaction_options.apply(
            lambda row: f'{row["Lp."]}. {row["ticker"]} — {row["trade_date"].strftime("%Y-%m-%d")} — ID {row["id"]}',
            axis=1,
        )

        selected_description = st.selectbox(
            "Wybierz transakcję do usunięcia",
            transaction_options["opis"].tolist(),
        )

        selected_id = int(
            transaction_options[
                transaction_options["opis"] == selected_description
            ]["id"].iloc[0]
        )

        if st.button("Usuń wybraną transakcję"):
            delete_transaction(user_id, selected_id)

            st.success("Transakcja została usunięta.")
            st.rerun()


# ==========================================================
# TAB 8 — RAPORTY
# ==========================================================

with tab8:
    st.subheader("Raporty")

    if transactions.empty:
        st.info("Brak danych do wygenerowania raportu.")
    else:
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


# ==========================================================
# TAB 9 — EKSPORT I INFORMACJE
# ==========================================================

with tab9:
    st.subheader("Import danych z CSV")

    st.markdown(
        """
        Możesz wgrać plik CSV z transakcjami, np. przygotowany ręcznie albo na podstawie danych zewnętrznych.
        """
    )

    uploaded_csv = st.file_uploader(
        "Wgraj plik CSV z transakcjami",
        type=["csv"],
        key="csv_import_export_tab",
    )

    if uploaded_csv is not None:
        if st.button("Importuj transakcje z CSV"):
            try:
                csv_df = pd.read_csv(uploaded_csv)
                count = import_csv_transactions(user_id, csv_df)

                st.success(f"Zaimportowano transakcje: {count}")
                st.rerun()

            except Exception as exc:
                st.error(str(exc))

    with st.expander("Wymagany format CSV"):
        st.code(
            """trade_date,ticker,transaction_type,quantity,price,fee
2026-03-01,AAPL,buy,10,210.50,2.99
2026-03-15,AAPL,sell,3,219.80,2.99""",
            language="csv",
        )

    st.divider()

    st.subheader("Eksport danych")

    if transactions.empty:
        st.info("Brak danych do eksportu.")
    else:
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

        **Zakres funkcji:**
        - rejestracja i logowanie użytkownika,
        - bezpieczne przechowywanie haseł w postaci hasha,
        - zapis transakcji kupna i sprzedaży,
        - osobny moduł sprzedaży akcji,
        - automatyczne pobieranie ceny po wpisaniu tickera,
        - import transakcji z pliku CSV,
        - analiza średniej ceny zakupu,
        - obliczanie zysku lub straty,
        - ranking najbardziej opłacalnych walorów,
        - wykresy historyczne,
        - porównanie spółek,
        - generowanie raportów,
        - zapis historii raportów,
        - eksport danych,
        - usunięcie konta użytkownika.

        **Źródło danych:**
        Aplikacja korzysta z danych online pobieranych za pomocą biblioteki `yfinance`.
        Dane z innych źródeł, takich jak analizy.pl, mogą być wykorzystane po ręcznym przygotowaniu danych w pliku CSV.

        **Zastrzeżenie:**
        System ma charakter informacyjny i edukacyjny.
        Nie jest narzędziem doradztwa inwestycyjnego ani rekomendacją inwestycyjną.
        """
    )
