"""本編と同じConverse APIで新モデルを試す。思考設定はモデルの既定値を使う。"""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

import boto3
from botocore.config import Config


def run_turn(client, model_id, messages, max_tokens):
    started = perf_counter()
    response = client.converse(
        modelId=model_id,
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens},
    )
    message = response["output"]["message"]
    # 表示用テキストと、次のターンへ渡す元のメッセージを分ける。
    answer = "\n".join(block["text"] for block in message["content"] if "text" in block)
    messages.append(message)
    return {
        "answer": answer,
        "stopReason": response["stopReason"],
        "usage": response["usage"],
        "elapsedSeconds": round(perf_counter() - started, 3),
        "contentTypes": [list(block) for block in message["content"]],
    }


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("出力上限には1以上を指定してください。")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default="us.anthropic.claude-opus-5-5")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--question", default="Amazon Bedrockとは何ですか？3文で簡潔に説明してください。"
    )
    parser.add_argument("--follow-up", help="続けて送る質問。元の応答を含めて会話を継続する")
    parser.add_argument("--max-tokens", type=positive_int, default=4096)
    parser.add_argument(
        "--output", type=Path,
        help="回答・使用量のJSON保存先（既存ファイルは上書きしない）",
    )
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("保存先が存在します。別のファイル名を指定してください。")
    client = boto3.client(
        "bedrock-runtime", region_name=args.region,
        config=Config(read_timeout=120, retries={"total_max_attempts": 1}),
    )
    messages = []
    turns = []
    for question in [args.question, args.follow_up]:
        if question is None:
            continue
        messages.append({"role": "user", "content": [{"text": question}]})
        result = run_turn(client, args.model_id, messages, args.max_tokens)
        turns.append({"question": question, **result})
        print(result["answer"])
        print("終了理由:", result["stopReason"])
        print("使用量:", json.dumps(result["usage"], ensure_ascii=False))
        print("所要時間:", result["elapsedSeconds"], "秒")
        if result["stopReason"] != "end_turn" or not result["answer"]:
            break
    if args.output:
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(
                {"modelId": args.model_id, "region": args.region, "turns": turns},
                file, ensure_ascii=False, indent=2,
            )
            file.write("\n")
    if any(turn["stopReason"] != "end_turn" or not turn["answer"] for turn in turns):
        raise SystemExit("回答が完了していません。終了理由と出力上限を確認してください。")


if __name__ == "__main__":
    main()
