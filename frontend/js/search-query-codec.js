var prksSearchQueryCodec = (function(exports) {
	Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
	//#region src/features/search/codec.ts
	/**
	* Canonical Search / Saved View query codec.
	*
	* One implementation. Vue imports these functions. The maintainer build also
	* emits them as the classic script `frontend/js/search-query-codec.js`, whose
	* global `prksSearchQueryCodec` is the only legacy bridge.
	*
	* This module does not parse hashes. `prksParseRoute` in `frontend/js/navigation.js`
	* remains the only hash parser. A search definition is already-parsed route params.
	*/
	var SEARCH_UNSAVABLE_MESSAGE = "This search combination cannot be saved as a view.";
	function recordOf(value) {
		if (value && typeof value === "object") return value;
		return {};
	}
	function paramsFromRoute(route) {
		const record = recordOf(route);
		const params = record.params;
		if (params && typeof params === "object") return params;
		return record;
	}
	function truthyAny(raw) {
		const anyRaw = raw == null ? "" : raw;
		return anyRaw === "1" || String(anyRaw).trim().toLowerCase() === "true" || String(anyRaw).trim().toLowerCase() === "yes";
	}
	function trimmed(value) {
		return String(value || "").trim();
	}
	function unsavable(empty = false) {
		return empty ? {
			ok: false,
			empty: true,
			unsavable: true,
			message: SEARCH_UNSAVABLE_MESSAGE
		} : {
			ok: false,
			unsavable: true,
			message: SEARCH_UNSAVABLE_MESSAGE
		};
	}
	/** Route params → a savable definition, or the existing unsavable result. */
	function definitionFromRoute(route) {
		const params = paramsFromRoute(route);
		const q = trimmed(params.q);
		const tag = trimmed(params.tag);
		const author = trimmed(params.author);
		const publisher = trimmed(params.publisher);
		const any = truthyAny(params.any);
		if (!q && !tag && !author && !publisher) return unsavable(true);
		if (any && (tag || author || publisher)) return unsavable();
		if (tag && q) return unsavable();
		if (any) {
			if (!q) return unsavable();
			return {
				ok: true,
				definition: {
					mode: "all",
					q,
					tag: "",
					author: "",
					publisher: ""
				}
			};
		}
		if (tag) return {
			ok: true,
			definition: {
				mode: "tag",
				q: "",
				tag,
				author,
				publisher
			}
		};
		return {
			ok: true,
			definition: {
				mode: "advanced",
				q,
				tag: "",
				author,
				publisher
			}
		};
	}
	/** Definition → canonical `#/search?...`. Parameter order follows mode. */
	function hashFromDefinition(definition) {
		const d = recordOf(definition);
		const p = new URLSearchParams();
		const mode = String(d.mode || "");
		const q = trimmed(d.q);
		const tag = trimmed(d.tag);
		const author = trimmed(d.author);
		const publisher = trimmed(d.publisher);
		if (mode === "all") {
			p.set("any", "1");
			if (q) p.set("q", q);
		} else if (mode === "tag") {
			if (tag) p.set("tag", tag);
			if (author) p.set("author", author);
			if (publisher) p.set("publisher", publisher);
		} else {
			if (q) p.set("q", q);
			if (author) p.set("author", author);
			if (publisher) p.set("publisher", publisher);
		}
		return "#/search?" + p.toString();
	}
	/** Saved definition → the `fetchSearch` arguments the coordinator already uses. */
	function optionsFromDefinition(definition) {
		const d = recordOf(definition);
		const q = trimmed(d.q);
		const tag = trimmed(d.tag);
		const author = trimmed(d.author);
		const publisher = trimmed(d.publisher);
		if (d.mode === "all") return {
			q,
			tag: null,
			options: { any: "1" }
		};
		if (d.mode === "tag") return {
			q: "",
			tag,
			options: {
				author,
				publisher
			}
		};
		return {
			q,
			tag: null,
			options: {
				author,
				publisher
			}
		};
	}
	/** Saved View summary. Display text is not re-trimmed. */
	function summaryText(definition) {
		const d = recordOf(definition);
		const parts = [];
		if (d.mode === "all") return "All: " + String(d.q || "");
		if (d.mode === "tag") {
			parts.push("Tag: " + String(d.tag || ""));
			if (d.author) parts.push("Author: " + d.author);
			if (d.publisher) parts.push("Publisher: " + d.publisher);
			return parts.join(" · ");
		}
		if (d.q) parts.push("Keywords: " + d.q);
		if (d.author) parts.push("Author: " + d.author);
		if (d.publisher) parts.push("Publisher: " + d.publisher);
		return parts.join(" · ");
	}
	//#endregion
	exports.definitionFromRoute = definitionFromRoute;
	exports.hashFromDefinition = hashFromDefinition;
	exports.optionsFromDefinition = optionsFromDefinition;
	exports.summaryText = summaryText;
	return exports;
})({});
if (typeof module === "object" && module != null && module.exports) module.exports = prksSearchQueryCodec;
