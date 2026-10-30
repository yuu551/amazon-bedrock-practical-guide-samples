import { useState, useEffect, useRef } from "react"

/**
 * ストリーミングテキストを段階的に表示するバッファ。
 *
 * LLM レスポンスがバッチで一括到着しても、一定速度でテキストを
 * 「ドリップ」し、Streamdown の animated (blurIn) と組み合わせて
 * 自然なストリーミング体験を実現する。
 */
export function useStreamBuffer(
  content: string,
  isStreaming: boolean,
  charsPerTick = 6,
  intervalMs = 30,
): { displayed: string; isDripping: boolean } {
  const [displayed, setDisplayed] = useState("")
  const [isDripping, setIsDripping] = useState(false)
  const targetRef = useRef("")
  const cursorRef = useRef(0)
  const isStreamingRef = useRef(isStreaming)
  const prevStreamingRef = useRef(false)

  isStreamingRef.current = isStreaming

  // React 18 バッチング対策: content が空になっても target を保持
  if (content.length > 0) {
    targetRef.current = content
  }

  // ストリーミング開始を検知 → リセット
  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) {
      targetRef.current = ""
      cursorRef.current = 0
      setDisplayed("")
      setIsDripping(true)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  // ドリップタイマー
  useEffect(() => {
    if (!isDripping) return

    const timer = setInterval(() => {
      const target = targetRef.current
      const cursor = cursorRef.current

      if (cursor >= target.length) {
        if (!isStreamingRef.current) {
          setIsDripping(false)
        }
        return
      }

      let nextPos = Math.min(cursor + charsPerTick, target.length)

      // 単語の途中で切らない
      if (nextPos < target.length) {
        const remaining = target.slice(nextPos)
        const boundaryMatch = remaining.match(/^[^\s]*[\s]/)
        if (boundaryMatch && boundaryMatch[0].length <= charsPerTick) {
          nextPos += boundaryMatch[0].length
        }
      }
      nextPos = Math.min(nextPos, target.length)

      cursorRef.current = nextPos
      setDisplayed(target.slice(0, nextPos))
    }, intervalMs)

    return () => clearInterval(timer)
  }, [isDripping, charsPerTick, intervalMs])

  return { displayed, isDripping }
}
