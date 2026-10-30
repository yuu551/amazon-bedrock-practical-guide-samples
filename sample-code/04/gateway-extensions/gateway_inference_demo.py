#!/usr/bin/env python3
"""AgentCore GatewayのInference Targetを作成し、モデルを1回呼び出す。"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError


MANTLE_POLICY_ARN = (
    "arn:aws:iam::aws:policy/AmazonBedrockMantleInferenceAccess"
)
TERMINAL_FAILURES = {"FAILED", "CREATE_FAILED", "UPDATE_FAILED"}


def inference_target_configuration() -> dict[str, Any]:
    """Bedrock Mantle組み込みコネクタのターゲット設定を返す。"""

    return {
        "inference": {
            "connector": {
                "source": {"connectorId": "bedrock-mantle"},
            }
        }
    }


def choose_model(models_response: dict[str, Any], model_contains: str | None) -> str:
    """モデル一覧からチャット確認に使うモデルIDを1つ選ぶ。"""

    model_ids = [
        item.get("id")
        for item in models_response.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if not model_ids:
        raise RuntimeError("Gatewayのモデル一覧が空でした")

    if model_contains:
        for model_id in model_ids:
            if model_contains.lower() in model_id.lower():
                return model_id
        raise RuntimeError(
            f"--model-contains={model_contains!r}に一致するモデルがありません"
        )

    preferences = (
        "deepseek.v3.2",
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    )
    for preferred in preferences:
        for model_id in model_ids:
            if preferred in model_id:
                return model_id
    return model_ids[0]


def assistant_text(response: dict[str, Any]) -> str:
    """MessagesまたはChat Completions互換レスポンスから本文を取り出す。"""

    content = response.get("content")
    if isinstance(content, list):
        text = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
    else:
        try:
            text = response["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise RuntimeError(f"回答本文を取得できませんでした: {response}") from error
    if not text:
        raise RuntimeError(f"回答本文が空でした: {response}")
    return text


def inference_request(model_id: str) -> tuple[str, dict[str, Any]]:
    """モデルに合うGatewayパスとリクエスト本文を返す。"""

    body = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": "AgentCore Gatewayの役割を日本語で一文で説明してください。",
            }
        ],
        "max_tokens": 100,
    }
    if "/anthropic.claude-" in model_id:
        return "v1/messages", body
    return "v1/chat/completions", {**body, "stream": False}


def wait_until(
    description: str,
    load_status: Callable[[], str],
    expected: str,
    timeout_seconds: int,
    interval_seconds: int = 2,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = load_status()
        if status == expected:
            return
        if status in TERMINAL_FAILURES:
            raise RuntimeError(f"{description}に失敗しました（status={status}）")
        time.sleep(interval_seconds)
    raise TimeoutError(
        f"{description}を{timeout_seconds}秒以内に確認できませんでした"
    )


def signed_json_request(
    session: boto3.Session,
    region: str,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GatewayデータプレーンへSigV4署名付きJSONリクエストを送る。"""

    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if url.rstrip("/").endswith("/v1/messages"):
        headers["anthropic-version"] = "2023-06-01"

    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS認証情報を取得できませんでした")
    aws_request = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(
        credentials.get_frozen_credentials(), "bedrock-agentcore", region
    ).add_auth(aws_request)

    request = Request(
        url=url,
        data=payload,
        headers=dict(aws_request.headers.items()),
        method=method,
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Gateway呼び出しに失敗しました（HTTP {error.code}）: {detail}"
        ) from error


