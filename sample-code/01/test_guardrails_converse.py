"""guardrails_converse.pyの設定境界を確認するテスト。"""

import os
import unittest
from unittest.mock import MagicMock, patch

import guardrails_converse


class GuardrailsConverseTest(unittest.TestCase):
    def test_trace_summary_reports_actual_pii_actions(self) -> None:
        trace = {
            "inputAssessment": {
                "message-1": {
                    "sensitiveInformationPolicy": {
                        "piiEntities": [
                            {
                                "type": "PHONE",
                                "action": "ANONYMIZED",
                                "detected": True,
                            },
                            {
                                "type": "EMAIL",
                                "action": "BLOCKED",
                                "detected": True,
                            },
                        ]
                    }
                }
            }
        }

        summary = guardrails_converse.summarize_trace(trace)

        self.assertIn("入力の機密情報フィルタ: PHONE=ANONYMIZED", summary)
        self.assertIn("入力の機密情報フィルタ: EMAIL=BLOCKED", summary)
        self.assertNotIn("モデルが拒否回答", summary)

    def test_trace_summary_includes_output_assessment(self) -> None:
        trace = {
            "outputAssessments": {
                "message-1": [
                    {
                        "topicPolicy": {
                            "topics": [
                                {"name": "Investment advice", "action": "BLOCKED"}
                            ]
                        }
                    }
                ]
            }
        }

        self.assertEqual(
            guardrails_converse.summarize_trace(trace),
            "出力のtopicPolicy: Investment advice=BLOCKED",
        )

    def test_missing_guardrail_id_stops_before_creating_client(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(guardrails_converse.boto3, "client") as client_factory,
            self.assertRaisesRegex(SystemExit, "GUARDRAIL_ID"),
        ):
            guardrails_converse.main()

        client_factory.assert_not_called()

    def test_configured_guardrail_id_is_used_for_all_requests(self) -> None:
        client = MagicMock()
        client.converse.return_value = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": "ok"}]}},
        }
        with (
            patch.dict(os.environ, {"GUARDRAIL_ID": "reader-guardrail-id"}, clear=True),
            patch.object(guardrails_converse.boto3, "client", return_value=client),
        ):
            guardrails_converse.main()

        self.assertEqual(client.converse.call_count, 3)
        for call in client.converse.call_args_list:
            self.assertEqual(
                call.kwargs["guardrailConfig"]["guardrailIdentifier"],
                "reader-guardrail-id",
            )


if __name__ == "__main__":
    unittest.main()
