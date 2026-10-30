from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


# Bedrock Converse API の document.name に使える文字は英数字・空白・ハイフン・
# 括弧・角括弧のみで、連続空白も禁止される。日本語ファイル名などは拒否される
# ため、API に渡す前にこの関数で安全な名前へ置き換える。
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9\-\(\)\[\] ]+")


def sanitize_document_name(name: str) -> str:
    cleaned = _SAFE_NAME_PATTERN.sub("-", name)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -")
    return cleaned or "document"


SUPPORTED_DOCUMENT_FORMATS = {
    ".csv": "csv",
    ".doc": "doc",
    ".docx": "docx",
    ".html": "html",
    ".md": "md",
    ".pdf": "pdf",
    ".txt": "txt",
    ".xls": "xls",
    ".xlsx": "xlsx",
}


def guess_document_format(path: Path) -> str | None:
    return SUPPORTED_DOCUMENT_FORMATS.get(path.suffix.lower())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def uses_reasoning_defaults(model_id: str | None) -> bool:
    return (model_id or "").endswith(REASONING_MODELS)


def converse_inference_config(
    model_id: str | None, max_tokens: int, temperature: float
) -> dict[str, Any]:
    """新しいモデルには温度を送らず、思考で使う分も出力上限に含める。"""
    if uses_reasoning_defaults(model_id):
        return {"maxTokens": max(max_tokens, 4096)}
    return {"maxTokens": max_tokens, "temperature": temperature}


# 新しいモデルではKB既定のプロンプトが用意されていない場合がある。
# 引用を返すため、AWSが展開する出力形式の指示も保持する。
KB_GENERATION_PROMPT = (
    "検索結果に基づいて日本語で回答してください。資料にないことは推測せず、不明と伝えてください。"
    "資料の適用時期が異なる場合は区別してください。\n"
    "質問: $query$\n検索結果:\n$search_results$\n$output_format_instructions$"
)
KB_ORCHESTRATION_PROMPT = (
    "会話履歴と質問から、関連資料を探す検索クエリを作成してください。\n"
    "会話履歴:\n$conversation_history$\n質問: $query$\n$output_format_instructions$"
)


def knowledge_base_prompts(model_id: str | None) -> dict[str, Any]:
    """新しいモデルのときだけ、RetrieveAndGenerateに渡す2つのプロンプトを返す。"""
    if not uses_reasoning_defaults(model_id):
        return {}
    return {
        "generationConfiguration": {
            "promptTemplate": {"textPromptTemplate": KB_GENERATION_PROMPT},
            "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
        },
        "orchestrationConfiguration": {
            "promptTemplate": {"textPromptTemplate": KB_ORCHESTRATION_PROMPT},
            "inferenceConfig": {"textInferenceConfig": {"maxTokens": 4096}},
        },
    }


def extract_text_from_converse_response(response: dict[str, Any]) -> str:
    content = response.get("output", {}).get("message", {}).get("content", [])
    parts: list[str] = []
    for block in content:
        text = block.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

    depth = 0
    in_string = False
    escape = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break

    if end is None:
        raise ValueError("JSON object appears to be incomplete.")

    return json.loads(text[start:end])


def build_metadata_prompt(schema: dict[str, Any]) -> str:
    description = schema.get(
        "description",
        "Knowledge Bases用ドキュメントのメタデータスキーマ。",
    )
    attributes = schema.get("attributes", [])

    lines = [
        "Amazon Bedrock Knowledge BasesのS3メタデータサイドカーファイルを生成してください。",
        description,
        "JSONだけを返してください。",
        "出力形式:",
        "{",
        '  "metadataAttributes": {',
        '    "field_name": {',
        '      "value": {',
        '        "type": "STRING | NUMBER | BOOLEAN | STRING_LIST",',
        '        "stringValue": "...",',
        '        "numberValue": 123,',
        '        "booleanValue": true,',
        '        "stringListValue": ["a", "b"]',
        "      },",
        '      "includeForEmbedding": true',
        "    }",
        "  }",
        "}",
        "文書の内容から根拠を示せるフィールドだけを含めてください。",
        "判断できないフィールドは値を推測せず、省略してください。",
        "次のスキーマに従ってください:",
    ]

    for attribute in attributes:
        allowed_values = attribute.get("allowed_values")
        line = f"- {attribute['key']} ({attribute['type']}): {attribute['description']}"
        if allowed_values:
            line += f" 指定可能な値: {', '.join(allowed_values)}"
        lines.append(line)

    return "\n".join(lines)


def _string_schema(attribute: dict[str, Any]) -> dict[str, Any]:
    string_schema: dict[str, Any] = {"type": "string"}
    allowed_values = attribute.get("allowed_values")
    if allowed_values:
        string_schema["enum"] = allowed_values
    return string_schema