class GatewayInferenceDemo:
    def __init__(
        self,
        session: boto3.Session,
        region: str,
        timeout_seconds: int,
        model_contains: str | None,
    ) -> None:
        self.session = session
        self.region = region
        self.timeout_seconds = timeout_seconds
        self.model_contains = model_contains
        self.iam = session.client("iam")
        self.sts = session.client("sts", region_name=region)
        self.control = session.client("bedrock-agentcore-control", region_name=region)
        self.role_name: str | None = None
        self.role_arn: str | None = None
        self.gateway_id: str | None = None
        self.gateway_url: str | None = None
        self.target_id: str | None = None

    def create_role(self, suffix: str) -> None:
        account_id = self.sts.get_caller_identity()["Account"]
        self.role_name = f"book-gateway-inference-{suffix}"
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
                }
            ],
        }
        response = self.iam.create_role(
            RoleName=self.role_name,
            Description="Temporary role for the book Gateway supplement",
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Tags=[{"Key": "Purpose", "Value": "book-gateway-supplement"}],
        )
        self.role_arn = response["Role"]["Arn"]
        self.iam.attach_role_policy(
            RoleName=self.role_name,
            PolicyArn=MANTLE_POLICY_ARN,
        )
        print(f"IAMロール: 作成 ({self.role_name})")
        # AgentCoreがロールを引き受ける前にIAMの反映を待つ。
        time.sleep(10)

    def create_gateway(self, suffix: str) -> None:
        assert self.role_arn is not None
        name = f"book-gateway-inference-{suffix}"
        deadline = time.monotonic() + 60
        while True:
            try:
                response = self.control.create_gateway(
                    name=name,
                    description="書籍補足のInference Target動作確認用",
                    roleArn=self.role_arn,
                    authorizerType="AWS_IAM",
                    tags={"Purpose": "book-gateway-supplement"},
                )
                break
            except ClientError as error:
                if (
                    error.response.get("Error", {}).get("Code")
                    != "ValidationException"
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(3)

        self.gateway_id = response["gatewayId"]
        self.gateway_url = response.get("gatewayUrl") or (
            f"https://{self.gateway_id}.gateway.bedrock-agentcore."
            f"{self.region}.amazonaws.com"
        )
        wait_until(
            "GatewayがREADYになること",
            lambda: self.control.get_gateway(
                gatewayIdentifier=self.gateway_id or ""
            )["status"],
            "READY",
            self.timeout_seconds,
        )
        print(f"Gateway: READY ({self.gateway_id})")

    def create_target(self, suffix: str) -> None:
        assert self.gateway_id is not None
        response = self.control.create_gateway_target(
            gatewayIdentifier=self.gateway_id,
            name=f"bedrock-mantle-{suffix}",
            description="Bedrock Mantleのモデルを公開するInference Target",
            targetConfiguration=inference_target_configuration(),
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        self.target_id = response["targetId"]
        deadline = time.monotonic() + self.timeout_seconds
        synchronized = False
        while time.monotonic() < deadline:
            target = self.control.get_gateway_target(
                gatewayIdentifier=self.gateway_id,
                targetId=self.target_id,
            )
            if target["status"] == "READY":
                break
            if target["status"] in TERMINAL_FAILURES:
                if synchronized:
                    raise RuntimeError(
                        "Inference Targetの作成に失敗しました: "
                        f"{target.get('statusReasons', [])}"
                    )
                print(
                    "Inference Targetを再同期: "
                    f"{target.get('statusReasons', [])}"
                )
                # IAMポリシーの反映直後は初回同期が失敗することがある。
                time.sleep(10)
                self.control.synchronize_gateway_targets(
                    gatewayIdentifier=self.gateway_id,
                    targetIdList=[self.target_id],
                )
                synchronized = True
            time.sleep(3)
        else:
            raise TimeoutError("Inference TargetがREADYになりませんでした")
        print(f"Inference Target: READY ({self.target_id})")

    def invoke(self) -> None:
        assert self.gateway_url is not None
        models_url = f"{self.gateway_url.rstrip('/')}/inference/v1/models"
        models = signed_json_request(
            self.session, self.region, "GET", models_url
        )
        model_id = choose_model(models, self.model_contains)
        print(f"モデル一覧: {len(models.get('data', []))}件")
        print(f"選択モデル: {model_id}")

        operation_path, request_body = inference_request(model_id)
        completion = signed_json_request(
            self.session,
            self.region,
            "POST",
            f"{self.gateway_url.rstrip('/')}/inference/{operation_path}",
            request_body,
        )
        print(f"モデル応答: {assistant_text(completion)}")

    def run(self) -> None:
        suffix = f"{int(time.time())}-{secrets.token_hex(2)}"
        identity = self.sts.get_caller_identity()
        print(f"実行アカウント: {identity['Account']} / リージョン: {self.region}")
        self.create_role(suffix)
        self.create_gateway(suffix)
        self.create_target(suffix)
        self.invoke()

    def cleanup(self) -> None:
        deleted: list[str] = []
        if self.gateway_id and self.target_id:
            try:
                self.control.delete_gateway_target(
                    gatewayIdentifier=self.gateway_id,
                    targetId=self.target_id,
                )
            except self.control.exceptions.ResourceNotFoundException:
                pass
            else:
                deadline = time.monotonic() + self.timeout_seconds
                while time.monotonic() < deadline:
                    try:
                        self.control.get_gateway_target(
                            gatewayIdentifier=self.gateway_id,
                            targetId=self.target_id,
                        )
                    except self.control.exceptions.ResourceNotFoundException:
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError("Inference Targetを削除できませんでした")
            deleted.append("Target")

        if self.gateway_id:
            try:
                self.control.delete_gateway(gatewayIdentifier=self.gateway_id)
            except self.control.exceptions.ResourceNotFoundException:
                pass
            else:
                deadline = time.monotonic() + self.timeout_seconds
                while time.monotonic() < deadline:
                    try:
                        self.control.get_gateway(gatewayIdentifier=self.gateway_id)
                    except self.control.exceptions.ResourceNotFoundException:
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError("Gatewayを削除できませんでした")
            deleted.append("Gateway")

        if self.role_name:
            try:
                self.iam.detach_role_policy(
                    RoleName=self.role_name,
                    PolicyArn=MANTLE_POLICY_ARN,
                )
            except self.iam.exceptions.NoSuchEntityException:
                pass
            try:
                self.iam.delete_role(RoleName=self.role_name)
            except self.iam.exceptions.NoSuchEntityException:
                pass
            else:
                deleted.append("IAMロール")

        if deleted:
            print(f"クリーンアップ: {'、'.join(deleted)}を削除")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一時GatewayでBedrock Mantleのモデルを1回呼び出します。"
    )
    parser.add_argument("--yes", action="store_true", help="一時リソースの作成と削除を許可する")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--model-contains",
        help="モデル一覧から選ぶIDの一部。省略時はDeepSeek V3.2を優先する",
    )
    arguments = parser.parse_args()
    if not arguments.yes:
        parser.error("実行するには--yesを指定してください")

    session = boto3.Session(region_name=arguments.region)
    demo = GatewayInferenceDemo(
        session,
        arguments.region,
        arguments.timeout,
        arguments.model_contains,
    )
    active_error = False
    try:
        demo.run()
    except BaseException:
        active_error = True
        raise
    finally:
        try:
            demo.cleanup()
        except Exception as cleanup_error:
            if active_error:
                print(f"クリーンアップにも失敗しました: {cleanup_error}", file=sys.stderr)
            else:
                raise


if __name__ == "__main__":
    main()
