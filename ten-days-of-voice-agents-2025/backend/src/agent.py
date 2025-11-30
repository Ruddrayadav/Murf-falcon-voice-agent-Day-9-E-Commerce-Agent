import logging
import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Annotated

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
    tokenize,
    function_tool,
)
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("day9-agent")

# Load environment
load_dotenv(".env.local")

# ============================================================
# E-COMMERCE BACKEND (INSIDE SAME FILE)
# ============================================================

BASE_DIR = Path(__file__).parent
CATALOG_FILE = BASE_DIR / "catalog.json"
ORDERS_FILE = BASE_DIR / "orders.json"

# Ensure orders.json exists
if not ORDERS_FILE.exists():
    with open(ORDERS_FILE, "w") as f:
        json.dump([], f)


def load_catalog():
    with open(CATALOG_FILE, "r") as f:
        return json.load(f)


def load_orders():
    with open(ORDERS_FILE, "r") as f:
        return json.load(f)


def save_orders(data):
    with open(ORDERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def list_catalog(query: str = ""):
    query = query.lower().strip()
    catalog = load_catalog()
    if not query:
        return catalog

    results = []
    for p in catalog:
        haystack = f"{p['name']} {p['description']} {p['category']} {p.get('color','')}".lower()
        if query in haystack:
            results.append(p)

    return results


def create_new_order(items: List[Dict[str, Any]]):
    catalog = load_catalog()
    orders = load_orders()

    resolved_items = []
    total_cost = 0

    for it in items:
        # Handle various key names the LLM might use
        pid = it.get("product_id") or it.get("id") or it.get("name")
        if not pid:
            return {"error": f"Invalid item format: {it}. Must contain product_id, id, or name."}
        
        qty = it.get("quantity", 1)

        product = next((p for p in catalog if p["id"] == pid), None)
        if not product:
            # Try finding by name or fuzzy search
            candidates = list_catalog(pid)
            if len(candidates) == 1:
                product = candidates[0]
            elif len(candidates) > 1:
                return {"error": f"Product '{pid}' is ambiguous. Found: {', '.join(c['name'] for c in candidates)}"}
            else:
                return {"error": f"Product '{pid}' not found"}

        resolved_items.append({
            "product_id": product["id"],
            "name": product["name"],
            "quantity": qty,
            "unit_price": product["price"]
        })

        total_cost += product["price"] * qty

    order = {
        "id": f"ord-{len(orders)+1}",
        "created_at": datetime.now().isoformat(),
        "items": resolved_items,
        "total_amount": total_cost,
        "currency": "INR"
    }

    orders.append(order)
    save_orders(orders)
    return order


def last_order():
    orders = load_orders()
    if not orders:
        return None
    return orders[-1]


# ============================================================
# AGENT IMPLEMENTATION
# ============================================================

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=self._prompt()
        )

    def _prompt(self):
        return """
You are LYRA — a premium, calm, Apple-store-style shopping assistant.

Your tasks:
1. Let users browse products via voice.
2. Call list_products() when the user searches for an item.
3. Call create_order() when the user buys something.
4. Call get_last_order() when they ask what they purchased.
5. NEVER invent products. Only respond using function tool results.
6. Keep responses short, warm, premium.
"""

    # ---------------------------
    # Day 9 TOOLS
    # ---------------------------

    @function_tool
    async def list_products(self, query: Annotated[str, "Search query"] = ""):
        data = list_catalog(query)
        if not data:
            return "No products found matching your search."

        out = "Here are some products:\n"
        for p in data:
            out += f"- {p['name']} (ID: {p['id']}) — ₹{p['price']}\n"
        return out

    @function_tool
    async def create_order(self, items: Annotated[List[Dict[str, Any]], "Items to purchase"]):
        try:
            logger.info(f"create_order called with: {items}")
            order = create_new_order(items)
            if "error" in order:
                return f"Order failed: {order['error']}"

            return f"Order placed! ID: {order['id']} — Total: ₹{order['total_amount']}."
        except Exception as e:
            logger.error(f"Error in create_order: {e}", exc_info=True)
            return f"An error occurred while processing your order: {str(e)}"

    @function_tool
    async def get_last_order(self):
        order = last_order()
        if not order:
            return "You haven't placed any orders yet."

        items = ", ".join([f"{it['quantity']}x {it['name']}" for it in order["items"]])
        return f"Your last order ({order['id']}) totals ₹{order['total_amount']} with: {items}."


# ============================================================
# PREWARM
# ============================================================

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


# ============================================================
# ENTRYPOINT
# ============================================================

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-matthew",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        preemptive_generation=True,
    )

    # Track usage
    usage = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on(event: MetricsCollectedEvent):
        metrics.log_metrics(event.metrics)
        usage.collect(event.metrics)

    async def finish():
        logger.info(usage.get_summary())

    ctx.add_shutdown_callback(finish)

    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC()
        ),
    )

    await ctx.connect()

    await session.say(
        "Hello, I’m Lyra. What would you like to shop for today?",
        add_to_chat_ctx=True
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm
        )
    )
