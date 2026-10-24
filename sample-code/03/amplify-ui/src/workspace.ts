export type Citation = {
  uri: string
  snippet: string
  chunkId?: string
  title?: string
  sourceUri?: string
  downloadUrl?: string
  updatedAt?: string
  start?: number
  end?: number
  status?: "available" | "restricted" | "unavailable"
}

export function citationKey(citation: Citation) {
  if (citation.chunkId) return `chunk:${citation.chunkId}`
  const snippet = citation.snippet.trim().replace(/\s+/g, " ")
  return `content:${citation.uri}::${snippet}`
}

export function citationHref(citation?: Citation) {
  if (!citation || citation.status === "restricted" || citation.status === "unavailable") return undefined
  const candidates = [citation.sourceUri, citation.downloadUrl, citation.uri]
  return candidates.find((candidate) => candidate && /^https?:\/\//i.test(candidate))
}

export function citationTitle(citation: Citation) {
  if (citation.title?.trim()) return citation.title.trim()
  const source = citation.sourceUri || citation.uri
  const tail = source.split(/[\\/]/).filter(Boolean).pop() || "参照ドキュメント"
  try {
    return decodeURIComponent(tail).replace(/\.(md|txt|html?|pdf)$/i, "")
  } catch {
    return tail.replace(/\.(md|txt|html?|pdf)$/i, "")
  }
}

export function formatCitationDate(value?: string) {
  if (!value) return "更新日不明"
  const compact = value.replace(/-/g, "")
  if (/^\d{8}$/.test(compact)) {
    return `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`
  }
  return value
}

export function withCitationLinks(
  answer: string,
  citations: Citation[],
  idPrefix = "citation",
) {
  if (!answer || citations.length === 0) return answer
  let marked = answer
  const hasMarkers = citations.some((_, index) =>
    marked.includes(`[${index + 1}]`),
  )

  if (!hasMarkers) {
    const positioned = citations
      .map((citation, index) => ({ end: citation.end, number: index + 1 }))
      .filter(
        (item): item is { end: number; number: number } =>
          typeof item.end === "number" && item.end >= 0 && item.end <= marked.length,
      )
      .sort((a, b) => b.end - a.end)

    if (positioned.length > 0) {
      for (const item of positioned) {
        marked = `${marked.slice(0, item.end)}[${item.number}]${marked.slice(item.end)}`
      }
    } else {
      marked = `${marked}\n\n参照 ${citations.map((_, index) => `[${index + 1}]`).join(" ")}`
    }
  }

  marked = marked.replace(/\[\d+\](?:[ \t]*\[\d+\])+/g, (markers) => {
    return [...new Set(markers.match(/\[\d+\]/g) ?? [])].join(" ")
  })

  return marked.replace(/\[(\d+)\](?!\()/g, (_match, rawNumber: string) => {
    const number = Number(rawNumber)
    const href = citationHref(citations[number - 1])
    return `[[${number}]](${href ?? `#${idPrefix}-${number}`} "引用元${number}")`
  })
}
