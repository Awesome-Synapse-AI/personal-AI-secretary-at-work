FROM local/core-ai-base:latest

WORKDIR /app

# Requirements are already baked into the base image.
COPY app /app/app
COPY db /app/db

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
