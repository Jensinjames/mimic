from types import SimpleNamespace

import pytest

from mimic import codegen


class FakeResponses:
    def __init__(self, text="class Client:\n    pass\n"):
        self.text = text
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=self.text)


class FakeClient:
    def __init__(self, text="class Client:\n    pass\n"):
        self.responses = FakeResponses(text)


def test_redact_json_secrets_recursively():
    text = '{"token":"abc","nested":{"password":"pw","safe":7},"items":[{"api_key":"k"}]}'
    redacted = codegen.redact_text(text)
    assert "abc" not in redacted
    assert "pw" not in redacted
    assert '"safe": 7' in redacted
    assert redacted.count("<redacted>") == 3


def test_generate_uses_responses_api_and_disables_storage():
    client = FakeClient("```python\nclass Demo:\n    pass\n```")
    source = codegen.generate(
        "api.example.com",
        [{"method": "GET", "path": "/v1", "status": 200, "query": "", "request_body": "", "response_body": "{}"}],
        model="gpt-test",
        client=client,
    )
    assert source == "class Demo:\n    pass\n"
    assert client.responses.kwargs["model"] == "gpt-test"
    assert client.responses.kwargs["store"] is False


def test_generate_requires_output_text():
    with pytest.raises(RuntimeError, match="no generated source"):
        codegen.generate("api.example.com", [], client=FakeClient("   "))
