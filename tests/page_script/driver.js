"use strict";
// The Node side of the page-script harness (#53). It reads one JSON request
// per line on stdin and writes one JSON reply per line on stdout; harness.py
// is the other side, and docs/page-safety.md describes the protocol.

const fs = require("fs");
const readline = require("readline");
const { pathToFileURL } = require("url");
const { JSDOM, VirtualConsole } = require("jsdom");

// A "page" reply: the page failed, not the harness.
class PageError extends Error {}

const NETWORK = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"];
const TIMERS = ["setTimeout", "setInterval", "requestAnimationFrame"];

function beforeParse(window) {
  for (const name of NETWORK) {
    window[name] = function () {
      throw new Error("the page-script harness loads no subresource: " + name);
    };
  }
  // With no timer, the setImmediate after each step is a true settle point.
  for (const name of TIMERS) {
    window[name] = function () {
      throw new Error("the page-script harness runs no timer: " + name);
    };
  }
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

// jsdom reports page errors to the virtual console synchronously; the first
// relevant one fails the step that caused it.
function drain(errors) {
  for (const e of errors.splice(0)) {
    if (e.type === "unhandled-exception") {
      const cause = e.cause || {};
      throw new PageError("page script error: " + cause.name + ": " + cause.message);
    }
    if (e.type === "not-implemented") {
      throw new PageError("page used something jsdom does not implement: " + e.message);
    }
  }
}

async function load(path) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => errors.push(e));
  // No resources option (D6): jsdom loads no script, sheet or frame the page
  // names (spike.md U2), and beforeParse stubs the network APIs.
  const dom = new JSDOM(fs.readFileSync(path, "utf8"), {
    url: pathToFileURL(path).href,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse,
  });
  const page = { dom, window: dom.window, document: dom.window.document, errors };
  try {
    if (page.document.readyState !== "complete") {
      await new Promise((resolve) => page.window.addEventListener("load", resolve, { once: true }));
    }
    await settle();
    drain(errors);
  } catch (e) {
    page.window.close();
    throw e;
  }
  return page;
}

// --- query and planted -------------------------------------------------------

// The live DOM keeps SVG case (viewBox; spike.md U2), and #54's html.parser
// lower-cases, so names are compared lower-cased.
function tagOf(el) {
  return el.localName.toLowerCase();
}

function attrsOf(el) {
  return Array.from(el.attributes, (a) => [a.name.toLowerCase(), a.value]);
}

function query(page, selector) {
  let found;
  try {
    found = page.document.querySelectorAll(selector);
  } catch (e) {
    throw new Error("bad selector " + JSON.stringify(selector) + ": " + e.message);
  }
  return Array.from(found, (el) => ({
    tag: tagOf(el),
    attrs: Array.from(el.attributes, (a) => [a.name, a.value]),
    classes: Array.from(el.classList),
    text: el.textContent,
  }));
}

function comparePairs(a, b) {
  if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
  if (a[1] !== b[1]) return a[1] < b[1] ? -1 : 1;
  return 0;
}

function directText(el) {
  return Array.from(el.childNodes)
    .filter((n) => n.nodeType === 3)
    .map((n) => n.data)
    .join("");
}

// An element's signature: its tag, its sorted attributes and, when it has
// none, its direct text.
function signature(el) {
  const pairs = attrsOf(el).sort(comparePairs);
  return JSON.stringify([tagOf(el), pairs, pairs.length ? null : directText(el)]);
}

// What a planted value builds when written raw as markup: in element
// context, and in double-, single- and unquoted attribute context. Parsed
// inertly in a template, so nothing runs.
function shapes(document, p) {
  const elements = new Set();
  const extras = new Set();
  const fragments = [p, '<x a="' + p + '">', "<x a='" + p + "'>", "<x a=" + p + ">"];
  fragments.forEach((fragment, i) => {
    const template = document.createElement("template");
    template.innerHTML = fragment;
    const wrapper = i === 0 ? null : template.content.firstElementChild;
    for (const el of template.content.querySelectorAll("*")) {
      if (el !== wrapper) elements.add(signature(el));
    }
    if (wrapper) {
      for (const pair of attrsOf(wrapper).slice(1)) extras.add(JSON.stringify(pair));
    }
  });
  return { elements, extras };
}

