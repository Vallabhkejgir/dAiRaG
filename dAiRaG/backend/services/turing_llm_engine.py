from __future__ import annotations
import json
import os
import re
import time
from backend.config import runtime_status
from typing import Any, Literal
try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None
from urllib import error as urllib_error
from urllib import request as urllib_request
from pydantic import BaseModel, Field
from ..observability import create_score, current_observation_id, current_trace_id, flush_langfuse, langfuse_enabled, start_observation
from .graph_query_engine import (
    CONNECTION_KEYWORDS,
    ENTITY_CONFIG,
    ENTITY_KEYWORDS,
    FIELD_KEYWORD_MAP,
    FIELD_PRIORITY,
    TOKEN_PATTERN,
    CypherChatEngine,
    CypherChatError,
    CypherQueryPlan,
)
try:
    from neo4j import GraphDatabase, Query
except ImportError:  # pragma: no cover - optional dependency
    GraphDatabase = None
    Query = None

# DEFAULT_TURING_MODEL = "gemini-1.5-flash"
# DEFAULT_TURING_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"
# DEFAULT_TURING_PROVIDER = "google"

ScalarValue = str | int | float | bool | None
ParamValue = ScalarValue | list[ScalarValue]

keys = runtime_status()

FORBIDDEN_CYPHER_PATTERNS = [
    r"\bCREATE\b",
    r"\bMERGE\b",
    r"\bDELETE\b",
    r"\bDETACH\b",
    r"\bSET\b",
    r"\bREMOVE\b",
    r"\bCALL\b",
    r"\bLOAD\s+CSV\b",
    r"\bFOREACH\b",
    r"\bDROP\b",
    r"\bALTER\b",
    r"\bRENAME\b",
    r"\bGRANT\b",
    r"\bDENY\b",
    r"\bREVOKE\b",
    r"\bUSE\b",
    r"^START\b",
    r"\bIMPORT\b",
    r"\bAPOC\.\b",
]

GENERAL_KNOWLEDGE_PATTERNS = [
    r"\bcapital of\b",
    r"\bwho is\b",
    r"\bwho was\b",
    r"\bpresident of\b",
    r"\bprime minister\b",
    r"\bweather\b",
    r"\btemperature\b",
    r"\bpopulation\b",
    r"\bexchange rate\b",
    r"\bstock price\b",
    r"\bmovie\b",
    r"\bsong\b",
    r"\brecipe\b",
    r"\bnews\b",
    r"\bsports?\b",
]

DOMAIN_HINT_KEYWORDS = {
    "sap",
    "o2c",
    "order to cash",
    "order-to-cash",
    "sales order",
    "billing",
    "invoice",
    "delivery",
    "shipment",
    "payment",
    "customer",
    "product",
    "plant",
    "plants",
    "address",
    "journal entry",
    "journal entries",
    "journal item",
    "journal items",
    "ledger",
    "gl account",
    "accounting document item",
    "storage location",
    "storage locations",
    "receivable",
    "clearing",
    "graph",
    "knowledge base",
    "database",
    "neo4j",
    "cypher",
    "relationship",
    "linked",
    "connected",
}

MEASURE_KEYWORDS = {
    "count",
    "how many",
    "total",
    "sum",
    "average",
    "avg",
    "minimum",
    "maximum",
    "list",
    "show",
    "find",
}

OUT_OF_DOMAIN_REPLY = (
    "I can only answer questions grounded in this SAP Order-to-Cash knowledge base. "
    "Try asking about customers, addresses, products, plants, orders, deliveries, invoices, payments, journal entry items, counts, amounts, statuses, or relationships between those records."
)

DIRECT_RELATIONSHIP_LINES = [
    "- (:Customer)-[:HAS_ADDRESS]->(:Address)",
    "- (:Customer)-[:PLACED]->(:Order)",
    "- (:Order)-[:CONTAINS_PRODUCT]->(:Product)",
    "- (:Order)-[:FULFILLED_BY]->(:Delivery)",
    "- (:Delivery)-[:DELIVERS_PRODUCT]->(:Product)",
    "- (:Product)-[:AVAILABLE_AT_PLANT]->(:Plant)",
    "- (:Delivery)-[:INVOICED_AS]->(:Invoice)",
    "- (:Customer)-[:RECEIVED_INVOICE]->(:Invoice)",
    "- (:Invoice)-[:BILLS_PRODUCT]->(:Product)",
    "- (:Customer)-[:MADE_PAYMENT]->(:Payment)",
    "- (:Payment)-[:SETTLES]->(:Invoice)",
    "- (:Customer)-[:HAS_JOURNAL_ENTRY_ITEM]->(:JournalEntryItem)",
    "- (:Invoice)-[:ACCOUNTED_AS]->(:JournalEntryItem)",
    "- (:Payment)-[:CLEARS_JOURNAL_ENTRY_ITEM]->(:JournalEntryItem)",
]

QUERY_RECIPE_LINES = [
    "- Order to invoices: MATCH (o:Order {order_id: $entity_id})-[:FULFILLED_BY]->(d:Delivery)-[:INVOICED_AS]->(i:Invoice) ...",
    "- Order to payments: MATCH (o:Order {order_id: $entity_id})-[:FULFILLED_BY]->(:Delivery)-[:INVOICED_AS]->(i:Invoice)<-[:SETTLES]-(p:Payment) ...",
    "- Customer to invoices: MATCH (c:Customer {customer_id: $entity_id})-[:RECEIVED_INVOICE]->(i:Invoice) ...",
    "- Product to plants: MATCH (product_node:Product {product_id: $entity_id})-[rel:AVAILABLE_AT_PLANT]->(plant_node:Plant) RETURN plant_node ..., properties(rel) AS rel_props ...",
    "- Product description to plants: MATCH (product_node:Product)-[:AVAILABLE_AT_PLANT]->(plant_node:Plant) WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term) ...",
    "- Invoice to payments: MATCH (p:Payment)-[:SETTLES]->(i:Invoice {invoice_id: $entity_id}) ...",
    "- Invoice to journal entries: MATCH (i:Invoice {invoice_id: $entity_id})-[:ACCOUNTED_AS]->(j:JournalEntryItem) ...",
    "- Payment to journal entries: MATCH (p:Payment {payment_id: $entity_id})-[:CLEARS_JOURNAL_ENTRY_ITEM]->(j:JournalEntryItem) ...",
    "- Customer to journal entries: MATCH (c:Customer {customer_id: $entity_id})-[:HAS_JOURNAL_ENTRY_ITEM]->(j:JournalEntryItem) ...",
    "- Order to journal entries: MATCH (o:Order {order_id: $entity_id})-[:FULFILLED_BY]->(:Delivery)-[:INVOICED_AS]->(:Invoice)-[:ACCOUNTED_AS]->(j:JournalEntryItem) ...",
    "- Orders without deliveries: MATCH (o:Order) WHERE NOT (o)-[:FULFILLED_BY]->(:Delivery) ...",
    "- Unpaid invoices: MATCH (i:Invoice) WHERE NOT (:Payment)-[:SETTLES]->(i) ...",
]

NON_DIRECT_RELATIONSHIP_WARNINGS = [
    "- There is no direct (:Order)-[:INVOICED_AS]->(:Invoice) edge. Use Order -> Delivery -> Invoice.",
    "- There is no direct (:Order)-[:SETTLES]->(:Payment) edge. Use Order -> Delivery -> Invoice <- Payment.",
    "- There is no direct (:Customer)-[:SETTLES]->(:Invoice) edge. Use Customer -> Payment -> Invoice or Customer -> RECEIVED_INVOICE -> Invoice depending on the question.",
    "- There is no direct (:Order)-[:AVAILABLE_AT_PLANT]->(:Plant) edge. Use Order -> Product -> Plant when the question is about where ordered products are available.",
    "- There is no direct (:Order)-[:ACCOUNTED_AS]->(:JournalEntryItem) edge. Use Order -> Delivery -> Invoice -> JournalEntryItem.",
    "- There is no direct (:Delivery)-[:ACCOUNTED_AS]->(:JournalEntryItem) edge. Use Delivery -> Invoice -> JournalEntryItem.",
]

DIRECT_RELATIONSHIPS = {
    ("Customer", "HAS_ADDRESS", "Address"),
    ("Customer", "PLACED", "Order"),
    ("Order", "CONTAINS_PRODUCT", "Product"),
    ("Order", "FULFILLED_BY", "Delivery"),
    ("Delivery", "DELIVERS_PRODUCT", "Product"),
    ("Product", "AVAILABLE_AT_PLANT", "Plant"),
    ("Delivery", "INVOICED_AS", "Invoice"),
    ("Customer", "RECEIVED_INVOICE", "Invoice"),
    ("Invoice", "BILLS_PRODUCT", "Product"),
    ("Customer", "MADE_PAYMENT", "Payment"),
    ("Payment", "SETTLES", "Invoice"),
    ("Customer", "HAS_JOURNAL_ENTRY_ITEM", "JournalEntryItem"),
    ("Invoice", "ACCOUNTED_AS", "JournalEntryItem"),
    ("Payment", "CLEARS_JOURNAL_ENTRY_ITEM", "JournalEntryItem"),
}
ALLOWED_NODE_LABELS = {config["label"] for config in ENTITY_CONFIG.values()}
ALLOWED_RELATIONSHIP_TYPES = {relationship for _, relationship, _ in DIRECT_RELATIONSHIPS}

