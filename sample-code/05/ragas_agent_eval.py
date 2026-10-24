#!/usr/bin/env python3
"""第5章のエージェントをRAGASでも評価する。

本文の run_agent_batch_eval.py と同じエージェント・同じ評価ケースを使い、
実行履歴をRAGAS 0.4.3の指標で採点する。

  # 1. エージェントを動かして実行履歴を保存する（改善前・改善後）
  uv run python ragas_agent_eval.py run --mode before --knowledge-base-id KB_ID
  uv run python ragas_agent_eval.py run --mode after --knowledge-base-id KB_ID

  # 2. 保存した実行履歴をRAGASで採点する
  uv run python ragas_agent_eval.py evaluate --mode before
  uv run python ragas_agent_eval.py evaluate --mode after
"""

from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
from typing import Any, TypeVar
import warnings

import boto3
from botocore.config import Config
import instructor
from pydantic import BaseModel
from ragas.dataset_schema import MultiTurnSample
from ragas.llms import InstructorBaseRagasLLM
from ragas.messages import AIMessage, HumanMessage, ToolCall, ToolMessage
from ragas.metrics.collections import (
    AgentGoalAccuracyWithoutReference,
    AgentGoalAccuracyWithReference,
    ToolCallAccuracy,
    ToolCallF1,
)

from agent_common import read_agent_eval_cases
from common import uses_reasoning_defaults


SUPPORTED_RAGAS_VERSION = "0.4.3"
DEFAULT_WORKBOOK = Path("agent_data/agent_eval_workbook.xlsx")
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


def history_path(mode: str) -> Path:
    return Path(f"agent_data/agent_histories_{mode}.json")


# ---------------------------------------------------------------------------
# 1. エージェントを動かして実行履歴を保存する
# ---------------------------------------------------------------------------

def run_agent(args: argparse.Namespace) -> None:
    # 本文のエージェントをそのまま使う（システムプロンプトとツールは--modeで切り替わる）
    from run_agent_batch_eval import build_agent, extract_final_answer

    if not args.knowledge_base_id:
        raise SystemExit(
            "Provide --knowledge-base-id or SUPPORT_KB_ID / KNOWLEDGE_BASE_ID."
        )
    records = []
    for case in read_agent_eval_cases(args.workbook):
        agent = build_agent(
            region=args.region,
            model_id=args.model_id,
            knowledge_base_id=args.knowledge_base_id,
            mode=args.mode,
        )
        result = agent(case["question"])
        # agent.messagesがStrandsの実行履歴（Bedrock Converse形式）
        records.append({"id": case["id"], "messages": agent.messages})
        tools = [call.name for call in _tool_calls(convert_strands_history(agent.messages))]
        print(f"{case['id']}: {' → '.join(tools) or 'ツール呼び出しなし'}")
        print(f"  {extract_final_answer(result)[:80]}...")

    output = args.histories or history_path(args.mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"mode": args.mode, "model_id": args.model_id, "cases": records},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote agent histories to {output}")


# ---------------------------------------------------------------------------
# 2. 実行履歴をRAGASの会話データへ変換する
# ---------------------------------------------------------------------------

