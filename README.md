# dAiRaG

A graph-grounded SAP Order-to-Cash analytics application that combines Neo4j, a React graph explorer, NL-to-Cypher querying, and Langfuse observability to help users inspect business process data in natural language.

## Live App

- App: https://dairag.onrender.com
- Health: https://dairag.onrender.com/api/health

## What dAiRaG Does

- Visualizes the SAP Order-to-Cash flow as an interactive graph.
- Lets users ask natural-language questions and translates them into Cypher.
- Answers questions with graph-grounded evidence from Neo4j.
- Supports process exploration across customers, orders, deliveries, invoices, payments, plants, products, and journal entry items.
- Captures traces, token usage, cost, and in-app LLM-as-judge evaluations with Langfuse.

## Core Features

- Interactive graph explorer with focus, unfocus, reset view, metadata cards, zoom controls, and chat-driven graph updates.
- Natural-language to Cypher pipeline backed by the Gemini LLM gateway.
- Implemented input and output guardrails, including out-of-domain question fallback messages and grounded response handling.
- Query rewriting and expansion for better retrieval.
- Implemented Cypher generation and final response caching for repeated queries to improve consistency and reduce repeated inference overhead.
- Langfuse tracing, usage metrics, cost tracking, and in-app judge scores for answer quality.
- Dockerized local development setup plus a self-contained production image for deployment.

## Tech Stack

### Backend

- Python 3.11
- FastAPI
- Uvicorn
- Neo4j Python Driver
- Pydantic
- python-dotenv
- Langfuse SDK

### Frontend

- React 19
- Vite 7
- Vanilla canvas-based graph rendering inside a React app shell
- Nginx for containerized static frontend serving

### Infrastructure and Deployment

- Neo4j AuraDB
- Docker and Docker Compose
- Render
- Docker Hub

## Main Dependencies

### Python

- `fastapi==0.121.1`
- `uvicorn==0.38.0`
- `pydantic==2.12.4`
- `neo4j==6.1.0`
- `python-dotenv==1.2.1`
- `langfuse==4.0.6`

### Frontend

- `react ^19.1.0`
- `react-dom ^19.1.0`
- `vite ^7.0.0`
- `@vitejs/plugin-react ^5.0.0`

## Project Structure

```text
.
|-- dAiRaG/
|   |-- backend/
|   |   |-- api.py
|   |   |-- config.py
|   |   |-- observability/
|   |   `-- services/
|   |-- frontend/
|   |   |-- src/
|   |   |-- data/
|   |   |-- package.json
|   |   `-- nginx.conf
|   `-- run_server.py
|-- dataset/
|   `-- combined/
|-- docker-compose.yml
|-- Dockerfile.prod
|-- requirements.txt
|-- render.yaml
`-- DEPLOYMENT.md
```

## Local Setup

### 1. Install dependencies

Python backend:

```powershell
pip install -r requirements.txt
```

Frontend:

```powershell
cd dAiRaG/frontend
npm install
npm run build
```

### 2. Configure environment variables

Create `dAiRaG/.env` with the required values:

- `GEMINI_API_KEY`
- `GEMINI_API_GW_KEY`
- `GEMINI_AUTHORIZATION`
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`

### 3. Run locally without Docker

```powershell
cd dAiRaG
python run_server.py --host 127.0.0.1 --port 8000
```

Open:

- `http://127.0.0.1:8000/`

### 4. Run locally with Docker Compose

```powershell
docker compose up --build
```

Open:

- Frontend: `http://127.0.0.1:3000/`
- Backend health: `http://127.0.0.1:8000/api/health`

## Deployment

The production deployment uses the self-contained image defined in [Dockerfile.prod](Dockerfile.prod). It packages the backend, the built React frontend, the graph JSON, and the local schedule-lookup dataset used by the app.

- Production image: `docker.io/parag3/dairag-app:prod`
- Render Blueprint: [render.yaml](render.yaml)
- Deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)

## Observability and Evaluation

dAiRaG uses Langfuse for:

- request and generation tracing
- token, latency, throughput, and cost capture from the Gemini LLM path
- in-app LLM-as-judge style scoring for groundedness, relevance, correctness, completeness, faithfulness, refusal quality, and Cypher quality

## Notes

- The dataset is intentionally kept out of Git and baked into the production image when deploying from a local build.
- The deployed Render service may cold start if the instance is idle.
- `/api/health` is the primary health check endpoint used by Render.
