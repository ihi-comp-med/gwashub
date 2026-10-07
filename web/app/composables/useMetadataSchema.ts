/** The study-metadata field catalogue (sections → fields, each section cohort- or dataset-level): the built-in fields,
 * fetched once, plus the current project's own fields (project.metadata_custom_sections / _units) on project pages. */
export function useMetadataSchema() {
  const builtinSchema = useState<any[] | null>('metadata-schema', () => null)
  const builtinUnits = useState<any[] | null>('metadata-units', () => null)
  const defaultConfig = useState<{ shown: string[], required: string[] } | null>('metadata-default', () => null)
  if (!builtinSchema.value) {
    api<{ sections: any[], units: any[], default_config: any }>('/metadata-schema').then((r) => {
      builtinSchema.value = r.sections
      builtinUnits.value = r.units
      defaultConfig.value = r.default_config
    }).catch(() => {})
  }
  const route = useRoute()
  const project = route.params.pid ? useProject().project : ref<any>(null)
  const schema = computed(() => builtinSchema.value && [...builtinSchema.value, ...(project.value?.metadata_custom_sections ?? [])])
  const units = computed(() => builtinUnits.value && [...builtinUnits.value, ...(project.value?.metadata_custom_units ?? [])])
  /** Labels of required fields missing from a metadata record (grid sections count if any cell is filled). */
  function missingRequired(meta: Record<string, any>, config?: { required: string[] } | null) {
    const has = (k: string) => meta[k] !== undefined && meta[k] !== null && meta[k] !== ''
    return (config?.required ?? []).filter((k) => {
      const sec = (schema.value ?? []).find((s: any) => s.key === k && s.grid)
      return sec ? !sec.grid.rows.some((r: any) => sec.grid.cols.some((c: any) => has(`${r.key}_${c.key}`))) : !has(k)
    }).map(k => (units.value ?? []).find((u: any) => u.key === k)?.label ?? k)
  }
  return { schema, units, defaultConfig, missingRequired }
}
