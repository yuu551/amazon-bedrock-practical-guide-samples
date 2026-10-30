import { initializeTracing, flushTracing, withObservation, recordOutput, recordUsage } from "./instrumentation"
import { converseConfig, knowledgeBasePrompts } from "./model-settings"
import {
  BedrockAgentRuntimeClient,
  RetrieveAndGenerateStreamCommand,
  RetrieveAndGenerateCommand,
  type RetrieveAndGenerateResponse,
} from "@aws-sdk/client-bedrock-agent-runtime"
import {
  BedrockRuntimeClient,
  ConverseCommand,
  ConverseStreamCommand,
} from "@aws-sdk/client-bedrock-runtime"
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3"
import { getSignedUrl } from "@aws-sdk/s3-request-presigner"

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

type SearchConfig = {
  searchType?: "SEMANTIC" | "HYBRID"
  useRerank?: boolean
  useQueryDecomposition?: boolean
  useOrchestration?: boolean
}

type MetadataFilter = {
  equals?: { key: string; value: string }
  [key: string]: unknown
}

const REGION = process.env.BEDROCK_REGION ?? process.env.AWS_REGION ?? "us-east-1"
const KNOWLEDGE_BASE_ID = process.env.KNOWLEDGE_BASE_ID ?? ""
const MODEL_ID = process.env.MODEL_ID ?? ""
const RERANK_MODEL_ARN = process.env.RERANK_MODEL_ARN ?? ""
const ROUTE_MODEL_ARN = process.env.ROUTE_MODEL_ARN || MODEL_ID
const SOURCE_BUCKET_NAME = process.env.KNOWLEDGE_BASE_SOURCE_BUCKET_NAME ?? ""
const SOURCE_PREFIXES = (process.env.KNOWLEDGE_BASE_SOURCE_PREFIXES ?? "")
  .split(",")
  .map((prefix) => prefix.replace(/^\/+|\/+$/g, ""))
  .filter(Boolean)

const agentRuntime = new BedrockAgentRuntimeClient({ region: REGION })
const bedrockRuntime = new BedrockRuntimeClient({ region: REGION })
const s3 = new S3Client({ region: REGION })

const ROUTES = [
  { name: "faq", description: "社員向けFAQ", filter: { equals: { key: "document_type", value: "faq" } } },
  { name: "manual", description: "業務マニュアル・手順書", filter: { equals: { key: "document_type", value: "manual" } } },
  { name: "policy", description: "規程・ルール文書", filter: { equals: { key: "document_type", value: "policy" } } },
] as const

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
  const title = optionalString(metadata.title) ?? optionalString(metadata.document_title) ?? optionalString(metadata.file_name)
  const updatedAt = optionalString(metadata.updated_at) ?? optionalString(metadata.last_modified) ?? optionalString(metadata.effective_date)
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

