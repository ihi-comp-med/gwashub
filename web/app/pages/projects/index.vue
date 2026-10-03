<script setup lang="ts">
const { data: projects, error } = useApiGet<any[]>('/projects')
const { me, load } = useAuth()
onMounted(load)
const newOpen = ref(false)
const statuses: DatasetStatus[] = ['passed', 'review', 'failed', 'processing']
</script>

<template>
  <div class="container page">
    <div class="row">
      <h1 class="page-title">Your projects</h1><span class="spacer" />
      <Button v-if="me?.is_admin" v-tooltip.bottom="'Platform admins only'" label="New project" icon="pi pi-plus" @click="newOpen = true" />
    </div>
    <p class="page-sub">Projects you're a member of. Choose one to see its datasets, pipeline and meta-analyses.</p>
    <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>
    <div class="grid grid-2">
      <Card v-for="p in projects || []" :key="p.id" class="pcard" @click="navigateTo(`/projects/${p.id}`)">
        <template #title>
          <div class="row">{{ p.short }}<span class="spacer" /><Tag :value="p.role" :severity="p.role === 'reviewer' ? 'contrast' : 'secondary'" /></div>
        </template>
        <template #subtitle>{{ p.name }}</template>
        <template #content>
          <p class="desc">{{ p.description }}</p>
          <div class="row chips">
            <template v-for="s in statuses" :key="s">
              <Tag v-if="p.counts[s]" :severity="STATUS_META[s].severity" :value="`${p.counts[s]} ${STATUS_META[s].label}`" />
            </template>
            <span v-if="!p.counts.total" class="muted small">No datasets yet</span>
          </div>
          <div class="row muted foot">
            <span><i class="pi pi-users" /> {{ p.members }} members</span>
            <span><i class="pi pi-database" /> {{ p.counts.total }} datasets</span>
          </div>
        </template>
      </Card>
    </div>
    <NewProjectDialog v-if="me?.is_admin" v-model:visible="newOpen" />
  </div>
</template>

<style scoped>
.pcard { cursor: pointer; transition: box-shadow .15s, transform .15s; }
.pcard:hover { box-shadow: 0 6px 20px rgba(48, 56, 128, .12); transform: translateY(-1px); }
.desc { margin: 0 0 12px; color: var(--gh-muted); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.chips { gap: 6px; margin-bottom: 12px; }
.foot { gap: 16px; font-size: 0.85rem; }
.foot i { margin-right: 4px; }
.small { font-size: 0.85rem; }
</style>
