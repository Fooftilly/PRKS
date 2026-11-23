<script setup lang="ts">
/**
 * First-run setup (#612): the page a web process serves while it waits for
 * setup (frontend/setup.html, backend/api/setup.py).
 *
 * Three short steps: the setup token, the owner account and Library, then a
 * review with Finish setup. Nothing is written before Finish. The token is the
 * claim: without the one the operator issued, nothing happens, and once
 * setup is done no token opens it again. A refused token returns to step 1;
 * any other refusal stays on the review with the reason. After setup the
 * page waits for PRKS to switch over to the application and opens it.
 *
 * Messages are fixed text chosen by the server's code. A token or password is
 * never logged, echoed or kept after the page leaves.
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import PrksButton from '../../components/PrksButton.vue'
import PrksField from '../../components/PrksField.vue'
import PrksInlineMessage from '../../components/PrksInlineMessage.vue'
import PrksState from '../../components/PrksState.vue'
import {
  DEFAULT_LIBRARY_NAME,
  TOKEN_PATTERN,
  readSetupState,
  submitSetup,
  waitForApplication,
  type SetupResult,
} from './setup-service'

type Step = 'loading' | 'unreachable' | 'token' | 'account' | 'review' | 'opening' | 'stuck'

const props = withDefaults(
  defineProps<{
    /** Navigates to the application; tests replace it. */
    openApplication?: () => void
    wait?: () => Promise<boolean>
  }>(),
  {
    openApplication: () => window.location.assign('/'),
    wait: () => waitForApplication(),
  },
)

const TOKEN_COMMAND = 'python prks_app.py admin bootstrap issue-token --root DATA_ROOT'

const step = ref<Step>('loading')
const token = ref('')
const login = ref('')
const displayName = ref('')
const password = ref('')
const passwordAgain = ref('')
const libraryName = ref(DEFAULT_LIBRARY_NAME)
const tokenError = ref('')
const loginError = ref('')
const passwordError = ref('')
const passwordAgainError = ref('')
const libraryError = ref('')
const displayNameError = ref('')
const finishError = ref('')
const busy = ref(false)
const resumed = ref(false)
const heading = ref<HTMLElement | null>(null)

const stepNumber = computed(() => (step.value === 'token' ? 1 : step.value === 'account' ? 2 : 3))
const shownDisplayName = computed(() => displayName.value.trim() || login.value.trim())
const shownLibraryName = computed(() => libraryName.value.trim() || DEFAULT_LIBRARY_NAME)

async function focusHeading(): Promise<void> {
  await nextTick()
  heading.value?.focus()
}

async function go(next: Step): Promise<void> {
  step.value = next
  await focusHeading()
}

async function opening(): Promise<void> {
  await go('opening')
  if (await props.wait()) {
    props.openApplication()
    return
  }
  await go('stuck')
}

async function load(): Promise<void> {
  step.value = 'loading'
  const state = await readSetupState()
  if (state === null) {
    await go('unreachable')
    return
  }
  if (state === 'ready') {
    await opening()
    return
  }
  resumed.value = state === 'bootstrapping'
  await go('token')
}

function checkToken(): boolean {
  const value = token.value.trim()
  tokenError.value = !value
    ? 'Enter the setup token.'
    : TOKEN_PATTERN.test(value)
      ? ''
      : 'This is not a setup token. Copy the whole token the command printed.'
  return !tokenError.value
}

function checkAccount(): boolean {
  loginError.value = login.value.trim() ? '' : 'Enter a login.'
  passwordError.value = password.value ? '' : 'Enter a password.'
  passwordAgainError.value =
    password.value && password.value !== passwordAgain.value ? 'The two passwords differ.' : ''
  libraryError.value = ''
  displayNameError.value = ''
  return !loginError.value && !passwordError.value && !passwordAgainError.value
}

function tokenNext(): void {
  if (checkToken()) void go('account')
}

function accountNext(): void {
  if (checkAccount()) void go('review')
}

