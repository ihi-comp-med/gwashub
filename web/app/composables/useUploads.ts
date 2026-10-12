// Uploads running in this browser tab, by dataset ID. An upload carries on when you leave the upload page; the dataset
// panel and the Datasets list show its progress from here (the server only knows the dataset is "uploading").
export interface RunningUpload { datasetId: number, projectId: string, label: string, fileName: string, percent: number }

export function useUploads() {
  const uploads = useState<Record<number, RunningUpload>>('uploads', () => ({}))
  return {
    uploads,
    start(u: RunningUpload) { uploads.value = { ...uploads.value, [u.datasetId]: u } },
    progress(datasetId: number, percent: number) {
      const u = uploads.value[datasetId]
      if (u && u.percent !== percent) uploads.value = { ...uploads.value, [datasetId]: { ...u, percent } }
    },
    finish(datasetId: number) {
      const next = { ...uploads.value }
      delete next[datasetId]
      uploads.value = next
    },
  }
}
