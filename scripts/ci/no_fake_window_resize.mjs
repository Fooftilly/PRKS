// Shared by legacy JavaScript and Vue/TypeScript ESLint configurations.
// Ban only synthetic resize events dispatched on the global Window. Ordinary
// resize listeners, ResizeObserver callbacks, and element events are allowed.
const message =
  'Do not dispatch a synthetic window resize event. Update the owned pane/layout lifecycle using ResizeObserver instead.';

const targets = [
  "CallExpression[callee.type='MemberExpression'][callee.object.name=/^(window|globalThis)$/][callee.property.name='dispatchEvent']",
  "CallExpression[callee.type='MemberExpression'][callee.object.name=/^(window|globalThis)$/][callee.property.value='dispatchEvent']",
];

export const noFakeWindowResize = [
  'error',
  ...targets.map((target) => ({
    selector: `${target} > NewExpression[arguments.0.value='resize']`,
    message,
  })),
];

// An unqualified dispatchEvent call usually means Window.dispatchEvent, but
// a lexical helper of the same name can legitimately dispatch on an element.
// AST selectors cannot distinguish them; check ESLint's actual lexical scope.
export const noFakeWindowResizePlugin = {
  rules: {
    'no-bare-global-resize': {
      meta: {
        type: 'problem',
        schema: [],
        messages: { synthetic: message },
      },
      create(context) {
        return {
          CallExpression(node) {
            if (node.callee.type !== 'Identifier' || node.callee.name !== 'dispatchEvent') return;
            const event = node.arguments[0];
            if (!event || event.type !== 'NewExpression' ||
                !event.arguments[0] || event.arguments[0].type !== 'Literal' ||
                event.arguments[0].value !== 'resize') return;

            // A declared local dispatchEvent shadows the browser global.
            // If no local declaration owns the identifier, conservatively
            // treat the bare call as an implicit Window call.
            for (let scope = context.sourceCode.getScope(node); scope; scope = scope.upper) {
              const binding = scope.set.get('dispatchEvent');
              if (binding) {
                if (binding.defs.length) return;
                break;
              }
            }
            context.report({ node, messageId: 'synthetic' });
          },
        };
      },
    },
  },
};
