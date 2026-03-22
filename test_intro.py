"""Test self-introduction flow."""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8000"

def get(path):
    r = urllib.request.urlopen(BASE + path)
    return json.loads(r.read())

def post(path, data):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def patch(path, data):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH"
    )
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

# 1. Get users
print("=== Users ===")
users = get("/api/users")
for u in users:
    intro = (u.get("self_introduction") or "").strip()
    print(f"  id={u['id']} name={u['name']} intro={'SET (' + str(len(intro)) + ' chars)' if intro else 'EMPTY'}")

# 2. Get active user
print("\n=== Session Info ===")
info = get("/api/session-info")
print(f"  active user: {info.get('user_name')}")

# 3. Test ask endpoint with intro question
print("\n=== Testing 'tell me about yourself' ===")
resp = post("/api/ask", {"question": "tell me about yourself"})
print(f"  source: {resp.get('source')}")
print(f"  answer preview: {(resp.get('answer') or '')[:120]}")
