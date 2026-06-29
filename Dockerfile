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

# The web server (uvicorn src.web.app:app) arrives in a later phase. Until then
# the image's default entrypoint is the existing CLI; this CI-only phase builds
# and pushes the image but does NOT run the container, so this CMD is just a
# valid default (swapped to uvicorn when the FastAPI app lands).
CMD ["python", "run.py"]
