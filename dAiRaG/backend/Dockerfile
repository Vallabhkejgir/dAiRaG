FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY dAiRaG /app/dAiRaG

WORKDIR /app/dAiRaG

EXPOSE 8000

CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]
