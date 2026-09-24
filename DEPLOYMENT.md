# Deployment

## Recommended Production Path

Use the single-container production image when deploying dAiRaG to a cloud container platform. It packages:

- the FastAPI backend
- the built React frontend
- `frontend/data/graph_data.json`
- the local `dataset/combined` files used by schedule lookups

Build it from the repo root:

```powershell
docker build -f Dockerfile.prod -t dairag-app:prod .
```

Run it locally:

```powershell
docker run --rm -p 8000:8000 --env-file dAiRaG/.env dairag-app:prod
```

Then open:

- `http://127.0.0.1:8000/`

## Render

Render supports both Dockerfile-based deploys and prebuilt image deploys. For this project, use a prebuilt image on Render because the app needs local graph and dataset files that are not committed to Git. Render's current docs confirm that image-backed web services are supported and that web services should bind to `0.0.0.0`, with port `10000` as the default expected port.

Relevant docs:

- https://render.com/docs/deploying-an-image
- https://render.com/docs/docker
- https://render.com/docs/web-services
- https://render.com/docs/blueprint-spec

### 1. Build the production image

```powershell
docker build -f Dockerfile.prod -t dairag-app:prod .
```

### 2. Push the image to a registry

Example for Docker Hub:

```powershell
docker tag dairag-app:prod <dockerhub-username>/dairag-app:prod
docker push <dockerhub-username>/dairag-app:prod
```

Example for GitHub Container Registry:

```powershell
docker tag dairag-app:prod ghcr.io/<github-username>/dairag-app:prod
docker push ghcr.io/<github-username>/dairag-app:prod
```

### 3. Choose one Render setup path

Option A: Dashboard

1. In Render, create a new Web Service.
2. Choose `Existing Image`.
3. Provide the image URL you pushed.
4. If the image is private, add a registry credential in Render and attach it.
5. Set the environment variables listed below.
6. Deploy.

Option B: Blueprint

1. Update [render.yaml](C:/Users/parag/Downloads/dAiRaG/render.yaml) with your real image URL.
2. If the image is private, uncomment the `creds` block and set the Render registry credential name.
3. Push `render.yaml` to your repo.
4. In Render, create a new Blueprint from the repo.
5. Fill in the `sync: false` environment variable values in the dashboard.
6. Deploy.

### 4. Required environment variables on Render

Set these in the Render dashboard or via Blueprint prompts:

- `GEMINI_API_KEY`
- `GEMINI_API_GW_KEY`
- `GEMINI_AUTHORIZATION`
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

Common fixed values:

- `PORT=10000`
- `HOST=0.0.0.0`
- `DAIRAG_SERVE_FRONTEND=true`
- `LANGFUSE_HOST=https://cloud.langfuse.com`
- `LANGFUSE_JUDGE_ENABLED=true`
- `LANGFUSE_JUDGE_MODEL=gpt-4`
- `GEMINI_PROVIDER=openai`
- `GEMINI_MODEL=gpt-4`
- `GEMINI_CYPHER_MODEL=gpt-4`
- `GEMINI_ANSWER_MODEL=gpt-4`

## Push To A Registry

Example for Docker Hub:

```powershell
docker tag dairag-app:prod <dockerhub-username>/dairag-app:prod
docker push <dockerhub-username>/dairag-app:prod
```

Example for GitHub Container Registry:

```powershell
docker tag dairag-app:prod ghcr.io/<github-username>/dairag-app:prod
docker push ghcr.io/<github-username>/dairag-app:prod
```

## Notes

- The current two-container [docker-compose.yml](C:/Users/parag/Downloads/dAiRaG/docker-compose.yml) is still useful for local development.
- [Dockerfile.prod](C:/Users/parag/Downloads/dAiRaG/Dockerfile.prod) is the deployment image for Render and similar platforms.
- The dataset is not committed to git, so a Git-built Docker deploy on Render will not include it.
- The production image is the correct deployment artifact because it already packages the required local data.
