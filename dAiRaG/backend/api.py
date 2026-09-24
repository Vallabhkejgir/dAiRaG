from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import runtime_status
from .observability import attach_trace_context, current_trace_id, current_trace_url, shutdown_langfuse, start_observation
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .services.graph_query_engine import CypherChatEngine, CypherChatError, SalesScheduleLookupEngine
from .services.turing_llm_engine import GeminiCypherChatEngine


BACKEND_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = SERVICE_ROOT / "frontend"
FRONTEND_BUILD_DIR = FRONTEND_DIR / "dist"
GRAPH_DATA_PATH = FRONTEND_DIR / "data" / "graph_data.json"
SERVE_FRONTEND = os.getenv("DAIRAG_SERVE_FRONTEND", "true").strip().lower() not in {"0", "false", "no"}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_|-]{2,}")
STOPWORDS = {
    "show",
    "find",
    "what",
    "which",
    "where",
    "when",
    "who",
    "for",
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "linked",
    "connected",
    "around",
    "about",
}
ENTITY_TYPE_KEYWORDS = {
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
    "customer": "Customer",
    "customers": "Customer",
    "product": "Product",
    "products": "Product",
    "plant": "Plant",
    "plants": "Plant",
    "address": "Address",
    "addresses": "Address",
    "journal entry": "JournalEntryItem",
    "journal entries": "JournalEntryItem",
    "journal entry item": "JournalEntryItem",
    "journal entry items": "JournalEntryItem",
    "accounting item": "JournalEntryItem",
    "accounting items": "JournalEntryItem",
}

ENTITY_COUNT_FIELDS = {
    "Order": "Order",
    "Delivery": "Delivery",
    "Invoice": "Invoice",
    "Payment": "Payment",
    "Customer": "Customer",
    "Product": "Product",
    "Plant": "Plant",
    "Address": "Address",
    "JournalEntryItem": "JournalEntryItem",
}

ENTITY_FIELD_PRIORITY = {
    "Customer": [
        "customer_id",
        "name",
        "full_name",
        "business_partner_id",
        "business_partner_category",
        "business_partner_grouping",
        "last_change_date",
    ],
    "Address": [
        "address_id",
        "street_name",
        "city_name",
        "region",
        "country",
        "postal_code",
        "validity_end_date",
    ],
    "Product": [
        "product_id",
        "product_description",
        "product_type",
        "product_group",
        "base_unit",
        "gross_weight",
        "net_weight",
    ],
    "Plant": [
        "plant_id",
        "plant_name",
        "valuation_area",
        "sales_organization",
        "distribution_channel",
        "division",
        "address_id",
    ],
    "Order": [
        "order_id",
        "customer_id",
        "order_type",
        "transaction_currency",
        "total_net_amount",
        "requested_delivery_date",
        "overall_delivery_status",
    ],
    "Delivery": [
        "delivery_id",
        "actual_goods_movement_date",
        "shipping_point",
        "overall_goods_movement_status",
        "overall_picking_status",
    ],
    "Invoice": [
        "invoice_id",
        "customer_id",
        "billing_document_type",
        "transaction_currency",
        "total_net_amount",
        "billing_document_date",
        "accounting_document",
    ],
    "Payment": [
        "payment_document",
        "customer_id",
        "transaction_currency",
        "amount_in_transaction_currency",
        "clearing_date",
        "posting_date",
        "company_code",
    ],
    "JournalEntryItem": [
        "journal_entry_item_id",
        "invoice_id",
        "customer_id",
        "accounting_document",
        "accounting_document_item",
        "gl_account",
        "transaction_currency",
        "amount_in_transaction_currency",
        "clearing_payment_id",
        "clearing_date",
    ],
}

