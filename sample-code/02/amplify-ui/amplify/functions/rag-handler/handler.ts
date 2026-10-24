import {
  BedrockAgentRuntimeClient,
  RetrieveAndGenerateStreamCommand,
} from "@aws-sdk/client-bedrock-agent-runtime"
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3"
import { getSignedUrl } from "@aws-sdk/s3-request-presigner"
import { knowledgeBasePrompts } from "./model-settings"

declare const awslambda: {
  streamifyResponse: <E, C>(
    handler: (
      event: E,
      responseStream: NodeJS.WritableStream & {
        setContentType: (type: string) => void
      },
      context: C,
    ) => Promise<void>,
  ) => unknown
  HttpResponseStream: {
    from: (
      stream: NodeJS.WritableStream,
      metadata: { statusCode: number; headers?: Record<string, string> },
    ) => NodeJS.WritableStream
  }
}

type FunctionUrlEvent = {
  body?: string
  isBase64Encoded?: boolean
  requestContext?: { http?: { method?: string } }
}

const REGION = process.env.AWS_REGION ?? "us-east-1"
const KNOWLEDGE_BASE_ID = process.env.KNOWLEDGE_BASE_ID ?? ""
const MODEL_ID = process.env.MODEL_ID ?? ""
const SOURCE_BUCKET_NAME = process.env.KNOWLEDGE_BASE_SOURCE_BUCKET_NAME ?? ""
const SOURCE_PREFIXES = (process.env.KNOWLEDGE_BASE_SOURCE_PREFIXES ?? "")
  .split(",")
  .map((prefix) => prefix.replace(/^\/+|\/+$/g, ""))
  .filter(Boolean)

const agentRuntime = new BedrockAgentRuntimeClient({ region: REGION })
const s3 = new S3Client({ region: REGION })

type Citation = {
  uri: string
  snippet: string
  chunkId?: string
  title?: string
  sourceUri?: string
  downloadUrl?: string
  updatedAt?: string
  start?: number
  end?: number
  status: "available" | "restricted" | "unavailable"
}

type RetrievedReference = {
  location?: unknown
  content?: { text?: string }
  metadata?: Record<string, unknown>
}

function extractUri(location: unknown): string {
  const loc = location as Record<string, { uri?: string; url?: string } | undefined> | undefined
  if (!loc) return ""
  return (
    loc.s3Location?.uri ??
    loc.webLocation?.url ??
    loc.confluenceLocation?.url ??
    loc.salesforceLocation?.url ??
    loc.sharePointLocation?.url ??
    ""
  )
}

function optionalString(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim()
  if (typeof value === "number") return String(value)
  return undefined
}

function s3Object(uri: string) {
  const match = /^s3:\/\/([^/]+)\/(.+)$/i.exec(uri)
  if (!match || match[1] !== SOURCE_BUCKET_NAME) return null
  const key = decodeURIComponent(match[2])
  if (!SOURCE_PREFIXES.some((prefix) => key.startsWith(`${prefix}/`))) return null
  return { bucket: match[1], key }
}

async function createDownloadUrl(uri: string) {
  const object = s3Object(uri)
  if (!object) return undefined
  try {
    return await getSignedUrl(
      s3,
      new GetObjectCommand({
        Bucket: object.bucket,
        Key: object.key,
        ...(/\.(md|txt)$/i.test(object.key)
          ? { ResponseContentType: "text/plain; charset=utf-8" }
          : {}),
      }),
      { expiresIn: 900 },
    )
  } catch {
    return undefined
  }
}

