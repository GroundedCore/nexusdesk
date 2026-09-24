"""Local demo HTTP service; never connect to production business data."""

from fastapi import FastAPI, Query

app = FastAPI(title="Demo business API")


@app.get("/orders")
async def order(order_id: str = Query(pattern=r"^DEMO-[0-9]{3}$")):
    return {"demo": True, "order_id": order_id, "status": "演示：已发货"}
