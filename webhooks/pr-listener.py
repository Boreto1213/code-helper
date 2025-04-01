from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import hmac
import hashlib
import json
from pydantic import BaseModel
from typing import Optional
import httpx
import os
from dotenv import load_dotenv

app = FastAPI()

# Load environment variables
load_dotenv()

# Add GitHub token for API access
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Your webhook secret (set this in your environment variables in production)
WEBHOOK_SECRET = "your-webhook-secret"

# Near the top of your file, after loading environment variables
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is not set")

class PullRequestPayload(BaseModel):
    action: str
    number: int
    pull_request: dict
    repository: dict
    sender: dict

def verify_signature(payload_body: bytes, signature_header: str) -> bool:
    """Verify that the webhook payload was sent from GitHub"""
    if not signature_header:
        return False

    # Get the signature from the header
    sha_name, signature = signature_header.split('=')
    if sha_name != 'sha256':
        return False

    # Create our own signature
    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode('utf-8'),
        payload_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)

@app.get("/")
async def root():
    return {"message": "Webhook server is running"}

async def get_pr_changes(pr_url: str, pr_info: dict) -> dict:
    """Fetch the PR changes from GitHub API"""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-Webhook"
    }
    
    async with httpx.AsyncClient() as client:
        # Print the URL being called (without the token) for debugging
        print(f"Calling GitHub API: {pr_url}/files")
        
        files_response = await client.get(f"{pr_url}/files", headers=headers)
        
        if files_response.status_code != 200:
            print(f"Error response: {files_response.text}")
            raise HTTPException(
                status_code=files_response.status_code,
                detail=f"Failed to fetch PR changes: {files_response.text}"
            )
        
        files = files_response.json()
        
        # Fetch complete file content for each changed file
        changed_files = []
        for file in files:
            # Get the raw content URL from the file info
            contents_url = file.get('contents_url', '')
            if contents_url:
                # Extract owner, repo, and path from the contents_url
                # Example URL: https://api.github.com/repos/owner/repo/contents/path/to/file
                parts = contents_url.split('/')
                if len(parts) >= 7:
                    owner = parts[4]
                    repo = parts[5]
                    path = '/'.join(parts[7:])
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{pr_info['head_branch']}/{path}"
                    
                    # Fetch the complete file content
                    content_response = await client.get(raw_url, headers=headers)
                    if content_response.status_code == 200:
                        complete_content = content_response.text
                    else:
                        print(f"Failed to fetch content for {file.get('filename')}: {content_response.text}")
                        complete_content = "Could not fetch complete file content"
                else:
                    complete_content = "Invalid contents URL format"
            else:
                complete_content = "No contents URL available"
            
            changed_files.append({
                "filename": file.get('filename', ''),
                "status": file.get('status', ''),
                "additions": file.get('additions', 0),
                "deletions": file.get('deletions', 0),
                "patch": file.get('patch', ''),  # This contains the actual diff
                "complete_content": complete_content  # Add the complete file content
            })
        
        changes = {
            "files_changed": len(files),
            "additions": sum(f.get('additions', 0) for f in files),
            "deletions": sum(f.get('deletions', 0) for f in files),
            "changed_files": changed_files
        }
        
        # Now we can use pr_info since it's passed as a parameter
        print(f"""
New PR #{pr_info['number']} by {pr_info['author']}
Title: {pr_info['title']}
From: {pr_info['head_branch']} → {pr_info['base_branch']}
Changes:
- Files changed: {changes['files_changed']}
- Additions: {changes['additions']}
- Deletions: {changes['deletions']}

Changed files:
{chr(10).join(f'''
File: {f['filename']} ({f['status']})
Complete file content:
{f['complete_content']}
Diff:
{f['patch']}
''' for f in changes['changed_files'])}
""")
        
        return changes

