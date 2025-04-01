import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the FastAPI app
from main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

@patch("httpx.AsyncClient.post")
def test_process_prompt_success(mock_post):
    # Mock the response from DeepSeek API
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "This is a mocked response from DeepSeek API"
                }
            }
        ]
    }
    mock_post.return_value = mock_response
    
    # Test our API endpoint
    response = client.post(
        "/process-prompt",
        json={"content": "Test prompt"}
    )
    
    # Verify the response
    assert response.status_code == 200
    assert response.json() == {"generated_text": "This is a mocked response from DeepSeek API"}
    
    # Verify DeepSeek API was called with correct data
    mock_post.assert_called_once()
    call_args = mock_post.call_args[1]["json"]
    assert call_args["messages"][0]["content"] == "Test prompt"

@patch("httpx.AsyncClient.post")
def test_process_prompt_api_error(mock_post):
    # Mock an error response from DeepSeek API
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad request"
    mock_response.raise_for_status.side_effect = Exception("API Error")
    mock_post.return_value = mock_response
    
    # Test our API endpoint
    response = client.post(
        "/process-prompt",
        json={"content": "Test prompt"}
    )
    
    # Verify the response indicates an error
    assert response.status_code == 500 