function hasRawAncestor(node) {
  const parent = node.parentElement;
  return parent !== null && parent.closest("script, style, template") !== null;
}

function plantedReport(page, planted, rules) {
  const { document, window } = page;
  const allowedTags = new Set(rules.allowed_tags);
  const allowedAttrs = new Set(rules.allowed_attrs);
  const ownScriptIds = new Set(rules.own_script_ids);
  const built = planted.map((p) => shapes(document, p));
  const created = [];
  const seen = new Set();
  const add = (item) => {
    const key = JSON.stringify(item);
    if (!seen.has(key)) {
      seen.add(key);
      created.push(item);
    }
  };
  const violations = [];
  const rendered = new Set();
  const filter = window.NodeFilter.SHOW_ELEMENT | window.NodeFilter.SHOW_COMMENT | window.NodeFilter.SHOW_TEXT;
  const walker = document.createTreeWalker(document, filter);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if (node.nodeType === 8) {
      violations.push({ rule: "comment", tag: null, attr: null });
      continue;
    }
    if (node.nodeType === 3) {
      // Judged per text node: script, style and template text is not rendered.
      if (!hasRawAncestor(node)) {
        planted.forEach((p, i) => {
          if (node.data.includes(p)) rendered.add(i);
        });
      }
      continue;
    }
    const tag = tagOf(node);
    const attrs = attrsOf(node);
    if (!allowedTags.has(tag)) violations.push({ rule: "tag", tag, attr: null });
    for (const [name] of attrs) {
      if (!allowedAttrs.has(name)) violations.push({ rule: "attribute", tag, attr: name });
    }
    if (tag === "script") {
      const id = node.getAttribute("id");
      if (!ownScriptIds.has(id)) violations.push({ rule: "script", tag: "script", attr: id });
    }
    const sig = signature(node);
    planted.forEach((p, i) => {
      for (const [, value] of attrs) {
        if (value.includes(p)) rendered.add(i);
      }
      if (built[i].elements.has(sig)) {
        add({ node: "element", tag, value: p });
        for (const [name] of attrs) add({ node: "attribute", tag, attr: name, value: p });
      }
      for (const pair of attrs) {
        if (built[i].extras.has(JSON.stringify(pair))) add({ node: "attribute", tag, attr: pair[0], value: p });
      }
    });
  }
  // A created match supersedes: the value is not also unrendered.
  const createdValues = new Set(created.map((c) => c.value));
  const unrendered = planted.filter((p, i) => !rendered.has(i) && !createdValues.has(p));
  return { created, violations, unrendered };
}

// --- the request loop ----------------------------------------------------------

function needPage(state) {
  if (state.page === null) throw new Error("no page is open");
  return state.page;
}

async function handle(request, state) {
  switch (request.op) {
    case "open":
      if (state.page !== null) throw new Error("a page is already open");
      state.page = await load(request.path);
      return null;
    case "query":
      return query(needPage(state), request.selector);
    case "planted":
      return plantedReport(needPage(state), request.planted, request.rules);
    case "version":
      return { node: process.versions.node };
    case "close":
      state.closing = true;
      return null;
    default:
      throw new Error("unknown op: " + JSON.stringify(request.op));
  }
}

async function main() {
  const state = { page: null, closing: false };
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    let reply;
    try {
      reply = { ok: true, result: await handle(JSON.parse(line), state) };
    } catch (e) {
      reply = { ok: false, kind: e instanceof PageError ? "page" : "harness", error: String(e && e.message) };
    }
    process.stdout.write(JSON.stringify(reply) + "\n");
    if (state.closing) break;
  }
  // Closing the window leaves nothing pending, so Node exits once stdout drains.
  if (state.page !== null) state.page.window.close();
}

main();
