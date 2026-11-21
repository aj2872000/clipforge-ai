import os
import sys
import argparse
from huggingface_hub import HfApi, login

def deploy(repo_id: str, token: str = None):
    print(f"🚀 Deploying ClipForge AI to Hugging Face Space: {repo_id}...")
    
    # If token passed or in env
    hf_token = token or os.environ.get("HF_TOKEN")
    if not hf_token:
        # Prompt user if running interactively
        print("Please provide your Hugging Face Access Token (with 'Write' permission).")
        print("You can get one at: https://huggingface.co/settings/tokens")
        hf_token = input("Enter HF Token: ").strip()
        if not hf_token:
            print("[ERROR] Hugging Face Token is required to deploy.")
            sys.exit(1)

    api = HfApi(token=hf_token)

    try:
        user_info = api.whoami()
        username = user_info['name']
        print(f"✓ Authenticated as: {username}")
        
        # If user passed just space name (without username/)
        if "/" not in repo_id:
            repo_id = f"{username}/{repo_id}"

        print(f"📦 Creating / Verifying Docker Space: {repo_id}...")
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            exist_ok=True
        )

        project_dir = os.path.dirname(os.path.abspath(__file__))
        print(f"Uploading project files from {project_dir}...")
        
        api.upload_folder(
            folder_path=project_dir,
            repo_id=repo_id,
            repo_type="space",
            ignore_patterns=[
                "storage/downloads/*",
                "!storage/downloads/.gitkeep",
                "storage/clips/*",
                "!storage/clips/.gitkeep",
                "storage/subtitles/*",
                "!storage/subtitles/.gitkeep",
                ".git/*",
                "__pycache__/*",
                "*.pyc",
                ".vscode/*"
            ]
        )

        space_url = f"https://huggingface.co/spaces/{repo_id}"
        app_url = f"https://{repo_id.replace('/', '-')}.hf.space"

        print("\n" + "="*50)
        print("🎉 Deployment successfully initiated!")
        print(f"Space Repo: {space_url}")
        print(f"Live App:   {app_url}")
        print("="*50)
        print("\nHugging Face is now building the Docker image. Your app will be live in 1-2 minutes.")
        
    except Exception as e:
        print(f"[ERROR] Deployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ClipForge AI to Hugging Face Spaces")
    parser.add_argument("--repo-id", default="clipforge-ai", help="Space name (e.g., 'clipforge-ai' or 'username/clipforge-ai')")
    parser.add_argument("--token", default=None, help="Hugging Face User Access Token (Write access)")
    args = parser.parse_args()

    deploy(args.repo_id, args.token)