def _metadata_value_schema(attribute: dict[str, Any]) -> dict[str, Any]:
    attribute_type = attribute["type"]
    if attribute_type == "STRING":
        return {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["STRING"]},
                "stringValue": _string_schema(attribute),
            },
            "required": ["type", "stringValue"],
            "additionalProperties": False,
        }
    if attribute_type == "NUMBER":
        return {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["NUMBER"]},
                "numberValue": {"type": "number"},
            },
            "required": ["type", "numberValue"],
            "additionalProperties": False,
        }
    if attribute_type == "BOOLEAN":
        return {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["BOOLEAN"]},
                "booleanValue": {"type": "boolean"},
            },
            "required": ["type", "booleanValue"],
            "additionalProperties": False,
        }
    if attribute_type == "STRING_LIST":
        item_schema = {"type": "string"}
        allowed_values = attribute.get("allowed_values")
        if allowed_values:
            item_schema["enum"] = allowed_values
        return {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["STRING_LIST"]},
                "stringListValue": {
                    "type": "array",
                    "items": item_schema,
                },
            },
            "required": ["type", "stringListValue"],
            "additionalProperties": False,
        }
    raise ValueError(f"Unsupported metadata attribute type: {attribute_type}")


def build_metadata_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    metadata_attribute_properties: dict[str, Any] = {}
    for attribute in schema.get("attributes", []):
        metadata_attribute_properties[attribute["key"]] = {
            "type": "object",
            "properties": {
                "value": _metadata_value_schema(attribute),
                "includeForEmbedding": {"type": "boolean"},
            },
            "required": ["value", "includeForEmbedding"],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "metadataAttributes": {
                "type": "object",
                "properties": metadata_attribute_properties,
                "additionalProperties": False,
            }
        },
        "required": ["metadataAttributes"],
        "additionalProperties": False,
    }


def build_metadata_output_config(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "textFormat": {
            "type": "json_schema",
            "structure": {
                "jsonSchema": {
                    "name": "kb_metadata_sidecar",
                    "description": schema.get(
                        "description",
                        "Amazon Bedrock Knowledge Bases用メタデータサイドカーの出力。",
                    ),
                    "schema": json.dumps(
                        build_metadata_output_schema(schema),
                        ensure_ascii=False,
                    ),
                }
            },
        }
    }


def extract_json_from_converse_response(response: dict[str, Any]) -> dict[str, Any] | None:
    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        payload = block.get("json")
        if isinstance(payload, dict):
            return payload
    return None


def generate_metadata_payload(
    runtime_client: Any,
    model_id: str,
    schema: dict[str, Any],
    document_name: str,
    document_format: str,
    document_bytes: bytes,
    use_structured_output: bool = True,
) -> dict[str, Any]:
    request = {
        "modelId": model_id,
        "system": [{"text": build_metadata_prompt(schema)}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            "この文書のメタデータを生成してください。"
                            "JSONオブジェクトだけを返し、それ以外は出力しないでください。"
                        )
                    },
                    {
                        "document": {
                            "format": document_format,
                            "name": sanitize_document_name(document_name),
                            "source": {"bytes": document_bytes},
                        }
                    },
                ],
            }
        ],
        "inferenceConfig": converse_inference_config(model_id, 1200, 0),
    }

    if use_structured_output:
        request["outputConfig"] = build_metadata_output_config(schema)

    try:
        response = runtime_client.converse(**request)
    except Exception:
        if not use_structured_output:
            raise
        request.pop("outputConfig", None)
        response = runtime_client.converse(**request)

    json_payload = extract_json_from_converse_response(response)
    if json_payload is not None:
        return json_payload
    return extract_json_object(extract_text_from_converse_response(response))


def metadata_output_path(source_path: Path, output_dir: Path | None = None) -> Path:
    parent = output_dir or source_path.parent
    return parent / f"{source_path.name}.metadata.json"


