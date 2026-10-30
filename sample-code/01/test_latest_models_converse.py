"""回答の抽出、思考ブロックを含む会話の継続、未完了時の終了を確認する。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from latest_models_converse import main, run_turn


class LatestModelTests(unittest.TestCase):
    def test_reasoning_first_and_follow_up_keeps_original_blocks(self):
        first = {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "", "signature": "test-signature"}}},
            {"text": "前半"}, {"text": "後半"},
        ]}
        client = Mock()
        client.converse.return_value = {
            "output": {"message": first}, "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 30, "totalTokens": 40},
        }
        history = [{"role": "user", "content": [{"text": "質問"}]}]
        result = run_turn(client, "test-model", history, 4096)
        self.assertEqual(result["answer"], "前半\n後半")
        self.assertIs(history[1], first)
        history.append({"role": "user", "content": [{"text": "続きの質問"}]})
        before = deepcopy(history)
        # Mockに可変リストへの参照が残るため、呼び出した瞬間の値で検証する。
        def second(**kwargs):
            self.assertEqual(kwargs["messages"], before)
            self.assertEqual(kwargs["inferenceConfig"], {"maxTokens": 4096})
            self.assertNotIn("additionalModelRequestFields", kwargs)
            return {
                "output": {"message": {"role": "assistant", "content": [{"text": "続き"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 20, "outputTokens": 15, "totalTokens": 35},
            }
        client.converse.side_effect = second
        self.assertEqual(run_turn(client, "test-model", history, 4096)["answer"], "続き")

    def test_output_limit_saves_result_then_exits_without_follow_up(self):
        client = Mock()
        client.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [
                {"reasoningContent": {"redactedContent": b"test"}},
            ]}},
            "stopReason": "max_tokens",
            "usage": {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            args = [
                "latest_models_converse.py", "--model-id", "test-model",
                "--max-tokens", "20", "--follow-up", "続きの質問",
                "--output", str(output),
            ]
            with patch("sys.argv", args), patch("builtins.print"), patch(
                "latest_models_converse.boto3.client", return_value=client,
            ):
                with self.assertRaises(SystemExit) as caught:
                    main()
            self.assertNotEqual(caught.exception.code, 0)
            client.converse.assert_called_once()
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["turns"]), 1)
        result = saved["turns"][0]
        self.assertEqual(result["answer"], "")
        self.assertEqual(result["stopReason"], "max_tokens")
        self.assertEqual(result["usage"]["outputTokens"], 20)
        self.assertNotIn("redactedContent", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
