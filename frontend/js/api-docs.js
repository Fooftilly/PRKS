/* The owner-only API reference page (/api/docs). */
(function () {
    'use strict';
    window.SwaggerUIBundle({
        url: '/api/openapi.json',
        dom_id: '#prks-api-docs',
        deepLinking: true,
        // The validator badge would send the document to validator.swagger.io.
        validatorUrl: null,
        // Try it out stays on reads: a mutation needs the Library epoch header.
        supportedSubmitMethods: ['get', 'head'],
    });
})();
