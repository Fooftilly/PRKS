<script setup lang="ts">
import { computed, ref } from 'vue'
import PrksIconButton from '../components/PrksIconButton.vue'
import {
  GROUP_LINK,
  LIBRARY_LINKS,
  ORGANIZE_LINKS,
  PEOPLE_INDEX,
  PROCESSING_LINK,
  RESEARCH_LINKS,
  allNavTargets,
  peopleRoleLinks,
  progressLinks,
} from './catalog'
import {
  activeNavHref,
  disclosureContainsCurrent,
  disclosureExpanded,
  peopleToggleLabel,
  toggleNavDisclosure,
  useAppChrome,
  type NavFamily,
} from './chrome'
import ShellIcon from './ShellIcon.vue'

const chrome = useAppChrome()

const localEpoch = ref(0)
const targets = allNavTargets()
const roleLinks = peopleRoleLinks()
const progressNav = progressLinks()

const currentHref = computed(() => {
  void localEpoch.value
  return activeNavHref(targets, chrome.value)
})

function familyOpen(which: NavFamily): boolean {
  void localEpoch.value
  void chrome.value.disclosureRevision
  void chrome.value.route
  return disclosureExpanded(which, chrome.value.route)
}

function familyContains(which: NavFamily): boolean {
  void localEpoch.value
  void chrome.value.disclosureRevision
  void chrome.value.route
  return disclosureContainsCurrent(which, chrome.value.route)
}

const peopleOpen = computed(() => familyOpen('people'))
const researchOpen = computed(() => familyOpen('research'))
const progressOpen = computed(() => familyOpen('progress'))
const peopleContains = computed(() => familyContains('people'))
const researchContains = computed(() => familyContains('research'))
const progressContains = computed(() => familyContains('progress'))
const peopleLabel = computed(() => peopleToggleLabel(peopleOpen.value))

const processingBadge = computed(() => {
  const count = chrome.value.processingCount
  if (count == null || !Number.isFinite(count)) return ''
  const ariaLabel = count + (count === 1 ? ' file for processing' : ' files for processing')
  return window.prksNavAttentionBadgeHtml?.({ count, ariaLabel }) ?? ''
})

function isCurrent(href: string): boolean {
  return currentHref.value === href
}

function currentAttr(href: string): 'page' | undefined {
  return isCurrent(href) ? 'page' : undefined
}

function onToggle(which: NavFamily, event: MouseEvent): void {
  event.preventDefault()
  event.stopPropagation()
  toggleNavDisclosure(which, chrome.value.route)
  localEpoch.value += 1
}

function openSettings(): void {
  window.openModal?.('settings-modal')
}

function toggleSidebar(forceOpen?: boolean): void {
  window.prksToggleSidebarDrawer?.(forceOpen)
}
</script>

