"""Provision the local demo client and save its key only in the demo backend."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from limiter.models import APIClient

path = ROOT.parent / "demo_website_using_rate_limiter" / ".runtime" / "credentials.json"
path.parent.mkdir(parents=True, exist_ok=True)
api_client, _ = APIClient.objects.get_or_create(name="local-demo")
existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
key = existing.get("api_key", "")
if not key or hashlib.sha256(key.encode()).hexdigest() != api_client.api_key_hash:
    key = api_client.generate_api_key()
path.write_text(json.dumps({
    "api_client_id": api_client.pk,
    "api_client_name": api_client.name,
    "api_key": key,
}, indent=2), encoding="utf-8")
if os.name == "nt":
    owner = subprocess.check_output(["whoami"], text=True).strip()
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                    f"{owner}:(F)", "SYSTEM:(F)"], check=True, capture_output=True)
else:
    path.chmod(0o600)
print("Demo credentials ready in the demo project's .runtime/credentials.json.")
