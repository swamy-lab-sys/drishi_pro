"""Route-level tests for the modular interview blueprint."""

import json

import pytest

from flask import Response
from web.server import app
from app.services import interview_service


@pytest.fixture
def client():
    with app.test_client() as c:
        yield c


def test_api_ask_uses_db(monkeypatch, client):
    called = {}

    def fake_find(question, want_code=False):
        called["question"] = question
        return ("db-answer", 0.9, 12)

    def fake_set_complete(question, answer, metrics):
        called["saved"] = (question, answer, metrics)

    monkeypatch.setattr(interview_service.qa_database, "find_answer", fake_find)
    monkeypatch.setattr(interview_service.answer_storage, "set_complete_answer", fake_set_complete)
    response = client.post("/api/ask", json={"question": "lambda"})
    data = response.get_json()
    assert response.status_code == 200
    assert data["answer"] == "db-answer"
    assert data["question"].lower().startswith("what is")
    assert called["saved"][0].lower().startswith("what is")


def test_api_stream_forwards(monkeypatch, client):
    monkeypatch.setattr(interview_service, "stream_response", lambda: Response("event: test\ndata: {}\n\n", mimetype="text/event-stream"))
    response = client.get("/api/stream")
    assert response.status_code == 200
    assert b"event: test" in response.get_data()


def test_api_cc_question_forward(monkeypatch, client):
    monkeypatch.setattr(interview_service, "cc_question_payload", lambda payload: ({"status": "ok"}, 202))
    response = client.post("/api/cc_question", json={"question": "lambda"})
    assert response.status_code == 202
    assert response.get_json()["status"] == "ok"
