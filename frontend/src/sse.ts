/**
 * Fetch-based SSE reader that supports Authorization headers.
 * Calls onMessage for each parsed SSE data event.
 * Returns a cleanup function.
 */
export function openSSE(
  url: string,
  onMessage: (data: unknown) => void,
  onDone?: () => void,
  onError?: (err: unknown) => void
): () => void {
  const token = localStorage.getItem('token')
  let cancelled = false

  const run = async () => {
    try {
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })

      if (!res.ok || !res.body) {
        onError?.(new Error(`HTTP ${res.status}`))
        onDone?.()
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''

      while (!cancelled) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (line.startsWith('data:')) {
              const raw = line.slice(5).trim()
              if (!raw) continue
              try {
                onMessage(JSON.parse(raw))
              } catch {
                onMessage(raw)
              }
            }
          }
        }
      }
    } catch (err) {
      if (!cancelled) onError?.(err)
    } finally {
      if (!cancelled) onDone?.()
    }
  }

  run()
  return () => { cancelled = true }
}
