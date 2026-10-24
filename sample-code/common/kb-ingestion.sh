#!/usr/bin/env bash
# Knowledge Bases の取り込みを、ジョブの終了ステータスと文書統計の両方で検証する。
# 呼び出し先は set -euo pipefail を有効にしてから source すること。

wait_for_kb_ingestion() {
  if [ "$#" -lt 3 ]; then
    echo "wait_for_kb_ingestion: kb_id, data_source_id, job_id are required" >&2
    return 2
  fi

  local kb_id="$1"
  local data_source_id="$2"
  local job_id="$3"
  local label="${4:-ingestion}"
  local region="${5:-${AWS_REGION:-us-east-1}}"
  local max_attempts="${6:-60}"
  local interval_seconds="${7:-15}"
  local expected_scanned="${8:-}"
  local attempt job_json job_status failed scanned indexed modified deleted skipped reasons

  if ! command -v jq >/dev/null 2>&1; then
    echo "[${label}] error: jq is required to validate ingestion statistics" >&2
    return 2
  fi

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    job_json="$(aws bedrock-agent get-ingestion-job \
      --knowledge-base-id "$kb_id" \
      --data-source-id "$data_source_id" \
      --ingestion-job-id "$job_id" \
      --region "$region" \
      --output json)"

    job_status="$(jq -r '.ingestionJob.status // "UNKNOWN"' <<<"$job_json")"
    failed="$(jq -r '.ingestionJob.statistics.numberOfDocumentsFailed // 0' <<<"$job_json")"
    scanned="$(jq -r '.ingestionJob.statistics.numberOfDocumentsScanned // 0' <<<"$job_json")"
    indexed="$(jq -r '.ingestionJob.statistics.numberOfNewDocumentsIndexed // 0' <<<"$job_json")"
    modified="$(jq -r '.ingestionJob.statistics.numberOfModifiedDocumentsIndexed // 0' <<<"$job_json")"
    deleted="$(jq -r '.ingestionJob.statistics.numberOfDocumentsDeleted // 0' <<<"$job_json")"
    skipped="$(jq -r '.ingestionJob.statistics.numberOfDocumentsSkipped // 0' <<<"$job_json")"

    echo "[${label}] status=${job_status} scanned=${scanned} new=${indexed} modified=${modified} deleted=${deleted} skipped=${skipped} failed=${failed}"

    case "$job_status" in
      COMPLETE)
        if ! [[ "$failed" =~ ^[0-9]+$ ]] || [ "$failed" -ne 0 ]; then
          reasons="$(jq -r '.ingestionJob.failureReasons[]?' <<<"$job_json")"
          echo "[${label}] error: ingestion completed with ${failed} failed document(s)" >&2
          [ -z "$reasons" ] || printf '%s\n' "$reasons" >&2
          return 1
        fi
        if [ -n "$expected_scanned" ] && [ "$scanned" -ne "$expected_scanned" ]; then
          echo "[${label}] error: expected ${expected_scanned} scanned document(s), got ${scanned}" >&2
          return 1
        fi
        return 0
        ;;
      FAILED | STOPPED)
        reasons="$(jq -r '.ingestionJob.failureReasons[]?' <<<"$job_json")"
        echo "[${label}] error: ingestion job ended with status ${job_status}" >&2
        [ -z "$reasons" ] || printf '%s\n' "$reasons" >&2
        return 1
        ;;
    esac

    sleep "$interval_seconds"
  done

  echo "[${label}] error: ingestion job timed out after ${max_attempts} checks" >&2
  return 1
}
