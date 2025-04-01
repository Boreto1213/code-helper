# DeepSeek Proxy API

A FastAPI application that receives text prompts from another API and forwards them to the DeepSeek API.

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with your DeepSeek API key:
   ```
   cp .env.example .env
   ```
   Then edit the `.env` file and replace `your_deepseek_api_key_here` with your actual DeepSeek API key.

## Running the API

Run the API locally with:

```
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

## API Endpoints

### POST /process-prompt

Receives a text prompt and forwards it to the DeepSeek API.

**Request Body:**

```json
{
  "content": "Your prompt text here"
}
```

**Response:**

```json
{
  "generated_text": "Response from DeepSeek API"
}
```

### GET /health

Health check endpoint.

**Response:**

```json
{
  "status": "healthy"
}
```

## API Documentation

FastAPI automatically generates interactive API documentation, available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
