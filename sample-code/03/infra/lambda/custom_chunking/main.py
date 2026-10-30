"""Bedrock Knowledge Bases の POST_CHUNKING Lambda。

FAQ Markdown を「## Q」で始まる見出しごとに1チャンクへ分割し、
FAQ番号とカテゴリのメタデータを付与して intermediate S3 バケットへ書き戻す。

Bedrock KB の契約:
- priorTask が "PARSING"（NONE chunking と組み合わせた場合）のとき、
  contentBatches にはパース済みの全文が入る。
- Lambda は分割結果を {"fileContents": [{"contentBody": "...", ...}]} 形式で
  intermediate バケットへ書き出し、そのキーを outputFiles で返す。
"""
import json
import os
import re
import boto3

s3 = boto3.client("s3")


def load_content_batches(bucket: str, batches: list[dict]) -> str:
    """Bedrock が書き込んだ contentBatches を連結して 1 本の文字列にする。"""
    texts: list[str] = []
    for batch in batches:
        obj = s3.get_object(Bucket=bucket, Key=batch["key"])
        body = json.loads(obj["Body"].read())
        for item in body.get("fileContents", []):
            texts.append(item.get("contentBody", ""))
    return "\n".join(texts)


def split_faq(text: str) -> list[dict]:
    """`## Q01. 〜` で始まる見出しごとに分割して chunks を返す。"""
    pattern = re.compile(r"^##\s*(Q\d+)\.\s*(.+?)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    chunks: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        faq_id = m.group(1)
        question = m.group(2).strip()
        chunks.append({
            "contentBody": block,
            "contentType": "str",
            "contentMetadata": {
                "faq_id": faq_id,
                "question": question,
                "chunk_source": "custom-lambda",
            },
        })
    return chunks


def handler(event, context):
    bucket = event["bucketName"]
    input_files = event["inputFiles"]
    output_files = []

    for input_file in input_files:
        full_text = load_content_batches(bucket, input_file["contentBatches"])
        chunks = split_faq(full_text)
        if not chunks:
            # 区切りが見つからない場合は 1 チャンクとしてそのまま通す
            chunks = [{
                "contentBody": full_text,
                "contentType": "str",
                "contentMetadata": {"chunk_source": "custom-lambda-fallback"},
            }]

        s3_uri = input_file["originalFileLocation"]["s3_location"]["uri"]
        basename = s3_uri.rsplit("/", 1)[-1]
        output_key = f"custom-chunk-output/{basename}.chunks.json"
        payload = {"fileContents": chunks}
        s3.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )

        output_files.append({
            "originalFileLocation": input_file["originalFileLocation"],
            "fileMetadata": input_file.get("fileMetadata", {}),
            "contentBatches": [{"key": output_key}],
        })

    return {"outputFiles": output_files}
