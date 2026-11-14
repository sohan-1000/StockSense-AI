"""FastAPI application for product demand forecasts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from forecast_transformer import generate_monthly_forecast


app = FastAPI(title="Product Demand Forecast API", version="1.0.0")
STATIC_DIR = Path("static")


@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/forecast")
async def forecast(months: int = Query(default=6, ge=1, le=12)) -> dict:
    result = await asyncio.to_thread(generate_monthly_forecast, months)
    return {"model": "global_transformer_demand_model", "months": months, "forecast_start": result["month"].min(), "forecast_end": result["month"].max(), "forecasts": result.to_dict(orient="records")}


@app.get("/api/forecast/{product_id}")
async def product_forecast(product_id: str, months: int = Query(default=6, ge=1, le=12)) -> dict:
    result = await asyncio.to_thread(generate_monthly_forecast, months)
    product = result[result["product_id"] == product_id.upper()]
    if product.empty:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"model": "global_transformer_demand_model", "months": months, "product_id": product_id.upper(), "product_name": product.iloc[0]["product_name"], "forecasts": product.to_dict(orient="records")}
