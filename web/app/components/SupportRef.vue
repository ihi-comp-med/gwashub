<script setup lang="ts">
// A small reference line (ids and the storage folder) people can copy and send when they ask for help.
const props = defineProps<{ parts: (string | number | null | undefined)[], location?: string | null }>()
const toast = useToast()
const text = computed(() => [...props.parts.filter(p => p !== null && p !== undefined && p !== ''), props.location].filter(Boolean).join(' · '))

async function copy() {
  try {
    await navigator.clipboard.writeText(text.value)
    toast.add({ severity: 'success', summary: 'Copied', detail: 'Paste it into your message when you ask for help.', life: 2500 })
  } catch {
    toast.add({ severity: 'warn', summary: 'Could not copy', detail: 'Select the text and copy it instead.', life: 3500 })
  }
}
</script>

<template>
  <div v-if="text" class="support-ref">
    <span class="lbl">Reference for help requests:</span>
    <span class="txt">{{ text }}</span>
    <button type="button" class="cp" aria-label="Copy reference" v-tooltip.top="'Copy'" @click="copy"><i class="pi pi-copy" /></button>
  </div>
</template>

<style scoped>
.support-ref { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 28px; font-size: 0.7rem; color: #9ca3af; }
.txt { font-family: ui-monospace, Menlo, monospace; word-break: break-all; }
.cp { background: none; border: 0; padding: 2px 4px; color: #9ca3af; cursor: pointer; border-radius: 4px; }
.cp:hover { color: var(--gh-accent); background: var(--gh-bg); }
.cp i { font-size: 0.75rem; }
</style>