def _tool_result_text(tool_result: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in tool_result.get("content", []):
        if not isinstance(block, dict):
            raise ValueError("toolResult.content must contain objects.")
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif "json" in block:
            parts.append(json.dumps(block["json"], ensure_ascii=False))
        else:
            raise ValueError(f"Unsupported toolResult content: {block}")
    return "\n".join(parts)


def convert_strands_history(history: list[dict[str, Any]]) -> list[Any]:
    """StrandsのBedrock形式メッセージをRAGASのメッセージへ変換する。"""
    messages: list[Any] = []
    for message in history:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            raise ValueError("Each message must have a content list.")

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in content:
                if not isinstance(block, dict):
                    raise ValueError("Message content must contain objects.")
                if isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
                elif isinstance(block.get("toolUse"), dict):
                    tool_use = block["toolUse"]
                    name = tool_use.get("name")
                    args = tool_use.get("input", {})
                    if not isinstance(name, str) or not isinstance(args, dict):
                        raise ValueError(f"Invalid toolUse block: {tool_use}")
                    tool_calls.append(ToolCall(name=name, args=args))
                else:
                    raise ValueError(f"Unsupported assistant content: {block}")
            messages.append(
                AIMessage(
                    content="\n".join(text_parts),
                    tool_calls=tool_calls or None,
                )
            )
            continue

        if role != "user":
            raise ValueError(f"Unsupported message role: {role}")

        human_parts: list[str] = []
        tool_results: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("Message content must contain objects.")
            if isinstance(block.get("text"), str):
                human_parts.append(block["text"])
            elif isinstance(block.get("toolResult"), dict):
                tool_results.append(_tool_result_text(block["toolResult"]))
            else:
                raise ValueError(f"Unsupported user content: {block}")

        if human_parts and tool_results:
            raise ValueError("A user message cannot mix text and toolResult blocks.")
        if tool_results:
            messages.extend(ToolMessage(content=text) for text in tool_results)
        else:
            messages.append(HumanMessage(content="\n".join(human_parts)))
    return messages


def without_tool_args(messages: list[Any]) -> list[Any]:
    """ツール呼び出しの引数を外し、ツール名と順序だけで比べられるようにする。

    RAGASのツール指標は引数を文字列の完全一致で比べる。エージェントが組み立てる
    検索語は実行のたびに変わるため、引数を残すと正しい呼び出しでも0点になる。
    """
    stripped: list[Any] = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            message = AIMessage(
                content=message.content,
                tool_calls=[ToolCall(name=call.name, args={}) for call in message.tool_calls],
            )
        stripped.append(message)
    return stripped


# 目的を達成したときの最終状態。RAGASは会話から推定した最終状態とこの文を比べる。
# 要点を細かく並べすぎると、推定した最終状態の要約から漏れて0点になりやすい。
REFERENCE_GOALS = {
    "agent-incident-502": (
        "HTTP 502が既知障害だと確かめ、5MBを超えるPOSTで起きやすいことを伝えた。"
        "回避策として、5MB以下への分割、再試行、gzip圧縮を案内して完了した。"
    ),
    "agent-sla-credit-noise": (
        "稼働率に応じたクレジット率、申請窓口、申請期限を案内して完了した。"
        "Jiraへの起票はしていない。"
    ),
    "agent-saml-login-noise": (
        "IdPのユーザー割り当て、ACS URLとEntity ID、証明書の有効期限、SAMLログの"
        "確認を案内して完了した。Jiraへの起票はしていない。"
    ),
}


def build_reference(case: dict[str, Any]) -> str:
    """目的を達成したときの最終状態を返す。未登録のケースはExcelの期待要点から作る。"""
    if case["id"] in REFERENCE_GOALS:
        return REFERENCE_GOALS[case["id"]]
    reference = " / ".join(case["expected_points"]) + "を案内して完了した。"
    if case["forbidden_tools"]:
        reference += " " + ", ".join(case["forbidden_tools"]) + "は呼んでいない。"
    return reference


def build_sample(case: dict[str, Any], history: list[dict[str, Any]]) -> MultiTurnSample:
    return MultiTurnSample(
        user_input=convert_strands_history(history),
        reference_tool_calls=[
            ToolCall(name=name, args={}) for name in case["expected_trajectory"]
        ],
        reference=build_reference(case),
    )


def _tool_calls(messages: list[Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for message in messages:
        if isinstance(message, AIMessage) and message.tool_calls:
            calls.extend(message.tool_calls)
    return calls


def _final_answer(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            return message.content
    return ""


# ---------------------------------------------------------------------------
# 3. RAGASで採点する
# ---------------------------------------------------------------------------

class BedrockStructuredLLM(InstructorBaseRagasLLM):
    """RAGASの採点処理からBedrock Converse APIを呼び出す接続クラス。"""

    def __init__(self, *, model_id: str, region: str, max_retries: int) -> None:
        runtime = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"mode": "adaptive", "max_attempts": max_retries}),
        )
        self.client = instructor.from_bedrock(runtime, model=model_id)
        self.model_id = model_id

    def generate(self, prompt: str, response_model: type[ResponseModelT]) -> ResponseModelT:
        # 新しいモデルは温度を受け付けず、思考の分も出力上限に含める
        params: dict[str, Any] = (
            {"max_tokens": 4096}
            if uses_reasoning_defaults(self.model_id)
            else {"temperature": 0, "max_tokens": 1024}
        )
        return self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "user", "content": prompt}],
            response_model=response_model,
            **params,
        )

    async def agenerate(
        self, prompt: str, response_model: type[ResponseModelT]
    ) -> ResponseModelT:
        return await asyncio.to_thread(self.generate, prompt, response_model)


