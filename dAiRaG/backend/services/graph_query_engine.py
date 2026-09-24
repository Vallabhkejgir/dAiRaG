from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import load_runtime_env
from ..observability import start_observation

load_runtime_env()

SERVICES_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SERVICES_DIR.parent.parent
DATASET_ROOT = SERVICE_ROOT.parent / "dataset"
DEFAULT_COMBINED_DIR = DATASET_ROOT / "combined"

try:
    from neo4j import GraphDatabase, Query
except ImportError:  # pragma: no cover - optional dependency
    GraphDatabase = None
    Query = None

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_|-]{2,}")
NON_IDENTIFIER_TOKENS = {"for", "from", "into", "with", "about", "around", "show", "find", "what", "which", "where", "when", "who"}
COUNT_KEYWORDS = ("how many", "count", "counts", "number of")
SCHEDULE_HINTS = (
    "schedule line",
    "schedule lines",
    "confirmed delivery date",
    "confirmed date",
    "confirmed quantity",
    "confirmed qty",
)
CONNECTION_KEYWORDS = (
    "linked",
    "connected",
    "neighbor",
    "neighbors",
    "relationship",
    "relationships",
    "around",
    "expand",
)
PRODUCT_TEXT_MATCH_KEYWORDS = (
    "contain",
    "contains",
    "containing",
    "involving",
    "involves",
    "including",
    "includes",
    "matching",
    "matches",
    "named",
    "called",
    "like",
)
PRODUCT_FILTER_EDGE_STOPWORDS = {
    "a",
    "an",
    "all",
    "any",
    "for",
    "matching",
    "named",
    "called",
    "product",
    "products",
    "with",
    "where",
    "whose",
    "the",
}
ORDERED_PRODUCT_KEYWORDS = ("ordered", "bought", "purchased")
ORDERED_PRODUCT_EDGE_STOPWORDS = PRODUCT_FILTER_EDGE_STOPWORDS | {
    "that",
    "who",
    "ordered",
    "bought",
    "purchased",
    "customer",
    "customers",
    "order",
    "orders",
}
INVOICE_PRODUCT_EDGE_STOPWORDS = PRODUCT_FILTER_EDGE_STOPWORDS | {
    "invoice",
    "invoices",
    "id",
    "ids",
    "show",
    "showing",
    "purchase",
    "purchases",
    "purchased",
    "bought",
    "ordered",
    "bill",
    "bills",
    "billed",
    "billing",
}

ENTITY_CONFIG: dict[str, dict[str, str]] = {
    "Customer": {"label": "Customer", "id_property": "customer_id", "title": "customer"},
    "Address": {"label": "Address", "id_property": "address_uuid", "title": "address"},
    "Product": {"label": "Product", "id_property": "product_id", "title": "product"},
    "Plant": {"label": "Plant", "id_property": "plant_id", "title": "plant"},
    "Order": {"label": "Order", "id_property": "order_id", "title": "order"},
    "Delivery": {"label": "Delivery", "id_property": "delivery_id", "title": "delivery"},
    "Invoice": {"label": "Invoice", "id_property": "invoice_id", "title": "invoice"},
    "Payment": {"label": "Payment", "id_property": "payment_id", "title": "payment"},
    "JournalEntryItem": {"label": "JournalEntryItem", "id_property": "journal_entry_item_id", "title": "journal entry item"},
}

ENTITY_KEYWORDS = {
    "customer": "Customer",
    "customers": "Customer",
    "address": "Address",
    "addresses": "Address",
    "product": "Product",
    "products": "Product",
    "plant": "Plant",
    "plants": "Plant",
    "sales order": "Order",
    "sales orders": "Order",
    "order": "Order",
    "orders": "Order",
    "delivery": "Delivery",
    "deliveries": "Delivery",
    "invoice": "Invoice",
    "invoices": "Invoice",
    "payment": "Payment",
    "payments": "Payment",
    "journal entry": "JournalEntryItem",
    "journal entries": "JournalEntryItem",
    "journal entry item": "JournalEntryItem",
    "journal entry items": "JournalEntryItem",
    "accounting item": "JournalEntryItem",
    "accounting items": "JournalEntryItem",
}

FIELD_KEYWORD_MAP = {
    "amount": {"total_net_amount", "amount_in_transaction_currency", "net_amount"},
    "currency": {"transaction_currency", "company_code_currency"},
    "status": {
        "overall_delivery_status",
        "overall_goods_movement_status",
        "overall_picking_status",
        "billing_document_is_cancelled",
    },
    "date": {
        "billing_document_date",
        "posting_date",
        "document_date",
        "requested_delivery_date",
        "clearing_date",
        "creation_date",
    },
    "gl account": {"gl_account"},
    "profit center": {"profit_center"},
    "cost center": {"cost_center"},
    "plant name": {"plant_name"},
    "valuation area": {"valuation_area"},
    "storage location": {"storage_locations", "storage_location_count", "production_inventory_managed_location", "storage_location"},
    "journal": {"journal_entry_item_id", "accounting_document", "accounting_document_item"},
    "accounting": {"accounting_document", "invoice_accounting_document", "payment_document"},
    "document": {
        "accounting_document",
        "invoice_accounting_document",
        "payment_document",
        "billing_document_date",
        "document_date",
    },
}

FIELD_PRIORITY = {
    "Customer": ["customer_id", "full_name", "business_partner_id", "last_change_date"],
    "Address": ["address_uuid", "street_name", "city_name", "country", "postal_code"],
    "Product": ["product_id", "product_description", "product_type", "product_group"],
    "Plant": ["plant_id", "plant_name", "valuation_area", "sales_organization", "distribution_channel", "division", "address_id"],
    "Order": ["order_id", "customer_id", "total_net_amount", "requested_delivery_date", "overall_delivery_status"],
    "Delivery": ["delivery_id", "actual_goods_movement_date", "shipping_point", "overall_goods_movement_status"],
    "Invoice": ["invoice_id", "customer_id", "total_net_amount", "billing_document_date", "accounting_document"],
    "Payment": ["payment_id", "customer_id", "amount_in_transaction_currency", "clearing_date", "posting_date"],
    "JournalEntryItem": ["journal_entry_item_id", "invoice_id", "customer_id", "accounting_document", "accounting_document_item", "gl_account", "amount_in_transaction_currency", "clearing_payment_id", "clearing_date"],
}

JOURNAL_ENTRY_RELATED_FIELDS = {
    "gl_account",
    "profit_center",
    "cost_center",
    "accounting_document",
    "accounting_document_item",
    "journal_entry_item_id",
    "reference_document",
    "financial_account_type",
    "assignment_reference",
    "clearing_payment_id",
    "clearing_date",
}

PROCESS_RELATIONSHIP_MAP = {
    ("Customer", "Address"): {"HAS_ADDRESS"},
    ("Address", "Customer"): {"HAS_ADDRESS"},
    ("Customer", "Order"): {"PLACED"},
    ("Order", "Customer"): {"PLACED"},
    ("Customer", "Invoice"): {"RECEIVED_INVOICE", "PLACED", "FULFILLED_BY", "INVOICED_AS"},
    ("Invoice", "Customer"): {"RECEIVED_INVOICE"},
    ("Customer", "Payment"): {"MADE_PAYMENT", "RECEIVED_INVOICE", "SETTLES"},
    ("Payment", "Customer"): {"MADE_PAYMENT"},
    ("Order", "Delivery"): {"FULFILLED_BY"},
    ("Delivery", "Order"): {"FULFILLED_BY"},
    ("Order", "Invoice"): {"FULFILLED_BY", "INVOICED_AS"},
    ("Invoice", "Order"): {"INVOICED_AS", "FULFILLED_BY"},
    ("Order", "Payment"): {"FULFILLED_BY", "INVOICED_AS", "SETTLES"},
    ("Payment", "Order"): {"SETTLES", "INVOICED_AS", "FULFILLED_BY"},
    ("Delivery", "Invoice"): {"INVOICED_AS"},
    ("Invoice", "Delivery"): {"INVOICED_AS"},
    ("Delivery", "Payment"): {"INVOICED_AS", "SETTLES"},
    ("Payment", "Delivery"): {"SETTLES", "INVOICED_AS"},
    ("Invoice", "Payment"): {"SETTLES"},
    ("Payment", "Invoice"): {"SETTLES"},
    ("Order", "Product"): {"CONTAINS_PRODUCT"},
    ("Delivery", "Product"): {"DELIVERS_PRODUCT"},
    ("Invoice", "Product"): {"BILLS_PRODUCT"},
    ("Product", "Plant"): {"AVAILABLE_AT_PLANT"},
    ("Plant", "Product"): {"AVAILABLE_AT_PLANT"},
    ("Product", "Order"): {"CONTAINS_PRODUCT"},
    ("Product", "Delivery"): {"DELIVERS_PRODUCT"},
    ("Product", "Invoice"): {"BILLS_PRODUCT"},
    ("Customer", "JournalEntryItem"): {"HAS_JOURNAL_ENTRY_ITEM"},
    ("JournalEntryItem", "Customer"): {"HAS_JOURNAL_ENTRY_ITEM"},
    ("Invoice", "JournalEntryItem"): {"ACCOUNTED_AS"},
    ("JournalEntryItem", "Invoice"): {"ACCOUNTED_AS"},
    ("Payment", "JournalEntryItem"): {"CLEARS_JOURNAL_ENTRY_ITEM"},
    ("JournalEntryItem", "Payment"): {"CLEARS_JOURNAL_ENTRY_ITEM"},
    ("Order", "JournalEntryItem"): {"FULFILLED_BY", "INVOICED_AS", "ACCOUNTED_AS"},
    ("JournalEntryItem", "Order"): {"ACCOUNTED_AS", "INVOICED_AS", "FULFILLED_BY"},
    ("Delivery", "JournalEntryItem"): {"INVOICED_AS", "ACCOUNTED_AS"},
    ("JournalEntryItem", "Delivery"): {"ACCOUNTED_AS", "INVOICED_AS"},
}

