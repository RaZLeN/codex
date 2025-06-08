# CRM Offer Service

This service provides product offers for parsed customer requests. It exposes a
FastAPI endpoint that accepts a text request, breaks it into items using OpenAI
(if configured), looks up best offers from an SQLite database, and returns a
structured response.

## Running

1. Install dependencies:
   ```bash
   pip install fastapi uvicorn openai
   ```
2. Create a SQLite database `products.db` with a table `products` containing
   `sku`, `manufacturer`, and `price` columns. Populate it with your product
   data.
3. Set the `OPENAI_API_KEY` environment variable if you want to use OpenAI for
   parsing.
4. Start the service:
   ```bash
   uvicorn src.app:app --reload
   ```
5. Send POST requests to `/query` with JSON body `{ "text": "..." }`.
