// Shared by legacy JavaScript and Vue/TypeScript ESLint configurations.
// Ban only synthetic resize events dispatched on the global Window. Ordinary
// resize listeners, ResizeObserver callbacks, and element events are allowed.
const message =
  'Do not dispatch a synthetic window resize event. Update the owned pane/layout lifecycle using ResizeObserver instead.';

const targets = [
  "CallExpression[callee.type='MemberExpression'][callee.object.name=/^(window|globalThis)$/][callee.property.name='dispatchEvent']",
  "CallExpression[callee.type='MemberExpression'][callee.object.name=/^(window|globalThis)$/][callee.property.value='dispatchEvent']",
  "CallExpression[callee.name='dispatchEvent']",
];

export const noFakeWindowResize = [
  'error',
  ...targets.map((target) => ({
    selector: `${target} > NewExpression[arguments.0.value='resize']`,
    message,
  })),
];