PROCESS_DEPTH_MAP = {
    ("Customer", "Address"): 1,
    ("Customer", "Order"): 1,
    ("Customer", "Invoice"): 1,
    ("Customer", "Payment"): 1,
    ("Order", "Delivery"): 1,
    ("Order", "Invoice"): 2,
    ("Order", "Payment"): 3,
    ("Delivery", "Invoice"): 1,
    ("Delivery", "Payment"): 2,
    ("Invoice", "Payment"): 1,
    ("Order", "Product"): 1,
    ("Delivery", "Product"): 1,
    ("Invoice", "Product"): 1,
    ("Product", "Plant"): 1,
    ("Plant", "Product"): 1,
    ("Customer", "JournalEntryItem"): 1,
    ("Invoice", "JournalEntryItem"): 1,
    ("Payment", "JournalEntryItem"): 1,
    ("Order", "JournalEntryItem"): 3,
    ("Delivery", "JournalEntryItem"): 2,
    ("JournalEntryItem", "Customer"): 1,
    ("JournalEntryItem", "Invoice"): 1,
    ("JournalEntryItem", "Payment"): 1,
    ("JournalEntryItem", "Order"): 3,
    ("JournalEntryItem", "Delivery"): 2,
}

ID_PROPS = [config["id_property"] for config in ENTITY_CONFIG.values()]
DISPLAY_PROPS = [
    "full_name",
    "name",
    "product_description",
    "plant_name",
    "confirmed_delivery_date",
    "street_name",
    *ID_PROPS,
]


def entity_id_expr(alias: str) -> str:
    return "coalesce(" + ", ".join(f"{alias}.{prop}" for prop in ID_PROPS) + ")"


def entity_label_expr(alias: str) -> str:
    return "coalesce(" + ", ".join(f"{alias}.{prop}" for prop in DISPLAY_PROPS) + ")"


def clean_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def format_business_date(value: Any) -> str:
    raw = clean_scalar(value)
    if not raw:
        return ""
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%B %d, %Y").replace(" 0", " ")
    except ValueError:
        return raw


def _observation_response_summary(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "queryMode": response.get("queryMode"),
        "focusNodeId": response.get("focusNodeId"),
        "revealNodeCount": len(response.get("revealNodeIds") or []),
        "evidenceNodeCount": len(response.get("evidenceNodeIds") or []),
        "hasCypher": bool(response.get("cypher")),
    }