def validate_metadata_payload(
    schema: dict[str, Any],
    payload: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    metadata_attributes = payload.get("metadataAttributes")
    if not isinstance(metadata_attributes, dict):
        return ['Missing or invalid "metadataAttributes" object.']

    attributes_by_key = {
        attribute["key"]: attribute for attribute in schema.get("attributes", [])
    }

    for key, item in metadata_attributes.items():
        attribute = attributes_by_key.get(key)
        if attribute is None:
            issues.append(f"Unexpected metadata field: {key}")
            continue
        if not isinstance(item, dict):
            issues.append(f"Field {key} must be an object.")
            continue
        if not isinstance(item.get("includeForEmbedding"), bool):
            issues.append(f"Field {key} must include boolean includeForEmbedding.")

        value = item.get("value")
        if not isinstance(value, dict):
            issues.append(f"Field {key} must include a value object.")
            continue

        expected_type = attribute["type"]
        actual_type = value.get("type")
        if actual_type != expected_type:
            issues.append(
                f"Field {key} expected type {expected_type} but got {actual_type!r}."
            )
            continue

        allowed_values = attribute.get("allowed_values")
        if expected_type == "STRING":
            string_value = value.get("stringValue")
            if not isinstance(string_value, str):
                issues.append(f"Field {key} must include stringValue.")
                continue
            if allowed_values and string_value not in allowed_values:
                issues.append(
                    f"Field {key} has unsupported value {string_value!r}."
                )
        elif expected_type == "NUMBER":
            number_value = value.get("numberValue")
            if not isinstance(number_value, (int, float)):
                issues.append(f"Field {key} must include numberValue.")
        elif expected_type == "BOOLEAN":
            boolean_value = value.get("booleanValue")
            if not isinstance(boolean_value, bool):
                issues.append(f"Field {key} must include booleanValue.")
        elif expected_type == "STRING_LIST":
            string_list_value = value.get("stringListValue")
            if not isinstance(string_list_value, list) or not all(
                isinstance(item_value, str) for item_value in string_list_value
            ):
                issues.append(f"Field {key} must include stringListValue.")
                continue
            if allowed_values:
                invalid_values = [
                    item_value
                    for item_value in string_list_value
                    if item_value not in allowed_values
                ]
                if invalid_values:
                    issues.append(
                        f"Field {key} has unsupported values: {', '.join(invalid_values)}."
                    )
        else:
            issues.append(f"Unsupported schema type for field {key}: {expected_type}")

    return issues


def extract_retrieved_references(response: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for citation in response.get("citations", []):
        references.extend(citation.get("retrievedReferences", []))
    return references


def reference_text(reference: dict[str, Any]) -> str:
    content = reference.get("content", {})
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return ""


def reference_location(reference: dict[str, Any]) -> str:
    location = reference.get("location", {})
    if "s3Location" in location:
        return location["s3Location"].get("uri", "")
    if "webLocation" in location:
        return location["webLocation"].get("url", "")
    if "confluenceLocation" in location:
        return location["confluenceLocation"].get("url", "")
    if "salesforceLocation" in location:
        return location["salesforceLocation"].get("url", "")
    if "sharePointLocation" in location:
        return location["sharePointLocation"].get("url", "")
    if "kendraDocumentLocation" in location:
        return location["kendraDocumentLocation"].get("uri", "")
    if "customDocumentLocation" in location:
        return location["customDocumentLocation"].get("id", "")
    if "sqlLocation" in location:
        return location["sqlLocation"].get("query", "")
    return ""


def keyword_score(text: str, expected_keywords: list[str] | None) -> float | None:
    if not expected_keywords:
        return None
    lowered_text = text.lower()
    matches = sum(1 for keyword in expected_keywords if keyword.lower() in lowered_text)
    return matches / len(expected_keywords)


def expected_source_hit(
    references: list[dict[str, Any]],
    expected_source_substring: str | None,
) -> bool | None:
    if not expected_source_substring:
        return None
    lowered_hint = expected_source_substring.lower()
    return any(lowered_hint in reference_location(ref).lower() for ref in references)


def expected_sources_recall(
    references: list[dict[str, Any]],
    expected_sources: list[str] | None,
) -> float | None:
    """期待ソースのうち、引用に含まれた割合を返す。"""
    if not expected_sources:
        return None
    locations = [reference_location(ref).lower() for ref in references]
    hits = 0
    for hint in expected_sources:
        lowered = hint.lower()
        if any(lowered in loc for loc in locations):
            hits += 1
    return hits / len(expected_sources)


def top_citation_hit(
    references: list[dict[str, Any]],
    expected_sources: list[str] | None,
) -> bool | None:
    """最初に引用されたドキュメントが期待ソースのいずれかを指すかを返す。"""
    if not expected_sources or not references:
        return None
    top = reference_location(references[0]).lower()
    return any(hint.lower() in top for hint in expected_sources)


def judge_answer_completeness(
    runtime_client: Any,
    judge_model_id: str,
    question: str,
    answer: str,
    expected_keywords: list[str] | None,
) -> float | None:
    """軽量LLMによる回答完全性の判定。

    スコアは 0.0 / 0.5 / 1.0 の3値で、質問に対する網羅度を表す。
    第5章で扱う本格的なLLM-as-Judgeの導入前に、改善の効き方を比較するための
    簡易指標として使う想定。
    """
    if not answer.strip():
        return 0.0
    keyword_hint = (
        f"Keywords that should appear: {', '.join(expected_keywords)}."
        if expected_keywords
        else ""
    )
    prompt = (
        "Evaluate whether the answer completely addresses the question.\n"
        "Return a single number: 1.0 (fully addresses), 0.5 (partial), 0.0 (irrelevant or missing).\n"
        "Ignore formatting and politeness. Focus on whether the required facts are present.\n\n"
        f"Question: {question}\n"
        f"Answer: {answer}\n"
        f"{keyword_hint}\n"
        "Score:"
    )
    try:
        response = runtime_client.converse(
            modelId=judge_model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig=converse_inference_config(judge_model_id, 16, 0),
        )
    except Exception:
        return None
    text = extract_text_from_converse_response(response).strip()
    for token in text.replace(",", " ").split():
        try:
            value = float(token)
        except ValueError:
            continue
        if value >= 0.75:
            return 1.0
        if value >= 0.25:
            return 0.5
        return 0.0
    return None


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    table = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        table.append("| " + " | ".join(row) + " |")
    return "\n".join(table)