# Add this function to create the PR review prompt
def create_pr_review_prompt(pr_info: dict, changes: dict) -> str:
    files_with_changes = "\n".join([
        f"File: {f['filename']} ({f['status']})\n"
        f"Complete file content:\n{f['complete_content']}\n"
        f"Changes:\n{f['patch']}\n"
        for f in changes['changed_files'] if f['patch']
    ])
    
    prompt = f"""Please review this pull request:

Title: {pr_info['title']}
Author: {pr_info['author']}
Branch: {pr_info['head_branch']} → {pr_info['base_branch']}

Summary of changes:
- Files changed: {changes['files_changed']}
- Additions: {changes['additions']}
- Deletions: {changes['deletions']}

Detailed changes:
{files_with_changes}

Please provide a code review that includes:
1. Overall assessment
2. Potential issues or bugs
3. Code style and best practices
4. Suggestions for improvements

Your response must strictly adhere to the following format:
[File path]:[Line number(s)]
Type: [Comment | Request Change]
Feedback: [Detailed feedback and reasoning here.]
Suggestion: [Explicit suggestion or corrected code snippet, if applicable.]

IMPORTANT: Use the line numbers from the complete file content to reference specific lines in your comments. The line numbers should match the actual line numbers in the complete file content.
"""
    return prompt

# Add this function to send the review request
async def send_to_deepseek(prompt: str, pr_data: dict):
    async with httpx.AsyncClient() as client:
        try:
            # Convert HTML URL to API URL
            repo_url = pr_data['repository']['url']  # This is already in API format
            pr_number = pr_data['number']
            api_url = f"{repo_url}/pulls/{pr_number}"
            
            response = await client.post(
                "http://localhost:8000/process-prompt",
                json={
                    "content": prompt,
                    "pr_url": api_url  # Use the API URL
                }
            )
            response.raise_for_status()
            review = response.json()
            print("\n=== DeepSeek PR Review ===")
            print(review['generated_text'])
            print("========================\n")
        except Exception as e:
            print(f"Error getting PR review: {str(e)}")

@app.post("/webhook")
async def github_webhook(request: Request):
    # Get the signature from headers
    signature_header = request.headers.get('x-hub-signature-256')
    event_type = request.headers.get('x-github-event')
    
    # Get the raw request body
    body = await request.body()
    
    # Verify webhook signature
    if not verify_signature(body, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse the payload
    payload = json.loads(body)

    # Print the raw payload for debugging
    # print("Raw payload:", json.dumps(payload, indent=2))

    # Handle pull request events
    if event_type == 'pull_request':
        pr_data = PullRequestPayload(**payload)
        
        # Handle different PR actions
        if pr_data.action == 'opened':
            # Get basic PR info
            pr_info = {
                "number": pr_data.number,
                "title": pr_data.pull_request.get('title', ''),
                "author": pr_data.pull_request.get('user', {}).get('login', ''),
                "base_branch": pr_data.pull_request.get('base', {}).get('ref', ''),
                "head_branch": pr_data.pull_request.get('head', {}).get('ref', '')
            }
            
            try:
                # Get PR changes
                changes = await get_pr_changes(pr_data.pull_request.get('url', ''), pr_info)
                
                # Create and send review prompt
                review_prompt = create_pr_review_prompt(pr_info, changes)
                await send_to_deepseek(review_prompt, payload)
                
            except Exception as e:
                print(f"Error processing PR: {str(e)}")
                import traceback
                print(traceback.format_exc())
            
        elif pr_data.action == 'closed':
            merged = pr_data.pull_request.get('merged', False)
            status = 'merged' if merged else 'closed'
            print(f"PR #{pr_data.number} was {status}")
            # Add your custom logic for closed PRs here
            
        elif pr_data.action == 'synchronize':
            print(f"PR #{pr_data.number} was updated with new commits")
            # Add your custom logic for updated PRs here
            
        # Add more conditions for other PR actions as needed
        
        return JSONResponse(content={
            "status": "success",
            "message": f"Processed {pr_data.action} event for PR #{pr_data.number}"
        })
    
    return JSONResponse(content={"status": "success", "message": "Event received"})

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)