async function buildCitation(
  ref: RetrievedReference,
  span?: { start?: number; end?: number },
): Promise<Citation | null> {
  const uri = extractUri(ref.location)
  if (!uri) return null
  const metadata = ref.metadata ?? {}
  const chunkId = optionalString(metadata["x-amz-bedrock-kb-chunk-id"])
  const sourceUri = optionalString(metadata.sourceUri)
  const title =
    optionalString(metadata.title) ??
    optionalString(metadata.document_title) ??
    optionalString(metadata.file_name)
  const updatedAt =
    optionalString(metadata.updated_at) ??
    optionalString(metadata.last_modified) ??
    optionalString(metadata.effective_date)
  const access = optionalString(metadata.access_status)?.toLowerCase()
  const target = sourceUri ?? uri
  const downloadUrl = /^s3:\/\//i.test(target)
    ? await createDownloadUrl(target)
    : undefined
  const status = access === "restricted"
    ? "restricted"
    : /^https?:\/\//i.test(target) || downloadUrl
      ? "available"
      : "unavailable"
  return {
    uri,
    snippet: ref.content?.text ?? "",
    chunkId,
    title,
    sourceUri,
    downloadUrl,
    updatedAt,
    start: span?.start,
    end: span?.end,
    status,
  }
}

function citationKey(citation: Citation) {
  if (citation.chunkId) return `chunk:${citation.chunkId}`
  const snippet = citation.snippet.trim().replace(/\s+/g, " ")
  return `content:${citation.uri}::${snippet}`
}

function writeSSE(stream: NodeJS.WritableStream, event: string, data: unknown) {
  stream.write(`event: ${event}\n`)
  stream.write(`data: ${JSON.stringify(data)}\n\n`)
}

export const handler = awslambda.streamifyResponse(
  async (event: FunctionUrlEvent, responseStream, _context) => {
    const method = event.requestContext?.http?.method ?? "POST"

    const stream = awslambda.HttpResponseStream.from(responseStream, {
      statusCode: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
      },
    })

    if (method === "OPTIONS") {
      stream.end()
      return
    }

    try {
      const rawBody = event.isBase64Encoded
        ? Buffer.from(event.body ?? "", "base64").toString("utf-8")
        : (event.body ?? "{}")
      const parsed = JSON.parse(rawBody) as { question?: string; filter?: unknown }
      const question = (parsed.question ?? "").trim()
      if (!question) {
        writeSSE(stream, "error", { message: "question フィールドは必須です。" })
        stream.end()
        return
      }

      const command = new RetrieveAndGenerateStreamCommand({
        input: { text: question },
        retrieveAndGenerateConfiguration: {
          type: "KNOWLEDGE_BASE",
          knowledgeBaseConfiguration: {
            knowledgeBaseId: KNOWLEDGE_BASE_ID,
            modelArn: MODEL_ID,
            ...knowledgeBasePrompts(MODEL_ID),
          },
        },
      })

      const response = await agentRuntime.send(command)
      const seenCitations = new Map<string, Citation>()

      if (!response.stream) {
        writeSSE(stream, "error", { message: "ストリームが取得できませんでした。" })
        stream.end()
        return
      }

      for await (const eventChunk of response.stream) {
        if (eventChunk.output?.text) {
          writeSSE(stream, "chunk", { text: eventChunk.output.text })
        }
        if (eventChunk.citation) {
          // 新旧SDK両方に対応: 新版は eventChunk.citation 直下、旧版は eventChunk.citation.citation 配下
          const citationLayer = eventChunk.citation as Record<string, unknown>
          const innerLayer = (citationLayer.citation ?? {}) as Record<string, unknown>
          const refs =
            ((innerLayer.retrievedReferences ?? citationLayer.retrievedReferences) as
              | RetrievedReference[]
              | undefined) ?? []
          const generatedPart = (innerLayer.generatedResponsePart ?? citationLayer.generatedResponsePart) as
            | { textResponsePart?: { span?: { start?: number; end?: number } } }
            | undefined
          const span = generatedPart?.textResponsePart?.span
          for (const ref of refs) {
            const citation = await buildCitation(ref, span)
            if (!citation) continue
            const key = citationKey(citation)
            if (!seenCitations.has(key)) {
              seenCitations.set(key, citation)
              writeSSE(stream, "citation", citation)
            }
          }
        }
        if (eventChunk.guardrail) {
          writeSSE(stream, "guardrail", eventChunk.guardrail)
        }
      }

      writeSSE(stream, "done", { count: seenCitations.size })
      stream.end()
    } catch (error) {
      const message = error instanceof Error ? error.message : "内部エラーが発生しました。"
      writeSSE(stream, "error", { message })
      stream.end()
    }
  },
)
