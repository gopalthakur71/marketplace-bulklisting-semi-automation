FROM python:3.12-slim

WORKDIR /app

# Dependencies first for layer caching (changes to src/ won't reinstall deps).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code and the data it reads at runtime.
COPY src ./src
COPY config ./config
COPY templates ./templates
COPY run.py ./

EXPOSE 8080
CMD ["uvicorn", "src.web.main:app", "--host", "0.0.0.0", "--port", "8080"]