class SalesScheduleLookupEngine:
    def __init__(self, combined_dir: Path) -> None:
        self.combined_dir = combined_dir
        self.sales_order_headers = load_jsonl(combined_dir / "sales_order_headers.jsonl")
        self.sales_order_items = load_jsonl(combined_dir / "sales_order_items.jsonl")
        self.sales_order_schedule_lines = load_jsonl(combined_dir / "sales_order_schedule_lines.jsonl")
        self.headers_by_order_id: dict[str, dict[str, Any]] = {}
        self.items_by_order_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.item_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        self.schedule_lines_by_order_id: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in self.sales_order_headers:
            order_id = clean_scalar(row.get("salesOrder"))
            if order_id:
                self.headers_by_order_id[order_id] = row

        for row in self.sales_order_items:
            order_id = clean_scalar(row.get("salesOrder"))
            order_item_id = clean_scalar(row.get("salesOrderItem"))
            if not order_id:
                continue
            self.items_by_order_id[order_id].append(row)
            if order_item_id:
                self.item_by_key[(order_id, order_item_id)] = row

        for row in self.sales_order_schedule_lines:
            order_id = clean_scalar(row.get("salesOrder"))
            if order_id:
                self.schedule_lines_by_order_id[order_id].append(row)

    @classmethod
    def from_files(cls, combined_dir: Path | None = None) -> "SalesScheduleLookupEngine | None":
        target_dir = combined_dir or DEFAULT_COMBINED_DIR
        required = [
            target_dir / "sales_order_headers.jsonl",
            target_dir / "sales_order_items.jsonl",
            target_dir / "sales_order_schedule_lines.jsonl",
        ]
        if not all(path.exists() for path in required):
            return None
        return cls(target_dir)

    def execute(self, message: str) -> dict[str, Any] | None:
        lower = " ".join(message.lower().split())
        if not self.is_schedule_query(lower):
            return None

        with start_observation(
            name="sales-schedule.lookup",
            as_type="span",
            input={"message": message},
            metadata={"engine": "sales_data"},
        ) as observation:
            order_id = self.extract_order_id(message)
            customer_id = self.extract_customer_id(message)

            if self.is_count_query(lower):
                response = self.build_count_response()
                observation.update(output=_observation_response_summary(response))
                return response

            if not order_id:
                response = {
                    "reply": (
                        "I can answer schedule-line questions from the sales data when you include a sales order id. "
                        "Try something like `show schedule lines for order 740509` or `what is the confirmed delivery date for customer 320000083 and sales order 740509?`"
                    ),
                    "focusNodeId": None,
                    "revealNodeIds": [],
                    "evidenceNodeIds": [],
                    "viewMode": "global",
                    "expandFocus": False,
                    "focusDepth": 0,
                    "queryMode": "sales_data",
                    "cypher": None,
                    "cypherParams": {},
                }
                observation.update(output=_observation_response_summary(response))
                return response

            response = self.build_schedule_response(message, order_id, customer_id)
            observation.update(
                output=_observation_response_summary(response),
                metadata={"orderId": order_id, "hasCustomerId": bool(customer_id)},
            )
            return response

    def is_schedule_query(self, lower: str) -> bool:
        return any(hint in lower for hint in SCHEDULE_HINTS)

    def is_count_query(self, lower: str) -> bool:
        return any(keyword in lower for keyword in COUNT_KEYWORDS)

    def extract_order_id(self, message: str) -> str | None:
        patterns = [
            r"\bsales\s+order(?:\s+id)?\s+([A-Za-z0-9:_|-]+)\b",
            r"\border(?:\s+id)?\s+([A-Za-z0-9:_|-]+)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return clean_scalar(match.group(1))
        return None

    def extract_customer_id(self, message: str) -> str | None:
        match = re.search(r"\bcustomer(?:\s+id)?\s+([A-Za-z0-9:_|-]+)\b", message, re.IGNORECASE)
        if not match:
            return None
        return clean_scalar(match.group(1))

    def build_count_response(self) -> dict[str, Any]:
        total = len(self.sales_order_schedule_lines)
        return {
            "reply": f"There are {total} schedule lines in the sales data.",
            "focusNodeId": None,
            "revealNodeIds": [],
            "evidenceNodeIds": [],
            "viewMode": "global",
            "expandFocus": False,
            "focusDepth": 0,
            "queryMode": "sales_data",
            "cypher": None,
            "cypherParams": {},
        }

    def build_schedule_response(self, message: str, order_id: str, customer_id: str | None) -> dict[str, Any]:
        header = self.headers_by_order_id.get(order_id)
        if customer_id and header and clean_scalar(header.get("soldToParty")) != customer_id:
            rows: list[dict[str, Any]] = []
        else:
            rows = list(self.schedule_lines_by_order_id.get(order_id, []))

        enriched_rows = self.enrich_rows(order_id, rows)
        if not enriched_rows:
            reply = (
                f"I could not find any schedule lines for customer {customer_id} and sales order {order_id}."
                if customer_id
                else f"I could not find any schedule lines for sales order {order_id}."
            )
            return {
                "reply": reply,
                "focusNodeId": f"Order:{order_id}" if header else None,
                "revealNodeIds": [f"Order:{order_id}"] if header else [],
                "evidenceNodeIds": [f"Order:{order_id}"] if header else [],
                "viewMode": "focus" if header else "global",
                "expandFocus": bool(header),
                "focusDepth": 1 if header else 0,
                "queryMode": "sales_data",
                "cypher": None,
                "cypherParams": {k: v for k, v in {"order_id": order_id, "customer_id": customer_id}.items() if v},
            }

        reply = self.render_reply(message, order_id, customer_id, enriched_rows)
        evidence_node_ids = [f"Order:{order_id}"]
        reveal_node_ids = [f"Order:{order_id}"]
        if customer_id:
            evidence_node_ids.append(f"Customer:{customer_id}")
        for row in enriched_rows:
            product_id = row.get("product_id")
            if product_id:
                node_id = f"Product:{product_id}"
                if node_id not in evidence_node_ids:
                    evidence_node_ids.append(node_id)
                if node_id not in reveal_node_ids:
                    reveal_node_ids.append(node_id)
        if customer_id and f"Customer:{customer_id}" not in reveal_node_ids:
            reveal_node_ids.append(f"Customer:{customer_id}")

        return {
            "reply": reply,
            "focusNodeId": f"Order:{order_id}",
            "revealNodeIds": reveal_node_ids,
            "evidenceNodeIds": evidence_node_ids,
            "viewMode": "focus",
            "expandFocus": True,
            "focusDepth": 1,
            "queryMode": "sales_data",
            "cypher": None,
            "cypherParams": {k: v for k, v in {"order_id": order_id, "customer_id": customer_id}.items() if v},
        }

    def enrich_rows(self, order_id: str, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        enriched: list[dict[str, str]] = []
        for row in rows:
            order_item_id = clean_scalar(row.get("salesOrderItem"))
            item = self.item_by_key.get((order_id, order_item_id), {})
            enriched.append(
                {
                    "schedule_line_id": f"{order_id}|{order_item_id}|{clean_scalar(row.get('scheduleLine'))}",
                    "order_id": order_id,
                    "order_item_id": order_item_id,
                    "schedule_line": clean_scalar(row.get("scheduleLine")),
                    "confirmed_delivery_date": format_business_date(row.get("confirmedDeliveryDate")),
                    "confirmed_order_quantity": clean_scalar(row.get("confdOrderQtyByMatlAvailCheck")),
                    "order_quantity_unit": clean_scalar(row.get("orderQuantityUnit")),
                    "product_id": clean_scalar(item.get("material")),
                    "production_plant": clean_scalar(item.get("productionPlant")),
                }
            )
        return enriched

    def render_reply(self, message: str, order_id: str, customer_id: str | None, rows: list[dict[str, str]]) -> str:
        lower = " ".join(message.lower().split())
        unique_dates = [row["confirmed_delivery_date"] for row in rows if row["confirmed_delivery_date"]]
        deduped_dates: list[str] = []
        for value in unique_dates:
            if value not in deduped_dates:
                deduped_dates.append(value)

        if "confirmed delivery date" in lower or "confirmed date" in lower:
            if len(deduped_dates) == 1:
                prefix = (
                    f"The confirmed delivery date for customer {customer_id} and sales order {order_id} is"
                    if customer_id
                    else f"The confirmed delivery date for sales order {order_id} is"
                )
                return f"{prefix} {deduped_dates[0]}."

            summaries = []
            for row in rows[:8]:
                confirmed_date = row.get("confirmed_delivery_date") or "not confirmed"
                summaries.append(f"item {row.get('order_item_id')} schedule {row.get('schedule_line')}: {confirmed_date}")
            prefix = (
                f"For customer {customer_id} and sales order {order_id}, the confirmed delivery dates are:"
                if customer_id
                else f"For sales order {order_id}, the confirmed delivery dates are:"
            )
            suffix = "" if len(rows) <= 8 else f", and {len(rows) - 8} more"
            return prefix + " " + "; ".join(summaries) + suffix + "."

        summaries = []
        for row in rows[:8]:
            detail = f"item {row.get('order_item_id')} / schedule {row.get('schedule_line')}: {row.get('confirmed_delivery_date') or 'not confirmed'}"
            quantity = " ".join(part for part in [row.get("confirmed_order_quantity", ""), row.get("order_quantity_unit", "")] if part)
            if quantity:
                detail += f" ({quantity})"
            if row.get("product_id"):
                detail += f" for product {row.get('product_id')}"
            if row.get("production_plant"):
                detail += f" at plant {row.get('production_plant')}"
            summaries.append(detail)
        prefix = (
            f"For customer {customer_id} and sales order {order_id}, the schedule line details are:"
            if customer_id
            else f"For sales order {order_id}, the schedule line details are:"
        )
        suffix = "" if len(rows) <= 8 else f", and {len(rows) - 8} more"
        return prefix + " " + "; ".join(summaries) + suffix + "."


@dataclass
class EvidenceQueryPlan:
    cypher: str
    params: dict[str, Any]


@dataclass
class CypherQueryPlan:
    cypher: str
    params: dict[str, Any]
    render: Callable[[list[dict[str, Any]]], str]
    focus_entity_type: str | None = None
    focus_entity_id: str | None = None
    view_mode: str = "global"
    expand_focus: bool = False
    focus_depth: int = 0
    evidence_cypher: str | None = None
    evidence_params: dict[str, Any] | None = None
    evidence_builder: Callable[[list[dict[str, Any]]], EvidenceQueryPlan | None] | None = None
    reveal_evidence_nodes: bool = False
    reveal_evidence_limit: int = 25


class CypherChatError(RuntimeError):
    pass


class CypherChatEngine:
    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database
        self.timeout_seconds = timeout_seconds
        self._driver = None

    @classmethod
    def from_env(cls) -> CypherChatEngine | None:
        password = os.getenv("NEO4J_PASSWORD")
        if not password or GraphDatabase is None:
            return None
        uri = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
        username = os.getenv("NEO4J_USERNAME", os.getenv("NEO4J_USER", "neo4j"))
        database = os.getenv("NEO4J_DATABASE") or None
        timeout = float(os.getenv("NEO4J_TIMEOUT_SECONDS", "8"))
        return cls(uri=uri, username=username, password=password, database=database, timeout_seconds=timeout)

    def _get_driver(self):
        if GraphDatabase is None:
            raise CypherChatError(
                "Neo4j chat is configured, but the `neo4j` Python package is not installed. Run `pip install neo4j`."
            )
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
        return self._driver

    def execute(self, message: str) -> dict[str, Any]:
        with start_observation(
            name="cypher.execute",
            as_type="span",
            input={"message": message},
            metadata={"engine": "template"},
        ) as observation:
            with start_observation(
                name="cypher.plan",
                as_type="span",
                input={"message": message},
                metadata={"engine": "template"},
            ) as planning_observation:
                plan = self.plan_message(message)
                planning_observation.update(
                    output={
                        "planned": bool(plan),
                        "focusEntityType": plan.focus_entity_type if plan else None,
                        "viewMode": plan.view_mode if plan else "global",
                    }
                )
            if not plan:
                response = {
                    "reply": (
                        "I can translate supported order-to-cash questions into Cypher when I have a clear entity or count intent. "
                        "Try examples like `how many invoices`, `show invoice 900001`, `show deliveries for order 740506`, "
                        "`show journal entries for invoice 90504219`, or `how many lipbalm products`."
                    ),
                    "focusNodeId": None,
                    "revealNodeIds": [],
                    "evidenceNodeIds": [],
                    "viewMode": "global",
                    "expandFocus": False,
                    "focusDepth": 0,
                    "queryMode": "cypher",
                    "cypher": None,
                    "cypherParams": {},
                }
                observation.update(output=_observation_response_summary(response))
                return response

            try:
                driver = self._get_driver()
                with start_observation(
                    name="neo4j.query",
                    as_type="span",
                    input={"cypher": plan.cypher, "params": plan.params},
                    metadata={"engine": "template"},
                ) as query_observation:
                    with driver.session(database=self.database) as session:
                        result = session.run(Query(plan.cypher), plan.params)
                        records = result.data()
                    query_observation.update(output={"recordCount": len(records)})
            except Exception as exc:  # pragma: no cover - depends on runtime connection
                observation.update(output={"error": f"Neo4j query execution failed: {exc}"})
                raise CypherChatError(f"Neo4j query execution failed: {exc}") from exc

            focus_entity_type = plan.focus_entity_type
            focus_entity_id = plan.focus_entity_id
            if not focus_entity_type and records:
                first = records[0]
                if first.get("entity_type") and first.get("entity_id"):
                    focus_entity_type = first["entity_type"]
                    focus_entity_id = first["entity_id"]

            reveal_node_ids = self.build_reveal_node_ids(records, focus_entity_type, focus_entity_id)
            evidence_node_ids, revealed_evidence_node_ids = self.collect_plan_evidence_node_ids(
                driver,
                plan,
                records,
                reveal_node_ids,
            )
            reveal_node_ids = self.merge_node_ids(reveal_node_ids, revealed_evidence_node_ids)

            response = {
                "reply": plan.render(records),
                "focusNodeId": self.to_graph_node_id(focus_entity_type, focus_entity_id)
                if focus_entity_type and focus_entity_id
                else None,
                "revealNodeIds": reveal_node_ids,
                "evidenceNodeIds": evidence_node_ids,
                "viewMode": plan.view_mode,
                "expandFocus": plan.expand_focus,
                "focusDepth": plan.focus_depth,
                "queryMode": "cypher",
                "cypher": plan.cypher,
                "cypherParams": plan.params,
            }
            observation.update(
                output=_observation_response_summary(response),
                metadata={"recordCount": len(records)},
            )
            return response

    def build_reveal_node_ids(
        self,
        records: list[dict[str, Any]],
        focus_entity_type: str | None,
        focus_entity_id: str | None,
        limit: int = 8,
    ) -> list[str]:
        reveal_node_ids: list[str] = []
        if focus_entity_type and focus_entity_id:
            reveal_node_ids.append(self.to_graph_node_id(focus_entity_type, focus_entity_id))
        for record in records[:limit]:
            entity_type = record.get("entity_type")
            entity_id = record.get("entity_id")
            if entity_type and entity_id:
                reveal_node_ids.append(self.to_graph_node_id(str(entity_type), str(entity_id)))
        return self.merge_node_ids(reveal_node_ids)

    def merge_node_ids(self, *node_id_groups: list[str]) -> list[str]:
        merged: list[str] = []
        for node_ids in node_id_groups:
            for node_id in node_ids or []:
                if node_id and node_id not in merged:
                    merged.append(node_id)
        return merged

    def answer_query_can_double_as_evidence(self, plan: CypherQueryPlan) -> bool:
        cypher = plan.cypher or ""
        return bool(
            re.search(r"AS\s+entity_id", cypher, re.IGNORECASE)
            or re.search(r"__[A-Za-z_]*entity_id", cypher, re.IGNORECASE)
            or re.search(r"__[A-Za-z_]*entity_ids", cypher, re.IGNORECASE)
        )

    def resolve_evidence_plan(
        self,
        plan: CypherQueryPlan,
        records: list[dict[str, Any]],
    ) -> EvidenceQueryPlan | None:
        if plan.evidence_builder:
            try:
                return plan.evidence_builder(records)
            except Exception:
                return None
        if plan.evidence_cypher:
            return EvidenceQueryPlan(
                cypher=plan.evidence_cypher,
                params=dict(plan.evidence_params if plan.evidence_params is not None else plan.params),
            )
        if self.answer_query_can_double_as_evidence(plan):
            return EvidenceQueryPlan(
                cypher=plan.cypher,
                params=dict(plan.params),
            )
        return None

    def execute_evidence_plan(self, driver: Any, evidence_plan: EvidenceQueryPlan) -> list[dict[str, Any]]:
        with driver.session(database=self.database) as session:
            return session.run(Query(evidence_plan.cypher), evidence_plan.params).data()

    def collect_plan_evidence_node_ids(
        self,
        driver: Any,
        plan: CypherQueryPlan,
        records: list[dict[str, Any]],
        reveal_node_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        evidence_plan = self.resolve_evidence_plan(plan, records)
        if not evidence_plan:
            return CypherChatEngine.collect_evidence_node_ids(self, records, reveal_node_ids), []

        try:
            evidence_records = self.execute_evidence_plan(driver, evidence_plan)
        except Exception:
            return CypherChatEngine.collect_evidence_node_ids(self, records, reveal_node_ids), []

        evidence_node_ids = CypherChatEngine.collect_evidence_node_ids(self, evidence_records, reveal_node_ids)
        revealed_evidence_node_ids = (
            evidence_node_ids[: plan.reveal_evidence_limit] if plan.reveal_evidence_nodes else []
        )
        return evidence_node_ids, revealed_evidence_node_ids

    def plan_message(self, message: str) -> CypherQueryPlan | None:
        typed = self.extract_typed_identifier(message)
        typed_identifiers = self.extract_typed_identifiers(message)
        requested_fields = self.detect_requested_fields(message)
        detected_types = self.detect_entity_types(message)

        ranking_plan = self.plan_product_ranking_query(message)
        if ranking_plan:
            return ranking_plan

        if not typed:
            text_search_plan = self.plan_product_text_search_query(message)
            if text_search_plan:
                return text_search_plan

        if self.is_count_query(message):
            plan = self.plan_count_query(message)
            if plan:
                return plan

        if typed:
            source_type, entity_id = typed
            target_types = [entity_type for entity_type in detected_types if entity_type != source_type]
            if target_types:
                return self.plan_connection_query(source_type, entity_id, target_types[0])
            if requested_fields:
                return self.plan_field_query(source_type, entity_id, requested_fields)
            if any(keyword in message.lower() for keyword in CONNECTION_KEYWORDS):
                return self.plan_neighbor_query(source_type, entity_id)
            return self.plan_entity_query(source_type, entity_id)

        candidate = self.extract_candidate_identifier(message)
        if candidate:
            if requested_fields:
                return self.plan_generic_lookup(candidate, requested_fields=requested_fields)
            return self.plan_generic_lookup(candidate)

        return None

    def extract_product_ranking_intent(self, message: str) -> str | None:
        lower = " ".join(message.lower().split())
        patterns = (
            r"\bmost\s+(?:bought|purchased|ordered)\s+products?\b",
            r"\btop\s+(?:bought|purchased|ordered)\s+products?\b",
            r"\bbest\s+selling\s+products?\b",
            r"\bproducts?\s+(?:bought|purchased|ordered)\s+the\s+most\b",
            r"\bwhich\s+products?\s+(?:is|are)?\s*(?:the\s+)?(?:most\s+bought|most\s+purchased|most\s+ordered|best\s+selling)\b",
        )
        return "most_bought" if any(re.search(pattern, lower, re.IGNORECASE) for pattern in patterns) else None

    def plan_product_ranking_query(self, message: str) -> CypherQueryPlan | None:
        intent = self.extract_product_ranking_intent(message)
        if intent == "most_bought":
            return self.plan_most_bought_product_query()
        return None

    def plan_most_bought_product_query(self) -> CypherQueryPlan:
        cypher = (
            "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product:Product) "
            "WITH product, count(DISTINCT invoice) AS purchase_count "
            "ORDER BY purchase_count DESC, coalesce(product.product_description, product.product_id) ASC "
            "LIMIT 1 "
            "RETURN 'Product' AS entity_type, product.product_id AS entity_id, "
            "coalesce(product.product_description, product.product_id) AS label, properties(product) AS props, "
            "purchase_count"
        )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return "I could not determine the most bought product from the graph."
            top_record = records[0]
            purchase_count = top_record.get("purchase_count", 0)
            return (
                f"The most bought product is {top_record['label']} ({top_record['entity_id']}), "
                f"ordered {purchase_count} time(s)."
            )

        def build_evidence(records: list[dict[str, Any]]) -> EvidenceQueryPlan | None:
            if not records:
                return None
            product_id = records[0].get("entity_id")
            if product_id in (None, ""):
                return None
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (product:Product {product_id: $product_id}) "
                    "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product) "
                    "OPTIONAL MATCH (payment:Payment)-[:SETTLES]->(invoice) "
                    "RETURN product.product_id AS Product__entity_id, "
                    "invoice.invoice_id AS Invoice__entity_id, payment.payment_id AS Payment__entity_id "
                    "LIMIT 100"
                ),
                params={"product_id": product_id},
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={},
            render=render,
            view_mode="focus",
            expand_focus=True,
            focus_depth=2,
            evidence_builder=build_evidence,
            reveal_evidence_nodes=True,
            reveal_evidence_limit=40,
        )

    def plan_product_text_search_query(self, message: str) -> CypherQueryPlan | None:
        ordered_product_filters = self.extract_ordered_product_description_filters(message)
        if ordered_product_filters:
            plan = self.plan_ordered_product_query(message, ordered_product_filters)
            if plan:
                return plan

        invoice_product_filter = self.extract_invoice_product_description_filter(message)
        if invoice_product_filter:
            return self.plan_invoices_by_product_description_query(
                invoice_product_filter,
                count_only=self.is_count_query(message),
            )

        plant_product_filter = self.extract_plant_product_description_filter(message)
        if plant_product_filter:
            return self.plan_plants_by_product_description_query(
                plant_product_filter,
                count_only=self.is_count_query(message),
            )

        product_description_filter = self.extract_product_description_filter(message)
        if product_description_filter:
            return self.plan_product_description_query(
                product_description_filter,
                count_only=self.is_count_query(message),
            )

        return None

    def is_count_query(self, message: str) -> bool:
        lower = message.lower()
        return any(keyword in lower for keyword in COUNT_KEYWORDS)

    def detect_entity_types(self, message: str) -> list[str]:
        lower = message.lower()
        found: list[str] = []
        for keyword, entity_type in ENTITY_KEYWORDS.items():
            if re.search(rf"\b{re.escape(keyword)}\b", lower) and entity_type not in found:
                found.append(entity_type)
        return found

    def detect_requested_fields(self, message: str) -> set[str]:
        lower = message.lower()
        requested: set[str] = set()
        for keyword, fields in FIELD_KEYWORD_MAP.items():
            if keyword in lower:
                requested.update(fields)
        return requested

    def singularize_search_word(self, token: str) -> str:
        lower = token.lower()
        if len(lower) <= 3:
            return lower
        if lower.endswith("ies") and len(lower) > 4:
            return lower[:-3] + "y"
        if lower.endswith("s") and not lower.endswith("ss"):
            return lower[:-1]
        return lower

    def build_text_search_variants(self, candidate: str) -> list[str]:
        text = " ".join(candidate.strip().strip("'\"").lower().split())
        if not text:
            return []

        seeds: set[str] = {text, text.replace("-", " ")}
        for seed in list(seeds):
            tokens = [token for token in re.split(r"[\s\-_/]+", seed) if token]
            if tokens:
                singular_tokens = tokens[:-1] + [self.singularize_search_word(tokens[-1])]
                seeds.add(" ".join(singular_tokens))

        variants: set[str] = set()
        for seed in seeds:
            normalized = " ".join(seed.split()).strip(" ,.?;:!\t\n\r-_/")
            if len(normalized) < 2:
                continue
            variants.add(normalized)
            compact = re.sub(r"[\s\-_/]+", "", normalized)
            if len(compact) >= 2:
                variants.add(compact)
            hyphenated = re.sub(r"[\s_/]+", "-", normalized)
            if len(hyphenated) >= 2:
                variants.add(hyphenated)

        return sorted(variants, key=lambda value: (-len(value), value))

    def build_search_term_groups(self, search_terms: str | list[str]) -> list[list[str]]:
        if isinstance(search_terms, str):
            normalized_terms = [search_terms]
        else:
            normalized_terms = [term for term in search_terms if term]
        groups: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for term in normalized_terms:
            variants = self.build_text_search_variants(term)
            if not variants:
                continue
            key = tuple(variants)
            if key in seen:
                continue
            seen.add(key)
            groups.append(variants)
        return groups

    def format_search_terms(self, search_terms: str | list[str]) -> str:
        if isinstance(search_terms, str):
            return search_terms.strip()
        terms = [term.strip() for term in search_terms if term and term.strip()]
        if not terms:
            return ""
        if len(terms) == 1:
            return terms[0]
        if len(terms) == 2:
            return f"{terms[0]} and {terms[1]}"
        return ", ".join(terms[:-1]) + f", and {terms[-1]}"

    def split_ordered_product_filter_terms(self, candidate: str) -> list[str]:
        text = " ".join(candidate.strip().strip("'\"").split())
        if not text:
            return []
        parts = [part for part in re.split(r"\s+(?:and|&)\s+|\s*,\s*", text, flags=re.IGNORECASE) if part.strip()]
        normalized_terms: list[str] = []
        for part in parts or [text]:
            normalized = self.normalize_ordered_product_filter(part)
            if normalized and normalized not in normalized_terms:
                normalized_terms.append(normalized)
        return normalized_terms

    def extract_product_description_filter(self, message: str) -> str | None:
        lower = " ".join(message.lower().split())
        if "product" not in lower and "products" not in lower:
            return None

        quoted_match = re.search(r"[\"']([^\"']{2,80})[\"']", message)
        if quoted_match and any(keyword in lower for keyword in PRODUCT_TEXT_MATCH_KEYWORDS):
            return self.normalize_product_description_filter(quoted_match.group(1))

        patterns = [
            r"(?:product(?:\s+name|\s+description)?|name|description)\s+(?:contains|containing|including|includes|matching|matches|involving|like|named|called)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"(?:how many|count|counts|number of|show|list|find)(?:\s+me)?\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})\s+products?\b",
            r"products?\s+(?:with|where)\s+(?:product\s+)?(?:name|description)\s+(?:contains|containing|including|includes|matching|matches|involving|like|named|called)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"products?\s+(?:containing|matching|including|involving|like)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                candidate = re.split(r"\b(?:in the graph|in neo4j|please|thanks|thank you)\b", candidate, maxsplit=1)[0]
                normalized = self.normalize_product_description_filter(candidate)
                if normalized:
                    return normalized
        return None


    def normalize_product_description_filter(self, candidate: str) -> str | None:
        text = " ".join(candidate.strip().strip("'\"").split())
        if not text:
            return None
        tokens = [token for token in re.split(r"\s+", text) if token]
        while tokens and tokens[0].lower() in PRODUCT_FILTER_EDGE_STOPWORDS:
            tokens.pop(0)
        while tokens and tokens[-1].lower() in PRODUCT_FILTER_EDGE_STOPWORDS:
            tokens.pop()
        normalized = " ".join(tokens).strip(" ,.?;:!\t\n\r")
        if len(normalized) < 2:
            return None
        return normalized

    def extract_plant_product_description_filter(self, message: str) -> str | None:
        lower = " ".join(message.lower().split())
        if not re.search(r"\bplants?\b", lower):
            return None

        quoted_match = re.search(r"[\"']([^\"']{2,80})[\"']", message)
        if quoted_match:
            normalized = self.normalize_product_description_filter(quoted_match.group(1))
            if normalized:
                return normalized

        patterns = [
            r"(?:how many|count|counts|number of|show|list|find|which|what)\s+plants?\s+(?:have|has|with|for|carrying|stocking|stock|carry)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"plants?\s+(?:for|with|having|that have|carrying|stocking|stock|carry)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"plant\s+names?\s+for\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"(?:plant|plants)\s+names?\s+of\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                candidate = re.split(r"\b(?:in the graph|in neo4j|please|thanks|thank you|available|availability)\b", candidate, maxsplit=1)[0]
                normalized = self.normalize_product_description_filter(candidate)
                if normalized:
                    return normalized
        return None

    def extract_invoice_product_description_filter(self, message: str) -> str | None:
        lower = " ".join(message.lower().split())
        if not re.search(r"\binvoices?\b", lower):
            return None

        quoted_match = re.search(r"[\"']([^\"']{2,80})[\"']", message)
        if quoted_match:
            normalized = self.normalize_invoice_product_description_filter(quoted_match.group(1))
            if normalized:
                return normalized

        patterns = [
            r"(?:show|list|find|which|what|give|display)?\s*invoices?\s+(?:ids?\s+)?(?:showing|for|with|containing|including|involving|matching)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"invoice\s+ids?\s+(?:showing|for|with|containing|including|involving|matching)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"invoices?\s+for\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})\s+(?:purchase|purchases|product|products)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                candidate = re.split(
                    r"\b(?:in the graph|in neo4j|please|thanks|thank you)\b",
                    candidate,
                    maxsplit=1,
                )[0]
                normalized = self.normalize_invoice_product_description_filter(candidate)
                if normalized:
                    return normalized

        if re.search(r"\b(?:purchase|purchases|product|products|billed|billing)\b", lower):
            heuristic_candidate = re.sub(r"^\s*(?:show|list|find|which|what|give|display)\s+", "", lower)
            heuristic_candidate = re.sub(r"^\s*invoices?\s+(?:ids?\s+)?", "", heuristic_candidate)
            normalized = self.normalize_invoice_product_description_filter(heuristic_candidate)
            if normalized:
                return normalized

        return None

    def extract_ordered_product_description_filters(self, message: str) -> list[str]:
        lower = " ".join(message.lower().split())
        if not any(re.search(rf"\b{keyword}\b", lower) for keyword in ORDERED_PRODUCT_KEYWORDS):
            return []

        quoted_matches = re.findall(r"[\"']([^\"']{2,80})[\"']", message)
        if quoted_matches:
            quoted_terms: list[str] = []
            for quoted_match in quoted_matches:
                for term in self.split_ordered_product_filter_terms(quoted_match):
                    if term not in quoted_terms:
                        quoted_terms.append(term)
            if quoted_terms:
                return quoted_terms

        patterns = [
            r"(?:customers?|orders?)\s+(?:that|who)?\s*(?:ordered|bought|purchased)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
            r"(?:ordered|bought|purchased)\s+([a-z0-9][a-z0-9 &'\-+/]{1,80})",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower, re.IGNORECASE)
            if match:
                candidate = match.group(1)
                candidate = re.split(
                    r"(?:in the graph|in neo4j|please|thanks|thank you|by customers?|for customers?)",
                    candidate,
                    maxsplit=1,
                )[0]
                return self.split_ordered_product_filter_terms(candidate)
        return []

    def extract_ordered_product_description_filter(self, message: str) -> str | None:
        filters = self.extract_ordered_product_description_filters(message)
        return filters[0] if filters else None

    def normalize_ordered_product_filter(self, candidate: str) -> str | None:
        text = " ".join(candidate.strip().strip("'\"").split())
        if not text:
            return None
        tokens = [token for token in re.split(r"\s+", text) if token]
        while tokens and tokens[0].lower() in ORDERED_PRODUCT_EDGE_STOPWORDS:
            tokens.pop(0)
        while tokens and tokens[-1].lower() in ORDERED_PRODUCT_EDGE_STOPWORDS:
            tokens.pop()
        normalized = " ".join(tokens).strip(" ,.?;:!\t\n\r")
        if len(normalized) < 2:
            return None
        return normalized

    def normalize_invoice_product_description_filter(self, candidate: str) -> str | None:
        text = " ".join(candidate.strip().strip("'\"").split())
        if not text:
            return None
        tokens = [token for token in re.split(r"\s+", text) if token]
        while tokens and tokens[0].lower() in INVOICE_PRODUCT_EDGE_STOPWORDS:
            tokens.pop(0)
        while tokens and tokens[-1].lower() in INVOICE_PRODUCT_EDGE_STOPWORDS:
            tokens.pop()
        normalized = " ".join(tokens).strip(" ,.?;:!\t\n\r")
        if len(normalized) < 2:
            return None
        return normalized

    def extract_typed_identifier(self, message: str) -> tuple[str, str] | None:
        lower = message.lower()
        for keyword, entity_type in sorted(ENTITY_KEYWORDS.items(), key=lambda item: len(item[0]), reverse=True):
            pattern = re.compile(rf"\b{re.escape(keyword)}\s+(?:id\s+)?([A-Za-z0-9:_|-]+)\b", re.IGNORECASE)
            match = pattern.search(lower)
            if match:
                candidate = match.group(1)
                if self.looks_like_identifier(candidate):
                    return entity_type, candidate
        return None

    def extract_typed_identifiers(self, message: str) -> dict[str, list[str]]:
        lower = message.lower()
        found: dict[str, list[str]] = {}
        for keyword, entity_type in sorted(ENTITY_KEYWORDS.items(), key=lambda item: len(item[0]), reverse=True):
            pattern = re.compile(rf"\b{re.escape(keyword)}\s+(?:id\s+)?([A-Za-z0-9:_|-]+)\b", re.IGNORECASE)
            for match in pattern.finditer(lower):
                candidate = match.group(1)
                if not self.looks_like_identifier(candidate):
                    continue
                found.setdefault(entity_type, [])
                if candidate not in found[entity_type]:
                    found[entity_type].append(candidate)
        return found

    def extract_candidate_identifier(self, message: str) -> str | None:
        for token in TOKEN_PATTERN.findall(message):
            if self.looks_like_identifier(token):
                return token
        return None

    def looks_like_identifier(self, token: str) -> bool:
        lower = token.lower()
        if lower in NON_IDENTIFIER_TOKENS:
            return False
        return any(character.isdigit() for character in token) or any(character in token for character in ":|-")

    def to_graph_node_id(self, entity_type: str, entity_id: str) -> str:
        return f"{entity_type}:{entity_id}"

    def collect_evidence_node_ids(self, records: list[dict[str, Any]], reveal_node_ids: list[str]) -> list[str]:
        evidence_node_ids = list(reveal_node_ids)
        for record in records[:25]:
            for key, value in record.items():
                if key.endswith("__entity_id") and value not in (None, ""):
                    entity_type = key[: -len("__entity_id")]
                    if entity_type in ENTITY_CONFIG:
                        evidence_node_ids.append(self.to_graph_node_id(entity_type, str(value)))
                    continue
                if key.endswith("__entity_ids") and isinstance(value, list):
                    entity_type = key[: -len("__entity_ids")]
                    if entity_type not in ENTITY_CONFIG:
                        continue
                    for item in value:
                        if item in (None, ""):
                            continue
                        evidence_node_ids.append(self.to_graph_node_id(entity_type, str(item)))
        return [
            node_id
            for index, node_id in enumerate(evidence_node_ids)
            if node_id and node_id not in evidence_node_ids[:index]
        ]

    def plan_ordered_product_query(self, message: str, search_terms: str | list[str]) -> CypherQueryPlan | None:
        lower = message.lower()
        count_only = self.is_count_query(message)
        if re.search(r"\bcustomers?\b", lower):
            return self.plan_customers_by_ordered_product_query(search_terms, count_only=count_only)
        if re.search(r"\borders?\b", lower):
            return self.plan_orders_by_ordered_product_query(search_terms, count_only=count_only)
        return None

    def plan_schedule_line_request(
        self,
        message: str,
        typed_identifiers: dict[str, list[str]],
        requested_fields: set[str],
    ) -> CypherQueryPlan | None:
        lower = message.lower()
        schedule_intent = bool({"confirmed_delivery_date", "confirmed_order_quantity", "schedule_line", "schedule_line_id"} & requested_fields) or "schedule line" in lower or "schedule lines" in lower
        order_ids = typed_identifiers.get("Order", [])
        customer_ids = typed_identifiers.get("Customer", [])
        if not schedule_intent or not order_ids:
            return None

        order_id = order_ids[0]
        customer_id = customer_ids[0] if customer_ids else None
        params: dict[str, Any] = {"order_id": order_id}
        if customer_id:
            params["customer_id"] = customer_id
            cypher = (
                "MATCH (customer_node:Customer {customer_id: $customer_id})-[:PLACED]->(order_node:Order {order_id: $order_id}) "
                "MATCH (order_node)-[:HAS_SALES_ORDER_ITEM]->(item_node:SalesOrderItem)-[:HAS_SCHEDULE_LINE]->(schedule_node:SalesOrderScheduleLine) "
                "RETURN 'SalesOrderScheduleLine' AS entity_type, schedule_node.schedule_line_id AS entity_id, "
                "coalesce(schedule_node.confirmed_delivery_date, schedule_node.schedule_line_id) AS label, properties(schedule_node) AS props, "
                "item_node.sales_order_item_id AS SalesOrderItem__entity_id, order_node.order_id AS Order__entity_id, customer_node.customer_id AS Customer__entity_id "
                "ORDER BY item_node.order_item_id, schedule_node.schedule_line LIMIT 25"
            )
        else:
            cypher = (
                "MATCH (order_node:Order {order_id: $order_id})-[:HAS_SALES_ORDER_ITEM]->(item_node:SalesOrderItem)-[:HAS_SCHEDULE_LINE]->(schedule_node:SalesOrderScheduleLine) "
                "RETURN 'SalesOrderScheduleLine' AS entity_type, schedule_node.schedule_line_id AS entity_id, "
                "coalesce(schedule_node.confirmed_delivery_date, schedule_node.schedule_line_id) AS label, properties(schedule_node) AS props, "
                "item_node.sales_order_item_id AS SalesOrderItem__entity_id, order_node.order_id AS Order__entity_id "
                "ORDER BY item_node.order_item_id, schedule_node.schedule_line LIMIT 25"
            )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                if customer_id:
                    return f"I could not find any schedule lines for customer {customer_id} and sales order {order_id}."
                return f"I could not find any schedule lines for sales order {order_id}."
            summaries = []
            for record in records[:8]:
                props = record.get("props", {}) or {}
                item_id = props.get("order_item_id")
                schedule_line = props.get("schedule_line")
                confirmed_date = props.get("confirmed_delivery_date") or "not confirmed"
                confirmed_qty = props.get("confirmed_order_quantity")
                unit = props.get("order_quantity_unit")
                quantity_text = " ".join(part for part in [str(confirmed_qty or ""), str(unit or "")] if part)
                detail = f"Item {item_id} / Schedule {schedule_line}: {confirmed_date}"
                if quantity_text:
                    detail += f" ({quantity_text})"
                summaries.append(detail)
            suffix = "" if len(records) <= 8 else f", and {len(records) - 8} more"
            if customer_id:
                return f"For customer {customer_id} and sales order {order_id}, the schedule line confirmations are: " + "; ".join(summaries) + suffix + "."
            return f"For sales order {order_id}, the schedule line confirmations are: " + "; ".join(summaries) + suffix + "."

        return CypherQueryPlan(
            cypher=cypher,
            params=params,
            render=render,
            focus_entity_type="Order",
            focus_entity_id=order_id,
            view_mode="focus",
            expand_focus=True,
            focus_depth=2,
        )

    def plan_count_query(self, message: str) -> CypherQueryPlan | None:
        entity_types = self.detect_entity_types(message)
        if not entity_types:
            return None
        parts = []
        for entity_type in entity_types:
            label = ENTITY_CONFIG[entity_type]["label"]
            parts.append(f"MATCH (n:{label}) RETURN '{entity_type}' AS entity_type, count(n) AS total")
        cypher = " UNION ALL ".join(parts)

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return "I could not retrieve counts from Neo4j."
            summary = " | ".join(f"{record['entity_type']}: {record['total']}" for record in records)
            return f"Current graph counts -> {summary}."

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan | None:
            sample_types = [entity_type for entity_type in entity_types if entity_type in ENTITY_CONFIG]
            if not sample_types:
                return None
            projections = [f"null AS {entity_type}__entity_id" for entity_type in sample_types]
            branches: list[str] = []
            for entity_type in sample_types:
                config = ENTITY_CONFIG[entity_type]
                branch_projections = projections.copy()
                branch_index = sample_types.index(entity_type)
                branch_projections[branch_index] = f"n.{config['id_property']} AS {entity_type}__entity_id"
                branches.append(
                    f"MATCH (n:{config['label']}) RETURN {', '.join(branch_projections)} LIMIT 25"
                )
            return EvidenceQueryPlan(
                cypher=" UNION ALL ".join(branches),
                params={},
            )

        return CypherQueryPlan(cypher=cypher, params={}, render=render, evidence_builder=build_evidence)

    def plan_customers_by_ordered_product_query(self, search_terms: str | list[str], count_only: bool) -> CypherQueryPlan:
        search_label = self.format_search_terms(search_terms)
        search_term_groups = self.build_search_term_groups(search_terms)
        evidence_search_terms = sorted({term for group in search_term_groups for term in group})

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan:
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (customer:Customer)-[:PLACED]->(order:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
                    "WITH customer, collect(DISTINCT order) AS orders, collect(DISTINCT product) AS products "
                    "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
                    "UNWIND orders AS order "
                    "MATCH (order)-[:CONTAINS_PRODUCT]->(product:Product) "
                    "WHERE any(search_term IN $evidence_search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                    "RETURN DISTINCT customer.customer_id AS Customer__entity_id, order.order_id AS Order__entity_id, product.product_id AS Product__entity_id "
                    "LIMIT 100"
                ),
                params={
                    "search_term_groups": search_term_groups,
                    "evidence_search_terms": evidence_search_terms,
                },
            )

        if count_only:
            cypher = (
                "MATCH (customer:Customer)-[:PLACED]->(:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
                "WITH customer, collect(DISTINCT product) AS products "
                "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
                "RETURN 'Customer' AS entity_type, count(customer) AS total"
            )

            def render(records: list[dict[str, Any]]) -> str:
                total = records[0]["total"] if records else 0
                return f'I found {total} customer node(s) that ordered products matching "{search_label}".'

            return CypherQueryPlan(
                cypher=cypher,
                params={"search_term_groups": search_term_groups},
                render=render,
                view_mode="global",
                focus_depth=0,
                evidence_builder=build_evidence,
            )

        cypher = (
            "MATCH (customer:Customer)-[:PLACED]->(order:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
            "WITH customer, collect(DISTINCT order) AS orders, collect(DISTINCT product) AS products "
            "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
            "WITH customer, orders[0] AS sample_order, products[0] AS sample_product "
            "WITH collect({customer: customer, order: sample_order, product: sample_product}) AS customer_matches "
            "UNWIND customer_matches[0..25] AS row "
            "RETURN 'Customer' AS entity_type, row.customer.customer_id AS entity_id, "
            "coalesce(row.customer.full_name, row.customer.customer_id) AS label, properties(row.customer) AS props, "
            "size(customer_matches) AS total, row.order.order_id AS Order__entity_id, "
            "row.product.product_id AS Product__entity_id"
        )

        def render(records: list[dict[str, Any]]) -> str:
            total = records[0]["total"] if records else 0
            if not records:
                return f'I could not find any customer nodes that ordered products matching "{search_label}".'
            preview = ", ".join(f"{record['label']} ({record['entity_id']})" for record in records[:5])
            suffix = "" if total <= 5 else f", and {total - 5} more"
            return (
                f'I found {total} customer node(s) that ordered products matching "{search_label}": '
                f"{preview}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"search_term_groups": search_term_groups},
            render=render,
            view_mode="global",
            focus_depth=0,
            evidence_builder=build_evidence,
        )

    def plan_orders_by_ordered_product_query(self, search_terms: str | list[str], count_only: bool) -> CypherQueryPlan:
        search_label = self.format_search_terms(search_terms)
        search_term_groups = self.build_search_term_groups(search_terms)
        evidence_search_terms = sorted({term for group in search_term_groups for term in group})

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan:
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (customer:Customer)-[:PLACED]->(order:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
                    "WITH order, collect(DISTINCT customer) AS customers, collect(DISTINCT product) AS products "
                    "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
                    "UNWIND customers AS customer "
                    "UNWIND products AS product "
                    "WITH customer, order, product "
                    "WHERE any(search_term IN $evidence_search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                    "RETURN DISTINCT customer.customer_id AS Customer__entity_id, order.order_id AS Order__entity_id, product.product_id AS Product__entity_id "
                    "LIMIT 100"
                ),
                params={
                    "search_term_groups": search_term_groups,
                    "evidence_search_terms": evidence_search_terms,
                },
            )

        if count_only:
            cypher = (
                "MATCH (:Customer)-[:PLACED]->(order:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
                "WITH order, collect(DISTINCT product) AS products "
                "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
                "RETURN 'Order' AS entity_type, count(order) AS total"
            )

            def render(records: list[dict[str, Any]]) -> str:
                total = records[0]["total"] if records else 0
                return f'I found {total} order node(s) containing products matching "{search_label}".'

            return CypherQueryPlan(
                cypher=cypher,
                params={"search_term_groups": search_term_groups},
                render=render,
                view_mode="global",
                focus_depth=0,
                evidence_builder=build_evidence,
            )

        cypher = (
            "MATCH (customer:Customer)-[:PLACED]->(order:Order)-[:CONTAINS_PRODUCT]->(product:Product) "
            "WITH order, collect(DISTINCT customer) AS customers, collect(DISTINCT product) AS products "
            "WHERE all(term_group IN $search_term_groups WHERE any(product_node IN products WHERE any(search_term IN term_group WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS search_term))) "
            "WITH order, customers[0] AS sample_customer, products[0] AS sample_product "
            "WITH collect({order: order, customer: sample_customer, product: sample_product}) AS order_matches "
            "UNWIND order_matches[0..25] AS row "
            "RETURN 'Order' AS entity_type, row.order.order_id AS entity_id, "
            "coalesce(row.order.order_id) AS label, properties(row.order) AS props, "
            "size(order_matches) AS total, row.customer.customer_id AS Customer__entity_id, "
            "row.product.product_id AS Product__entity_id"
        )

        def render(records: list[dict[str, Any]]) -> str:
            total = records[0]["total"] if records else 0
            if not records:
                return f'I could not find any order nodes containing products matching "{search_label}".'
            preview = ", ".join(str(record["entity_id"]) for record in records[:5])
            suffix = "" if total <= 5 else f", and {total - 5} more"
            return (
                f'I found {total} order node(s) containing products matching "{search_label}": '
                f"{preview}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"search_term_groups": search_term_groups},
            render=render,
            view_mode="global",
            focus_depth=0,
            evidence_builder=build_evidence,
        )

    def plan_plants_by_product_description_query(self, search_term: str, count_only: bool) -> CypherQueryPlan:
        normalized_search = search_term.strip()
        search_terms = self.build_text_search_variants(normalized_search)

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan:
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (product:Product)-[:AVAILABLE_AT_PLANT]->(plant:Plant) "
                    "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                    "RETURN DISTINCT plant.plant_id AS Plant__entity_id, product.product_id AS Product__entity_id "
                    "LIMIT 100"
                ),
                params={"search_terms": search_terms},
            )

        if count_only:
            cypher = (
                "MATCH (product:Product)-[:AVAILABLE_AT_PLANT]->(plant:Plant) "
                "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                "RETURN 'Plant' AS entity_type, count(DISTINCT plant) AS total"
            )

            def render(records: list[dict[str, Any]]) -> str:
                total = records[0]["total"] if records else 0
                return f'I found {total} plant node(s) connected to products matching "{normalized_search}".'

            return CypherQueryPlan(
                cypher=cypher,
                params={"search_terms": search_terms},
                render=render,
                view_mode="global",
                focus_depth=0,
                evidence_builder=build_evidence,
            )

        cypher = (
            "MATCH (product:Product)-[rel:AVAILABLE_AT_PLANT]->(plant:Plant) "
            "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
            "WITH plant, collect(DISTINCT product)[0] AS sample_product, collect(DISTINCT rel)[0] AS sample_rel "
            "WITH collect({plant: plant, product: sample_product, rel: sample_rel}) AS plant_matches "
            "UNWIND plant_matches[0..25] AS row "
            "RETURN 'Plant' AS entity_type, row.plant.plant_id AS entity_id, "
            "coalesce(row.plant.plant_name, row.plant.plant_id) AS label, properties(row.plant) AS props, "
            "properties(row.rel) AS rel_props, size(plant_matches) AS total, row.product.product_id AS Product__entity_id"
        )

        def render(records: list[dict[str, Any]]) -> str:
            total = records[0]["total"] if records else 0
            if not records:
                return f'I could not find any plant nodes connected to products matching "{normalized_search}".'
            preview = []
            for record in records[:5]:
                rel_props = record.get("rel_props", {}) or {}
                storage_count = rel_props.get("storage_location_count")
                if storage_count:
                    preview.append(f"{record['label']} ({storage_count} storage locations)")
                else:
                    preview.append(str(record["label"]))
            suffix = "" if total <= 5 else f", and {total - 5} more"
            return (
                f'I found {total} plant node(s) connected to products matching "{normalized_search}": '
                f"{', '.join(preview)}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"search_terms": search_terms},
            render=render,
            view_mode="global",
            focus_depth=0,
            evidence_builder=build_evidence,
        )

    def plan_invoices_by_product_description_query(self, search_term: str, count_only: bool) -> CypherQueryPlan:
        normalized_search = search_term.strip()
        search_terms = self.build_text_search_variants(normalized_search)

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan:
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product:Product) "
                    "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                    "OPTIONAL MATCH (payment:Payment)-[:SETTLES]->(invoice) "
                    "RETURN DISTINCT invoice.invoice_id AS Invoice__entity_id, product.product_id AS Product__entity_id, payment.payment_id AS Payment__entity_id "
                    "LIMIT 100"
                ),
                params={"search_terms": search_terms},
            )

        if count_only:
            cypher = (
                "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product:Product) "
                "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                "RETURN 'Invoice' AS entity_type, count(DISTINCT invoice) AS total"
            )

            def render(records: list[dict[str, Any]]) -> str:
                total = records[0]["total"] if records else 0
                return f'I found {total} invoice node(s) connected to products matching "{normalized_search}".'

            return CypherQueryPlan(
                cypher=cypher,
                params={"search_terms": search_terms},
                render=render,
                view_mode="global",
                focus_depth=0,
                evidence_builder=build_evidence,
            )

        cypher = (
            "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product:Product) "
            "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
            "WITH invoice, collect(DISTINCT product)[0] AS sample_product "
            "WITH collect({invoice: invoice, product: sample_product}) AS invoice_matches "
            "UNWIND invoice_matches[0..25] AS row "
            "RETURN 'Invoice' AS entity_type, row.invoice.invoice_id AS entity_id, "
            "coalesce(row.invoice.invoice_id) AS label, properties(row.invoice) AS props, "
            "size(invoice_matches) AS total, row.product.product_id AS Product__entity_id"
        )

        def render(records: list[dict[str, Any]]) -> str:
            total = records[0]["total"] if records else 0
            if not records:
                return f'I could not find any invoice nodes connected to products matching "{normalized_search}".'
            preview = ", ".join(str(record["label"]) for record in records[:5])
            suffix = "" if total <= 5 else f", and {total - 5} more"
            return (
                f'I found {total} invoice node(s) connected to products matching "{normalized_search}": '
                f"{preview}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"search_terms": search_terms},
            render=render,
            view_mode="global",
            focus_depth=0,
            evidence_builder=build_evidence,
        )

    def plan_product_description_query(self, search_term: str, count_only: bool) -> CypherQueryPlan:
        normalized_search = search_term.strip()
        search_terms = self.build_text_search_variants(normalized_search)

        def build_evidence(_records: list[dict[str, Any]]) -> EvidenceQueryPlan:
            return EvidenceQueryPlan(
                cypher=(
                    "MATCH (product:Product) "
                    "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(product.product_description, '')) CONTAINS search_term) "
                    "RETURN DISTINCT product.product_id AS Product__entity_id "
                    "LIMIT 100"
                ),
                params={"search_terms": search_terms},
            )

        if count_only:
            cypher = (
                "MATCH (n:Product) "
                "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(n.product_description, '')) CONTAINS search_term) "
                "RETURN 'Product' AS entity_type, count(n) AS total"
            )

            def render(records: list[dict[str, Any]]) -> str:
                total = records[0]["total"] if records else 0
                return (
                    f'I found {total} product node(s) with product descriptions containing "{normalized_search}".'
                )

            return CypherQueryPlan(
                cypher=cypher,
                params={"search_terms": search_terms},
                render=render,
                view_mode="global",
                focus_depth=0,
                evidence_builder=build_evidence,
            )

        cypher = (
            "MATCH (n:Product) "
            "WHERE any(search_term IN $search_terms WHERE toLower(coalesce(n.product_description, '')) CONTAINS search_term) "
            "RETURN 'Product' AS entity_type, n.product_id AS entity_id, "
            "coalesce(n.product_description, n.product_id) AS label, properties(n) AS props "
            "ORDER BY label LIMIT 10"
        )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f'I could not find any product nodes with descriptions containing "{normalized_search}".'
            preview = ", ".join(f"{record['label']} ({record['entity_id']})" for record in records[:5])
            suffix = "" if len(records) <= 5 else f", and {len(records) - 5} more"
            return (
                f'I found {len(records)} product node(s) with descriptions containing "{normalized_search}": '
                f"{preview}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"search_terms": search_terms},
            render=render,
            view_mode="global",
            focus_depth=0,
        )

    def plan_entity_query(self, entity_type: str, entity_id: str) -> CypherQueryPlan:
        config = ENTITY_CONFIG[entity_type]
        cypher = (
            f"MATCH (n:{config['label']} {{{config['id_property']}: $entity_id}}) "
            f"RETURN '{entity_type}' AS entity_type, n.{config['id_property']} AS entity_id, "
            f"{entity_label_expr('n')} AS label, properties(n) AS props LIMIT 1"
        )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f"I could not find {config['title']} {entity_id} in Neo4j."
            record = records[0]
            props = record.get("props", {})
            parts = []
            for field in FIELD_PRIORITY.get(entity_type, []):
                value = props.get(field)
                if value:
                    parts.append(f"{field.replace('_', ' ')}: {value}")
                if len(parts) == 4:
                    break
            if not parts:
                parts.append(f"entity id: {record['entity_id']}")
            return f"{record['label']} is a {entity_type.lower()} node. " + " | ".join(parts) + "."

        return CypherQueryPlan(
            cypher=cypher,
            params={"entity_id": entity_id},
            render=render,
            focus_entity_type=entity_type,
            focus_entity_id=entity_id,
            view_mode="focus",
            focus_depth=1,
        )

    def plan_field_query(self, entity_type: str, entity_id: str, requested_fields: set[str]) -> CypherQueryPlan:
        journal_plan = self.plan_related_journal_field_query(entity_type, entity_id, requested_fields)
        if journal_plan is not None:
            return journal_plan

        base_plan = self.plan_entity_query(entity_type, entity_id)

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f"I could not find {ENTITY_CONFIG[entity_type]['title']} {entity_id} in Neo4j."
            props = records[0].get("props", {})
            lines = []
            for field in sorted(requested_fields):
                value = props.get(field)
                if value:
                    lines.append(f"{field.replace('_', ' ')}: {value}")
            if not lines:
                return f"I found {entity_type.lower()} {entity_id}, but none of the requested fields were available."
            return f"For {records[0]['label']}, I found " + " | ".join(lines) + "."

        base_plan.render = render
        return base_plan

    def plan_related_journal_field_query(
        self,
        entity_type: str,
        entity_id: str,
        requested_fields: set[str],
    ) -> CypherQueryPlan | None:
        journal_fields = requested_fields & JOURNAL_ENTRY_RELATED_FIELDS
        if not journal_fields or entity_type == "JournalEntryItem":
            return None

        source = ENTITY_CONFIG[entity_type]
        focus_depth = PROCESS_DEPTH_MAP.get((entity_type, "JournalEntryItem"), 1)
        journal_label_expr = "coalesce(journal_node.journal_entry_item_id)"

        if entity_type == "Invoice":
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:ACCOUNTED_AS]->(journal_node:JournalEntryItem) "
                f"RETURN DISTINCT 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, "
                f"{journal_label_expr} AS label, properties(journal_node) AS props ORDER BY label LIMIT 25"
            )
        elif entity_type == "Payment":
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:CLEARS_JOURNAL_ENTRY_ITEM]->(journal_node:JournalEntryItem) "
                f"RETURN DISTINCT 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, "
                f"{journal_label_expr} AS label, properties(journal_node) AS props ORDER BY label LIMIT 25"
            )
        elif entity_type == "Customer":
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:HAS_JOURNAL_ENTRY_ITEM]->(journal_node:JournalEntryItem) "
                f"RETURN DISTINCT 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, "
                f"{journal_label_expr} AS label, properties(journal_node) AS props ORDER BY label LIMIT 25"
            )
        elif entity_type == "Order":
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:FULFILLED_BY]->(:Delivery)-[:INVOICED_AS]->(:Invoice)-[:ACCOUNTED_AS]->(journal_node:JournalEntryItem) "
                f"RETURN DISTINCT 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, "
                f"{journal_label_expr} AS label, properties(journal_node) AS props ORDER BY label LIMIT 25"
            )
        elif entity_type == "Delivery":
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:INVOICED_AS]->(:Invoice)-[:ACCOUNTED_AS]->(journal_node:JournalEntryItem) "
                f"RETURN DISTINCT 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, "
                f"{journal_label_expr} AS label, properties(journal_node) AS props ORDER BY label LIMIT 25"
            )
        else:
            return None

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return (
                    f"I could not find any related journal entry items for {source['title']} {entity_id}."
                )

            if len(records) == 1:
                props = records[0].get("props", {})
                lines = []
                for field in sorted(journal_fields):
                    value = props.get(field)
                    if value:
                        lines.append(f"{field.replace('_', ' ')}: {value}")
                if lines:
                    return f"For {source['title']} {entity_id}, I found " + " | ".join(lines) + "."
                return (
                    f"I found a related journal entry item for {source['title']} {entity_id}, but none of the requested fields were available."
                )

            summaries = []
            for record in records[:5]:
                props = record.get("props", {})
                field_parts = []
                for field in sorted(journal_fields):
                    value = props.get(field)
                    if value:
                        field_parts.append(f"{field.replace('_', ' ')}: {value}")
                if field_parts:
                    summaries.append(f"{record['entity_id']} ({'; '.join(field_parts)})")
                else:
                    summaries.append(str(record["entity_id"]))
            suffix = "" if len(records) <= 5 else f", and {len(records) - 5} more"
            return (
                f"For {source['title']} {entity_id}, related journal entry items include "
                + ", ".join(summaries)
                + suffix
                + "."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"entity_id": entity_id},
            render=render,
            focus_entity_type=entity_type,
            focus_entity_id=entity_id,
            view_mode="focus",
            expand_focus=True,
            focus_depth=focus_depth,
        )


    def plan_connection_query(self, source_type: str, entity_id: str, target_type: str) -> CypherQueryPlan:
        source = ENTITY_CONFIG[source_type]
        target = ENTITY_CONFIG[target_type]
        focus_depth = PROCESS_DEPTH_MAP.get((source_type, target_type), 2)
        target_label_expr = f"coalesce(target.{target['id_property']}, {entity_label_expr('target')})"

        if (source_type, target_type) == ("Order", "SalesOrderItem"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:HAS_SALES_ORDER_ITEM]->(target:SalesOrderItem) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props ORDER BY label LIMIT 25"
            )
        elif (source_type, target_type) == ("Order", "SalesOrderScheduleLine"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:HAS_SALES_ORDER_ITEM]->(item_node:SalesOrderItem)-[:HAS_SCHEDULE_LINE]->(target:SalesOrderScheduleLine) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"coalesce(target.confirmed_delivery_date, target.{target['id_property']}) AS label, properties(target) AS props, item_node.sales_order_item_id AS SalesOrderItem__entity_id ORDER BY label LIMIT 25"
            )
        elif (source_type, target_type) == ("SalesOrderItem", "SalesOrderScheduleLine"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:HAS_SCHEDULE_LINE]->(target:SalesOrderScheduleLine) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"coalesce(target.confirmed_delivery_date, target.{target['id_property']}) AS label, properties(target) AS props ORDER BY label LIMIT 25"
            )
        elif (source_type, target_type) == ("Order", "Invoice"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:FULFILLED_BY]->(delivery:Delivery)-[:INVOICED_AS]->(target:Invoice) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Order", "Payment"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:FULFILLED_BY]->(delivery:Delivery)-[:INVOICED_AS]->(invoice:Invoice)<-[:SETTLES]-(target:Payment) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Customer", "Invoice"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:RECEIVED_INVOICE]->(target:Invoice) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Customer", "Payment"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:MADE_PAYMENT]->(target:Payment) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Invoice", "Payment"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})<-[:SETTLES]-(target:Payment) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Product", "Plant"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[rel:AVAILABLE_AT_PLANT]->(target:Plant) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, properties(rel) AS rel_props "
                f"ORDER BY label LIMIT 25"
            )
        elif (source_type, target_type) == ("Plant", "Product"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})<-[rel:AVAILABLE_AT_PLANT]-(target:Product) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, properties(rel) AS rel_props "
                f"ORDER BY label LIMIT 25"
            )
        elif (source_type, target_type) == ("Payment", "Invoice"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:SETTLES]->(target:Invoice) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Payment", "Order"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:SETTLES]->(invoice:Invoice)<-[:INVOICED_AS]-(delivery:Delivery)<-[:FULFILLED_BY]-(target:Order) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Delivery", "Payment"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:INVOICED_AS]->(invoice:Invoice)<-[:SETTLES]-(target:Payment) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Invoice", "Order"):
            cypher = (
                f"MATCH (target:Order)-[:FULFILLED_BY]->(delivery:Delivery)-[:INVOICED_AS]->(start:{source['label']} {{{source['id_property']}: $entity_id}}) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Order", "JournalEntryItem"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:FULFILLED_BY]->(delivery:Delivery)-[:INVOICED_AS]->(invoice:Invoice)-[:ACCOUNTED_AS]->(target:JournalEntryItem) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("Delivery", "JournalEntryItem"):
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}})-[:INVOICED_AS]->(invoice:Invoice)-[:ACCOUNTED_AS]->(target:JournalEntryItem) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("JournalEntryItem", "Order"):
            cypher = (
                f"MATCH (target:Order)-[:FULFILLED_BY]->(delivery:Delivery)-[:INVOICED_AS]->(invoice:Invoice)-[:ACCOUNTED_AS]->(start:{source['label']} {{{source['id_property']}: $entity_id}}) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, delivery.delivery_id AS via_delivery_id, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        elif (source_type, target_type) == ("JournalEntryItem", "Delivery"):
            cypher = (
                f"MATCH (target:Delivery)-[:INVOICED_AS]->(invoice:Invoice)-[:ACCOUNTED_AS]->(start:{source['label']} {{{source['id_property']}: $entity_id}}) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props, invoice.invoice_id AS via_invoice_id "
                f"ORDER BY label LIMIT 10"
            )
        else:
            rel_types = PROCESS_RELATIONSHIP_MAP.get((source_type, target_type), set())
            relationship_filter = ":" + "|".join(sorted(rel_types)) if rel_types else ""
            relationship_segment = f"[{relationship_filter}]" if relationship_filter else "[]"
            cypher = (
                f"MATCH (start:{source['label']} {{{source['id_property']}: $entity_id}}) "
                f"MATCH (start)-{relationship_segment}-(target:{target['label']}) "
                f"RETURN DISTINCT '{target_type}' AS entity_type, target.{target['id_property']} AS entity_id, "
                f"{target_label_expr} AS label, properties(target) AS props "
                f"ORDER BY label LIMIT 10"
            )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f"I could not find any {target['title']} nodes connected to {source['title']} {entity_id}."
            labels = []
            for record in records[:5]:
                rel_props = record.get("rel_props", {}) or {}
                if target_type == "Plant" and rel_props.get("storage_location_count"):
                    labels.append(f"{record['label']} ({rel_props['storage_location_count']} storage locations)")
                else:
                    labels.append(str(record["label"]))
            total = records[0].get("total", len(records)) if records else 0
            suffix = "" if total <= 5 else f", and {total - 5} more"
            return (
                f"I found {total} {target['title']} node(s) connected to {source['title']} {entity_id}: "
                f"{', '.join(labels)}{suffix}."
            )

        return CypherQueryPlan(
            cypher=cypher,
            params={"entity_id": entity_id},
            render=render,
            focus_entity_type=source_type,
            focus_entity_id=entity_id,
            view_mode="focus",
            expand_focus=True,
            focus_depth=focus_depth,
        )

    def plan_neighbor_query(self, entity_type: str, entity_id: str) -> CypherQueryPlan:
        config = ENTITY_CONFIG[entity_type]
        cypher = (
            f"MATCH (start:{config['label']} {{{config['id_property']}: $entity_id}})-[r]-(neighbor) "
            f"RETURN DISTINCT labels(neighbor)[0] AS entity_type, {entity_id_expr('neighbor')} AS entity_id, "
            f"{entity_label_expr('neighbor')} AS label, type(r) AS relationship_type "
            f"ORDER BY entity_type, label LIMIT 20"
        )

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f"I could not find direct relationships for {config['title']} {entity_id}."
            grouped: dict[str, list[str]] = {}
            for record in records:
                grouped.setdefault(record["entity_type"], []).append(record["label"])
            parts = []
            for related_type, labels in sorted(grouped.items()):
                preview = ", ".join(labels[:3])
                suffix = "" if len(labels) <= 3 else f", and {len(labels) - 3} more"
                parts.append(f"{related_type}: {preview}{suffix}")
            return "Connected entities -> " + " | ".join(parts)

        return CypherQueryPlan(
            cypher=cypher,
            params={"entity_id": entity_id},
            render=render,
            focus_entity_type=entity_type,
            focus_entity_id=entity_id,
            view_mode="focus",
            expand_focus=True,
            focus_depth=2,
        )

    def plan_generic_lookup(self, entity_id: str, requested_fields: set[str] | None = None) -> CypherQueryPlan:
        parts = []
        for entity_type, config in ENTITY_CONFIG.items():
            parts.append(
                f"MATCH (n:{config['label']} {{{config['id_property']}: $entity_id}}) "
                f"RETURN '{entity_type}' AS entity_type, n.{config['id_property']} AS entity_id, "
                f"{entity_label_expr('n')} AS label, properties(n) AS props LIMIT 1"
            )
        cypher = " UNION ALL ".join(parts)

        def render(records: list[dict[str, Any]]) -> str:
            if not records:
                return f"I could not match `{entity_id}` to a known graph entity in Neo4j."
            record = records[0]
            props = record.get("props", {})
            if requested_fields:
                lines = []
                for field in sorted(requested_fields):
                    value = props.get(field)
                    if value:
                        lines.append(f"{field.replace('_', ' ')}: {value}")
                if lines:
                    return f"For {record['label']}, I found " + " | ".join(lines) + "."
            parts = []
            for field in FIELD_PRIORITY.get(record["entity_type"], []):
                value = props.get(field)
                if value:
                    parts.append(f"{field.replace('_', ' ')}: {value}")
                if len(parts) == 4:
                    break
            return f"{record['label']} is a {record['entity_type'].lower()} node. " + " | ".join(parts) + "."

        return CypherQueryPlan(
            cypher=cypher,
            params={"entity_id": entity_id},
            render=render,
            view_mode="focus",
            focus_depth=1,
        )
