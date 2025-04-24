from app.utils.general import parse_review_comments
from fastapi import FastAPI, HTTPException
import os
from dotenv import load_dotenv
from app.models.github import PromptRequest
from app.models.deepseek import DeepSeekResponse
from app.services.github import ReviewBot
from app.services.deepseek import DeepSeekService
from app.services.gemini import GeminiService

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="AI Code Review API", 
              description="API for code review using various AI models")

# Initialize services
review_bot = ReviewBot()
deepseek_service = DeepSeekService()
gemini_service = GeminiService()

@app.post("/process-prompt/deepseek", response_model=DeepSeekResponse)
async def process_prompt_deepseek(request: PromptRequest):
    """
    Receives a prompt as a string and forwards it to the DeepSeek API
    """
    try:
        # Process the prompt through DeepSeek
        response = await deepseek_service.process_prompt(request.content)
        
        # Parse and create GitHub review if PR URL is provided
        if request.pr_url:
            try:
                comments = parse_review_comments(response.generated_text)
                await review_bot.create_github_review(request.pr_url, comments)
                print(f"Created GitHub review with {len(comments)} comments")
            except Exception as e:
                print(f"Error creating GitHub review: {str(e)}")
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.post("/process-prompt/gemini", response_model=DeepSeekResponse)
async def process_prompt_gemini(request: PromptRequest):
    """
    Receives a prompt as a string and forwards it to the Gemini API
    """
    try:
        # Process the prompt through Gemini
        response = await gemini_service.process_prompt(request.content)
        
        # Parse and create GitHub review if PR URL is provided
        if request.pr_url:
            try:
                comments = parse_review_comments(response.generated_text)
                await review_bot.create_github_review(request.pr_url, comments)
                print(f"Created GitHub review with {len(comments)} comments")
            except Exception as e:
                print(f"Error creating GitHub review: {str(e)}")
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 