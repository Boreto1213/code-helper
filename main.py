from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
import httpx
import os
import re
from dotenv import load_dotenv
from typing import List, Optional
import json
import jwt
import time
from datetime import datetime, timedelta, UTC

# Load environment variables from .env file
load_dotenv()

# Get DeepSeek API key from environment variables
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY environment variable is not set")

app = FastAPI(title="DeepSeek Proxy API", 
              description="API for forwarding prompts to DeepSeek LLM API")

class Comment:
    def __init__(self, file: str, line: int, message: str, suggestion: Optional[str] = None):
        self.file = file
        self.line = line
        self.message = message
        self.suggestion = suggestion

class PromptRequest(BaseModel):
    content: str
    pr_url: Optional[str] = None  # Add PR URL for GitHub API calls
    
    class Config:
        # Allow arbitrary length strings
        max_length = None

class DeepSeekResponse(BaseModel):
    generated_text: str
    
async def get_deepseek_client() -> httpx.AsyncClient:
    """
    Creates and returns an async HTTP client with the DeepSeek API headers
    """
    return httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        },
        timeout=60.0  # Increased timeout for potentially long DeepSeek requests
    )

def create_pr_review_prompt(pr_info: dict, changes: dict) -> str:
    """Create a structured prompt that will generate parseable responses"""
    # First, create a list of valid line numbers for each file
    file_line_numbers = {}
    for file in changes['changed_files']:
        if file['patch']:
            lines = []
            current_line = None
            for line in file['patch'].split('\n'):
                if line.startswith('@@'):
                    # Extract the line number from the patch header
                    try:
                        current_line = int(line.split(',')[0].split('@@')[1].strip().split(' ')[0])
                    except (IndexError, ValueError):
                        continue
                elif line.startswith('+') or line.startswith('-'):
                    if current_line is not None:
                        lines.append(current_line)
                        current_line += 1
            file_line_numbers[file['filename']] = sorted(set(lines))
    
    # Create a summary of available line numbers for each file
    line_number_summary = "\n".join([
        f"File: {filename}\nAvailable line numbers: {', '.join(map(str, lines))}"
        for filename, lines in file_line_numbers.items()
    ])
    
    files_with_changes = "\n".join([
        f"File: {f['filename']}\nChanges:\n{f['patch']}\n"
        for f in changes['changed_files'] if f['patch']
    ])
    
    prompt = f"""Please review this pull request and provide specific comments in the following format:

For each issue or suggestion, use this structure:
[filename.ext]:<line_number>
Your comment about the code
```suggestion
Your suggested code change (if applicable)
```

CRITICAL INSTRUCTIONS:
1. You MUST use EXACT line numbers from the provided diff only. Here are the available line numbers for each file:
{line_number_summary}

2. DO NOT comment on line 1 of any file unless it is explicitly shown in the diff with a + or - prefix.

3. Only comment on lines that are shown in the diff with + or - prefixes.

4. The line numbers in your comments MUST match exactly with the line numbers shown in the diff.

5. If you want to comment on multiple lines, use a range like this: [filename.ext]:start-end
   For example: [main.py]:13-21
   The parser will automatically use the first line number (13 in this case).

For example, if you see this in the diff:
@@ -15,3 +15,4 @@
  def some_function():
      print("Hello")
+     print("World")  # This is on line 18
      return True

You can comment on line 18 like this:
[main.py]:18
Consider adding a docstring to explain the function's purpose
```suggestion
def some_function():
    Prints a greeting and returns True.
    print("Hello")
    print("World")
    return True
```

PR Details:
Title: {pr_info['title']}
Author: {pr_info['author']}
Branch: {pr_info['head_branch']} → {pr_info['base_branch']}

Changes to review:
{files_with_changes}

Please provide a detailed review focusing on:
1. Code quality and best practices
2. Potential bugs or issues
3. Performance considerations
4. Specific suggestions for improvement

Remember: Your comments will be rejected if they reference line numbers that are not shown in the diff.
"""
    return prompt