function traceCitations(seen: Map<string, Citation>) {
  return [...seen.values()].map(({ uri, snippet, chunkId, title }) => ({ uri, snippet, chunkId, title }))
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

function buildVectorSearchConfig(filter: MetadataFilter | undefined, config: SearchConfig) {
  const vsc: Record<string, unknown> = {}
  if (config.searchType) vsc.overrideSearchType = config.searchType
  if (config.useRerank && RERANK_MODEL_ARN) {
    vsc.rerankingConfiguration = {
      type: "BEDROCK_RERANKING_MODEL",
      bedrockRerankingConfiguration: {
        modelConfiguration: { modelArn: RERANK_MODEL_ARN },
        numberOfRerankedResults: 5,
      },
    }
  }
  if (filter) vsc.filter = filter
  return vsc
}

async function emitCitation(
  stream: NodeJS.WritableStream,
  ref: RetrievedReference,
  seen: Map<string, Citation>,
  span?: { start?: number; end?: number },
) {
  const citation = await buildCitation(ref, span)
  if (!citation) return
  const key = citationKey(citation)
  if (!seen.has(key)) {
    seen.set(key, citation)
    writeSSE(stream, "citation", citation)
  }
}

// ---------------------------------------------------------------------------
// 通常分岐: RetrieveAndGenerateStream + vsc + orchestration(QueryDecomposition)
// ---------------------------------------------------------------------------

async function runStreamingRag(
  stream: NodeJS.WritableStream,
  question: string,
  filter: MetadataFilter | undefined,
  config: SearchConfig,
  seen: Map<string, Citation>,
) {
  const vsc = buildVectorSearchConfig(filter, config)
  const kbConfig: Record<string, unknown> = {
    knowledgeBaseId: KNOWLEDGE_BASE_ID,
    modelArn: MODEL_ID,
    ...knowledgeBasePrompts(MODEL_ID),
  }
  if (Object.keys(vsc).length > 0) {
    kbConfig.retrievalConfiguration = { vectorSearchConfiguration: vsc }
  }
  if (config.useQueryDecomposition) {
    kbConfig.orchestrationConfiguration = {
      ...knowledgeBasePrompts(MODEL_ID).orchestrationConfiguration,
      queryTransformationConfiguration: { type: "QUERY_DECOMPOSITION" },
    }
  }

  const command = new RetrieveAndGenerateStreamCommand({
    input: { text: question },
    retrieveAndGenerateConfiguration: {
      type: "KNOWLEDGE_BASE",
      knowledgeBaseConfiguration: kbConfig as never,
    },
  })

  return withObservation("retrieve-and-generate", command.input, async (span) => {
    const response = await agentRuntime.send(command)
    if (!response.stream) {
      throw new Error("ストリームが取得できませんでした。")
    }

    let answer = ""
    for await (const chunk of response.stream) {
      if (chunk.output?.text) {
        answer += chunk.output.text
        writeSSE(stream, "chunk", { text: chunk.output.text })
      }
      if (chunk.citation?.citation) {
        const refs = chunk.citation.citation.retrievedReferences ?? []
        const span = chunk.citation.citation.generatedResponsePart?.textResponsePart?.span
        for (const ref of refs) await emitCitation(stream, ref, seen, span)
      }
      if (chunk.guardrail) {
        writeSSE(stream, "guardrail", chunk.guardrail)
      }
    }
    recordOutput(span, { answer, citations: traceCitations(seen) })
    return answer
  })
}

// ---------------------------------------------------------------------------
// マルチソース統合: 質問分析 → ルート別検索 → 最終統合(ConverseStream)
// ---------------------------------------------------------------------------

async function analyzeQuestion(question: string): Promise<{ route: string; query: string }[]> {
  const routeCatalog = ROUTES.map((r) => `- ${r.name}: ${r.description}`).join("\n")
  return withObservation("analyze-question", { question, routes: ROUTES }, async (span) => {
    const response = await bedrockRuntime.send(
      new ConverseCommand({
        modelId: MODEL_ID,
        system: [
          {
            text:
              "You analyze questions and choose 1-3 retrieval routes.\n" +
              "Return a JSON array of objects with 'route' and 'query' keys.\n" +
              "Rewrite each query to be retrieval-friendly. Return JSON only.",
          },
        ],
        messages: [
          {
            role: "user",
            content: [{ text: `Question: ${question}\n\nAvailable routes:\n${routeCatalog}` }],
          },
        ],
        inferenceConfig: converseConfig(MODEL_ID, 800, 0),
      }),
    )
    recordUsage(span, response.usage)
    if (response.stopReason && response.stopReason !== "end_turn") {
      throw new Error(`質問分析が完了していません: ${response.stopReason}`)
    }
    const text = (response.output?.message?.content ?? [])
      .flatMap((block) => block.text === undefined ? [] : [block.text])
      .join("\n")
    const start = text.indexOf("[")
    const end = text.lastIndexOf("]") + 1
    if (start >= 0 && end > start) {
      try {
        const plans = JSON.parse(text.slice(start, end))
        recordOutput(span, { rawResponse: text, plans })
        return plans
      } catch {
        recordOutput(span, { rawResponse: text, fallback: true, plans: [{ route: "faq", query: question }] })
        return [{ route: "faq", query: question }]
      }
    }
    recordOutput(span, { rawResponse: text, fallback: true, plans: [{ route: "faq", query: question }] })
    return [{ route: "faq", query: question }]
  }, { type: "generation", model: MODEL_ID })
}

async function retrieveForRoute(
  plan: { route: string; query: string },
  routeFilter: MetadataFilter | undefined,
  searchType: "SEMANTIC" | "HYBRID" = "HYBRID",
): Promise<{ route: string; answer: string; refs: RetrievedReference[] }> {
  const vsc: Record<string, unknown> = { numberOfResults: 5, overrideSearchType: searchType }
  if (routeFilter) vsc.filter = routeFilter

  return withObservation(`retrieve-and-generate:${plan.route}`, { query: plan.query, filter: routeFilter, searchType, knowledgeBaseId: KNOWLEDGE_BASE_ID }, async (span) => {
    const response: RetrieveAndGenerateResponse = await agentRuntime.send(
      new RetrieveAndGenerateCommand({
        input: { text: plan.query },
        retrieveAndGenerateConfiguration: {
          type: "KNOWLEDGE_BASE",
          knowledgeBaseConfiguration: {
            knowledgeBaseId: KNOWLEDGE_BASE_ID,
            modelArn: ROUTE_MODEL_ARN,
            ...knowledgeBasePrompts(ROUTE_MODEL_ARN),
            retrievalConfiguration: { vectorSearchConfiguration: vsc as never },
          },
        },
      }),
    )
    const answer = response.output?.text ?? ""
    const refs: RetrievedReference[] = []
    for (const group of response.citations ?? []) {
      for (const ref of group.retrievedReferences ?? []) refs.push(ref)
    }
    recordOutput(span, { answer, citations: refs.map((ref) => ({ uri: extractUri(ref.location), text: ref.content?.text })) })
    return { route: plan.route, answer, refs }
  })
}

async function runOrchestratedRag(
  stream: NodeJS.WritableStream,
  question: string,
  seen: Map<string, Citation>,
  config: SearchConfig,
) {
  const routesByName = new Map<string, (typeof ROUTES)[number]>(
    ROUTES.map((r) => [r.name as string, r]),
  )

  writeSSE(stream, "status", { phase: "analyze", message: "質問を分析中..." })
  const plans = await analyzeQuestion(question)

  writeSSE(stream, "status", { phase: "retrieve", plans })
  const routeResults: { route: string; answer: string }[] = []
  for (const plan of plans) {
    const route = routesByName.get(plan.route)
    if (!route) continue
    const result = await retrieveForRoute(plan, route.filter, config.searchType)
    routeResults.push({ route: result.route, answer: result.answer })
    for (const ref of result.refs) await emitCitation(stream, ref, seen)
  }

  writeSSE(stream, "status", { phase: "synthesize", message: "最終回答を生成中..." })

  const routeSections = routeResults
    .map((r) => `[${r.route}] ${r.answer}`)
    .join("\n\n")

  return withObservation("synthesize", { question, routeResults }, async (span) => {
    const synth = await bedrockRuntime.send(
      new ConverseStreamCommand({
        modelId: MODEL_ID,
        system: [
          {
            text:
              "複数の情報源から集めた回答を統合して、最終回答を作成してください。\n" +
              "情報源間で矛盾がある場合は隠さずに指摘してください。\n" +
              "日本語で回答してください。Markdownで整形してください。",
          },
        ],
        messages: [
          {
            role: "user",
            content: [{ text: `質問: ${question}\n\n各情報源の回答:\n${routeSections}` }],
          },
        ],
        inferenceConfig: converseConfig(MODEL_ID, 2048, 0.2),
      }),
    )

    if (!synth.stream) {
      throw new Error("統合ストリームを取得できませんでした。")
    }

    let answer = ""
    for await (const chunk of synth.stream) {
      if (chunk.messageStop && chunk.messageStop.stopReason !== "end_turn") {
        throw new Error(`最終回答が完了していません: ${chunk.messageStop.stopReason}`)
      }
      if (chunk.metadata?.usage) recordUsage(span, chunk.metadata.usage)
      const delta = chunk.contentBlockDelta?.delta?.text
      if (delta) {
        answer += delta
        writeSSE(stream, "chunk", { text: delta })
      }
    }
    recordOutput(span, answer)
    return answer
  }, { type: "generation", model: MODEL_ID })
}

// ---------------------------------------------------------------------------
// Lambda handler
// ---------------------------------------------------------------------------

export const handleRequest = async (event: FunctionUrlEvent, responseStream: NodeJS.WritableStream, requestContext: { awsRequestId?: string } = {}) => {
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

  const seen = new Map<string, Citation>()
  const started = Date.now()

  try {
    const rawBody = event.isBase64Encoded
      ? Buffer.from(event.body ?? "", "base64").toString("utf-8")
      : (event.body ?? "{}")
    const parsed = JSON.parse(rawBody) as {
      session_id?: string
      question?: string
      filter?: MetadataFilter
      config?: SearchConfig
    }
    const question = (parsed.question ?? "").trim()
    if (!question) {
      writeSSE(stream, "error", { message: "question フィールドは必須です。" })
      stream.end()
      return
    }

    await initializeTracing()
    const config = parsed.config ?? {}
    let traceId: string | undefined
    await withObservation("rag-request", { question, filter: parsed.filter, config }, async (span) => {
      traceId = span.isRecording() ? span.spanContext().traceId : undefined
      if (traceId) console.info(JSON.stringify({ event: "rag-trace", traceId, requestId: requestContext.awsRequestId }))
      const answer = config.useOrchestration
        ? await runOrchestratedRag(stream, question, seen, config)
        : await runStreamingRag(stream, question, parsed.filter, config, seen)
      recordOutput(span, { answer, citations: traceCitations(seen) })
    }, { type: "agent", sessionId: parsed.session_id ?? requestContext.awsRequestId })

    writeSSE(stream, "done", {
      citationCount: seen.size,
      traceId,
      latencyMs: Date.now() - started,
    })
    stream.end()
  } catch (error) {
    const message = error instanceof Error ? error.message : "内部エラーが発生しました。"
    writeSSE(stream, "error", { message })
    stream.end()
  } finally {
    await flushTracing()
  }
}

export const handler = awslambda.streamifyResponse(handleRequest)
