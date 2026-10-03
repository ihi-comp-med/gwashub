<script setup lang="ts">
// Dev mode: no passwords, pick a seeded user. Password mode (interim server): email + password.
// Cognito mode (AWS): a button to the hosted sign-in page (password + authenticator app).
const { login } = useAuth()
const toast = useToast()
const route = useRoute()
const { data: mode, error } = useApiGet<{ mode: 'dev' | 'password' | 'cognito' }>('/auth/mode')
const dev = computed(() => mode.value?.mode === 'dev')
const cognito = computed(() => mode.value?.mode === 'cognito')
const SIGNIN_ERRORS: Record<string, string> = {
  not_registered: "You signed in, but there's no GWASHub account for that email address. Ask your project admin to invite you.",
  expired: 'The sign-in took too long or was started in another tab. Please try again.',
  failed: 'Sign-in failed. Please try again.',
  cognito: 'Sign-in was cancelled or failed. Please try again.',
}
const signinError = computed(() => SIGNIN_ERRORS[String(route.query.error ?? '')] ?? '')
const { data: users } = useApiGet<any[]>(() => dev.value ? '/auth/users' : null)
const email = ref('')
const password = ref('')
async function signIn(e: string, pw = '') {
  try { await login(e, pw) }
  catch (err: any) { toast.add({ severity: 'error', summary: 'Sign-in failed', detail: err.message, life: 4000 }) }
}
</script>

<template>
  <div class="container page wrap">
    <Card class="card">
      <template #title>Sign in to GWASHub</template>
      <template #subtitle>
        <span v-if="dev">Local development mode: choose a demo user. No password is needed.</span>
        <span v-else-if="cognito">Sign in with your email, password and authenticator app. New here? Use the invitation email from your project admin.</span>
        <span v-else-if="mode">Sign in with the email and password your project admin gave you.</span>
      </template>
      <template #content>
        <Message v-if="error" severity="error" :closable="false" class="mb">
          Can't reach the API ({{ error }}). Start it with
          <code>.venv/bin/uvicorn gwashub_api.main:app --app-dir api --port 8000</code>.
        </Message>
        <Message v-if="signinError" severity="warn" :closable="false" class="mb">{{ signinError }}</Message>
        <a v-if="cognito" href="/api/auth/login" class="p-button signin"><i class="pi pi-sign-in" />&nbsp;Sign in</a>
        <form v-else-if="mode && !dev" class="pw" @submit.prevent="signIn(email, password)">
          <InputText v-model="email" type="email" placeholder="Email" autocomplete="username" />
          <InputText v-model="password" type="password" placeholder="Password" autocomplete="current-password" />
          <Button type="submit" label="Sign in" :disabled="!email || !password" />
        </form>
        <template v-if="dev">
        <ul class="users">
          <li v-for="u in users || []" :key="u.email">
            <button class="user" @click="signIn(u.email)">
              <Avatar :label="initials(u.name)" shape="circle" />
              <span class="who"><strong>{{ u.name }}</strong><span class="muted small">{{ u.email }} · {{ u.institution }}</span></span>
              <span class="roles">
                <Tag v-for="(role, p) in u.roles" :key="p" :value="`${p}: ${role}`" :severity="role === 'reviewer' ? 'contrast' : 'secondary'" />
              </span>
            </button>
          </li>
        </ul>
        <form class="row other" @submit.prevent="signIn(email)">
          <InputText v-model="email" placeholder="or type an email" class="grow" />
          <Button type="submit" label="Sign in" :disabled="!email" />
        </form>
        </template>
      </template>
    </Card>
  </div>
</template>

<style scoped>
.wrap { display: flex; justify-content: center; }
.card { width: 100%; max-width: 640px; }
.mb { margin-bottom: 12px; }
.users { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.user { width: 100%; display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 1px solid var(--gh-border); border-radius: 8px; background: var(--gh-surface); cursor: pointer; text-align: left; font: inherit; color: inherit; }
.user:hover { border-color: var(--gh-accent); background: #eef0f8; }
.who { display: flex; flex-direction: column; flex: 1; min-width: 0; }
.roles { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.small { font-size: 0.8rem; }
.other { margin-top: 16px; flex-wrap: nowrap; }
.grow { flex: 1; }
.pw { display: flex; flex-direction: column; gap: 12px; }
.signin { text-decoration: none; justify-content: center; width: 100%; }
</style>