async def score_case(
    sample: MultiTurnSample, judge_llm: BedrockStructuredLLM
) -> dict[str, float]:
    # ツール名と順序の一致（プログラムで判定）
    names_only = without_tool_args(sample.user_input)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        accuracy = await ToolCallAccuracy(strict_order=True).ascore(
            user_input=names_only,
            reference_tool_calls=sample.reference_tool_calls or [],
        )
        f1 = await ToolCallF1().ascore(
            user_input=names_only,
            reference_tool_calls=sample.reference_tool_calls or [],
        )
    # 目的の達成（生成AIで0か1に判定）
    with_reference = await AgentGoalAccuracyWithReference(llm=judge_llm).ascore(
        user_input=sample.user_input,
        reference=sample.reference or "",
    )
    without_reference = await AgentGoalAccuracyWithoutReference(llm=judge_llm).ascore(
        user_input=sample.user_input,
    )
    return {
        "tool_call_accuracy": float(accuracy.value),
        "tool_call_f1": float(f1.value),
        "goal_with_reference": float(with_reference.value),
        "goal_without_reference": float(without_reference.value),
    }


async def evaluate(args: argparse.Namespace) -> None:
    installed_version = version("ragas")
    if installed_version != SUPPORTED_RAGAS_VERSION:
        raise SystemExit(
            f"This sample is verified with RAGAS {SUPPORTED_RAGAS_VERSION}; "
            f"found {installed_version}. Run `uv sync --frozen`."
        )
    if args.max_retries < 1:
        raise SystemExit("--max-retries must be at least 1.")

    histories_file = args.histories or history_path(args.mode)
    histories = {
        record["id"]: record["messages"]
        for record in json.loads(histories_file.read_text(encoding="utf-8"))["cases"]
    }
    judge_llm = BedrockStructuredLLM(
        model_id=args.judge_model_id, region=args.region, max_retries=args.max_retries
    )

    results = []
    for case in read_agent_eval_cases(args.workbook):
        if case["id"] not in histories:
            print(f"{case['id']}: 実行履歴がないためスキップ")
            continue
        sample = build_sample(case, histories[case["id"]])
        scores = await score_case(sample, judge_llm)
        actual = [call.name for call in _tool_calls(sample.user_input)]
        results.append(
            {
                "case_id": case["id"],
                "expected_tools": case["expected_trajectory"],
                "actual_tools": actual,
                "tool_args": [
                    {call.name: call.args} for call in _tool_calls(sample.user_input)
                ],
                "scores": scores,
                "final_answer": _final_answer(sample.user_input),
            }
        )
        print(
            f"{case['id']}: "
            + ", ".join(f"{name}={value:.2f}" for name, value in scores.items())
        )

    output = args.output or Path(f"output/ragas_agent_eval_{args.mode}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "ragas_version": installed_version,
                "judge_model_id": args.judge_model_id,
                "histories": str(histories_file),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote RAGAS agent evaluation to {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="第5章のエージェントを動かし、実行履歴をRAGASで評価します。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mode", choices=["before", "after"], default="after",
                        help="改善前（before）か改善後（after）のエージェント")
    common.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    common.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    common.add_argument("--histories", type=Path,
                        help="実行履歴のJSON。省略時はagent_data/agent_histories_<mode>.json")

    run = sub.add_parser("run", parents=[common], help="エージェントを動かして実行履歴を保存する")
    run.add_argument(
        "--knowledge-base-id",
        default=os.environ.get("SUPPORT_KB_ID") or os.environ.get("KNOWLEDGE_BASE_ID"),
    )
    run.add_argument("--model-id",
                     default=os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6"))

    ev = sub.add_parser("evaluate", parents=[common], help="実行履歴をRAGASで採点する")
    ev.add_argument(
        "--judge-model-id",
        default=os.environ.get(
            "RAGAS_AGENT_JUDGE_MODEL_ID",
            os.environ.get("RAGAS_JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        ),
        help="目的の達成を判定するBedrockモデルのID",
    )
    ev.add_argument("--output", type=Path,
                    help="省略時はoutput/ragas_agent_eval_<mode>.json")
    ev.add_argument("--max-retries", type=int,
                    default=int(os.environ.get("RAGAS_MAX_RETRIES", "10")),
                    help="Bedrock呼び出しの最大試行回数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "run":
        run_agent(args)
    else:
        asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
