// Shared by legacy JavaScript and Vue/TypeScript ESLint configurations.
// A scope-aware rule is needed: 'window', 'globalThis', and 'dispatchEvent'
// can all be lexically shadowed by element-local helpers.
const message =
  'Do not dispatch a synthetic window resize event. Update the owned pane/layout lifecycle using ResizeObserver instead.';

function memberKey(node) {
  if (!node || node.type !== 'MemberExpression') return null;
  if (!node.computed && node.property.type === 'Identifier') return node.property.name;
  if (node.computed && node.property.type === 'Literal') return node.property.value;
  return null;
}

function resolvesToGlobal(name, scope) {
  for (let current = scope; current; current = current.upper) {
    const binding = current.set.get(name);
    if (binding) return binding.defs.length === 0;
  }
  return true; // Browser globals may not be declared in this ESLint scope.
}

function globalWindowReceiver(node, scope) {
  if (node.type === 'Identifier') {
    return ['window', 'globalThis', 'self'].includes(node.name) &&
      resolvesToGlobal(node.name, scope);
  }
  return node.type === 'MemberExpression' &&
    (memberKey(node) === 'window' || memberKey(node) === 'self') &&
    node.object.type === 'Identifier' &&
    node.object.name === 'globalThis' &&
    resolvesToGlobal('globalThis', scope);
}

function isResizeType(type) {
  return (type?.type === 'Literal' && type.value === 'resize') ||
    (type?.type === 'TemplateLiteral' && type.expressions.length === 0 &&
      type.quasis.length === 1 && type.quasis[0].value.cooked === 'resize');
}

export const noFakeWindowResizePlugin = {
  rules: {
    'no-synthetic-window-resize': {
      meta: {
        type: 'problem',
        schema: [],
        messages: { synthetic: message },
      },
      create(context) {
        return {
          CallExpression(node) {
            const event = node.arguments[0];
            if (!event || event.type !== 'NewExpression' ||
                !isResizeType(event.arguments[0])) return;
            const scope = context.sourceCode.getScope(node);
            const callee = node.callee;
            if (callee.type === 'Identifier') {
              if (callee.name !== 'dispatchEvent' ||
                  !resolvesToGlobal('dispatchEvent', scope)) return;
            } else if (callee.type === 'MemberExpression') {
              if (memberKey(callee) !== 'dispatchEvent' ||
                  !globalWindowReceiver(callee.object, scope)) return;
            } else {
              return;
            }
            context.report({ node, messageId: 'synthetic' });
          },
        };
      },
    },
  },
};
