/**
 * Send a file in parts (straight to S3 on AWS, to the API locally), several at a time with retries.
 * `urlFor(n)` returns a fresh upload URL for part n (presigned URLs expire, so ask just before use).
 * Returns the parts' ETags for the "complete" call.
 */
export async function uploadInParts(file: File, opts: {
  partSize: number, parts: number, urlFor: (n: number) => Promise<string>, onProgress?: (fraction: number) => void,
  parallel?: number, retries?: number,
}): Promise<{ n: number, etag: string }[]> {
  const { partSize, parts, urlFor, onProgress, parallel = 4, retries = 3 } = opts
  let done = 0
  const bump = (n: number) => { done += n; onProgress?.(Math.min(0.99, done / file.size)) }
  const etags: { n: number, etag: string }[] = []
  const queue = Array.from({ length: parts }, (_, i) => i + 1)
  async function worker() {
    while (queue.length) {
      const n = queue.shift()!
      const blob = file.slice((n - 1) * partSize, n * partSize)
      for (let attempt = 1; ; attempt++) {
        try {
          etags.push({ n, etag: await putPart(await urlFor(n), blob, bump) })
          break
        }
        catch (e) {
          if (attempt >= retries) throw e
          await new Promise(r => setTimeout(r, 1000 * attempt))
        }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(parallel, parts) }, worker))
  onProgress?.(1)
  return etags
}

function putPart(url: string, blob: Blob, onBytes: (n: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    if (url.startsWith('/')) xhr.withCredentials = true
    let sent = 0
    xhr.upload.onprogress = (e) => { onBytes(e.loaded - sent); sent = e.loaded }
    xhr.onload = () => {
      const etag = xhr.getResponseHeader('ETag')
      if (xhr.status >= 200 && xhr.status < 300 && etag) resolve(etag)
      else { onBytes(-sent); reject(new Error(`part upload failed (${xhr.status})`)) }
    }
    xhr.onerror = () => { onBytes(-sent); reject(new Error('network error')) }
    xhr.send(blob)
  })
}
