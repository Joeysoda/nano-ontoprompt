FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY requirements-reasoning.txt .
RUN pip install --no-cache-dir --no-deps -r requirements-reasoning.txt
COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt
COPY . .