def parse_review_comments(review_text: str) -> List[Comment]:
    comments = []
    current_file = None
    current_line = None
    
    # Updated regex patterns to handle the AI's format
    file_pattern = r'([^:]+):(\d+)(?:-(\d+))?'
    suggestion_pattern = r'```suggestion\n(.*?)```'
    
    lines = review_text.split('\n')
    current_message = []
    current_suggestion = None
    
    for i, line in enumerate(lines):
        # Check for file and line reference
        file_match = re.search(file_pattern, line)
        if file_match:
            # Save previous comment if exists
            if current_file and current_message:
                comments.append(Comment(
                    current_file,
                    current_line,
                    '\n'.join(current_message).strip(),
                    current_suggestion
                ))
            
            # Handle both formats: file.ext:line and file.ext:start-end
            current_file = file_match.group(1).strip()
            current_line = int(file_match.group(2))
            
            current_message = []
            current_suggestion = None
            continue
        
        # Check for code suggestion
        if '```suggestion' in line:
            # Get all lines until the closing ```
            suggestion_lines = []
            j = i + 1
            while j < len(lines) and '```' not in lines[j]:
                suggestion_lines.append(lines[j])
                j += 1
            current_suggestion = '\n'.join(suggestion_lines).strip()
            continue
        
        # Skip suggestion blocks
        if '```' in line:
            continue
            
        # Add line to current message if we have a file context
        if current_file and line.strip():
            current_message.append(line)
    
    # Add the last comment
    if current_file and current_message:
        comments.append(Comment(
            current_file,
            current_line,
            '\n'.join(current_message).strip(),
            current_suggestion
        ))
    
    # Debug output
    print("\nParsed Comments:")
    for comment in comments:
        print(f"\nFile: {comment.file}, Line: {comment.line}")
        print(f"Message: {comment.message}")
        if comment.suggestion:
            print(f"Suggestion: {comment.suggestion}")
    
    return comments

