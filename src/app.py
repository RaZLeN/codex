import os
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel
import sqlite3
import openai
import json

DATABASE_PATH = os.environ.get("DATABASE_PATH", "products.db")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

app = FastAPI()

class ProductRequest(BaseModel):
    text: str

class Item(BaseModel):
    sku: str
    manufacturer: Optional[str] = None
    quantity: Optional[int] = None
    offer: Optional[float] = None

class ProductResponse(BaseModel):
    items: List[Item]


def parse_request(text: str) -> List[Item]:
    """Parse raw text into structured items using OpenAI"""
    if not OPENAI_API_KEY:
        # Fallback stub if no API key is provided
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        items = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                sku = parts[0]
                qty = int(parts[1]) if parts[1].isdigit() else None
                manuf = parts[2] if len(parts) > 2 else None
                items.append(Item(sku=sku, manufacturer=manuf, quantity=qty))
        return items

    # Using OpenAI to parse the request
    prompt = (
        "Parse the following request into items with fields 'sku', 'manufacturer', and 'quantity'.\n"
        f"Request: {text}\n"
        "Return JSON array."
    )
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content
    try:
        items_data = json.loads(content)
        return [Item(**item) for item in items_data]
    except Exception:
        return []


def get_best_offer(sku: str, manufacturer: Optional[str], quantity: Optional[int]) -> Optional[float]:
    conn = sqlite3.connect(DATABASE_PATH)
    cur = conn.cursor()
    if manufacturer:
        cur.execute(
            "SELECT price FROM products WHERE sku=? AND manufacturer=? ORDER BY price ASC LIMIT 1",
            (sku, manufacturer),
        )
    else:
        cur.execute(
            "SELECT price FROM products WHERE sku=? ORDER BY price ASC LIMIT 1",
            (sku,),
        )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

@app.post("/query", response_model=ProductResponse)
async def query_products(req: ProductRequest):
    items = parse_request(req.text)
    for item in items:
        item.offer = get_best_offer(item.sku, item.manufacturer, item.quantity)
    return ProductResponse(items=items)
