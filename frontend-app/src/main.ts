import { mountPrksVue, PRKS_VUE_ROOT_ID } from './mount'

const target = document.getElementById(PRKS_VUE_ROOT_ID)
if (target) {
  mountPrksVue(target)
}
