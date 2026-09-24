from __future__ import annotations

import importlib.util
import os
import socket
from pathlib import Path
from typing import TypedDict, Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


BACKEND_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = BACKEND_DIR.parent
REPO_ROOT = SERVICE_ROOT.parent

ENV_PATHS = [
    SERVICE_ROOT / ".env",
    REPO_ROOT / ".env",
]

_LOADED_ENV_PATHS: list[str] = []


class RuntimeStatus(TypedDict):
    envFilesLoaded: list[str]

    llmProvider: str | None

    langfuseSdkInstalled: bool
    langfusePublicKeyConfigured: bool
    langfuseSecretKeyConfigured: bool
    langfuseBaseUrl: str
    langfuseEnabled: bool
    langfuseJudgeEnabled: bool
    langfuseJudgeModel: str | None

    geminiApiKeyConfigured: bool
    geminiBaseUrl: str
    geminiProviderName: str
    geminiModel: str

    neo4jDriverInstalled: bool
    dotenvInstalled: bool
    neo4jPasswordConfigured: bool

    neo4jUri: str
    neo4jHost: str
    neo4jPort: int
    neo4jUsername: str
    neo4jDatabase: str | None

    neo4jBoltReachable: bool
    cypherRuntimeReady: bool
    llmCypherRuntimeReady: bool

    missing: list[str]


def load_runtime_env() -> list[str]:
    global _LOADED_ENV_PATHS

    loaded: list[str] = []

    if load_dotenv is None:
        _LOADED_ENV_PATHS = loaded
        return loaded

    for path in ENV_PATHS:
        if path.exists():
            load_dotenv(path, override=False)
            loaded.append(str(path))

    _LOADED_ENV_PATHS = loaded
    return loaded


def loaded_env_paths() -> list[str]:
    if not _LOADED_ENV_PATHS:
        load_runtime_env()

    return list(_LOADED_ENV_PATHS)


def parse_neo4j_host_port(uri: str) -> tuple[str, int]:
    parsed = urlparse(uri)

    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 7687

    return host, port


def tcp_reachable(
    host: str,
    port: int,
    timeout_seconds: float = 1.0,
) -> bool:
    try:
        with socket.create_connection(
            (host, port),
            timeout=timeout_seconds,
        ):
            return True

    except OSError:
        return False


def package_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def runtime_status() -> RuntimeStatus:
    env_files = load_runtime_env()

    neo4j_uri = os.getenv(
        "NEO4J_URI",
        "bolt://127.0.0.1:7687",
    )

    neo4j_username = os.getenv(
        "NEO4J_USERNAME",
        os.getenv("NEO4J_USER", "neo4j"),
    )

    neo4j_database = os.getenv("NEO4J_DATABASE") or None

    host, port = parse_neo4j_host_port(neo4j_uri)

    neo4j_driver = package_installed("neo4j")

    dotenv_package = load_dotenv is not None

    raw_gemini_key = (
        os.getenv("GEMINI_API_KEY") or ""
    ).strip()

    raw_langfuse_public_key = (
        os.getenv("LANGFUSE_PUBLIC_KEY") or ""
    ).strip()

    raw_langfuse_secret_key = (
        os.getenv("LANGFUSE_SECRET_KEY") or ""
    ).strip()

    langfuse_base_url = (
        os.getenv("LANGFUSE_HOST")
        or os.getenv("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    ).strip()

    langfuse_judge_enabled = (
        os.getenv("LANGFUSE_JUDGE_ENABLED", "true")
        .strip()
        .lower()
        not in {"0", "false", "no"}
    )

    langfuse_judge_model = os.getenv(
        "LANGFUSE_JUDGE_MODEL"
    )

    gemini_key = bool(raw_gemini_key)

    langfuse_public_key = bool(
        raw_langfuse_public_key
    )

    langfuse_secret_key = bool(
        raw_langfuse_secret_key
    )

    neo4j_password = bool(
        os.getenv("NEO4J_PASSWORD")
    )

    bolt_reachable = tcp_reachable(host, port)

    cypher_ready = (
        neo4j_driver
        and neo4j_password
        and bolt_reachable
    )

    llm_provider = "gemini" if gemini_key else None

    llm_cypher_ready = (
        cypher_ready
        and llm_provider is not None
    )

    langfuse_installed = package_installed(
        "langfuse"
    )

    langfuse_enabled = (
        langfuse_installed
        and langfuse_public_key
        and langfuse_secret_key
    )

    missing: list[str] = []

    if not neo4j_driver:
        missing.append("neo4j_driver")

    if not dotenv_package:
        missing.append("python_dotenv")

    if not neo4j_password:
        missing.append("NEO4J_PASSWORD")

    if not bolt_reachable:
        missing.append("neo4j_server")

    if llm_provider is None:
        if not gemini_key:
            missing.append("GEMINI_API_KEY")

    return {
        "envFilesLoaded": env_files,

        "llmProvider": llm_provider,

        "langfuseSdkInstalled": langfuse_installed,
        "langfusePublicKeyConfigured": langfuse_public_key,
        "langfuseSecretKeyConfigured": langfuse_secret_key,
        "langfuseBaseUrl": langfuse_base_url,
        "langfuseEnabled": langfuse_enabled,
        "langfuseJudgeEnabled": langfuse_judge_enabled,
        "langfuseJudgeModel": langfuse_judge_model,

        "geminiApiKeyConfigured": gemini_key,

        "geminiBaseUrl": os.getenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/models",
        ),

        "geminiProviderName": (
            os.getenv("GEMINI_PROVIDER")
            or "google"
        ),

        "geminiModel": os.getenv(
            "GEMINI_CYPHER_MODEL",
            os.getenv(
                "GEMINI_MODEL",
                "gemini-1.5-flash",
            ),
        ),

        "neo4jDriverInstalled": neo4j_driver,
        "dotenvInstalled": dotenv_package,
        "neo4jPasswordConfigured": neo4j_password,

        "neo4jUri": neo4j_uri,
        "neo4jHost": host,
        "neo4jPort": port,
        "neo4jUsername": neo4j_username,
        "neo4jDatabase": neo4j_database,

        "neo4jBoltReachable": bolt_reachable,
        "cypherRuntimeReady": cypher_ready,
        "llmCypherRuntimeReady": llm_cypher_ready,

        "missing": missing,
    }