function messageFor(result: Extract<SetupResult, { ok: false }>): string {
  switch (result.error) {
    case 'bootstrap_token_invalid':
      return 'This setup token is not valid. Use the latest token issued for this PRKS.'
    case 'bootstrap_token_expired':
      return 'This setup token has expired. Issue a new one and enter it here.'
    case 'bootstrap_token_malformed':
      return 'This is not a setup token. Copy the whole token the command printed.'
    case 'too_many_attempts':
      return result.retryAfter
        ? `Too many attempts. Try again in ${result.retryAfter} ${result.retryAfter === 1 ? 'second' : 'seconds'}.`
        : 'Too many attempts. Try again later.'
    case 'setup_busy':
      return 'Setup is already running from another tab or browser. Wait a moment, then try again.'
    case 'already_ready':
      return 'PRKS is already set up.'
    case 'origin_not_allowed':
      return 'PRKS refused this page’s address. Open PRKS at the address it was set up to serve.'
    case 'setup_refused':
      return result.message ?? 'PRKS refused to set up with this data location. Check the server’s log.'
    case 'unreachable':
    case 'unavailable':
      return 'PRKS did not answer. Check that it is running and that its database is reachable, then try again.'
    default:
      return 'PRKS could not finish setup. Try again.'
  }
}

function fieldError(result: Extract<SetupResult, { ok: false }>): boolean {
  if (result.error !== 'invalid_request') return false
  const tooLong = result.reason === 'too_long'
  switch (result.field) {
    case 'login':
      loginError.value = tooLong ? 'This login is too long.' : 'This login cannot be used.'
      break
    case 'display_name':
      displayNameError.value = tooLong ? 'This display name is too long.' : 'This display name cannot be used.'
      break
    case 'password':
      passwordError.value = tooLong
        ? 'This password is too long.'
        : result.reason === 'required' ? 'Enter a password.' : 'This password cannot be used.'
      break
    case 'library_name':
      libraryError.value = tooLong ? 'This Library name is too long.' : 'This Library name cannot be used.'
      break
    default:
      return false
  }
  void go('account')
  return true
}

async function finish(): Promise<void> {
  if (busy.value) return
  busy.value = true
  finishError.value = ''
  try {
    const result = await submitSetup({
      token: token.value,
      login: login.value,
      displayName: displayName.value,
      password: password.value,
      libraryName: libraryName.value,
    })
    if (result.ok) {
      password.value = ''
      passwordAgain.value = ''
      token.value = ''
      await opening()
      return
    }
    if (result.error === 'already_ready') {
      await opening()
      return
    }
    if (fieldError(result)) return
    if (result.error.startsWith('bootstrap_token_')) {
      tokenError.value = messageFor(result)
      await go('token')
      return
    }
    finishError.value = messageFor(result)
  } finally {
    busy.value = false
  }
}

onMounted(load)

defineExpose({ step })
</script>

