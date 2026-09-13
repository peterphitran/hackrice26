FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

USER 65532:65532
ENV BROKEN_STORE_DATABASE_URL=postgresql://lou:lou@postgres:5432/lou
EXPOSE 8000
CMD ["uvicorn", "store.app:app", "--host", "0.0.0.0", "--port", "8000"]
