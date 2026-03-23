"""Integration-style tests for the interview service endpoints."""

import json
import queue
from pathlib import Path

import pytest

import answer_cache
import answer_storage
import main
import qa_database
from app.services import interview_service


class _ImmediateThread:
    """Runs the target immediately to avoid background threading in tests."""

    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _make_stub_answer_storage(monkeypatch, storage_calls):
    def _set_complete_answer(question_text, answer_text, metrics):
        storage_calls["last"] = {
            "question": question_text,
            "answer": answer_text,
            "metrics": metrics,
        }

    def _set_processing_question(question_text):
        storage_calls["processing"] = question_text

    monkeypatch.setattr(answer_storage, "set_complete_answer", _set_complete_answer)
    monkeypatch.setattr(answer_storage, "set_processing_question", _set_processing_question)
    monkeypatch.setattr(answer_storage, "get_transcribing", lambda: "typing…")


def test_ask_question_payload_uses_db_hit(monkeypatch):
    calls = {}
    _make_stub_answer_storage(monkeypatch, calls)
    monkeypatch.setattr(qa_database, "find_answer", lambda *args, **kwargs: ("db-answer", 0.92, 7))

    payload, status = interview_service.ask_question_payload({"question": "lambda", "db_only": False})
    assert status == 200
    assert payload["answer"] == "db-answer"
    assert payload["source"] == "db"
    # DB-driven expansion: "lambda" is found in DB so it is not expanded
    assert "lambda" in calls["last"]["question"].lower()


def test_ask_question_payload_falls_back_to_llm(monkeypatch):
    calls = {}
    _make_stub_answer_storage(monkeypatch, calls)
    monkeypatch.setattr(qa_database, "find_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(interview_service.llm_client, "get_streaming_interview_answer", lambda *_, **__: ["chunk"])
    monkeypatch.setattr(interview_service.llm_client, "humanize_response", lambda text: f"humanized:{text}")
    monkeypatch.setattr(interview_service.llm_client, "get_coding_answer", lambda q: "")
    monkeypatch.setattr(main, "_submit_for_learning", lambda *_, **__: None)
    monkeypatch.setattr(answer_cache, "cache_answer", lambda *_, **__: None)
    monkeypatch.setattr(interview_service.threading, "Thread", _ImmediateThread, raising=False)

    payload, status = interview_service.ask_question_payload({"question": "lambda"})
    assert status == 200
    assert payload["status"] == "generating"
    assert "question" in payload


def test_cc_question_payload_handles_processing(monkeypatch):
    monkeypatch.setattr(qa_database, "find_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(interview_service.fragment_context, "merge_with_context", lambda text: (text, False))
    monkeypatch.setattr(interview_service, "append_chat_question", lambda *_, **__: None)
    monkeypatch.setattr(answer_storage, "set_complete_answer", lambda *_, **__: None)
    response, status = interview_service.cc_question_payload({"question": "lambda", "source": "cc"})
    assert status == 202
    assert response["status"] == "processing"


def test_stream_response_yields_init(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    drishi_dir = home_dir / ".drishi"
    drishi_dir.mkdir(parents=True)
    (drishi_dir / "current_answer.json").write_text(
        json.dumps({"session_id": "sid", "answers": [{"question": "lambda", "is_complete": True}]})
    )
    (drishi_dir / "transcribing.json").write_text(json.dumps({"text": "listening"}))

    monkeypatch.setattr(interview_service.Path, "home", classmethod(lambda cls: home_dir))
    monkeypatch.setattr(answer_storage, "get_transcribing", lambda: "listening")

    class StubQueue:
        def get(self, timeout=None):
            raise queue.Empty

        def get_nowait(self):
            raise queue.Empty

    unsubscribed = {"called": False}

    class StubEventBus:
        def subscribe(self):
            return StubQueue()

        def unsubscribe(self, _):
            unsubscribed["called"] = True

    import sys

    sys.modules["event_bus"] = StubEventBus()

    response = interview_service.stream_response()
    stream_iter = iter(response.response)
    # First chunk is the SSE retry directive, second is the init event
    first_chunk = next(stream_iter)
    init_event = first_chunk if "event: init" in first_chunk else next(stream_iter)
    assert "event: init" in init_event
    data_line = [line for line in init_event.splitlines() if line.startswith("data:")][0]
    payload = json.loads(data_line.split("data: ", 1)[1])
    assert payload["session_id"] == "sid"
    assert payload["answers"][0]["question"] == "lambda"
    stream_iter.close()
    assert unsubscribed["called"]
