import unittest
from unittest.mock import Mock

from botocore.session import Session
from botocore.validate import validate_parameters
from retrieval import Settings, agentic_request, retrieve_request, run
from app import error_event


class Stream:
    def __init__(self, items):
        self.items = items
        self.closed = False

    def __iter__(self):
        return iter(self.items)

    def close(self):
        self.closed = True


DOC = {"content": {"text": "監査ログ365日", "mimeType": "text/plain"},
       "sourceRetriever": {"identifier": "fixture"}}


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings("ABCDEFGHIJ")

    def test_requests_match_pinned_sdk(self):
        service = Session().get_service_model("bedrock-agent-runtime")
        for name, request in [("Retrieve", retrieve_request(self.settings, "質問")),
                              ("AgenticRetrieveStream", agentic_request(self.settings, "質問", False))]:
            validate_parameters(request, service.operation_model(name).input_shape)

    def test_native_final_replaces_deltas_and_keeps_citation_indices(self):
        stream = Stream([{"responseEvent": {"text": "途中"}},
                         {"result": {"results": [{"content": {"mimeType": "image/png"}}, DOC],
                                     "generatedResponse": {"answer": "確定した回答", "citations": [
                                         {"startIndex": 0, "endIndex": 6, "references": [{"resultIndex": 1}]}]}}}])
        runtime = Mock()
        runtime.agentic_retrieve_stream.return_value = {"stream": stream}
        rows = list(run(self.settings, "質問", "agentic-managed", runtime))
        self.assertEqual(rows[-1]["answer"], "確定した回答")
        self.assertEqual(rows[-1]["citations"][0]["references"][0]["resultIndex"], 1)
        self.assertEqual(next(r for r in rows if r["type"] == "sources")["documents"][1]["text"], "監査ログ365日")
        self.assertTrue(stream.closed)

    def test_missing_result_and_stream_exception_are_not_success(self):
        for items in [[{"responseEvent": {"text": "途中"}}], [{"throttlingException": {}}]]:
            stream, runtime = Stream(items), Mock()
            runtime.agentic_retrieve_stream.return_value = {"stream": stream}
            with self.assertRaises(RuntimeError):
                list(run(self.settings, "質問", "agentic-managed", runtime))
            self.assertTrue(stream.closed)

    def test_agentic_search_only_then_sonnet_stream(self):
        search = Stream([{"result": {"results": [DOC]}}])
        runtime, llm = Mock(), Mock()
        runtime.agentic_retrieve_stream.return_value = {"stream": search}
        answer = Stream([{"contentBlockDelta": {"delta": {"text": "365日"}}},
                         {"messageStop": {"stopReason": "end_turn"}},
                         {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 10}}}])
        llm.converse_stream.return_value = {"stream": answer}
        rows = list(run(self.settings, "質問", "agentic-model", runtime, llm))
        self.assertFalse(runtime.agentic_retrieve_stream.call_args.kwargs["generateResponse"])
        prompt = llm.converse_stream.call_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("監査ログ365日", prompt)
        self.assertEqual(llm.converse_stream.call_args.kwargs["modelId"], "us.anthropic.claude-sonnet-4-6")
        self.assertEqual(rows[-1]["answer"], "365日")
        self.assertEqual(rows[-1]["metrics"]["text_events"], 1)
        self.assertTrue(answer.closed)

    def test_empty_search_does_not_invoke_model(self):
        runtime, llm = Mock(), Mock()
        runtime.retrieve.return_value = {"retrievalResults": []}
        rows = list(run(self.settings, "質問", "retrieve-model", runtime, llm))
        llm.converse_stream.assert_not_called()
        self.assertEqual(rows[-1]["model"], "モデル呼び出しなし")

    def test_stream_exception_and_disconnect_are_not_success(self):
        for items in [[{"modelStreamErrorException": {}}], []]:
            runtime, llm = Mock(), Mock()
            runtime.retrieve.return_value = {"retrievalResults": [DOC]}
            stream = Stream(items)
            llm.converse_stream.return_value = {"stream": stream}
            with self.assertRaises(RuntimeError):
                list(run(self.settings, "質問", "retrieve-model", runtime, llm))
            self.assertTrue(stream.closed)

    def test_sonnet_default_stream_and_truncation(self):
        settings = Settings("ABCDEFGHIJ")
        self.assertEqual(settings.model, "us.anthropic.claude-sonnet-4-6")
        for stop in ("end_turn", "max_tokens"):
            runtime, llm = Mock(), Mock()
            runtime.retrieve.return_value = {"retrievalResults": [DOC]}
            stream = Stream([{"contentBlockDelta": {"delta": {"text": "365日"}}},
                             {"messageStop": {"stopReason": stop}},
                             {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 10}}}])
            llm.converse_stream.return_value = {"stream": stream}
            if stop == "end_turn":
                rows = list(run(settings, "質問", "retrieve-model", runtime, llm))
                self.assertEqual(rows[-1]["answer"], "365日")
                self.assertEqual(rows[-1]["usage"]["output_tokens"], 10)
                service = Session().get_service_model("bedrock-runtime")
                validate_parameters(llm.converse_stream.call_args.kwargs,
                                    service.operation_model("ConverseStream").input_shape)
            else:
                with self.assertRaises(RuntimeError):
                    list(run(settings, "質問", "retrieve-model", runtime, llm))
            self.assertTrue(stream.closed)

    def test_error_message_does_not_expose_vendor_body(self):
        exc = RuntimeError("秘密の本文")
        exc.response = object()  # dict以外のレスポンスでも本文を出さない。
        self.assertNotIn("秘密", str(error_event(exc)))


if __name__ == "__main__":
    unittest.main()
