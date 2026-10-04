<script setup lang="ts">
// Shared header for every page inside a project: title, your role, tabs.
const props = defineProps<{ project: any }>()
const route = useRoute()
const base = computed(() => `/projects/${props.project.id}`)
const tabs = computed(() => [
  { label: 'Overview', icon: 'pi pi-home', to: base.value },
  { label: 'Cohorts', icon: 'pi pi-sitemap', to: `${base.value}/cohorts` },
  { label: 'Datasets', icon: 'pi pi-table', to: `${base.value}/datasets` },
  { label: 'Meta-analysis', icon: 'pi pi-chart-bar', to: `${base.value}/meta` },
])
// Settings sit apart on the right; the left tabs are about the data.
const settingsTab = computed(() => ({ label: 'Pipeline settings', icon: 'pi pi-cog', to: `${base.value}/pipeline` }))
const isActive = (to: string) => route.path === to || (to.endsWith('/meta') && route.path.startsWith(to))
</script>

<template>
  <div class="ph">
    <div class="container">
      <NuxtLink to="/projects" class="crumb"><i class="pi pi-arrow-left" /> All projects</NuxtLink>
      <div class="row top">
        <div>
          <h1 class="page-title">{{ project.name }}</h1>
          <div class="row meta muted">
            <span><i class="pi pi-users" /> {{ project.members.length }} members</span>
            <span><i class="pi pi-database" /> {{ project.counts.total }} datasets</span>
            <span>Your role: <Tag :value="project.role" :severity="project.role === 'reviewer' ? 'contrast' : 'secondary'" /></span>
          </div>
        </div>
        <span class="spacer" />
        <Button label="Upload dataset" icon="pi pi-upload" @click="navigateTo(`${base}/upload`)" />
      </div>
      <nav class="tabs">
        <NuxtLink v-for="t in tabs" :key="t.to" :to="t.to" :class="['tab', { active: isActive(t.to) }]">
          <i :class="t.icon" /> {{ t.label }}
        </NuxtLink>
        <span class="spacer" />
        <NuxtLink :to="settingsTab.to" :class="['tab', { active: isActive(settingsTab.to) }]">
          <i :class="settingsTab.icon" /> {{ settingsTab.label }}
        </NuxtLink>
      </nav>
    </div>
  </div>
</template>

<style scoped>
.ph { background: var(--gh-surface); border-bottom: 1px solid var(--gh-border); padding-top: 20px; }
.crumb { text-decoration: none; font-size: 0.85rem; display: inline-flex; gap: 6px; align-items: center; margin-bottom: 10px; }
.top { align-items: flex-start; gap: 16px; }
.meta { gap: 20px; font-size: 0.9rem; margin-top: 6px; }
.meta i { margin-right: 4px; }
.tabs { display: flex; gap: 4px; margin-top: 18px; overflow-x: auto; }
.tab { padding: 10px 14px; color: var(--gh-muted); text-decoration: none; font-weight: 500; border-bottom: 2px solid transparent; white-space: nowrap; display: flex; gap: 8px; align-items: center; }
.tab.active { color: var(--gh-accent); border-bottom-color: var(--gh-accent); }
.tab:hover { color: var(--gh-text); }
</style>
