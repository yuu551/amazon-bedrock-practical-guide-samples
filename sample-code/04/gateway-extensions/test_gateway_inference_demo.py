import unittest
from unittest.mock import MagicMock, patch

from botocore.credentials import Credentials

from gateway_inference_demo import (
    assistant_text,
    choose_model,
    inference_request,
    inference_target_configuration,
    signed_json_request,
)


class GatewayInferenceDemoTest(unittest.TestCase):
    def test_inference_target_configuration_uses_mantle_connector(self) -> None:
        self.assertEqual(
            inference_target_configuration(),
            {
                "inference": {
                    "connector": {"source": {"connectorId": "bedrock-mantle"}}
                }
            },
        )

    def test_choose_model_prefers_deepseek(self) -> None:
        response = {
            "data": [
                {"id": "bedrock/anthropic.claude-haiku-4-5"},
                {"id": "bedrock/deepseek.v3.2"},
            ]
        }
        self.assertEqual(
            choose_model(response, None),
            "bedrock/deepseek.v3.2",
        )

    def test_choose_model_honors_filter(self) -> None:
        response = {
            "data": [
                {"id": "bedrock/anthropic.claude-haiku-4-5"},
                {"id": "bedrock/anthropic.claude-sonnet-4-6"},
            ]
        }
        self.assertEqual(
            choose_model(response, "sonnet-4-6"),
            "bedrock/anthropic.claude-sonnet-4-6",
        )

    def test_assistant_text_extracts_anthropic_compatible_response(self) -> None:
        response = {
            "content": [
                {"type": "thinking", "thinking": "..."},
                {"type": "text", "text": " Gatewayの応答です。 "},
            ]
        }
        self.assertEqual(assistant_text(response), "Gatewayの応答です。")

    def test_assistant_text_extracts_chat_completions_response(self) -> None:
        response = {
            "choices": [{"message": {"content": " Gatewayの応答です。 "}}]
        }
        self.assertEqual(assistant_text(response), "Gatewayの応答です。")

    def test_inference_request_selects_api_for_provider(self) -> None:
        anthropic_path, _ = inference_request("target/anthropic.claude-haiku-4-5")
        openai_path, openai_body = inference_request("target/deepseek.v3.2")
        self.assertEqual(anthropic_path, "v1/messages")
        self.assertEqual(openai_path, "v1/chat/completions")
        self.assertFalse(openai_body["stream"])

    def test_messages_request_sends_signed_anthropic_version_header(self) -> None:
        session = MagicMock()
        session.get_credentials.return_value = Credentials("test-key", "test-secret")
        with patch("gateway_inference_demo.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = b'{}'
            signed_json_request(
                session,
                "us-east-1",
                "POST",
                "https://example.com/inference/v1/messages",
                {"model": "target/anthropic.claude-haiku-4-5"},
            )
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_header("Anthropic-version"), "2023-06-01")
            self.assertIn("anthropic-version", sent.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
