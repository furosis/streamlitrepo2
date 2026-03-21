# Aplikacja wspomagająca inwestycje giełdowe

## Co robi aplikacja
- zapisuje transakcje kupna i sprzedaży do SQLite,
- liczy średnią cenę zakupu,
- pokazuje zysk/stratę zrealizowaną i niezrealizowaną,
- pobiera aktualne i historyczne kursy z yfinance,
- porównuje historyczne zachowanie kilku spółek,
- działa w Streamlit Cloud.

## Pliki
- `app.py` – główna aplikacja
- `requirements.txt` – zależności
- `example_transactions.csv` – przykładowe dane do importu

## Uruchomienie lokalne
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy na Streamlit Cloud
1. Wrzuć pliki do repozytorium GitHub.
2. Zaloguj się do Streamlit Cloud.
3. Kliknij **New app**.
4. Wybierz repozytorium, branch i plik `app.py`.
5. Kliknij **Deploy**.
