/** The study-metadata schema (sections → fields), fetched once per session. */
export function useMetadataSchema() {
  const schema = useState<any[] | null>('metadata-schema', () => null)
  if (!schema.value) api<{ sections: any[] }>('/metadata-schema').then(r => { schema.value = r.sections }).catch(() => {})
  const fieldCount = computed(() => (schema.value ?? []).reduce((n, s) => n + (s.fields?.length ?? 0) + (s.grid ? s.grid.rows.length * s.grid.cols.length : 0), 0))
  return { schema, fieldCount }
}
