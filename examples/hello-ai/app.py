"""Smallest ShipCube app that uses the AI gateway: GET / says hello, GET /ai asks Muse.

ShipCube injects SHIPCUBE_AI_GATEWAY. The app authenticates to the gateway with a Google ID token
for its own service account (audience = the gateway URL); it never holds a model key.
"""

import os

import google.auth.transport.requests
import google.oauth2.id_token
import requests
from flask import Flask, jsonify

app = Flask(__name__)
GATEWAY = os.environ.get("SHIPCUBE_AI_GATEWAY", "")


@app.get("/")
def index():
    return "Hello from ShipCube. Try /ai for a Muse reply through the AI gateway.\n"


@app.get("/ai")
def ai():
    if not GATEWAY:
        return jsonify(error="SHIPCUBE_AI_GATEWAY is not set"), 500
    token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), GATEWAY)
    r = requests.post(f"{GATEWAY}/v1/chat/completions", timeout=50,  # inside the 60 s request timeout headers={"Authorization": f"Bearer {token}"},
                      json={"model": "default", "max_tokens": 2000,
                            "messages": [{"role": "user", "content": "In one sentence, what is ShipCube?"}]})
    body = r.json()
    if r.status_code != 200:
        return jsonify(status=r.status_code, error=body.get("error")), r.status_code
    return jsonify(model=body.get("model"), reply=body["choices"][0]["message"]["content"],
                   usage=body.get("usage"), provider=r.headers.get("X-ShipCube-Provider"))
