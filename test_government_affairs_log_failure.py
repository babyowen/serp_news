from unittest.mock import Mock
import pytest
import government_affairs_scoring as scoring

def test_log_failure_does_not_repeat_successful_model_request(monkeypatch):
    monkeypatch.setattr(scoring, "value", lambda name: {scoring.TOPIC: "system"} if name == "KEYWORD_SPECIFIC_SYSTEM_PROMPTS" else "{content}")
    monkeypatch.setattr(scoring, "model_arguments", lambda _: {})
    monkeypatch.setattr(scoring, "model_credentials", lambda _: ("key", "https://example.test"))
    monkeypatch.setattr(scoring, "record_result", Mock(side_effect=OSError("disk full")))
    pool = Mock()
    client = pool.get_client.return_value.with_options.return_value
    client.chat.completions.create.return_value.choices = [Mock(message=Mock(content="3"))]
    monkeypatch.setattr(scoring.time, "sleep", lambda _: None)
    with pytest.raises(OSError, match="disk full"):
        scoring.score_result("title", "body", "term", scoring.TOPIC, pool)
    assert client.chat.completions.create.call_count == 1
