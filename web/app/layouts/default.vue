<script setup lang="ts">
const { me, logout, load } = useAuth()
onMounted(load) // pages outside /projects don't run the auth middleware
const route = useRoute()
const { data: site } = useApiGet<{ mode: string, banner: string }>('/auth/mode')

// Help menu: guided tours in the current project, or in the Practice project outside a project.
const { start } = useTours()
const { project } = useProject()
const helpMenu = ref()
const myProjects = ref<any[] | null>(null)
async function openHelp(ev: Event) {
  helpMenu.value.toggle(ev)
  myProjects.value = await api<any[]>('/projects').catch(() => [])
}
const practice = computed(() => (myProjects.value ?? []).find((p: any) => p.is_practice))
const tourProject = computed(() => (route.params.pid ? project.value : practice.value) ?? null)
const helpItems = computed(() => [
  { label: tourProject.value ? `Guided tours, in ${tourProject.value.short}` : 'Guided tours: open a project first',
    items: TOURS.map(t => {
      const reviewerOnly = t.reviewer && tourProject.value?.role !== 'reviewer'
      return { label: `${t.title}${reviewerOnly ? ' (reviewers)' : ''}`, icon: 'pi pi-compass', disabled: !tourProject.value || reviewerOnly,
               command: () => start(t, tourProject.value.id) }
    }) },
  { separator: true },
  ...(practice.value ? [{ label: 'Practice project (synthetic data)', icon: 'pi pi-graduation-cap', command: () => navigateTo(`/projects/${practice.value.id}`) }] : []),
  { label: 'About GWASHub', icon: 'pi pi-info-circle', command: () => navigateTo('/about') },
])
</script>

<template>
  <div v-if="site?.banner" class="mock-banner">{{ site.banner }}</div>
  <header class="header">
    <div class="container row">
      <NuxtLink :to="me ? '/projects' : '/'" class="brand"><i class="pi pi-chart-scatter" /> GWASHub</NuxtLink>
      <nav class="row nav">
        <NuxtLink v-if="me" to="/projects" :class="['nav-link', { active: route.path.startsWith('/projects') }]">Projects</NuxtLink>
        <NuxtLink v-if="me" to="/validation" :class="['nav-link', { active: route.path === '/validation' }]">Validation</NuxtLink>
        <NuxtLink to="/about" :class="['nav-link', { active: route.path === '/about' }]">About</NuxtLink>
      </nav>
      <span class="spacer" />
      <template v-if="me">
        <Button v-tooltip.bottom="'Help and guided tours'" icon="pi pi-question-circle" text rounded aria-label="Help and guided tours"
                aria-haspopup="true" data-tour="help-menu" @click="openHelp" />
        <Menu ref="helpMenu" :model="helpItems" popup class="help-menu" />
        <NuxtLink v-tooltip.bottom="'Account and API tokens'" to="/account" class="row me-link"><span class="muted email">{{ me.name }}</span>
          <Avatar :label="initials(me.name)" shape="circle" /></NuxtLink>
        <Button v-if="site?.mode === 'dev'" v-tooltip.bottom="'Switch user'" icon="pi pi-users" text rounded aria-label="Switch user" @click="navigateTo('/login')" />
        <Button v-tooltip.bottom="'Sign out'" icon="pi pi-sign-out" text rounded aria-label="Sign out" @click="logout" />
      </template>
      <Button v-else label="Sign in" size="small" outlined @click="navigateTo('/login')" />
    </div>
  </header>
  <main><slot /></main>
  <footer class="footer">
    <div class="container row muted">
      <span>GWASHub · developed by the <a href="https://hermesconsortium.org" target="_blank" rel="noopener">HERMES Consortium</a></span>
      <span class="spacer" /><NuxtLink to="/about">About</NuxtLink>
    </div>
  </footer>
</template>

<style scoped>
.me-link { gap: 8px; text-decoration: none; color: inherit; }
.header { background: var(--gh-surface); border-bottom: 1px solid var(--gh-border); padding: 10px 0; }
.brand { font-weight: 700; font-size: 1.15rem; color: var(--gh-text); text-decoration: none; display: flex; gap: 8px; align-items: center; }
.brand i { color: var(--gh-accent); }
.nav { margin-left: 24px; gap: 4px; }
.nav-link { color: var(--gh-muted); text-decoration: none; padding: 6px 10px; border-radius: 6px; font-weight: 500; }
.nav-link.active, .nav-link:hover { color: var(--gh-text); background: var(--gh-bg); }
.email { font-size: 0.85rem; }
.footer { border-top: 1px solid var(--gh-border); padding: 20px 0; font-size: 0.85rem; }
@media (max-width: 640px) { .nav { margin-left: 0; } .email { display: none; } }
</style>
