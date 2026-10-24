import { context, trace, SpanKind, SpanStatusCode, type Span } from "@opentelemetry/api"
import { logs } from "@opentelemetry/api-logs"
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node"
import { LangfuseSpanProcessor } from "@langfuse/otel"
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager"

let initialization: Promise<void> | undefined
let flush: (() => Promise<void>) | undefined
// AgentCore向けでは、コンソールに入出力を表示するためspanと同じ内容をログにも出す。
let emitContentLogs = false
const outputs = new WeakMap<Span, unknown>()

// Lambdaの実行環境ごとに一度初期化する。送信先はサーバー側の設定で選ぶ。
export async function initializeTracing(): Promise<void> {
  const destination = process.env.RAG_TRACE_DESTINATION
  if (!destination || destination === "none") return
  initialization ??= (async () => {
    if (destination === "agentcore") {
      const region = process.env.AWS_REGION ?? "us-east-1"
      const service = process.env.OTEL_SERVICE_NAME ?? "chapter03-rag"
      process.env.AGENT_OBSERVABILITY_ENABLED = "true"
      process.env.OTEL_AWS_APPLICATION_SIGNALS_ENABLED = "false"
      process.env.OTEL_AWS_SERVICE_EVENTS_ENABLED = "false"
      process.env.AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT = "true"
      process.env.OTEL_EXPORTER_OTLP_PROTOCOL = "http/protobuf"
      process.env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = `https://xray.${region}.amazonaws.com/v1/traces`
      process.env.OTEL_TRACES_EXPORTER = "otlp"
      const logGroup = `/aws/bedrock-agentcore/runtimes/${service}`
      process.env.OTEL_LOGS_EXPORTER = "otlp"
      process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT = `https://logs.${region}.amazonaws.com/v1/logs`
      process.env.OTEL_EXPORTER_OTLP_LOGS_HEADERS = `x-aws-log-group=${logGroup},x-aws-log-stream=otel-rt-logs,x-aws-metric-namespace=bedrock-agentcore`
      process.env.OTEL_METRICS_EXPORTER = "none"
      process.env.OTEL_NODE_ENABLED_INSTRUMENTATIONS = "aws-sdk"
      process.env.OTEL_RESOURCE_ATTRIBUTES = `service.name=${service},aws.log.group.names=${logGroup},aws.log.stream.names=otel-rt-logs`
      // 自作RAGの処理は下のwithObservationで明示的に計測する。
      await import("@aws/aws-distro-opentelemetry-node-autoinstrumentation/register")
      const proxy = trace.getTracerProvider() as unknown as {
        getDelegate(): { forceFlush(): Promise<void> }
      }
      const provider = proxy.getDelegate()
      const loggerProvider = logs.getLoggerProvider() as unknown as {
        getDelegate?(): { forceFlush?(): Promise<void> }
        forceFlush?(): Promise<void>
      }
      const logProvider = loggerProvider.getDelegate?.() ?? loggerProvider
      flush = async () => {
        await provider.forceFlush()
        await logProvider.forceFlush?.()
      }
      emitContentLogs = true
      return
    }
    if (destination !== "langfuse") throw new Error("Unknown trace destination")

    let publicKey = process.env.LANGFUSE_PUBLIC_KEY
    let secretKey = process.env.LANGFUSE_SECRET_KEY
    if (process.env.LANGFUSE_SECRET_ARN) {
      const client = new SecretsManagerClient({ region: process.env.AWS_REGION ?? "us-east-1" })
      const secret = await client.send(new GetSecretValueCommand({ SecretId: process.env.LANGFUSE_SECRET_ARN }))
      const keys = JSON.parse(secret.SecretString ?? "{}")
      publicKey = keys.publicKey
      secretKey = keys.secretKey
    }
    if (!publicKey || !secretKey) throw new Error("Langfuse credentials are missing")
    const processor = new LangfuseSpanProcessor({
      publicKey,
      secretKey,
      baseUrl: process.env.LANGFUSE_BASE_URL ?? "https://cloud.langfuse.com",
      mediaUploadEnabled: false,
      timeout: 5,
    })
    const provider = new NodeTracerProvider({ spanProcessors: [processor] })
    provider.register()
    flush = () => provider.forceFlush()
  })()
  try {
    await initialization
  } catch {
    // 認証情報や問い合わせ本文をログへ出さず、次の呼び出しで再試行する。
    initialization = undefined
    console.warn("RAG tracing initialization failed; check the trace destination settings.")
  }
}