class LlmCypherPlan(BaseModel):
    can_answer: bool = True
    refusal_reason: str | None = None
    cypher: str | None = None
    params: dict[str, ParamValue] = Field(default_factory=dict)
    evidence_cypher: str | None = None
    evidence_params: dict[str, ParamValue] = Field(default_factory=dict)
    focus_entity_type: str | None = None
    focus_entity_id: str | None = None
    view_mode: Literal["global", "focus"] = "global"
    expand_focus: bool = False
    focus_depth: int = Field(default=0, ge=0, le=4)
    reveal_evidence_nodes: bool = False
    reveal_evidence_limit: int = Field(default=25, ge=0, le=100)


def _observation_response_summary(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "queryMode": response.get("queryMode"),
        "llmProvider": response.get("llmProvider"),
        "focusNodeId": response.get("focusNodeId"),
        "revealNodeCount": len(response.get("revealNodeIds") or []),
        "evidenceNodeCount": len(response.get("evidenceNodeIds") or []),
        "hasCypher": bool(response.get("cypher")),
        "hasWarning": bool(response.get("warning")),
    }


class GeminiCypherChatEngine(CypherChatEngine):
    def __init__(
            self,
            uri: str,
            username: str,
            password: str,
            api_key: str,
            api_gw_key: str,
            authorization: str,
            database: str | None = None,
            timeout_seconds: float = 8.0,
            query_model: str = keys['geminiBaseUrl'],
            answer_model: str | None = None,
            provider_name: str = keys['geminiProviderName'],
            provider: str = "gemini",
            temperature: float = 0.2,
            planner_max_tokens: int = 900,
            answer_max_tokens: int = 450,
            judge_enabled: bool = True,
            judge_model: str | None = None,
            judge_max_tokens: int = 300,
            http_timeout_seconds: float = 30.0,
            max_retries: int = 1,
            retry_backoff_seconds: float = 1.5,
        ) -> None:
            CypherChatEngine.__init__(
                self,
                uri=uri,
                username=username,
                password=password,
                database=database,
                timeout_seconds=timeout_seconds,
            )
            self.provider = provider
            self.query_model = query_model
            self.answer_model = answer_model or query_model
 
            self.provider_name = provider_name
            self.temperature = temperature
            self.planner_max_tokens = planner_max_tokens
            self.answer_max_tokens = answer_max_tokens
            self.judge_enabled = judge_enabled
            self.judge_model = judge_model or answer_model or query_model
            self.judge_max_tokens = judge_max_tokens
            self.http_timeout_seconds = http_timeout_seconds
            self.max_retries = max(0, int(max_retries))
            self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
            self.api_key = api_key
            self.api_gw_key = api_gw_key
            self.authorization = authorization

    @classmethod
    def from_env(cls) -> GeminiCypherChatEngine | None:
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            api_key = (gemini_api_key or "").strip()
            password = os.getenv("NEO4J_PASSWORD")
            if not api_key or not password:
                return None

            if os.getenv("DISABLE_GEMINI_CYPHER_CHAT", "").lower() in {"1", "true", "yes"}:
                return None

            if GraphDatabase is None:
                return None

            uri = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
            username = os.getenv("NEO4J_USERNAME", os.getenv("NEO4J_USER", "neo4j"))
            database = os.getenv("NEO4J_DATABASE") or None
            timeout = float(os.getenv("NEO4J_TIMEOUT_SECONDS", "8"))
            query_model = (os.getenv("GEMINI_MODEL") or "").strip()
            answer_model = query_model
            
            provider_name = (os.getenv("GEMINI_PROVIDER"))
            temperature = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
            planner_max_tokens = int(os.getenv("GEMINI_PLANNER_MAX_TOKENS", "900"))
            answer_max_tokens = int(os.getenv("GEMINI_ANSWER_MAX_TOKENS", "450"))
            judge_enabled = os.getenv("LANGFUSE_JUDGE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
            judge_model = os.getenv("LANGFUSE_JUDGE_MODEL") or answer_model or query_model
            judge_max_tokens = int(os.getenv("LANGFUSE_JUDGE_MAX_TOKENS", "300"))
            http_timeout_seconds = float(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "30"))
            max_retries = int(os.getenv("GEMINI_MAX_RETRIES", "1"))
            retry_backoff_seconds = float(os.getenv("GEMINI_RETRY_BACKOFF_SECONDS", "1.5"))
            provider = "gemini"
            return cls(
                uri=uri,
                username=username,
                password=password,
                api_key=api_key,
                api_gw_key="",
                authorization="",
                database=database,
                timeout_seconds=timeout,
                query_model=query_model,
                answer_model=answer_model,
                
                provider=provider,
                temperature=temperature,
                planner_max_tokens=planner_max_tokens,
                answer_max_tokens=answer_max_tokens,
                judge_enabled=judge_enabled,
                judge_model=judge_model,
                judge_max_tokens=judge_max_tokens,
                http_timeout_seconds=http_timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )

    def execute(self, message: str) -> dict[str, Any]:
        with start_observation(
            name="llm-cypher.execute",
            as_type="span",
            input={"message": message},
            metadata={
                "provider": self.provider,
                "queryModel": self.query_model,
                "answerModel": self.answer_model,
            },
        ) as observation:
            trace_id = current_trace_id()
            observation_id = current_observation_id()

            def judge_response(
                reply: str,
                *,
                response_mode: Literal["answer", "refusal"] = "answer",
                cypher_text: str | None = None,
                cypher_params: dict[str, ParamValue] | None = None,
                result_rows: list[dict[str, Any]] | None = None,
            ) -> None:
                self.run_langfuse_judge(
                    message=message,
                    cypher=cypher_text or "",
                    params=cypher_params or {},
                    records=result_rows or [],
                    answer=reply,
                    trace_id=trace_id,
                    observation_id=observation_id,
                    response_mode=response_mode,
                )

            refusal = self.guard_out_of_domain_question(message)
            if refusal:
                response = self.build_refusal_response(refusal)
                judge_response(response["reply"], response_mode="refusal")
                observation.update(output=_observation_response_summary(response))
                return response

            supported_plan = CypherChatEngine.plan_message(self, message)
            plan = self.plan_message(message)
            refusal = self.guard_plan_against_question(message, plan)
            if refusal:
                if supported_plan:
                    response = self.execute_rule_fallback_plan(message, supported_plan)
                    response["warning"] = (
                        "The LLM planner could not verify a safe query for this request, so the app used the template query-rewrite and expansion fallback."
                    )
                    judge_response(
                        response["reply"],
                        response_mode="answer",
                        cypher_text=response.get("cypher"),
                        cypher_params=response.get("cypherParams") or {},
                        result_rows=[],
                    )
                    observation.update(output=_observation_response_summary(response))
                    return response
                response = self.build_refusal_response(refusal)
                judge_response(response["reply"], response_mode="refusal")
                observation.update(output=_observation_response_summary(response))
                return response
            if not plan.can_answer or not plan.cypher:
                if supported_plan:
                    response = self.execute_rule_fallback_plan(message, supported_plan)
                    response["warning"] = (
                        "The LLM planner could not build a safe query for this request, so the app used the template query-rewrite and expansion fallback."
                    )
                    judge_response(
                        response["reply"],
                        response_mode="answer",
                        cypher_text=response.get("cypher"),
                        cypher_params=response.get("cypherParams") or {},
                        result_rows=[],
                    )
                    observation.update(output=_observation_response_summary(response))
                    return response
                response = self.build_refusal_response(
                    plan.refusal_reason
                    or (
                        "I could not build a safe Cypher query for that request. "
                        "Try asking about a specific order, invoice, payment, customer, product, or a count question."
                    )
                )
                judge_response(response["reply"], response_mode="refusal")
                observation.update(output=_observation_response_summary(response))
                return response

            cypher = self.validate_cypher(plan.cypher)
            params = self.sanitize_params(plan.params)
            evidence_cypher = self.validate_cypher(plan.evidence_cypher) if plan.evidence_cypher else None
            evidence_params = self.sanitize_params(plan.evidence_params) if plan.evidence_cypher else {}

            driver = self._get_driver()
            response_query_mode = "llm_cypher"
            response_warning: str | None = None
            view_mode = plan.view_mode
            expand_focus = plan.expand_focus
            focus_depth = plan.focus_depth
            focus_entity_type = plan.focus_entity_type
            focus_entity_id = plan.focus_entity_id
            fallback_search_plan = self.plan_product_text_search_query(message)
            fallback_used = False
            llm_query_error: Exception | None = None
            records: list[dict[str, Any]] = []

            try:
                with start_observation(
                    name="neo4j.query",
                    as_type="span",
                    input={"cypher": cypher, "params": params},
                    metadata={"engine": "llm_cypher", "provider": self.provider},
                ) as query_observation:
                    with driver.session(database=self.database) as session:
                        records = [self.normalize_record(record) for record in session.run(Query(cypher), params).data()]
                    query_observation.update(output={"recordCount": len(records)})
            except Exception as exc:  # pragma: no cover - depends on runtime connection
                llm_query_error = exc

            can_try_fallback = (
                fallback_search_plan is not None
                and (fallback_search_plan.cypher != cypher or fallback_search_plan.params != params)
                and (llm_query_error is not None or not records)
            )
            if can_try_fallback:
                try:
                    with start_observation(
                        name="neo4j.query",
                        as_type="span",
                        input={"cypher": fallback_search_plan.cypher, "params": fallback_search_plan.params},
                        metadata={"engine": "template_fallback", "provider": self.provider},
                    ) as fallback_query_observation:
                        with driver.session(database=self.database) as session:
                            fallback_records = [
                                self.normalize_record(record)
                                for record in session.run(Query(fallback_search_plan.cypher), fallback_search_plan.params).data()
                            ]
                        fallback_query_observation.update(output={"recordCount": len(fallback_records)})
                except Exception as fallback_exc:
                    if llm_query_error is not None:
                        observation.update(output={"error": f"Neo4j query execution failed: {llm_query_error}"})
                        raise CypherChatError(f"Neo4j query execution failed: {llm_query_error}") from llm_query_error
                    observation.update(output={"error": f"Neo4j query execution failed: {fallback_exc}"})
                    raise CypherChatError(f"Neo4j query execution failed: {fallback_exc}") from fallback_exc

                records = fallback_records
                cypher = fallback_search_plan.cypher
                params = fallback_search_plan.params
                response_query_mode = "cypher"
                fallback_used = True
                if llm_query_error is not None:
                    response_warning = (
                        "The LLM-generated query could not be executed safely, so the app used the template query-rewrite and expansion fallback."
                    )
                else:
                    response_warning = (
                        "The LLM-generated query returned no rows, so the app used the template query-rewrite and expansion fallback."
                    )
                view_mode = fallback_search_plan.view_mode
                expand_focus = fallback_search_plan.expand_focus
                focus_depth = fallback_search_plan.focus_depth
                focus_entity_type = fallback_search_plan.focus_entity_type
                focus_entity_id = fallback_search_plan.focus_entity_id

            if llm_query_error is not None and not fallback_used:
                observation.update(output={"error": f"Neo4j query execution failed: {llm_query_error}"})
                raise CypherChatError(f"Neo4j query execution failed: {llm_query_error}") from llm_query_error

            if not focus_entity_type and view_mode == "focus" and records:
                first = records[0]
                if first.get("entity_type") and first.get("entity_id"):
                    focus_entity_type = str(first["entity_type"])
                    focus_entity_id = str(first["entity_id"])

            answer_already_judged = False
            if records:
                if fallback_search_plan is not None and response_query_mode == "cypher":
                    reply = fallback_search_plan.render(records)
                else:
                    reply = self.generate_grounded_answer(message, cypher, params, records)
                    answer_already_judged = True
            else:
                if fallback_search_plan is not None and response_query_mode == "cypher":
                    reply = fallback_search_plan.render([])
                else:
                    reply = "I ran the Cypher query successfully, but it returned no matching records."

            if not answer_already_judged:
                judge_response(
                    reply,
                    response_mode="answer",
                    cypher_text=cypher,
                    cypher_params=params,
                    result_rows=records,
                )

            reveal_node_ids = self.build_reveal_node_ids(records, focus_entity_type, focus_entity_id)
            active_evidence_plan = None
            if supported_plan is not None:
                active_evidence_plan = supported_plan if not fallback_used else fallback_search_plan
            elif response_query_mode == "llm_cypher":
                active_evidence_plan = CypherQueryPlan(
                    cypher=cypher,
                    params=params,
                    render=lambda _records: "",
                    focus_entity_type=focus_entity_type,
                    focus_entity_id=focus_entity_id,
                    view_mode=view_mode,
                    expand_focus=expand_focus,
                    focus_depth=focus_depth,
                    evidence_cypher=evidence_cypher,
                    evidence_params=evidence_params,
                    reveal_evidence_nodes=plan.reveal_evidence_nodes,
                    reveal_evidence_limit=plan.reveal_evidence_limit,
                )

            if active_evidence_plan is not None:
                evidence_node_ids, revealed_evidence_node_ids = self.collect_plan_evidence_node_ids(
                    driver,
                    active_evidence_plan,
                    records,
                    reveal_node_ids,
                )
                reveal_node_ids = self.merge_node_ids(reveal_node_ids, revealed_evidence_node_ids)
            else:
                evidence_node_ids = self.collect_evidence_node_ids(driver, cypher, params, reveal_node_ids, records)

            response = {
                "reply": reply,
                "focusNodeId": self.to_graph_node_id(focus_entity_type, focus_entity_id)
                if focus_entity_type and focus_entity_id
                else None,
                "revealNodeIds": reveal_node_ids,
                "evidenceNodeIds": evidence_node_ids,
                "viewMode": view_mode,
                "expandFocus": expand_focus,
                "focusDepth": focus_depth,
                "queryMode": response_query_mode,
                "cypher": cypher,
                "cypherParams": params,
                "llmProvider": self.provider,
            }
            if response_warning:
                response["warning"] = response_warning
            observation.update(
                output=_observation_response_summary(response),
                metadata={"recordCount": len(records), "responseQueryMode": response_query_mode},
            )
            return response

    def execute_rule_fallback_plan(self, message: str, plan: Any) -> dict[str, Any]:
            driver = self._get_driver()
            with driver.session(database=self.database) as session:
                records = [self.normalize_record(record) for record in session.run(Query(plan.cypher), plan.params).data()]

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

            return {
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
                "llmProvider": self.provider,
            }

    def build_refusal_response(self, reply: str) -> dict[str, Any]:
        return {
            "reply": reply,
            "focusNodeId": None,
            "revealNodeIds": [],
            "evidenceNodeIds": [],
            "viewMode": "global",
            "expandFocus": False,
            "focusDepth": 0,
            "queryMode": "llm_cypher",
            "cypher": None,
            "cypherParams": {},
            "llmProvider": self.provider,
        }

    def headers(self) -> dict[str, str]:
            return {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "x-api-gw-key": self.api_gw_key,
                "Authorization": self.authorization,
            }

    def call_gemini_chat(
            self,
            prompt: str,
            model: str,
            max_tokens: int,
            observation_name: str = "gemini.chat",
        ) -> dict[str, Any]:
            # Gemini API URL
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": self.temperature,
                    "maxOutputTokens": max_tokens
                }
            }
            request_started_at = time.perf_counter()

            with start_observation(
                name=observation_name,
                as_type="generation",
                input={"prompt": prompt},
                metadata={"provider": self.provider_name, "baseUrl": self.base_url},
                model=model,
                model_parameters={"temperature": self.temperature, "maxOutputTokens": max_tokens},
            ) as observation:
                if requests is not None:
                    for attempt in range(self.max_retries + 1):
                        try:
                            response = requests.post(
                                url,
                                json=payload,
                                timeout=self.http_timeout_seconds,
                            )
                        except requests.RequestException as exc:
                            if attempt < self.max_retries and self.should_retry_request_exception(exc):
                                self.sleep_before_retry(attempt)
                                continue
                            error_message = self.format_retry_error(f"Gemini request failed: {exc}", attempt)
                            observation.update(output={"error": error_message})
                            raise CypherChatError(error_message) from exc

                        if response.status_code >= 400:
                            error_message = self.describe_gemini_error(response.status_code, response.text, model)
                            if attempt < self.max_retries and self.should_retry_status(response.status_code, response.text):
                                self.sleep_before_retry(attempt)
                                continue
                            error_message = self.format_retry_error(error_message, attempt)
                            observation.update(output={"error": error_message, "statusCode": response.status_code})
                            raise CypherChatError(error_message)

                        try:
                            response_payload = response.json()
                        except ValueError as exc:
                            observation.update(output={"error": "Gemini returned a non-JSON response.", "statusCode": response.status_code})
                            raise CypherChatError("Gemini returned a non-JSON response.") from exc

                        output_payload, metadata_payload, usage_details, cost_details = self.build_generation_telemetry(
                            response_payload,
                            elapsed_seconds=time.perf_counter() - request_started_at,
                            attempt_count=attempt + 1,
                            status_code=response.status_code,
                        )
                        observation.update(
                            output=output_payload,
                            metadata=metadata_payload,
                            usage_details=usage_details,
                            cost_details=cost_details,
                        )
                        return response_payload

                # Fallback to urllib if requests not available
                request = urllib_request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                for attempt in range(self.max_retries + 1):
                    try:
                        with urllib_request.urlopen(request, timeout=self.http_timeout_seconds) as response:
                            body = response.read().decode("utf-8")
                    except urllib_error.HTTPError as exc:
                        body = exc.read().decode("utf-8", errors="replace")
                        error_message = self.describe_gemini_error(exc.code, body, model)
                        if attempt < self.max_retries and self.should_retry_status(exc.code, body):
                            self.sleep_before_retry(attempt)
                            continue
                        error_message = self.format_retry_error(error_message, attempt)
                        observation.update(output={"error": error_message, "statusCode": exc.code})
                        raise CypherChatError(error_message) from exc
                    except urllib_error.URLError as exc:
                        if attempt < self.max_retries:
                            self.sleep_before_retry(attempt)
                            continue
                        error_message = self.format_retry_error(f"Gemini request failed: {exc}", attempt)
                        observation.update(output={"error": error_message})
                        raise CypherChatError(error_message) from exc
                    except Exception as exc:
                        if attempt < self.max_retries and isinstance(exc, TimeoutError):
                            self.sleep_before_retry(attempt)
                            continue
                        error_message = self.format_retry_error(f"Gemini request failed: {exc}", attempt)
                        observation.update(output={"error": error_message})
                        raise CypherChatError(error_message) from exc

                    try:
                        response_payload = json.loads(body)
                    except json.JSONDecodeError as exc:
                        observation.update(output={"error": "Gemini returned a non-JSON response."})
                        raise CypherChatError("Gemini returned a non-JSON response.") from exc

                    output_payload, metadata_payload, usage_details, cost_details = self.build_generation_telemetry(
                        response_payload,
                        elapsed_seconds=time.perf_counter() - request_started_at,
                        attempt_count=attempt + 1,
                    )
                    observation.update(
                        output=output_payload,
                        metadata=metadata_payload,
                        usage_details=usage_details,
                        cost_details=cost_details,
                    )
                    return response_payload

                observation.update(output={"error": "Gemini request failed before a response could be processed."})
                raise CypherChatError("Gemini request failed before a response could be processed.")

    def should_retry_request_exception(self, exc: Exception) -> bool:
            if requests is None:
                return False
            return isinstance(exc, (requests.Timeout, requests.ConnectionError))

    def should_retry_status(self, status_code: int, body: str) -> bool:
            if status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                return True
            lower = body.lower()
            transient_markers = (
                "timeout",
                "timed out",
                "temporarily unavailable",
                "try again",
                "rate limit",
                "resource_exhausted",
                "upstream",
            )
            return any(marker in lower for marker in transient_markers)

    def sleep_before_retry(self, attempt: int) -> None:
            if self.retry_backoff_seconds <= 0:
                return
            time.sleep(self.retry_backoff_seconds * (attempt + 1))

    def format_retry_error(self, message: str, attempt: int) -> str:
            attempts = attempt + 1
            if attempts <= 1:
                return message
            return f"{message} after {attempts} attempts"

    def describe_gemini_error(self, status_code: int, body: str, model: str) -> str:
            lower = body.lower()
            if status_code == 403 or "forbidden" in lower or "invalid" in lower:
                return "Gemini request failed because the configured API credentials were rejected."
            if status_code in {402, 429} or "quota" in lower or "rate limit" in lower or "resource_exhausted" in lower:
                return (
                    f"Gemini request for model `{model}` failed because the configured project is out of credits, quota, or is rate-limited."
                )
            return f"Gemini request failed with HTTP {status_code}: {body[:400]}"

    def normalize_int_metric(self, value: Any) -> int | None:
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, (int, float)):
                return max(0, int(value))
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return max(0, int(float(stripped)))
                except ValueError:
                    return None
            return None

    def normalize_float_metric(self, value: Any) -> float | None:
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return float(stripped)
                except ValueError:
                    return None
            return None

    def extract_usage_details(self, payload: Any) -> dict[str, int] | None:
            if not isinstance(payload, dict):
                return None
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            token_usage = payload.get("tokenUsage") if isinstance(payload.get("tokenUsage"), dict) else {}
            prompt_tokens = self.normalize_int_metric(usage.get("prompt_tokens"))
            if prompt_tokens is None:
                prompt_tokens = self.normalize_int_metric(token_usage.get("promptTokens"))
            completion_tokens = self.normalize_int_metric(usage.get("completion_tokens"))
            if completion_tokens is None:
                completion_tokens = self.normalize_int_metric(token_usage.get("completionTokens"))
            total_tokens = self.normalize_int_metric(usage.get("total_tokens"))
            if total_tokens is None:
                total_tokens = self.normalize_int_metric(token_usage.get("totalTokens"))
            if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
                total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

            usage_details: dict[str, int] = {}
            if prompt_tokens is not None:
                usage_details["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                usage_details["completion_tokens"] = completion_tokens
            if total_tokens is not None:
                usage_details["total_tokens"] = total_tokens

            prompt_token_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
            completion_token_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
            cached_tokens = self.normalize_int_metric(prompt_token_details.get("cached_tokens"))
            reasoning_tokens = self.normalize_int_metric(completion_token_details.get("reasoning_tokens"))
            audio_prompt_tokens = self.normalize_int_metric(prompt_token_details.get("audio_tokens"))
            audio_completion_tokens = self.normalize_int_metric(completion_token_details.get("audio_tokens"))
            audio_tokens = None
            if audio_prompt_tokens is not None or audio_completion_tokens is not None:
                audio_tokens = (audio_prompt_tokens or 0) + (audio_completion_tokens or 0)

            if cached_tokens is not None:
                usage_details["cached_tokens"] = cached_tokens
            if reasoning_tokens is not None:
                usage_details["reasoning_tokens"] = reasoning_tokens
            if audio_tokens is not None:
                usage_details["audio_tokens"] = audio_tokens

            return usage_details or None

    def extract_cost_details(self, payload: Any) -> dict[str, float] | None:
            if not isinstance(payload, dict):
                return None
            token_usage = payload.get("tokenUsage") if isinstance(payload.get("tokenUsage"), dict) else {}
            breakdown = payload.get("costBreakdown") if isinstance(payload.get("costBreakdown"), dict) else {}
            if not breakdown and isinstance(token_usage.get("costBreakdown"), dict):
                breakdown = token_usage.get("costBreakdown")

            def nanousd_to_usd(raw_value: Any) -> float | None:
                numeric = self.normalize_float_metric(raw_value)
                if numeric is None:
                    return None
                return round(numeric / 1_000_000_000, 12)

            total_cost = nanousd_to_usd(token_usage.get("costNanousd"))
            prompt_cost = nanousd_to_usd(breakdown.get("promptTokensCost"))
            completion_cost = nanousd_to_usd(breakdown.get("completionTokensCost"))
            reasoning_cost = nanousd_to_usd(breakdown.get("reasoningTokensCost"))
            cached_cost = nanousd_to_usd(breakdown.get("cachedTokensCost"))
            audio_cost = nanousd_to_usd(breakdown.get("audioTokensCost"))

            if total_cost is None:
                subtotal = sum(value for value in [prompt_cost, completion_cost, reasoning_cost, cached_cost, audio_cost] if value is not None)
                total_cost = round(subtotal, 12) if subtotal else None

            cost_details: dict[str, float] = {}
            if prompt_cost is not None:
                cost_details["prompt_cost"] = prompt_cost
            if completion_cost is not None:
                cost_details["completion_cost"] = completion_cost
            if cached_cost is not None:
                cost_details["cached_cost"] = cached_cost
            if reasoning_cost is not None:
                cost_details["reasoning_cost"] = reasoning_cost
            if audio_cost is not None:
                cost_details["audio_cost"] = audio_cost
            if total_cost is not None:
                cost_details["total_cost"] = total_cost
            return cost_details or None

    def build_performance_metrics(self, elapsed_seconds: float, usage_details: dict[str, int] | None) -> dict[str, float | str]:
            metrics: dict[str, float | str] = {
                "totalDurationMs": round(max(0.0, elapsed_seconds) * 1000, 3)
            }
            if elapsed_seconds <= 0 or not usage_details:
                return metrics

            completion_tokens = usage_details.get("completion_tokens") or 0
            total_tokens = usage_details.get("total_tokens") or 0
            token_basis = completion_tokens or total_tokens
            if token_basis <= 0:
                return metrics

            metrics["tokenMetricBasis"] = "completion_tokens" if completion_tokens else "total_tokens"
            metrics["timePerTokenMs"] = round((elapsed_seconds * 1000) / token_basis, 6)
            metrics["throughputTokensPerSecond"] = round(token_basis / elapsed_seconds, 6)
            return metrics

    def build_generation_telemetry(self, response_payload: dict[str, Any], elapsed_seconds: float, attempt_count: int, status_code: int | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, int] | None, dict[str, float] | None]:
            usage_details = self.extract_usage_details(response_payload)
            cost_details = self.extract_cost_details(response_payload)
            performance_metrics = self.build_performance_metrics(elapsed_seconds, usage_details)
            finish_reason = None
            choices = response_payload.get("choices") if isinstance(response_payload.get("choices"), list) else []
            if choices:
                first_choice = choices[0] if isinstance(choices[0], dict) else {}
                finish_reason = first_choice.get("finish_reason")

            output_payload = {
                "responseText": self.extract_response_text(response_payload),
                "attemptCount": attempt_count,
            }
            if status_code is not None:
                output_payload["statusCode"] = status_code
            if finish_reason:
                output_payload["finishReason"] = finish_reason

            metadata = {
                "usage": response_payload.get("usage") if isinstance(response_payload, dict) else None,
                "tokenUsage": response_payload.get("tokenUsage") if isinstance(response_payload, dict) else None,
                "performance": performance_metrics,
            }
            return output_payload, metadata, usage_details, cost_details

    def should_run_langfuse_judge(self) -> bool:
            return self.judge_enabled and langfuse_enabled()

    def build_judge_prompt(
            self,
            message: str,
            cypher: str,
            params: dict[str, ParamValue],
            records: list[dict[str, Any]],
            answer: str,
            response_mode: Literal["answer", "refusal"] = "answer",
        ) -> str:
            payload = {
                "response_mode": response_mode,
                "question": message,
                "cypher": cypher or None,
                "params": params,
                "row_count": len(records),
                "rows": self.prepare_answer_rows(message, records)[:12],
                "answer": answer,
            }
            return (
                "You are evaluating an SAP Order-to-Cash assistant response. Use only the provided question, Cypher, and returned rows. "
                "Do not use outside knowledge. Return only one JSON object with keys grounded_score, relevance_score, correctness_score, faithfulness_score, completeness_score, cypher_quality_score, refusal_quality_score, verdict, and reasoning. "
                "Use null when a score does not apply. grounded_score must be 0 or 1 when present. verdict must be PASS or FAIL.\n\n"
                "Scoring guidance:\n"
                "- grounded_score: 1 only if the response stays within the provided rows or correctly refuses to go beyond them.\n"
                "- relevance_score: 1 means off-topic, 5 means directly answers the user question.\n"
                "- correctness_score: 1 means clearly wrong, 5 means fully correct based on the provided evidence or limitation.\n"
                "- faithfulness_score: 1 means the response overclaims or invents details, 5 means it strictly follows the evidence.\n"
                "- completeness_score: 1 means important requested details are missing, 5 means the response covers the ask as fully as the evidence allows.\n"
                "- cypher_quality_score: 1 means the Cypher is badly aligned with the question, 5 means it is well aligned, safe, and appropriate. Use null if there is no Cypher or no query was executed.\n"
                "- refusal_quality_score: 1 means the refusal is unhelpful or inappropriate, 5 means it correctly refuses and redirects helpfully. Use null unless response_mode is refusal.\n"
                "- verdict: PASS if the response is acceptable overall, otherwise FAIL.\n\n"
                "Evaluation input:\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )

    def parse_judge_result(self, text: str) -> dict[str, Any] | None:
            if not text:
                return None
            try:
                payload = self.extract_json_object(text)
            except Exception:
                return None

            result: dict[str, Any] = {}
            grounded_score = self.normalize_int_metric(payload.get("grounded_score"))
            if grounded_score is not None:
                result["grounded_score"] = 1 if grounded_score >= 1 else 0

            for field_name in (
                "relevance_score",
                "correctness_score",
                "faithfulness_score",
                "completeness_score",
                "cypher_quality_score",
                "refusal_quality_score",
            ):
                score_value = self.normalize_int_metric(payload.get(field_name))
                if score_value is not None:
                    result[field_name] = min(max(score_value, 1), 5)

            verdict = str(payload.get("verdict") or "").strip().upper()
            if verdict in {"PASS", "FAIL"}:
                result["verdict"] = verdict

            reasoning = str(payload.get("reasoning") or "").strip()
            if reasoning:
                result["reasoning"] = reasoning[:500]

            return result or None

    def write_langfuse_judge_scores(
            self,
            *,
            trace_id: str | None,
            observation_id: str | None,
            judge_result: dict[str, Any],
            response_mode: Literal["answer", "refusal"] = "answer",
        ) -> None:
            if not trace_id:
                return
            comment = str(judge_result.get("reasoning") or "").strip() or None
            metadata = {
                "judgeModel": self.judge_model,
                "judgeProvider": self.provider,
                "verdict": judge_result.get("verdict"),
                "responseMode": response_mode,
            }

            score_specs: list[tuple[str, str, str]] = [
                ("judge_groundedness", "grounded_score", "BOOLEAN"),
                ("judge_relevance", "relevance_score", "NUMERIC"),
                ("judge_correctness", "correctness_score", "NUMERIC"),
                ("judge_faithfulness", "faithfulness_score", "NUMERIC"),
                ("judge_completeness", "completeness_score", "NUMERIC"),
                ("judge_cypher_quality", "cypher_quality_score", "NUMERIC"),
                ("judge_refusal_quality", "refusal_quality_score", "NUMERIC"),
            ]
            for score_name, result_key, data_type in score_specs:
                score_value = judge_result.get(result_key)
                if score_value is None:
                    continue
                create_score(
                    name=score_name,
                    value=float(score_value),
                    data_type=data_type,
                    comment=comment,
                    metadata=metadata,
                    trace_id=trace_id,
                    observation_id=observation_id,
                )

            verdict = judge_result.get("verdict")
            if verdict:
                create_score(
                    name="judge_verdict",
                    value=str(verdict),
                    data_type="CATEGORICAL",
                    comment=comment,
                    metadata=metadata,
                    trace_id=trace_id,
                    observation_id=observation_id,
                )
            flush_langfuse()

    def run_langfuse_judge(
            self,
            *,
            message: str,
            cypher: str,
            params: dict[str, ParamValue],
            records: list[dict[str, Any]],
            answer: str,
            trace_id: str | None,
            observation_id: str | None,
            response_mode: Literal["answer", "refusal"] = "answer",
        ) -> dict[str, Any] | None:
            if not self.should_run_langfuse_judge():
                return None
            prompt = self.build_judge_prompt(message, cypher, params, records, answer, response_mode=response_mode)
            with start_observation(
                name="llm.judge",
                as_type="span",
                input={"question": message, "answerPreview": answer[:240], "responseMode": response_mode},
                metadata={"provider": self.provider, "model": self.judge_model, "responseMode": response_mode},
            ) as judge_observation:
                try:
                    response_payload = self.call_gemini_chat(
                        prompt,
                        model=self.judge_model,
                        max_tokens=self.judge_max_tokens,
                        observation_name="gemini.judge",
                    )
                    judge_text = self.extract_response_text(response_payload)
                    judge_result = self.parse_judge_result(judge_text)
                    if not judge_result:
                        judge_observation.update(output={"error": "Judge returned invalid JSON.", "responseMode": response_mode})
                        return None
                    self.write_langfuse_judge_scores(
                        trace_id=trace_id,
                        observation_id=observation_id,
                        judge_result=judge_result,
                        response_mode=response_mode,
                    )
                    judge_observation.update(output={**judge_result, "responseMode": response_mode})
                    return judge_result
                except Exception as exc:
                    judge_observation.update(output={"error": str(exc), "responseMode": response_mode})
                    return None

    def extract_response_text(self, payload: Any) -> str:
            if isinstance(payload, dict):
                output_text = payload.get("output_text")
                if isinstance(output_text, str) and output_text.strip():
                    return output_text.strip()

                choices = payload.get("choices")
                if isinstance(choices, list) and choices:
                    first_choice = choices[0] if isinstance(choices[0], dict) else {}
                    message = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
                    content = message.get("content") if isinstance(message, dict) else None
                    text = self.extract_text_from_content(content)
                    if text:
                        return text

                # Gemini format
                candidates = payload.get("candidates")
                if isinstance(candidates, list) and candidates:
                    first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
                    content = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
                    parts = content.get("parts") if isinstance(content, dict) else []
                    if isinstance(parts, list) and parts:
                        first_part = parts[0] if isinstance(parts[0], dict) else {}
                        text = first_part.get("text")
                        if isinstance(text, str) and text.strip():
                            return text.strip()

                for key in ("result", "response", "text", "message", "data"):
                    value = payload.get(key)
                    text = self.extract_response_text(value)
                    if text:
                        return text

                content = payload.get("content")
                text = self.extract_text_from_content(content)
                if text:
                    return text

            if isinstance(payload, list):
                for item in payload:
                    text = self.extract_response_text(item)
                    if text:
                        return text

            if isinstance(payload, str) and payload.strip():
                return payload.strip()

            return ""

    def extract_text_from_content(self, content: Any) -> str:
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                    else:
                        text = None
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    return "\n".join(parts).strip()
            if isinstance(content, dict):
                text = content.get("text") or content.get("content")
                if isinstance(text, str) and text.strip():
                    return text.strip()
            return ""

    def plan_message(self, message: str) -> LlmCypherPlan:
            with start_observation(
                name="llm.plan",
                as_type="span",
                input={"message": message},
                metadata={"provider": self.provider, "model": self.query_model},
            ) as observation:
                prompt = (
                    f"{self.planner_instructions()}\n\n"
                    "User question:\n"
                    f"{message}\n\n"
                    "Return only one JSON object with keys: can_answer, refusal_reason, cypher, params, evidence_cypher, evidence_params, focus_entity_type, focus_entity_id, view_mode, expand_focus, focus_depth, reveal_evidence_nodes, reveal_evidence_limit."
                )
                payload = self.call_gemini_chat(
                    prompt,
                    model=self.query_model,
                    max_tokens=self.planner_max_tokens,
                    observation_name="gemini.plan",
                )
                text = self.extract_response_text(payload)
                if not text:
                    observation.update(output={"error": "The Gemini planner returned an empty response."})
                    raise CypherChatError("The Gemini planner returned an empty response.")
                try:
                    json_payload = self.extract_json_object(text)
                    plan = LlmCypherPlan.model_validate(json_payload)
                    observation.update(
                        output={
                            "canAnswer": plan.can_answer,
                            "viewMode": plan.view_mode,
                            "focusEntityType": plan.focus_entity_type,
                            "hasCypher": bool(plan.cypher),
                            "hasEvidenceCypher": bool(plan.evidence_cypher),
                        }
                    )
                    return plan
                except Exception as exc:
                    observation.update(output={"error": "The Gemini planner did not return a valid JSON Cypher plan."})
                    raise CypherChatError("The Gemini planner did not return a valid JSON Cypher plan.") from exc

    def extract_json_object(self, text: str) -> dict[str, Any]:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                if not match:
                    raise
                return json.loads(match.group(0))

    def is_summary_or_ranking_question(self, message: str) -> bool:
            normalized = " ".join(message.lower().split())
            summary_patterns = (
                r"\bhow many\b",
                r"\bcount\b",
                r"\bnumber of\b",
                r"\bmost\b",
                r"\bleast\b",
                r"\btop\b",
                r"\bhighest\b",
                r"\blowest\b",
                r"\bbest selling\b",
                r"\bmost bought\b",
                r"\bmost purchased\b",
                r"\bmost ordered\b",
            )
            return any(re.search(pattern, normalized) for pattern in summary_patterns)

    def requested_answer_fields(self, message: str) -> set[str]:
            normalized = " ".join(message.lower().split())
            requested: set[str] = set()
            for keyword, mapped_fields in FIELD_KEYWORD_MAP.items():
                if re.search(rf"\b{re.escape(keyword)}\b", normalized):
                    requested.update(mapped_fields)
            return requested

    def filter_record_properties_for_answer(
            self,
            record: dict[str, Any],
            props: dict[str, Any],
            requested_fields: set[str],
            summary_or_ranking: bool,
            *,
            props_key: str,
        ) -> dict[str, Any]:
            if not isinstance(props, dict) or not props:
                return {}

            if summary_or_ranking and not requested_fields:
                return {}

            keep_fields = set(requested_fields)
            entity_type = str(record.get("entity_type") or "")
            config = ENTITY_CONFIG.get(entity_type)
            if config and props_key == "props":
                keep_fields.add(config["id_property"])

            if not keep_fields:
                return {}

            return {
                key: value
                for key, value in props.items()
                if key in keep_fields and value not in (None, "")
            }

    def prepare_answer_rows(self, message: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            requested_fields = self.requested_answer_fields(message)
            summary_or_ranking = self.is_summary_or_ranking_question(message)
            prepared_rows: list[dict[str, Any]] = []

            for record in records:
                prepared: dict[str, Any] = {}
                for key, value in record.items():
                    if key == "props" and isinstance(value, dict):
                        filtered_props = self.filter_record_properties_for_answer(
                            record,
                            value,
                            requested_fields,
                            summary_or_ranking,
                            props_key="props",
                        )
                        if filtered_props:
                            prepared[key] = filtered_props
                        continue
                    if key == "rel_props" and isinstance(value, dict):
                        filtered_rel_props = self.filter_record_properties_for_answer(
                            record,
                            value,
                            requested_fields,
                            summary_or_ranking,
                            props_key="rel_props",
                        )
                        if filtered_rel_props:
                            prepared[key] = filtered_rel_props
                        continue
                    prepared[key] = value
                prepared_rows.append(prepared)

            return prepared_rows

    def extract_node_ids_from_records(self, records: list[dict[str, Any]]) -> list[str]:
            node_ids: list[str] = []
            for record in records:
                entity_type = record.get("entity_type")
                entity_id = record.get("entity_id")
                if entity_type in ENTITY_CONFIG and entity_id not in (None, ""):
                    node_ids.append(self.to_graph_node_id(str(entity_type), str(entity_id)))

                for key, value in record.items():
                    if key.endswith("__entity_id") and value not in (None, ""):
                        entity_type = key[: -len("__entity_id")]
                        if entity_type in ENTITY_CONFIG:
                            node_ids.append(self.to_graph_node_id(entity_type, str(value)))
                        continue
                    if key.endswith("__entity_ids") and isinstance(value, list):
                        entity_type = key[: -len("__entity_ids")]
                        if entity_type not in ENTITY_CONFIG:
                            continue
                        for item in value:
                            if item in (None, ""):
                                continue
                            node_ids.append(self.to_graph_node_id(entity_type, str(item)))

            return self.dedupe_node_ids(node_ids)

    def generate_grounded_answer(
            self,
            message: str,
            cypher: str,
            params: dict[str, ParamValue],
            records: list[dict[str, Any]],
        ) -> str:
            payload = {
                "question": message,
                "cypher": cypher,
                "params": params,
                "row_count": len(records),
                "rows": self.prepare_answer_rows(message, records)[:12],
            }
            with start_observation(
                name="llm.answer",
                as_type="span",
                input={"question": message, "rowCount": len(records)},
                metadata={"provider": self.provider, "model": self.answer_model},
            ) as observation:
                trace_id = current_trace_id()
                observation_id = current_observation_id()
                prompt = (
                    f"{self.answer_instructions()}\n\n"
                    "Question and query results:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                    + "\n\nReturn only the final grounded answer text."
                )
                try:
                    response_payload = self.call_gemini_chat(
                        prompt,
                        model=self.answer_model,
                        max_tokens=self.answer_max_tokens,
                        observation_name="gemini.answer",
                    )
                    text = self.extract_response_text(response_payload)
                    if text:
                        answer = text.strip()
                        observation.update(output={"usedFallback": False, "answerPreview": answer[:240]})
                        self.run_langfuse_judge(
                            message=message,
                            cypher=cypher,
                            params=params,
                            records=records,
                            answer=answer,
                            trace_id=trace_id,
                            observation_id=observation_id,
                        )
                        return answer
                except Exception as exc:
                    observation.update(output={"error": str(exc), "usedFallback": True})
                fallback_answer = self.build_fallback_answer(records)
                observation.update(output={"usedFallback": True, "answerPreview": fallback_answer[:240]})
                self.run_langfuse_judge(
                    message=message,
                    cypher=cypher,
                    params=params,
                    records=records,
                    answer=fallback_answer,
                    trace_id=trace_id,
                    observation_id=observation_id,
                )
                return fallback_answer

    def build_fallback_answer(self, records: list[dict[str, Any]]) -> str:
            if not records:
                return "I ran the Cypher query successfully, but it returned no matching records."
            if len(records) == 1:
                parts = [f"{key}: {value}" for key, value in records[0].items() if value not in (None, "")]
                preview = " | ".join(parts[:8])
                return f"I found one matching record. {preview}."
            preview_rows = []
            for record in records[:3]:
                parts = [f"{key}: {value}" for key, value in record.items() if value not in (None, "")]
                preview_rows.append(" | ".join(parts[:5]))
            return (
                f"I found {len(records)} matching records. "
                + " ; ".join(preview_rows)
                + ("." if preview_rows else "")
            )

    def validate_cypher(self, cypher: str) -> str:
            compact = " ".join(cypher.strip().split())
            if not compact:
                raise CypherChatError("The generated Cypher query was empty.")
            if ";" in compact:
                raise CypherChatError("Only single-statement Cypher queries are allowed.")
            for pattern in FORBIDDEN_CYPHER_PATTERNS:
                if re.search(pattern, compact, re.IGNORECASE):
                    raise CypherChatError("The generated Cypher query used a disallowed operation.")
            if not re.search(r"\bMATCH\b", compact, re.IGNORECASE):
                raise CypherChatError("The generated Cypher query must include MATCH.")
            if not re.search(r"\bRETURN\b", compact, re.IGNORECASE):
                raise CypherChatError("The generated Cypher query must include RETURN.")

            self.validate_schema_usage(compact)

            limit_matches = re.findall(r"\bLIMIT\s+(\d+)\b", compact, re.IGNORECASE)
            if limit_matches:
                if int(limit_matches[-1]) > 25:
                    raise CypherChatError("The generated Cypher query exceeded the maximum allowed LIMIT of 25.")
            else:
                compact += " LIMIT 25"
            return compact

    def guard_plan_against_question(self, message: str, plan: LlmCypherPlan) -> str | None:
            refusal = self.guard_out_of_domain_question(message)
            if refusal:
                return refusal

            if plan.focus_entity_type and plan.focus_entity_type not in ENTITY_CONFIG:
                return "I could not verify the planned query against the known graph schema."

            if plan.focus_entity_id and not plan.focus_entity_type:
                return "I could not verify the planned graph focus for that request."

            if plan.view_mode not in {"global", "focus"}:
                return "I could not verify the planned graph view for that request."

            if plan.can_answer and plan.cypher:
                try:
                    self.validate_schema_usage(plan.cypher)
                    if plan.evidence_cypher:
                        self.validate_schema_usage(plan.evidence_cypher)
                except CypherChatError as exc:
                    return str(exc)

            return None

    def guard_out_of_domain_question(self, message: str) -> str | None:
            normalized = " ".join(message.lower().split())
            if not normalized:
                return OUT_OF_DOMAIN_REPLY

            has_entity_keyword = self.contains_keyword(normalized, ENTITY_KEYWORDS)
            has_field_keyword = self.contains_keyword(normalized, FIELD_KEYWORD_MAP)
            has_connection_keyword = self.contains_keyword(normalized, CONNECTION_KEYWORDS)
            has_domain_hint = self.contains_phrase(normalized, DOMAIN_HINT_KEYWORDS)
            has_measure_intent = self.contains_phrase(normalized, MEASURE_KEYWORDS)
            has_identifier = self.contains_identifier(message)

            if self.matches_general_knowledge(normalized) and not (has_entity_keyword or has_domain_hint):
                return OUT_OF_DOMAIN_REPLY

            if has_entity_keyword or has_domain_hint:
                return None

            if has_identifier and (has_field_keyword or has_connection_keyword or has_measure_intent):
                return None

            return OUT_OF_DOMAIN_REPLY

    def contains_identifier(self, message: str) -> bool:
            for token in TOKEN_PATTERN.findall(message):
                if any(character.isdigit() for character in token) or any(character in token for character in ":|-"):
                    return True
            return False

    def contains_keyword(self, text: str, keywords: dict[str, Any] | tuple[str, ...]) -> bool:
            candidates = keywords.keys() if isinstance(keywords, dict) else keywords
            return any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in candidates)

    def contains_phrase(self, text: str, phrases: set[str]) -> bool:
            return any(phrase in text for phrase in phrases)

    def matches_general_knowledge(self, text: str) -> bool:
            return any(re.search(pattern, text, re.IGNORECASE) for pattern in GENERAL_KNOWLEDGE_PATTERNS)

    def sanitize_params(self, params: dict[str, ParamValue]) -> dict[str, ParamValue]:
            sanitized: dict[str, ParamValue] = {}
            for key, value in (params or {}).items():
                sanitized[str(key)] = self.normalize_param_value(value)
            return sanitized

    def normalize_param_value(self, value: ParamValue | Any) -> ParamValue:
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, list):
                return [self.normalize_param_value(item) for item in value]
            return str(value)

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
            return {str(key): self.normalize_value(value) for key, value in record.items()}

    def normalize_value(self, value: Any) -> Any:
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, list):
                return [self.normalize_value(item) for item in value]
            if isinstance(value, dict):
                return {str(key): self.normalize_value(item) for key, item in value.items()}
            return str(value)

    def collect_evidence_node_ids(
            self,
            driver: Any,
            cypher: str,
            params: dict[str, ParamValue],
            reveal_node_ids: list[str],
            records: list[dict[str, Any]],
        ) -> list[str]:
            evidence_node_ids = [node_id for node_id in reveal_node_ids if node_id]
            record_node_ids = self.extract_node_ids_from_records(records)
            evidence_node_ids.extend(record_node_ids)
            if record_node_ids and (
                re.search(r"\bCOUNT\s*\(", cypher, re.IGNORECASE)
                or re.search(r"\bORDER\s+BY\b", cypher, re.IGNORECASE)
                or re.search(r"\bLIMIT\s+1\b", cypher, re.IGNORECASE)
            ):
                evidence_node_ids.extend(
                    self.collect_product_transactional_evidence(driver, record_node_ids)
                )
                return self.dedupe_node_ids(evidence_node_ids)
            evidence_query = self.build_evidence_query(cypher)
            if not evidence_query:
                return self.dedupe_node_ids(evidence_node_ids)

            try:
                with driver.session(database=self.database) as session:
                    evidence_records = [
                        self.normalize_record(record)
                        for record in session.run(Query(evidence_query), params).data()
                    ]
            except Exception:
                return self.dedupe_node_ids(evidence_node_ids)

            for record in evidence_records:
                for key, value in record.items():
                    if not key.endswith('__entity_id') or value in (None, ''):
                        continue
                    entity_type = key[: -len('__entity_id')]
                    if entity_type not in ENTITY_CONFIG:
                        continue
                    evidence_node_ids.append(self.to_graph_node_id(entity_type, str(value)))

            return self.dedupe_node_ids(evidence_node_ids)

    def collect_product_transactional_evidence(
            self,
            driver: Any,
            node_ids: list[str],
        ) -> list[str]:
            product_ids = [
                node_id.split(":", 1)[1]
                for node_id in node_ids
                if isinstance(node_id, str) and node_id.startswith("Product:") and ":" in node_id
            ]
            if not product_ids:
                return []

            transactional_cypher = (
                "MATCH (product:Product) WHERE product.product_id IN $product_ids "
                "MATCH (invoice:Invoice)-[:BILLS_PRODUCT]->(product) "
                "OPTIONAL MATCH (payment:Payment)-[:SETTLES]->(invoice) "
                "RETURN DISTINCT product.product_id AS Product__entity_id, "
                "invoice.invoice_id AS Invoice__entity_id, payment.payment_id AS Payment__entity_id "
                "LIMIT 100"
            )
            evidence_node_ids: list[str] = []
            try:
                with driver.session(database=self.database) as session:
                    evidence_records = [
                        self.normalize_record(record)
                        for record in session.run(Query(transactional_cypher), {"product_ids": product_ids}).data()
                    ]
            except Exception:
                return []

            for record in evidence_records:
                for key, value in record.items():
                    if key.endswith("__entity_id") and value not in (None, ""):
                        entity_type = key[: -len("__entity_id")]
                        if entity_type in ENTITY_CONFIG:
                            evidence_node_ids.append(self.to_graph_node_id(entity_type, str(value)))
            return self.dedupe_node_ids(evidence_node_ids)

    def build_evidence_query(self, cypher: str) -> str | None:
            if not cypher or re.search(r'\bUNION\b', cypher, re.IGNORECASE):
                return None

            parts = re.split(r'\bRETURN\b', cypher, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) != 2:
                return None

            query_prefix = parts[0].strip()
            if not query_prefix or re.search(r'\bWITH\b', query_prefix, re.IGNORECASE):
                return None

            labeled_variables = self.extract_labeled_variables(cypher)
            if not labeled_variables:
                return None

            normalized_variables: list[tuple[str, str, str]] = []
            for variable_name, entity_type in labeled_variables:
                config = ENTITY_CONFIG.get(entity_type)
                if not config:
                    continue
                normalized_variables.append((variable_name, entity_type, config['id_property']))

            if not normalized_variables:
                return None

            branches: list[str] = []
            for target_variable, _target_entity_type, _target_id_property in normalized_variables:
                projections: list[str] = []
                for variable_name, entity_type, id_property in normalized_variables:
                    if variable_name == target_variable:
                        projections.append(f"{variable_name}.{id_property} AS {entity_type}__entity_id")
                    else:
                        projections.append(f"null AS {entity_type}__entity_id")
                branches.append(f"{query_prefix} RETURN DISTINCT {', ' .join(projections)} LIMIT 25")

            return " UNION ALL ".join(branches)

    def extract_labeled_variables(self, cypher: str) -> list[tuple[str, str]]:
            variables: list[tuple[str, str]] = []
            seen_variables: set[str] = set()
            pattern = re.compile(
                r'\(\s*(?P<variable>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<label>[A-Za-z][A-Za-z0-9_]*)\b'
            )
            for match in pattern.finditer(cypher):
                variable_name = match.group('variable')
                entity_type = match.group('label')
                if variable_name in seen_variables or entity_type not in ENTITY_CONFIG:
                    continue
                seen_variables.add(variable_name)
                variables.append((variable_name, entity_type))
            return variables

    def dedupe_node_ids(self, node_ids: list[str]) -> list[str]:
            deduped: list[str] = []
            for node_id in node_ids:
                if node_id and node_id not in deduped:
                    deduped.append(node_id)
            return deduped

    def extract_explicit_relationship_triples(self, cypher: str) -> list[tuple[str, str, str]]:
            triples: list[tuple[str, str, str]] = []
            pattern = re.compile(
                r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<left>[A-Za-z][A-Za-z0-9_]*)[^)]*\)\s*"
                r"(?P<left_arrow><-)?-\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<rel>[A-Z_][A-Z0-9_]*)[^]]*\]-"
                r"(?P<right_arrow>>)?\s*\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*(?P<right>[A-Za-z][A-Za-z0-9_]*)[^)]*\)"
            )
            for match in pattern.finditer(cypher):
                left_label = match.group("left")
                relationship_type = match.group("rel")
                right_label = match.group("right")
                if match.group("left_arrow"):
                    triples.append((right_label, relationship_type, left_label))
                else:
                    triples.append((left_label, relationship_type, right_label))
            deduped: list[tuple[str, str, str]] = []
            for triple in triples:
                if triple not in deduped:
                    deduped.append(triple)
            return deduped

    def validate_schema_usage(self, cypher: str) -> None:
            node_labels = re.findall(r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*([A-Za-z][A-Za-z0-9_]*)\b", cypher)
            relationship_types = re.findall(r"\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*)?:\s*([A-Z_][A-Z0-9_]*)\b", cypher)
            for label in node_labels:
                if label not in ALLOWED_NODE_LABELS:
                    raise CypherChatError(f"The generated Cypher query used an unknown node label `{label}`.")
            for relationship_type in relationship_types:
                if relationship_type not in ALLOWED_RELATIONSHIP_TYPES:
                    raise CypherChatError(
                        f"The generated Cypher query used an unknown relationship type `{relationship_type}`."
                    )
            if re.search(r"\[[^]]*\|[^]]*\]", cypher):
                raise CypherChatError(
                    "The generated Cypher query used a union of relationship types. Use explicit schema-backed hops instead."
                )
            for source_label, relationship_type, target_label in self.extract_explicit_relationship_triples(cypher):
                if (source_label, relationship_type, target_label) not in DIRECT_RELATIONSHIPS:
                    raise CypherChatError(
                        "The generated Cypher query used a direct relationship that is not present in the O2C graph schema: "
                        f"(:{source_label})-[:{relationship_type}]->(:{target_label})."
                    )

    def planner_instructions(self) -> str:
            entity_lines = []
            for entity_type, config in ENTITY_CONFIG.items():
                key_fields = ", ".join(FIELD_PRIORITY.get(entity_type, [])[:5])
                entity_lines.append(
                    f"- {config['label']} uses primary id property `{config['id_property']}`; common fields: {key_fields}"
                )

            example_count = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (invoice_node:Invoice) RETURN 'Invoice' AS entity_type, count(invoice_node) AS total",
                    "params": {},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                    "evidence_cypher": None,
                    "evidence_params": {},
                    "reveal_evidence_nodes": False,
                    "reveal_evidence_limit": 25,
                }
            )
            example_deliveries = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (order_node:Order {order_id: $entity_id})-[:FULFILLED_BY]->(delivery_node:Delivery) RETURN 'Delivery' AS entity_type, delivery_node.delivery_id AS entity_id, coalesce(delivery_node.delivery_id) AS label, properties(delivery_node) AS props LIMIT 10",
                    "params": {"entity_id": "771093"},
                    "focus_entity_type": "Order",
                    "focus_entity_id": "771093",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 1,
                }
            )
            example_order_to_invoice_path = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (order_node:Order {order_id: $entity_id})-[:FULFILLED_BY]->(delivery_node:Delivery)-[:INVOICED_AS]->(invoice_node:Invoice) RETURN 'Invoice' AS entity_type, invoice_node.invoice_id AS entity_id, coalesce(invoice_node.invoice_id) AS label, properties(invoice_node) AS props, delivery_node.delivery_id AS via_delivery_id LIMIT 25",
                    "params": {"entity_id": "771093"},
                    "focus_entity_type": "Order",
                    "focus_entity_id": "771093",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 2,
                }
            )
            example_invoice_payments = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (payment_node:Payment)-[:SETTLES]->(invoice_node:Invoice {invoice_id: $entity_id}) RETURN 'Payment' AS entity_type, payment_node.payment_id AS entity_id, coalesce(payment_node.payment_id) AS label, properties(payment_node) AS props LIMIT 25",
                    "params": {"entity_id": "900001"},
                    "focus_entity_type": "Invoice",
                    "focus_entity_id": "900001",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 1,
                }
            )
            example_product_count = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (product_node:Product) WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term) RETURN 'Product' AS entity_type, count(product_node) AS total",
                    "params": {"search_term": "lipbalm"},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )
            example_product_plants = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (product_node:Product {product_id: $entity_id})-[rel:AVAILABLE_AT_PLANT]->(plant_node:Plant) RETURN 'Plant' AS entity_type, plant_node.plant_id AS entity_id, coalesce(plant_node.plant_name, plant_node.plant_id) AS label, properties(plant_node) AS props, properties(rel) AS rel_props LIMIT 25",
                    "params": {"entity_id": "B8907367022152"},
                    "focus_entity_type": "Product",
                    "focus_entity_id": "B8907367022152",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 1,
                }
            )
            example_plant_count_by_product_text = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (product_node:Product)-[:AVAILABLE_AT_PLANT]->(plant_node:Plant) WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term) RETURN 'Plant' AS entity_type, count(DISTINCT plant_node) AS total",
                    "params": {"search_term": "lipbalm"},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )
            example_invoices_by_product_text = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (invoice_node:Invoice)-[:BILLS_PRODUCT]->(product_node:Product) WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term) RETURN 'Invoice' AS entity_type, invoice_node.invoice_id AS entity_id, coalesce(invoice_node.invoice_id) AS label, properties(invoice_node) AS props, product_node.product_id AS Product__entity_id LIMIT 25",
                    "params": {"search_term": "lipbalm"},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )
            
            example_product_list = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (product_node:Product) WHERE toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term) RETURN 'Product' AS entity_type, product_node.product_id AS entity_id, coalesce(product_node.product_description, product_node.product_id) AS label, properties(product_node) AS props LIMIT 25",
                    "params": {"search_term": "lip"},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )
            example_orders_without_deliveries = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (order_node:Order) WHERE NOT (order_node)-[:FULFILLED_BY]->(:Delivery) RETURN 'Order' AS entity_type, order_node.order_id AS entity_id, coalesce(order_node.order_id) AS label, properties(order_node) AS props LIMIT 25",
                    "params": {},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )
            example_invoice_journal_entries = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (invoice_node:Invoice {invoice_id: $entity_id})-[:ACCOUNTED_AS]->(journal_node:JournalEntryItem) RETURN 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, coalesce(journal_node.journal_entry_item_id) AS label, properties(journal_node) AS props LIMIT 25",
                    "params": {"entity_id": "90504219"},
                    "focus_entity_type": "Invoice",
                    "focus_entity_id": "90504219",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 1,
                }
            )
            example_invoice_gl_account = json.dumps(
                {
                    "can_answer": True,
                    "refusal_reason": None,
                    "cypher": "MATCH (invoice_node:Invoice {invoice_id: $entity_id})-[:ACCOUNTED_AS]->(journal_node:JournalEntryItem) RETURN 'JournalEntryItem' AS entity_type, journal_node.journal_entry_item_id AS entity_id, coalesce(journal_node.gl_account, journal_node.journal_entry_item_id) AS label, properties(journal_node) AS props LIMIT 25",
                    "params": {"entity_id": "90504301"},
                    "focus_entity_type": "Invoice",
                    "focus_entity_id": "90504301",
                    "view_mode": "focus",
                    "expand_focus": True,
                    "focus_depth": 1,
                }
            )
            example_refusal = json.dumps(
                {
                    "can_answer": False,
                    "refusal_reason": "This question is outside the SAP Order-to-Cash knowledge base.",
                    "cypher": None,
                    "params": {},
                    "focus_entity_type": None,
                    "focus_entity_id": None,
                    "view_mode": "global",
                    "expand_focus": False,
                    "focus_depth": 0,
                }
            )

            return "\n".join(
                [
                    "You translate user questions about the SAP Order-to-Cash graph into safe, read-only Cypher.",
                    "This knowledge base is limited to SAP Order-to-Cash business data about customers, addresses, products, plants, orders, deliveries, invoices, payments, and journal entry items.",
                    "Return a structured plan only.",
                    "If the request is ambiguous, unsupported, or cannot be answered safely, set can_answer=false and explain why in refusal_reason.",
                    "If the request is not clearly about this SAP Order-to-Cash graph, set can_answer=false.",
                    "Reject general world-knowledge, geography, politics, history, coding, trivia, or small-talk questions.",
                    "Example: `What is the capital of India?` must return can_answer=false because that fact is not part of this graph.",
                    "Do not map country or city names to customer or address records unless the user is explicitly asking about address data stored in this graph.",
                    "If the message does not mention a graph entity, a graph relationship, a business field, or a plausible graph identifier, refuse it.",
                    "Use only the labels, properties, and direct relationship types listed below.",
                    "Always parameterize user-provided values inside params and reference them with $param_name in Cypher.",
                    "Never use write operations, procedures, APOC, multi-statement queries, or any Cypher that mutates data.",
                    "Always include RETURN. Include LIMIT <= 25 whenever the query can return multiple rows.",
                    "Do not return path objects. Return result rows only.",
                    "Prefer exact direct relationships when they exist, and use explicit multi-hop chains when the business process requires them.",
                    "Never invent direct edges that are actually multi-hop process chains.",
                    "Avoid variable names that look like Cypher clauses. Prefer aliases like source_node, target_node, order_node, delivery_node, invoice_node, payment_node.",
                    "For count queries, return rows with columns entity_type and total.",
                    "Also plan evidence_cypher whenever the answer query is aggregated, ranked, or otherwise loses the supporting path. evidence_cypher must be read-only and return only supporting node identifiers using columns like Customer__entity_id, Order__entity_id, Invoice__entity_id, Payment__entity_id, Product__entity_id, Plant__entity_id, or JournalEntryItem__entity_id.",
                    "When the answer query already returns the exact supporting nodes, you may set evidence_cypher to null and evidence_params to {}.",
                    "For ranking or top-k questions, evidence_cypher should traverse the business records that justify the winner instead of returning only the winning entity.",
                    "Set reveal_evidence_nodes=true only when the UI should reveal evidence nodes beyond the main result nodes; otherwise set it to false. Use reveal_evidence_limit to cap how many evidence nodes should be revealed in the graph.",
                    "For product-name or product-description text search, use case-insensitive matching with toLower(coalesce(product_node.product_description, '')) CONTAINS toLower($search_term).",
                    "Use query rewriting and expansion for compact product phrases when helpful. Treat variants like `lip balm`, `lipbalm`, and `lip-balm` as the same product concept, and do the same for phrases like `face wash` and `facewash`.",
                    "When a user asks for entities that ordered X and Y, build Cypher that requires both product concepts to be present rather than matching either one.",
                    "For plant questions by product id or product name, traverse Product -> AVAILABLE_AT_PLANT -> Plant.",
                    "For invoice questions by purchased product or product description, traverse Invoice -> BILLS_PRODUCT -> Product. Do not use INVOICED_AS for that lookup.",
                    "Plant names live on Plant.plant_name. Product names/descriptions live on Product.product_description.",
                    "Storage locations live on the AVAILABLE_AT_PLANT relationship, so include properties(rel) AS rel_props when the question asks about storage-location context.",
                    "For entity lookups, return columns entity_type, entity_id, label, and props where possible.",
                    "For connection queries, return the connected nodes as rows with entity_type, entity_id, label, and optionally props.",
                    "For path or process-trace questions, include helpful columns like via_delivery_id or via_invoice_id when they clarify the chain.",
                    "If the user asks for GL account or other accounting-item fields for an invoice, payment, customer, order, or delivery, traverse to related JournalEntryItem nodes and answer from those properties instead of refusing.",
                    "Do not refuse a field request just because the field is not stored directly on the named source node when it is available on a related JournalEntryItem through the documented schema.",
                    "Set focus_entity_type and focus_entity_id when the graph UI should focus a specific node.",
                    "Use view_mode='focus' for entity-centric answers and view_mode='global' for graph-wide counts.",
                    "For count queries, keep focus_entity_type and focus_entity_id null.",
                    "For relationship queries like `show deliveries for order 771093`, focus on the source business object when possible.",
                    f"Valid response example for `How many invoices are there?`: {example_count}",
                    f"Valid response example for `Show deliveries for order 771093`: {example_deliveries}",
                    f"Valid response example for `Show the path from order 771093 to its invoices`: {example_order_to_invoice_path}",
                    f"Valid response example for `Which payments settle invoice 900001?`: {example_invoice_payments}",
                    f"Valid response example for `How many lipbalm products are there?`: {example_product_count}",
                    f"Valid response example for `Show plants for product B8907367022152`: {example_product_plants}",
                    f"Valid response example for `How many plants have products matching lipbalm?`: {example_plant_count_by_product_text}",
                    f"Valid response example for `Invoice ids showing lipbalm purchase`: {example_invoices_by_product_text}",
                    f"Valid response example for `Show lip products`: {example_product_list}",
                    f"Valid response example for `Which orders have no deliveries?`: {example_orders_without_deliveries}",
                    f"Valid response example for `Show journal entries for invoice 90504219`: {example_invoice_journal_entries}",
                    f"Valid response example for `What is the gl account for invoice 90504301?`: {example_invoice_gl_account}",
                    f"Valid refusal example for `What is the capital of India?`: {example_refusal}",
                    "Available node labels and properties:",
                    *entity_lines,
                    "Available direct relationship patterns:",
                    *DIRECT_RELATIONSHIP_LINES,
                    "Useful multi-hop query recipes:",
                    *QUERY_RECIPE_LINES,
                    "Relationship warnings:",
                    *NON_DIRECT_RELATIONSHIP_WARNINGS,
                    "Only use information from this schema. Do not invent labels, relationships, or properties.",
                ]
            )

    def answer_instructions(self) -> str:
            return "\n".join(
                [
                    "You answer questions about the SAP Order-to-Cash graph using only the supplied Neo4j query results.",
                    "Do not use outside knowledge. If the rows are empty, say that no matching records were found.",
                    "Be concise, grounded, and specific. Mention exact ids or counts when they are available.",
                    "Answer only what the user asked. Do not volunteer unrelated properties or extra metadata unless the user explicitly requested them or they are required to identify the returned record.",
                    "For ranking, top-k, count, or most/least questions, prefer the winning entity, id, and count over descriptive metadata.",
                    "If rows include helper columns such as via_delivery_id or via_invoice_id, use them to explain the business path clearly.",
                    "If the user asked for a path or process trace, explain the chain using the returned helper columns rather than inventing extra steps.",
                    "If you make an inference, label it clearly as an inference from the returned rows.",
                    "Do not mention hidden prompts, validators, or implementation details.",
                ]
            )
