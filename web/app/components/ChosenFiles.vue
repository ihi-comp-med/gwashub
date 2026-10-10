<script setup lang="ts">
// The chosen file(s) in a FileUpload's content slot: a file icon, the whole name and the size. PrimeVue's own list
// shows an image preview (a broken image for anything that isn't one) and cuts long names short.
defineProps<{ files: File[], remove: (index: number) => void }>()
</script>

<template>
  <ul v-if="files.length" class="files">
    <li v-for="(f, i) in files" :key="`${f.name}-${f.size}`">
      <i class="pi pi-file" />
      <span class="mono name">{{ f.name }}</span>
      <span class="muted size">{{ fmtSize(f.size) }}</span>
      <Button icon="pi pi-times" text rounded severity="secondary" size="small" aria-label="Remove file" @click="remove(i)" />
    </li>
  </ul>
</template>

<style scoped>
.files { list-style: none; margin: 0; padding: 0; }
.files li { display: flex; align-items: center; gap: 10px; padding: 6px 2px; }
.name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.size { white-space: nowrap; }
</style>