class GitHubApp:
    def __init__(self, app_id: str, private_key: str):
        self.app_id = app_id
        self.private_key = private_key

    def generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication"""
        now = datetime.now(UTC)
        payload = {
            'iat': int(now.timestamp()),  # Issued at time
            'exp': int((now + timedelta(minutes=10)).timestamp()),  # Expires in 10 minutes
            'iss': self.app_id
        }
        
        return jwt.encode(payload, self.private_key, algorithm='RS256')

    async def get_installation_token(self, installation_id: str) -> str:
        """Get an installation access token"""
        jwt_token = self.generate_jwt()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Code-Helper-App"
                }
            )
            
            if response.status_code != 201:
                raise Exception(f"Failed to get installation token: {response.text}")
            
            return response.json()['token']

class ReviewBot:
    def __init__(self):
        self.app = GitHubApp(
            app_id=os.getenv("GITHUB_APP_ID"),
            private_key=os.getenv("GITHUB_APP_PRIVATE_KEY")
        )
        self.installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID")
        self._token = None
        self._token_expires_at = None

    async def get_token(self) -> str:
        """Get a valid installation token, refreshing if necessary"""
        if not self._token or not self._token_expires_at or datetime.now(UTC) >= self._token_expires_at:
            self._token = await self.app.get_installation_token(self.installation_id)
            self._token_expires_at = datetime.now(UTC) + timedelta(minutes=55)  # Tokens expire after 1 hour
        return self._token

# Modify the create_github_review function to use the ReviewBot
review_bot = ReviewBot()

async def create_github_review(pr_url: str, comments: List[Comment]):
    """Create GitHub review with comments and suggestions"""
    # Get fresh installation token
    token = await review_bot.get_token()
    
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Code-Helper-App"
    }
    
    # Only proceed if we have comments
    if not comments:
        print("No comments parsed from the review")
        return
    
    # First, fetch the PR diff to get the line positions
    async with httpx.AsyncClient() as client:
        try:
            # Get PR details to get the diff URL
            pr_response = await client.get(
                pr_url,
                headers=headers
            )
            if pr_response.status_code != 200:
                print(f"Error fetching PR details: {pr_response.text}")
                return
                
            pr_data = pr_response.json()
            
            # Extract owner, repo, and PR number from the PR URL
            # Example URL: https://api.github.com/repos/owner/repo/pulls/123
            pr_parts = pr_url.split('/')
            owner = pr_parts[-4]
            repo = pr_parts[-3]
            pr_number = pr_parts[-1]
            
            # Construct the correct diff URL
            diff_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
            
            # Get the diff content
            diff_response = await client.get(
                diff_url,
                headers=headers
            )
            if diff_response.status_code != 200:
                print(f"Error fetching diff: {diff_response.text}")
                return
                
            diff_data = diff_response.json()
            
            # Create review data with line comments
            review_data = {
                "body": "Code review by DeepSeek AI",
                "event": "COMMENT",
                "comments": []
            }
            
            # Process each comment and find its position in the diff
            for comment in comments:
                # Find the file in the diff
                file_found = False
                for file_data in diff_data:
                    if file_data['filename'] == comment.file:
                        file_found = True
                        # Get the patch content
                        patch = file_data.get('patch', '')
                        if not patch:
                            continue
                            
                        # Find the line in the patch
                        patch_lines = patch.split('\n')
                        line_found = False
                        position = 0
                        
                        for i, line in enumerate(patch_lines):
                            if line.startswith('@@'):
                                # Extract line number from diff hunk header
                                try:
                                    line_num = int(line.split(',')[0].split('@@')[1].strip().split(' ')[0])
                                    if line_num <= comment.line:
                                        position = i + (comment.line - line_num)
                                        line_found = True
                                        break
                                except (IndexError, ValueError):
                                    continue
                        
                        if line_found:
                            review_data["comments"].append({
                                "path": comment.file,
                                "position": position,
                                "body": f"{comment.message}\n\n" + (f"```suggestion\n{comment.suggestion}\n```" if comment.suggestion else "")
                            })
                        break
                
                if not file_found:
                    print(f"Warning: Could not find file {comment.file} in the diff")
            
            # Create the review with line comments
            response = await client.post(
                f"{pr_url}/reviews",
                headers=headers,
                json=review_data
            )
            
            if response.status_code == 201:
                print(f"Successfully created review with {len(comments)} line comments")
            else:
                print(f"Error creating review: {response.text}")
                
        except Exception as e:
            print(f"Request failed: {str(e)}")
            raise

@app.post("/process-prompt", response_model=DeepSeekResponse)
async def process_prompt(
    request: PromptRequest, 
    client: httpx.AsyncClient = Depends(get_deepseek_client)
):
    """
    Receives a prompt as a string and forwards it to the DeepSeek API
    """
    try:
        # Configure the request to DeepSeek API
        deepseek_request = {
            "model": "deepseek-coder",
            "messages": [
                {"role": "user", "content": request.content}
            ],
            "temperature": 0.7,
            "max_tokens": 2000  # Increased for longer reviews
        }
        
        # Make request to DeepSeek API
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=deepseek_request
        )
        
        # Check if the request was successful
        response.raise_for_status()
        data = response.json()
        
        # Log the response to console
        print("\nDeepSeek API Response:")
        print(data["choices"][0]["message"]["content"])
        
        review_text = data["choices"][0]["message"]["content"]
        
        # Parse and create GitHub review if PR URL is provided
        if request.pr_url:
            try:
                comments = parse_review_comments(review_text)
                await create_github_review(request.pr_url, comments)
                print(f"Created GitHub review with {len(comments)} comments")
            except Exception as e:
                print(f"Error creating GitHub review: {str(e)}")
        
        return DeepSeekResponse(
            generated_text=review_text
        )
        
    except httpx.HTTPStatusError as e:
        # Handle HTTP errors from the DeepSeek API
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"DeepSeek API error: {e.response.text}"
        )
    except Exception as e:
        # Handle any other errors
        raise HTTPException(
            status_code=500,
            detail=f"Error processing request: {str(e)}"
        )
    finally:
        # Close the HTTP client
        await client.aclose()

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 