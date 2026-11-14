"""Train and forecast six months of product demand with a global Transformer."""

from __future__ import annotations

import argparse
import os
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import tensorflow as tf


LOOKBACK = 28
MODEL_PATH = Path("models/global_transformer_demand_model.keras")


def month_end(start: date, months: int) -> date:
    total_month = start.month + months
    return date(start.year + (total_month - 1) // 12, (total_month - 1) % 12 + 1, 1) - timedelta(days=1)


def is_recurring_festival(day: date) -> int:
    return int((day.month == 1 and 13 <= day.day <= 15) or (day.month == 12 and 24 <= day.day <= 26))


def apply_seasonal_floor(monthly: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    historical = sales.assign(year=sales["date"].dt.year, month_number=sales["date"].dt.month)
    historical = historical.groupby(["product_id", "year", "month_number"], as_index=False)["quantity_sold"].sum()
    floors = []
    for row in monthly.itertuples(index=False):
        month_number = int(str(row.month).split("-")[1])
        values = historical[(historical["product_id"] == row.product_id) & (historical["month_number"] == month_number)]["quantity_sold"].tail(3)
        floors.append(values.median() * 0.7 if not values.empty else 0)
    adjusted = monthly.copy()
    adjusted["forecast_quantity"] = np.maximum(adjusted["forecast_quantity"], floors)
    return adjusted


@lru_cache(maxsize=1)
def load_resources() -> tuple[pd.DataFrame, pd.DataFrame, tf.keras.Model, dict[str, int], float, float]:
    sales = pd.read_csv("datasets/sales.csv", parse_dates=["date"])
    products = pd.read_csv("datasets/products.csv")[["product_id", "product_name"]]
    model = tf.keras.models.load_model(MODEL_PATH)
    product_ids = sorted(sales["product_id"].unique())
    product_codes = {product_id: index for index, product_id in enumerate(product_ids)}
    price_mean, price_std = sales["selling_price"].mean(), sales["selling_price"].std()
    return sales, products, model, product_codes, price_mean, price_std


def create_training_data(sales: pd.DataFrame) -> tuple[list[np.ndarray], np.ndarray]:
    product_ids = sorted(sales["product_id"].unique())
    product_codes = {product_id: index for index, product_id in enumerate(product_ids)}
    price_mean, price_std = sales["selling_price"].mean(), sales["selling_price"].std()
    sequences, product_inputs, covariates, targets = [], [], [], []

    for product_id, product_sales in sales.groupby("product_id", sort=True):
        product_sales = product_sales.sort_values("date").reset_index(drop=True)
        quantities = product_sales["quantity_sold"].to_numpy(dtype="float32")
        for index in range(LOOKBACK, len(product_sales)):
            row = product_sales.iloc[index]
            weekday = row["date"].weekday()
            sequences.append(quantities[index - LOOKBACK:index])
            product_inputs.append(product_codes[product_id])
            covariates.append([
                row["promotion_applied"],
                (row["selling_price"] - price_mean) / price_std,
                int(weekday >= 5),
                int(pd.notna(row["festival"])),
                np.sin(2 * np.pi * weekday / 7),
                np.cos(2 * np.pi * weekday / 7),
            ])
            targets.append(quantities[index])

    return [
        np.asarray(sequences, dtype="float32")[..., None],
        np.asarray(product_inputs, dtype="int32")[:, None],
        np.asarray(covariates, dtype="float32"),
    ], np.asarray(targets, dtype="float32")


def retrain_model(epochs: int = 25) -> None:
    sales = pd.read_csv("datasets/sales.csv", parse_dates=["date"])
    inputs, targets = create_training_data(sales)
    model = tf.keras.models.load_model(MODEL_PATH, compile=False)
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001), loss=tf.keras.losses.Huber(), metrics=["mae"])
    callbacks = [tf.keras.callbacks.EarlyStopping(monitor="val_mae", patience=3, restore_best_weights=True)]
    history = model.fit(inputs, targets, validation_split=0.1, epochs=epochs, batch_size=1024, shuffle=True, callbacks=callbacks, verbose=2)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    model.save(MODEL_PATH)
    load_resources.cache_clear()
    generate_monthly_forecast.cache_clear()
    print(f"Saved retrained model to {MODEL_PATH}")
    print(f"Best validation MAE: {min(history.history['val_mae']):.4f}")


@lru_cache(maxsize=3)
def generate_monthly_forecast(months: int = 6) -> pd.DataFrame:
    if months < 1 or months > 12:
        raise ValueError("months must be between 1 and 12")
    sales, products, model, product_codes, price_mean, price_std = load_resources()
    product_ids = sorted(product_codes)
    histories = defaultdict(list)
    for row in sales.sort_values(["product_id", "date"]).to_dict("records"):
        histories[row["product_id"]].append(float(row["quantity_sold"]))
    prices = sales.sort_values("date").groupby("product_id")["selling_price"].last().to_dict()
    start = sales["date"].max().date() + timedelta(days=1)
    end = month_end(start, months)
    daily_rows = []
    current = start
    while current <= end:
        sequences = np.asarray([histories[product_id][-LOOKBACK:] for product_id in product_ids], dtype="float32")[..., None]
        product_input = np.asarray([[product_codes[product_id]] for product_id in product_ids], dtype="int32")
        covariates = np.asarray([[
            0, (prices[product_id] - price_mean) / price_std, int(current.weekday() >= 5),
            is_recurring_festival(current), np.sin(2 * np.pi * current.weekday() / 7), np.cos(2 * np.pi * current.weekday() / 7),
        ] for product_id in product_ids], dtype="float32")
        predictions = model.predict([sequences, product_input, covariates], verbose=0).ravel().clip(min=0)
        for product_id, prediction in zip(product_ids, predictions):
            histories[product_id].append(float(prediction))
            daily_rows.append({"date": current, "product_id": product_id, "forecast_quantity": float(prediction)})
        current += timedelta(days=1)
    forecast = pd.DataFrame(daily_rows).merge(products, on="product_id", how="left")
    forecast["date"] = pd.to_datetime(forecast["date"])
    forecast["month"] = forecast["date"].dt.strftime("%Y-%m")
    monthly = forecast.groupby(["month", "product_id", "product_name"], as_index=False).agg(
        forecast_quantity=("forecast_quantity", "sum"),
    )
    monthly = apply_seasonal_floor(monthly, sales)
    monthly["forecast_quantity"] = monthly["forecast_quantity"].round(2)
    return monthly


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrain", action="store_true", help="retrain the saved Transformer before forecasting")
    parser.add_argument("--epochs", type=int, default=25, help="maximum training epochs")
    args = parser.parse_args()
    if args.retrain:
        retrain_model(args.epochs)
    monthly = generate_monthly_forecast(6)
    output = Path("forecasts/transformer_monthly_forecast_6_months.csv")
    output.parent.mkdir(exist_ok=True)
    monthly.to_csv(output, index=False)
    print(f"Forecast period: {monthly['month'].min()} through {monthly['month'].max()}")
    print(f"Wrote {len(monthly)} product-month rows to {output}")


if __name__ == "__main__":
    main()