export async function flushTracing(): Promise<void> {
  try {
    await flush?.()
  } catch {
    console.warn("RAG trace export failed; check the destination before relying on the trace.")
  }
}

function message(role: "user" | "assistant", value: unknown): string {
  return JSON.stringify([{ role, parts: [{ type: "text", content: typeof value === "string" ? value : JSON.stringify(value) }] }])
}

export function recordOutput(span: Span, output: unknown): void {
  outputs.set(span, output)
  span.setAttribute("langfuse.observation.output", JSON.stringify(output))
  span.setAttribute("gen_ai.output.messages", message("assistant", output))
}

export function recordUsage(span: Span, usage?: { inputTokens?: number; outputTokens?: number }): void {
  if (usage?.inputTokens !== undefined) span.setAttribute("gen_ai.usage.input_tokens", usage.inputTokens)
  if (usage?.outputTokens !== undefined) span.setAttribute("gen_ai.usage.output_tokens", usage.outputTokens)
}

export async function withObservation<T>(
  name: string,
  input: unknown,
  operation: (span: Span) => Promise<T>,
  options: { type?: "span" | "generation" | "agent"; model?: string; sessionId?: string } = {},
): Promise<T> {
  if (!flush) {
    const span = trace.getTracer("chapter03-rag").startSpan(name)
    try { return await operation(span) } finally { span.end() }
  }
  return trace.getTracer("chapter03-rag").startActiveSpan(name, { kind: SpanKind.INTERNAL }, async (span) => {
    span.setAttributes({
      "gen_ai.system": "aws.bedrock",
      "gen_ai.provider.name": "aws.bedrock",
      "langfuse.observation.type": options.type ?? "span",
      "langfuse.observation.input": JSON.stringify(input),
      "gen_ai.input.messages": message("user", input),
    })
    if (options.model) span.setAttribute("gen_ai.request.model", options.model)
    if (options.type === "generation") span.setAttribute("gen_ai.operation.name", "chat")
    if (options.type === "agent") {
      span.setAttribute("gen_ai.operation.name", "invoke_agent")
      span.setAttribute("gen_ai.agent.name", process.env.OTEL_SERVICE_NAME ?? "chapter03-rag")
    }
    if (options.sessionId) {
      span.setAttribute("session.id", options.sessionId)
      span.setAttribute("langfuse.session.id", options.sessionId)
    }
    try {
      return await operation(span)
    } catch (error) {
      const name = error instanceof Error ? error.name : "Error"
      span.setStatus({ code: SpanStatusCode.ERROR, message: name })
      span.setAttribute("error.type", name)
      span.setAttribute("langfuse.observation.level", "ERROR")
      throw error
    } finally {
      if (emitContentLogs) emitContentLog(span, name, input, options.sessionId)
      span.end()
    }
  })
}

// CloudWatchの生成AIオブザーバビリティは、span IDで結び付いたログから入出力を表示する。
function emitContentLog(span: Span, name: string, input: unknown, sessionId?: string): void {
  const text = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value))
  const body: Record<string, unknown> = { input: { messages: [{ role: "user", content: text(input) }] } }
  if (outputs.has(span)) body.output = { messages: [{ role: "assistant", content: text(outputs.get(span)) }] }
  logs.getLogger("chapter03-rag").emit({
    context: trace.setSpan(context.active(), span),
    body: body as never,
    attributes: { "event.name": name, ...(sessionId ? { "session.id": sessionId } : {}) },
  })
}