<template>
  <div class="sidebar-header sidebar-header--split">
    <a href="#/folders" class="sidebar-brand" aria-label="Home — Folder Library">
      <img class="logo sidebar-logo" :src="'/logo.svg'" alt="" width="32" height="32" fetchpriority="high" decoding="async">
      <div>
        <h2>PRKS</h2>
        <p>Personal Research</p>
        <p class="sidebar-caption-sm">Knowledge System</p>
      </div>
    </a>
    <div class="sidebar-header__actions">
      <PrksIconButton
        id="prks-sidebar-expand-btn"
        variant="ghost"
        label="Expand navigation"
        title="Expand navigation"
        @click="toggleSidebar(true)"
      >
        <ShellIcon class="ribbon-btn__icon" name="panel-left-open" size="ribbon" />
      </PrksIconButton>
      <PrksIconButton
        id="prks-sidebar-collapse-btn"
        variant="ghost"
        label="Collapse navigation"
        title="Collapse navigation"
        @click="toggleSidebar(false)"
      >
        <ShellIcon class="ribbon-btn__icon" name="panel-left-close" size="ribbon" />
      </PrksIconButton>
      <PrksIconButton
        class="settings-btn"
        variant="ghost"
        label="Settings"
        title="Settings"
        @click="openSettings"
      >
        <ShellIcon class="ribbon-btn__icon" name="settings" size="ribbon" />
      </PrksIconButton>
    </div>
  </div>

  <ul class="nav-menu">
    <li class="nav-section">Library</li>
    <li v-for="link in LIBRARY_LINKS" :key="link.href">
      <a
        :href="link.href"
        class="nav-link"
        :class="{ active: isCurrent(link.href) }"
        :aria-current="currentAttr(link.href)"
        :title="link.label"
      >
        <ShellIcon class="nav-link__icon" :name="link.icon" size="nav" />
        <span class="nav-link__label">{{ link.label }}</span>
      </a>
    </li>

    <li
      class="nav-disclosure nav-disclosure--section-break"
      :class="{ 'nav-disclosure--open': peopleOpen, 'nav-disclosure--contains-current': peopleContains }"
      data-nav-disclosure="people"
    >
      <div class="nav-disclosure__row">
        <a href="#/people" class="nav-link nav-disclosure__link" :class="{ active: isCurrent(PEOPLE_INDEX.href) }" :aria-current="currentAttr(PEOPLE_INDEX.href)" title="People">
          <ShellIcon class="nav-link__icon" name="users" size="nav" />
          <span class="nav-link__label">People</span>
        </a>
        <button
          type="button"
          class="nav-disclosure__toggle"
          data-nav-disclosure-toggle="people"
          :aria-expanded="peopleOpen ? 'true' : 'false'"
          aria-controls="prks-nav-people-children"
          :aria-label="peopleLabel"
          @click="onToggle('people', $event)"
        >
          <ShellIcon name="chevron-down" size="navSub" extra-class="nav-disclosure__chevron" />
        </button>
      </div>
      <ul id="prks-nav-people-children" class="nav-disclosure__children" :hidden="!peopleOpen">
        <li v-for="link in roleLinks" :key="link.href">
          <a
            :href="link.href"
            class="nav-link nav-link--sub"
            :class="{ active: isCurrent(link.href) }"
            :aria-current="currentAttr(link.href)"
          >
            <ShellIcon class="nav-link__icon" :role="link.role" />
            <span class="nav-link__label">{{ link.label }}</span>
          </a>
        </li>
        <li>
          <a
            :href="GROUP_LINK.href"
            class="nav-link nav-link--sub"
            :class="{ active: isCurrent(GROUP_LINK.href) }"
            :aria-current="currentAttr(GROUP_LINK.href)"
          >
            <ShellIcon class="nav-link__icon" name="folders" size="navSub" />
            <span class="nav-link__label">Groups</span>
          </a>
        </li>
      </ul>
    </li>

    <li
      class="nav-disclosure nav-disclosure--section-break"
      :class="{ 'nav-disclosure--open': researchOpen, 'nav-disclosure--contains-current': researchContains }"
      data-nav-disclosure="research"
    >
      <div class="nav-disclosure__row">
        <button
          type="button"
          class="nav-disclosure__toggle nav-disclosure__toggle--full"
          data-nav-disclosure-toggle="research"
          :aria-expanded="researchOpen ? 'true' : 'false'"
          aria-controls="prks-nav-research-children"
          title="Research"
          @click="onToggle('research', $event)"
        >
          <ShellIcon class="nav-link__icon" name="network" size="nav" />
          <span class="nav-link__label">Research</span>
          <ShellIcon name="chevron-down" size="navSub" extra-class="nav-disclosure__chevron" />
        </button>
      </div>
      <ul id="prks-nav-research-children" class="nav-disclosure__children" :hidden="!researchOpen">
        <li v-for="link in RESEARCH_LINKS" :key="link.href">
          <a
            :href="link.href"
            class="nav-link nav-link--sub"
            :class="{ active: isCurrent(link.href) }"
            :aria-current="currentAttr(link.href)"
          >
            <ShellIcon class="nav-link__icon" :name="link.icon" size="navSub" />
            <span class="nav-link__label">{{ link.label }}</span>
          </a>
        </li>
      </ul>
    </li>

    <li class="nav-section">Organize</li>
    <li v-for="link in ORGANIZE_LINKS" :key="link.href">
      <a
        :href="link.href"
        class="nav-link"
        :class="{ active: isCurrent(link.href) }"
        :aria-current="currentAttr(link.href)"
        :title="link.label"
      >
        <ShellIcon class="nav-link__icon" :name="link.icon" size="nav" />
        <span class="nav-link__label">{{ link.label }}</span>
      </a>
    </li>

    <li
      class="nav-disclosure nav-disclosure--section-break sidebar-progress-block"
      :class="{ 'nav-disclosure--open': progressOpen, 'nav-disclosure--contains-current': progressContains }"
      data-nav-disclosure="progress"
    >
      <div class="nav-disclosure__row">
        <button
          type="button"
          class="nav-disclosure__toggle nav-disclosure__toggle--full"
          data-nav-disclosure-toggle="progress"
          :aria-expanded="progressOpen ? 'true' : 'false'"
          aria-controls="prks-nav-progress-children"
          title="Progress"
          @click="onToggle('progress', $event)"
        >
          <ShellIcon class="nav-link__icon" name="list" size="nav" />
          <span class="nav-link__label">Progress</span>
          <ShellIcon name="chevron-down" size="navSub" extra-class="nav-disclosure__chevron" />
        </button>
      </div>
      <ul id="prks-nav-progress-children" class="progress-legend nav-disclosure__children" :hidden="!progressOpen">
        <li v-for="link in progressNav" :key="link.href">
          <a
            :href="link.href"
            class="nav-link progress-filter nav-link--sub"
            :data-status="link.status"
            :class="{ active: isCurrent(link.href) }"
            :aria-current="currentAttr(link.href)"
          >
            <ShellIcon class="nav-link__icon" :status="link.status" />
            {{ link.label }}
          </a>
        </li>
      </ul>
    </li>

    <li class="sidebar-nav-section-break">
      <a
        :href="PROCESSING_LINK.href"
        class="nav-link"
        data-prks-role="nav-processing"
        :class="{ active: isCurrent(PROCESSING_LINK.href) }"
        :aria-current="currentAttr(PROCESSING_LINK.href)"
        :title="PROCESSING_LINK.label"
      >
        <ShellIcon class="nav-link__icon" name="inbox" size="nav" />
        <span class="nav-link__label">Files for Processing</span>
        <!-- Badge markup is the classic attention primitive. Count text is numeric. -->
        <span data-prks-role="nav-processing-badge" v-html="processingBadge"></span>
      </a>
    </li>
  </ul>
</template>