FIELD_KEYWORD_MAP = {
    "amount": {
        "total_net_amount",
        "amount_in_transaction_currency",
        "amount_in_company_code_currency",
        "net_amount",
    },
    "currency": {"transaction_currency", "company_code_currency"},
    "accounting": {"accounting_document", "accounting_document_item", "invoice_accounting_document", "payment_document"},
    "document": {
        "accounting_document",
        "invoice_accounting_document",
        "payment_document",
        "billing_document_date",
        "document_date",
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
    "journal": {"journal_entry_item_id", "accounting_document", "accounting_document_item"},
    "status": {
        "overall_delivery_status",
        "overall_goods_movement_status",
        "overall_picking_status",
        "overall_proof_of_delivery_status",
        "billing_document_is_cancelled",
    },
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


class ChatRequest(BaseModel):
    message: str


def _chat_observability_summary(response: dict[str, Any]) -> dict[str, Any]:
    return {
        'queryMode': response.get('queryMode'),
        'llmProvider': response.get('llmProvider'),
        'focusNodeId': response.get('focusNodeId'),
        'revealNodeCount': len(response.get('revealNodeIds') or []),
        'evidenceNodeCount': len(response.get('evidenceNodeIds') or []),
        'hasCypher': bool(response.get('cypher')),
        'hasWarning': bool(response.get('warning')),
    }


def _attach_trace_metadata(response: dict[str, Any]) -> dict[str, Any]:
    trace_id = current_trace_id()
    if not trace_id:
        return response

    enriched = dict(response)
    enriched['traceId'] = trace_id
    trace_url = current_trace_url(trace_id)
    if trace_url:
        enriched['traceUrl'] = trace_url
    return enriched


class GraphStore:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.nodes_by_id = {node["id"]: node for node in payload["nodes"]}
        self.relationships_by_id = {
            relationship["id"]: relationship for relationship in payload["relationships"]
        }
        self.adjacency: dict[str, list[str]] = defaultdict(list)
        self.entity_id_index: dict[str, list[str]] = defaultdict(list)
        self.metadata_value_index: dict[str, list[str]] = defaultdict(list)

        for relationship in payload["relationships"]:
            self.adjacency[relationship["source"]].append(relationship["id"])
            self.adjacency[relationship["target"]].append(relationship["id"])

        for node in payload["nodes"]:
            entity_id = str(node.get("entityId", "")).strip().lower()
            label = str(node.get("label", "")).strip().lower()
            if entity_id:
                self.entity_id_index[entity_id].append(node["id"])
            if label:
                self.metadata_value_index[label].append(node["id"])
            for value in node.get("metadata", {}).values():
                cleaned = str(value).strip().lower()
                if cleaned:
                    self.metadata_value_index[cleaned].append(node["id"])

    def node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes_by_id.get(node_id)

    def relationship(self, relationship_id: str) -> dict[str, Any] | None:
        return self.relationships_by_id.get(relationship_id)

    def neighbors(self, node_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        items: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for relationship_id in self.adjacency.get(node_id, []):
            relationship = self.relationship(relationship_id)
            if not relationship:
                continue
            other_id = (
                relationship["target"]
                if relationship["source"] == node_id
                else relationship["source"]
            )
            other = self.node(other_id)
            if other:
                items.append((relationship, other))
        return items

    def reachable_nodes(
        self,
        start_id: str,
        target_type: str | None = None,
        max_depth: int = 4,
        allowed_relationship_types: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        visited = {start_id}
        matches: list[dict[str, Any]] = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for relationship_id in self.adjacency.get(current_id, []):
                relationship = self.relationship(relationship_id)
                if not relationship:
                    continue
                if allowed_relationship_types and relationship["type"] not in allowed_relationship_types:
                    continue
                neighbor_id = (
                    relationship["target"]
                    if relationship["source"] == current_id
                    else relationship["source"]
                )
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                neighbor = self.node(neighbor_id)
                if not neighbor:
                    continue
                if target_type and neighbor["entityType"] == target_type:
                    matches.append(neighbor)
                queue.append((neighbor_id, depth + 1))

        return matches

    def allowed_relationship_types(self, start_type: str, target_type: str) -> set[str] | None:
        return PROCESS_RELATIONSHIP_MAP.get((start_type, target_type))

    def focus_depth_for_target(self, start_type: str, target_type: str) -> int:
        return PROCESS_DEPTH_MAP.get((start_type, target_type), 2)

    def detect_target_types(self, message: str) -> list[str]:
        lower = message.lower()
        seen: list[str] = []
        for keyword, entity_type in ENTITY_TYPE_KEYWORDS.items():
            if keyword in lower and entity_type not in seen:
                seen.append(entity_type)
        return seen

    def detect_requested_fields(self, message: str) -> set[str]:
        lower = message.lower()
        fields: set[str] = set()
        for keyword, mapped_fields in FIELD_KEYWORD_MAP.items():
            if keyword in lower:
                fields.update(mapped_fields)
        return fields

    def find_candidate_nodes(self, message: str) -> list[dict[str, Any]]:
        lower = message.lower()
        exact_matches: list[str] = []
        metadata_matches: list[str] = []
        for token in TOKEN_PATTERN.findall(message):
            token_lower = token.lower()
            if token_lower in STOPWORDS:
                continue
            if any(character.isdigit() for character in token_lower) or len(token_lower) >= 5:
                exact_matches.extend(self.entity_id_index.get(token_lower, []))
                metadata_matches.extend(self.metadata_value_index.get(token_lower, []))

        matches = exact_matches or metadata_matches

        if not matches:
            scored: list[tuple[int, str]] = []
            for node in self.nodes_by_id.values():
                haystack = " ".join(
                    [
                        node.get("label", ""),
                        node.get("subtitle", ""),
                        node.get("entityId", ""),
                        " ".join(node.get("metadata", {}).values()),
                    ]
                ).lower()
                if lower and lower in haystack:
                    scored.append((len(node.get("label", "")), node["id"]))
            matches = [node_id for _, node_id in sorted(scored)[:6]]

        unique_matches: list[str] = []
        for node_id in matches:
            if node_id not in unique_matches:
                unique_matches.append(node_id)
        return [self.nodes_by_id[node_id] for node_id in unique_matches[:6]]

    def build_node_summary(self, node: dict[str, Any]) -> str:
        priority = ENTITY_FIELD_PRIORITY.get(node["entityType"], [])
        metadata = node.get("metadata", {})
        summary_parts: list[str] = []
        for key in priority:
            value = metadata.get(key)
            if value:
                summary_parts.append(f"{key.replace('_', ' ')}: {value}")
            if len(summary_parts) == 4:
                break
        if not summary_parts:
            summary_parts.append(node.get("entityId", node["label"]))
        return "; ".join(summary_parts)

    def build_connection_summary(self, node_id: str) -> str:
        grouped: dict[str, list[str]] = defaultdict(list)
        for relationship, neighbor in self.neighbors(node_id):
            grouped[neighbor["entityType"]].append(neighbor["label"])
        if not grouped:
            return "It has no direct relationships in the current graph export."

        parts = []
        for entity_type, labels in sorted(grouped.items()):
            preview = ", ".join(labels[:3])
            suffix = "" if len(labels) <= 3 else f", and {len(labels) - 3} more"
            parts.append(f"{entity_type}: {preview}{suffix}")
        return "Connected entities -> " + " | ".join(parts)

    def build_field_response(
        self,
        node: dict[str, Any],
        requested_fields: set[str],
    ) -> str | None:
        if not requested_fields:
            return None
        metadata = node.get("metadata", {})
        lines = []
        for field in sorted(requested_fields):
            value = metadata.get(field)
            if value:
                lines.append(f"{field.replace('_', ' ')}: {value}")
        if not lines:
            return None
        return f"For {node['label']}, I found " + " | ".join(lines) + "."

    def build_count_response(self, message: str) -> str | None:
        lower = message.lower()
        if "how many" not in lower and "count" not in lower:
            return None

        matches = []
        for keyword, entity_type in ENTITY_TYPE_KEYWORDS.items():
            if keyword in lower and entity_type in ENTITY_COUNT_FIELDS and entity_type not in matches:
                matches.append(entity_type)
        if not matches:
            return None

        node_counts = self.payload["manifest"]["node_counts"]
        parts = [f"{entity_type}: {node_counts[ENTITY_COUNT_FIELDS[entity_type]]}" for entity_type in matches]
        return "Current graph counts -> " + " | ".join(parts) + "."

    def process_flow_response(self, message: str) -> str | None:
        lower = message.lower()
        if any(keyword in lower for keyword in ["flow", "process", "journey", "path"]):
            return (
                "This graph follows the order-to-cash process as "
                "Customer -> Order -> Delivery -> Invoice -> Payment, with Product, Plant, Address, and JournalEntryItem linked as supporting context."
            )
        return None

    def build_chat_response(self, message: str) -> dict[str, Any]:
        count_response = self.build_count_response(message)
        if count_response:
            return {
                "reply": count_response,
                "focusNodeId": None,
                "revealNodeIds": [],
                "viewMode": "global",
                "expandFocus": False,
                "focusDepth": 0,
            }

        flow_response = self.process_flow_response(message)
        if flow_response:
            return {
                "reply": flow_response,
                "focusNodeId": None,
                "revealNodeIds": [],
                "viewMode": "global",
                "expandFocus": False,
                "focusDepth": 0,
            }

        candidates = self.find_candidate_nodes(message)
        if not candidates:
            return {
                "reply": (
                    "I couldn't match that to a graph entity yet. Try an order, invoice, payment, "
                    "customer, product, plant, address, or journal entry item id from the dataset."
                ),
                "focusNodeId": None,
                "revealNodeIds": [],
                "viewMode": "global",
                "expandFocus": False,
                "focusDepth": 0,
            }

        requested_types = self.detect_target_types(message)
        requested_fields = self.detect_requested_fields(message)

        primary = candidates[0]
        if requested_types == [primary["entityType"]]:
            requested_types = []
        if primary["entityType"] in requested_types and len(requested_types) > 1:
            requested_types = [entity_type for entity_type in requested_types if entity_type != primary["entityType"]]

        field_response = self.build_field_response(primary, requested_fields)
        if field_response:
            return {
                "reply": field_response,
                "focusNodeId": primary["id"],
                "revealNodeIds": [primary["id"]],
                "viewMode": "focus",
                "expandFocus": False,
                "focusDepth": 1,
            }

        if requested_types:
            target_type = requested_types[0]
            matches = self.reachable_nodes(
                primary["id"],
                target_type=target_type,
                max_depth=4,
                allowed_relationship_types=self.allowed_relationship_types(
                    primary["entityType"], target_type
                ),
            )
            if matches:
                labels = ", ".join(match["label"] for match in matches[:5])
                suffix = "" if len(matches) <= 5 else f", and {len(matches) - 5} more"
                reply = (
                    f"I found {len(matches)} {target_type.lower()} node(s) connected to {primary['label']}: "
                    f"{labels}{suffix}."
                )
                reveal_node_ids = [primary["id"], *[node["id"] for node in matches[:8]]]
                return {
                    "reply": reply,
                    "focusNodeId": primary["id"],
                    "revealNodeIds": reveal_node_ids,
                    "viewMode": "focus",
                    "expandFocus": True,
                    "focusDepth": self.focus_depth_for_target(
                        primary["entityType"], target_type
                    ),
                }

            return {
                "reply": (
                    f"I couldn't find any {target_type.lower()} nodes connected to {primary['label']} "
                    "within four hops in the current graph."
                ),
                "focusNodeId": primary["id"],
                "revealNodeIds": [primary["id"]],
                "viewMode": "focus",
                "expandFocus": False,
                "focusDepth": 1,
            }

        lower = message.lower()
        if any(keyword in lower for keyword in ["linked", "connected", "neighbor", "relationship", "around", "expand"]):
            return {
                "reply": self.build_connection_summary(primary["id"]),
                "focusNodeId": primary["id"],
                "revealNodeIds": [primary["id"]],
                "viewMode": "focus",
                "expandFocus": True,
                "focusDepth": 2,
            }

        reply = (
            f"{primary['label']} is a {primary['entityType'].lower()} node. "
            f"{self.build_node_summary(primary)}. "
            f"{self.build_connection_summary(primary['id'])}"
        )
        return {
            "reply": reply,
            "focusNodeId": primary["id"],
            "revealNodeIds": [primary["id"]],
            "viewMode": "focus",
            "expandFocus": False,
            "focusDepth": 1,
        }


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStore:
    if not GRAPH_DATA_PATH.exists():
        raise FileNotFoundError(
            "Graph data file is missing from the frontend bundle."
        )
    with GRAPH_DATA_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return GraphStore(payload)


@lru_cache(maxsize=1)
def get_sales_schedule_lookup_engine() -> SalesScheduleLookupEngine | None:
    return SalesScheduleLookupEngine.from_files()


@lru_cache(maxsize=1)
def get_gemini_cypher_chat_engine() -> GeminiCypherChatEngine | None:
    return GeminiCypherChatEngine.from_env()


@lru_cache(maxsize=1)
def get_cypher_chat_engine() -> CypherChatEngine | None:
    return CypherChatEngine.from_env()


def active_chat_mode() -> str:
    runtime = runtime_status()
    if runtime.get("llmCypherRuntimeReady") and get_gemini_cypher_chat_engine() is not None:
        return "llm_cypher"
    if runtime.get("cypherRuntimeReady") and get_cypher_chat_engine() is not None:
        return "cypher"
    return "rule"


app = FastAPI(title="SAP Order to Cash Graph Explorer")
if SERVE_FRONTEND:
    app.mount("/static", StaticFiles(directory=FRONTEND_BUILD_DIR), name="static")


@app.on_event("shutdown")
def shutdown_background_clients() -> None:
    shutdown_langfuse()


@app.get("/", response_model=None)
def read_index() -> FileResponse | dict[str, str]:
    if not SERVE_FRONTEND:
        return {"status": "ok", "message": "dAiRaG backend is running in API-only mode."}
    return FileResponse(FRONTEND_BUILD_DIR / "index.html")


@app.get("/api/health")
def read_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "chat_mode": active_chat_mode(),
        "runtime": runtime_status(),
    }


@app.get("/api/graph")
def read_graph() -> dict[str, Any]:
    try:
        return get_graph_store().payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat")
def chat_with_graph(request: ChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    with attach_trace_context(name="chat.request", tags=["api", "chat"]):
        with start_observation(
            name="chat.request",
            as_type="span",
            input={"message": message},
            metadata={"route": "/api/chat"},
        ) as observation:
            try:
                schedule_lookup_engine = get_sales_schedule_lookup_engine()
                if schedule_lookup_engine is not None:
                    schedule_response = schedule_lookup_engine.execute(message)
                    if schedule_response is not None:
                        schedule_response = _attach_trace_metadata(schedule_response)
                        observation.update(
                            output=_chat_observability_summary(schedule_response),
                            metadata={"fallbackReasonCount": 0},
                        )
                        return schedule_response

                runtime = runtime_status()
                fallback_reasons: list[str] = []

                if runtime.get("llmCypherRuntimeReady"):
                    gemini_cypher_engine = get_gemini_cypher_chat_engine()
                    if gemini_cypher_engine is not None:
                        try:
                            response = _attach_trace_metadata(gemini_cypher_engine.execute(message))
                            observation.update(
                                output=_chat_observability_summary(response),
                                metadata={"fallbackReasonCount": 0},
                            )
                            return response
                        except CypherChatError as exc:
                            fallback_reasons.append(str(exc))

                if runtime.get("cypherRuntimeReady"):
                    cypher_engine = get_cypher_chat_engine()
                    if cypher_engine is not None:
                        try:
                            response = cypher_engine.execute(message)
                            if fallback_reasons:
                                response["warning"] = "The LLM-to-Cypher planner was unavailable, so the app used the template Cypher fallback."
                                response["fallbackReasons"] = fallback_reasons
                            response = _attach_trace_metadata(response)
                            observation.update(
                                output=_chat_observability_summary(response),
                                metadata={"fallbackReasonCount": len(fallback_reasons)},
                            )
                            return response
                        except CypherChatError as exc:
                            fallback_reasons.append(str(exc))

                response = get_graph_store().build_chat_response(message)
                response["queryMode"] = "rule"
                response["cypher"] = None
                response["cypherParams"] = {}
                if fallback_reasons:
                    response["warning"] = "Live Cypher chat is temporarily unavailable, so the app used the local graph fallback."
                    response["fallbackReasons"] = fallback_reasons
                response = _attach_trace_metadata(response)
                observation.update(
                    output=_chat_observability_summary(response),
                    metadata={"fallbackReasonCount": len(fallback_reasons)},
                )
                return response
            except CypherChatError as exc:
                observation.update(output={"error": str(exc)})
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            except FileNotFoundError as exc:
                observation.update(output={"error": str(exc)})
                raise HTTPException(status_code=500, detail=str(exc)) from exc