<template>
  <section class="prks-setup__panel" aria-labelledby="prks-setup-heading">
    <p class="prks-setup__brand">PRKS</p>

    <PrksState v-if="step === 'loading'" kind="loading" message="Checking this PRKS…" />

    <template v-else-if="step === 'unreachable'">
      <h1 id="prks-setup-heading" ref="heading" class="prks-setup__title" tabindex="-1">PRKS did not answer</h1>
      <PrksInlineMessage tone="error" status>
        Check that PRKS is running and that its database is reachable, then try again.
      </PrksInlineMessage>
      <div class="prks-form-actions">
        <PrksButton variant="primary" @click="load">Try again</PrksButton>
      </div>
    </template>

    <template v-else-if="step === 'opening' || step === 'stuck'">
      <h1 id="prks-setup-heading" ref="heading" class="prks-setup__title" tabindex="-1">
        {{ step === 'opening' ? 'PRKS is set up' : 'PRKS is set up, but has not opened yet' }}
      </h1>
      <PrksState
        v-if="step === 'opening'"
        kind="loading"
        message="Opening your Library…"
      />
      <template v-else>
        <PrksInlineMessage tone="warning" status>
          PRKS did not start serving the Library. Check the server’s log, then open PRKS again.
        </PrksInlineMessage>
        <div class="prks-form-actions">
          <PrksButton variant="primary" @click="openApplication">Open PRKS</PrksButton>
        </div>
      </template>
    </template>

    <template v-else>
      <p class="prks-setup__step">Step {{ stepNumber }} of 3</p>

      <form v-if="step === 'token'" class="prks-stack" novalidate @submit.prevent="tokenNext">
        <h1 id="prks-setup-heading" ref="heading" class="prks-setup__title" tabindex="-1">Set up PRKS</h1>
        <p class="prks-setup__lead">
          This PRKS has no owner yet. Whoever runs it gives you a one-time setup token, so nobody else can
          claim it.
        </p>
        <PrksInlineMessage v-if="resumed">
          An earlier setup did not finish. Enter a setup token to finish it; nothing is created twice.
        </PrksInlineMessage>
        <PrksField
          label="Setup token"
          for-id="prks-setup-token"
          :error="tokenError"
          help="On the computer that runs PRKS, run the command below and copy the token it prints. DATA_ROOT is PRKS's data folder; PRKS prints the exact command while it waits for setup. A token works once and expires after 24 hours."
        >
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-token"
              v-model="token"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="password"
              name="token"
              autocomplete="one-time-code"
              spellcheck="false"
              autocapitalize="none"
              required
              aria-required="true"
              :aria-invalid="tokenError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <pre class="prks-setup__command"><code>{{ TOKEN_COMMAND }}</code></pre>
        <div class="prks-form-actions">
          <PrksButton variant="primary" type="submit">Continue</PrksButton>
        </div>
      </form>

      <form v-else-if="step === 'account'" class="prks-stack" novalidate @submit.prevent="accountNext">
        <h1 id="prks-setup-heading" ref="heading" class="prks-setup__title" tabindex="-1">Owner and Library</h1>
        <p class="prks-setup__lead">
          You will own this PRKS and its first Library. You sign in with this login and password.
        </p>
        <PrksField label="Login" for-id="prks-setup-login" :error="loginError">
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-login"
              v-model="login"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="text"
              name="username"
              autocomplete="username"
              spellcheck="false"
              autocapitalize="none"
              required
              aria-required="true"
              :aria-invalid="loginError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <PrksField
          label="Display name"
          for-id="prks-setup-display-name"
          :error="displayNameError"
          help="Optional. Defaults to the login."
        >
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-display-name"
              v-model="displayName"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="text"
              name="name"
              autocomplete="name"
              :aria-invalid="displayNameError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <PrksField label="Password" for-id="prks-setup-password" :error="passwordError">
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-password"
              v-model="password"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="password"
              name="new-password"
              autocomplete="new-password"
              required
              aria-required="true"
              :aria-invalid="passwordError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <PrksField label="Password again" for-id="prks-setup-password-again" :error="passwordAgainError">
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-password-again"
              v-model="passwordAgain"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="password"
              name="new-password-again"
              autocomplete="new-password"
              required
              aria-required="true"
              :aria-invalid="passwordAgainError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <PrksField
          label="Library name"
          for-id="prks-setup-library"
          :error="libraryError"
          help="Optional. Defaults to Library."
        >
          <template #default="{ labelledBy, describedBy }">
            <input
              id="prks-setup-library"
              v-model="libraryName"
              :aria-labelledby="labelledBy"
              :aria-describedby="describedBy"
              class="prks-input"
              type="text"
              name="library"
              autocomplete="off"
              :aria-invalid="libraryError ? 'true' : 'false'"
            />
          </template>
        </PrksField>
        <div class="prks-form-actions">
          <PrksButton variant="secondary" @click="go('token')">Back</PrksButton>
          <PrksButton variant="primary" type="submit">Continue</PrksButton>
        </div>
      </form>

      <form v-else class="prks-stack" novalidate @submit.prevent="finish">
        <h1 id="prks-setup-heading" ref="heading" class="prks-setup__title" tabindex="-1">Review</h1>
        <dl class="prks-setup__review">
          <dt>Login</dt>
          <dd>{{ login.trim() }}</dd>
          <dt>Display name</dt>
          <dd>{{ shownDisplayName }}</dd>
          <dt>Library</dt>
          <dd>{{ shownLibraryName }}</dd>
        </dl>
        <p class="prks-setup__note">
          The database, where files are stored and the network address are set by whoever runs PRKS. They
          are not changed here.
        </p>
        <p class="prks-setup__note">
          Back up your Library once it has something in it: Settings → Backup &amp; restore downloads a backup
          file. PRKS does not make backups on its own.
        </p>
        <PrksInlineMessage v-if="finishError" tone="error" status>{{ finishError }}</PrksInlineMessage>
        <div class="prks-form-actions">
          <PrksButton variant="secondary" :disabled="busy" @click="go('account')">Back</PrksButton>
          <PrksButton variant="primary" type="submit" :busy="busy" busy-label="Setting up…">Finish setup</PrksButton>
        </div>
      </form>
    </template>
  </section>
</template>
