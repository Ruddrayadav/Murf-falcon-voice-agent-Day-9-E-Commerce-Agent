import json
import os
from datetime import datetime

CATALOG_PATH = "catalog.json"
ORDERS_PATH = "orders.json"

# Load catalog
with open(CATALOG_PATH, "r") as f:
    CATALOG = json.load(f)

# Ensure orders file exists
if not os.path.exists(ORDERS_PATH):
    with open(ORDERS_PATH, "w") as f:
        json.dump([], f)

def load_orders():
    with open(ORDERS_PATH, "r") as f:
        return json.load(f)

def save_orders(data):
    with open(ORDERS_PATH, "w") as f:
        json.dump(data, f, indent=2)

def list_products(query: str = ""):
    query = query.lower().strip()
    if not query:
        return CATALOG
    
    results = []
    for p in CATALOG:
        haystack = f"{p['name']} {p['description']} {p['category']} {p.get('color','')}".lower()
        if query in haystack:
            results.append(p)
    
    return results

def create_order(items):
    """
    items: [{ "product_id": "abc", "quantity": 1 }]
    """
    orders = load_orders()
    resolved_items = []
    total_amount = 0

    for it in items:
        pid = it["product_id"]
        qty = it.get("quantity", 1)

        prod = next((p for p in CATALOG if p["id"] == pid), None)
        if not prod:
            return {"error": f"Product {pid} not found"}

        resolved_items.append({
            "product_id": pid,
            "name": prod["name"],
            "quantity": qty,
            "unit_price": prod["price"]
        })

        total_amount += prod["price"] * qty

    order = {
        "id": f"ord-{len(orders)+1}",
        "items": resolved_items,
        "total_amount": total_amount,
        "currency": "INR",
        "created_at": datetime.now().isoformat()
    }

    orders.append(order)
    save_orders(orders)
    return order

def get_last_order():
    orders = load_orders()
    if not orders:
        return None
    return orders[-1]
