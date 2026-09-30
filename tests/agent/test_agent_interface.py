import inspect

from agent import SQLAgent


def test_ask_signature_is_stable():
    params = list(inspect.signature(SQLAgent.ask).parameters)
    assert params == ["self", "question", "history"]
