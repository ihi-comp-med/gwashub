<script setup lang="ts">
// Shown instead of a project page when it can't be loaded (e.g. you're not a member).
defineProps<{ error: string | null }>()
</script>

<template>
  <div v-if="error" class="container page">
    <Message severity="warn" :closable="false" icon="pi pi-lock">
      {{ error === 'Project not found' ? "This project doesn't exist, or you're not a member of it." : error }}
    </Message>
    <NuxtLink to="/projects" class="back"><i class="pi pi-arrow-left" /> Your projects</NuxtLink>
  </div>
  <div v-else class="container page loading"><ProgressSpinner style="width: 40px; height: 40px" /></div>
</template>

<style scoped>
.back { display: inline-flex; gap: 6px; align-items: center; margin-top: 16px; text-decoration: none; }
.loading { display: flex; justify-content: center; }
</style>
