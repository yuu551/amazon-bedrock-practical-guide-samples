"""検索結果から同じ条件で回答を生成し、LLMで回答品質を採点する。"""

from __future__ import annotations

import json
import re
from typing import Any


METRICS = ("correctness", "completeness", "groundedness")

# 温度を受け付けず、適応的思考が既定で有効なモデル。
# モデルIDと推論プロファイルARNのどちらでも末尾で判定する。
REASONING_MODELS = (
    "anthropic.claude-sonnet-5",
    "anthropic.claude-sonnet-5-5",
    "anthropic.claude-opus-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-haiku-5-5",
    "openai.gpt-6-sol",
    "openai.gpt-6.1-sol",
    "openai.gpt-6-luna",
)


def inference_config(model_id: str, max_tokens: int) -> dict[str, Any]:
    """新しいモデルには温度を送らず、思考で使う分も出力上限に含める。"""
    if model_id.endswith(REASONING_MODELS):
        return {"maxTokens": max(max_tokens, 4096)}
    return {"temperature": 0, "maxTokens": max_tokens}


def extract_text(response: dict[str, Any]) -> str:
    return "".join(
        block.get("text", "")
        for block in response.get("output", {}).get("message", {}).get("content", [])
        if isinstance(block, dict)
    ).strip()


def extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"JudgeのJSONを抽出できませんでした: {text[:200]}")
        return json.loads(candidate[start : end + 1])


def format_context(results: list[dict[str, Any]]) -> str:
    parts = []
    for index, result in enumerate(results, 1):
        text = result.get("text", "").strip()
        parts.append(f"[{index}] 出典: {result.get('file', '?')}\n{text}")
    return "\n\n".join(parts)


def generate_answer(
    runtime: Any,
    *,
    model_id: str,
    question: str,
    results: list[dict[str, Any]],
) -> str:
    prompt = f"""あなたはSampleConnect Platformの問い合わせ対応アシスタントです。
次の検索結果だけを根拠に、日本語で質問へ回答してください。
必要な条件や手順を省略せず、根拠の末尾に[1]のような引用番号を付けてください。
検索結果だけでは答えられない内容は推測せず、その旨を明示してください。

# 質問
{question}

# 検索結果
{format_context(results)}
""".strip()
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=inference_config(model_id, 1600),
    )
    return extract_text(response)


def judge_answer(
    runtime: Any,
    *,
    model_id: str,
    question: str,
    expected_points: list[str],
    answer: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    prompt = f"""あなたはRAG回答の品質評価者です。
質問、期待する要点、生成回答、回答生成時に渡された検索結果を読み、採点してください。

# 質問
{question}

# 期待する要点
{json.dumps(expected_points, ensure_ascii=False)}

# 生成回答
{answer}

# 回答生成時に渡された検索結果
{format_context(results)}

# 採点基準
- correctness: 質問への回答に事実誤認がないか
- completeness: 期待する要点と質問で求められた条件を満たすか
- groundedness: 回答の主張が検索結果に支えられているか

各項目は1.0（満たす）、0.5（一部満たす）、0.0（満たさない）のいずれかにしてください。
JSONオブジェクトだけを返してください。

{{
  "scores": {{
    "correctness": 0.0,
    "completeness": 0.0,
    "groundedness": 0.0
  }},
  "reason_by_metric": {{
    "correctness": "",
    "completeness": "",
    "groundedness": ""
  }},
  "overall_summary": ""
}}
""".strip()
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=inference_config(model_id, 1000),
    )
    judged = extract_json(extract_text(response))
    scores = judged.get("scores", {})
    for metric in METRICS:
        value = float(scores.get(metric, 0))
        if value not in {0.0, 0.5, 1.0}:
            raise ValueError(f"{metric}のスコアが採点基準外です: {value}")
        scores[metric] = value
    judged["scores"] = scores
    return judged


def evaluate_answer(
    runtime: Any,
    *,
    answer_model_id: str,
    judge_model_id: str,
    question: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    answer = generate_answer(
        runtime,
        model_id=answer_model_id,
        question=question["question"],
        results=results,
    )
    judge = judge_answer(
        runtime,
        model_id=judge_model_id,
        question=question["question"],
        expected_points=question.get("expected_keywords", []),
        answer=answer,
        results=results,
    )
    return {"answer": answer, "judge": judge}
