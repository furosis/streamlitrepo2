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
