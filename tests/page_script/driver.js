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
  // Network and timers both throw. With no timer, the setImmediate after
  // each step is a true settle point.
  for (const [names, refusal] of [[NETWORK, "loads no subresource"], [TIMERS, "runs no timer"]]) {
    for (const name of names) {
      window[name] = function () {
        throw new Error("the page-script harness " + refusal + ": " + name);
      };
    }
  }
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

// A thrown value, named: an error's name and message, anything else as text.
function describe(value) {
  if (value !== null && (typeof value === "object" || typeof value === "function") && "message" in value) {
    return value.name + ": " + value.message;
  }
  return String(value);
}

// The errors of the page the current step runs on. A promise the page leaves
// rejected lands there, rather than ending Node (its default) and failing a
// later, unrelated call on the shared driver.
let activeErrors = null;
process.on("unhandledRejection", (reason) => {
  if (activeErrors !== null) {
    activeErrors.push({ type: "unhandled-rejection", cause: reason });
  } else {
    process.stderr.write("unhandled rejection outside a step: " + describe(reason) + "\n");
  }
});

// jsdom reports page errors to the virtual console synchronously; the first
// relevant one fails the step that caused it.
function drain(errors) {
  for (const e of errors.splice(0)) {
    if (e.type === "unhandled-exception" || e.type === "unhandled-rejection") {
      throw new PageError("page script error: " + (e.cause === undefined ? e.message : describe(e.cause)));
    }
    if (e.type === "not-implemented") {
      throw new PageError("page used something jsdom does not implement: " + e.message);
    }
  }
}

async function load(path) {
  const errors = [];
  activeErrors = errors;
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
    const pairKeys = attrs.map((pair) => JSON.stringify(pair));
    planted.forEach((p, i) => {
      for (const [, value] of attrs) {
        if (value.includes(p)) rendered.add(i);
      }
      if (built[i].elements.has(sig)) {
        add({ node: "element", tag, value: p });
        for (const [name] of attrs) add({ node: "attribute", tag, attr: name, value: p });
      }
      pairKeys.forEach((key, j) => {
        if (built[i].extras.has(key)) add({ node: "attribute", tag, attr: attrs[j][0], value: p });
      });
    });
  }
  // A created match supersedes: the value is not also unrendered.
  const createdValues = new Set(created.map((c) => c.value));
  const unrendered = planted.filter((p, i) => !rendered.has(i) && !createdValues.has(p));
  return { created, violations, unrendered };
}

// --- drive -----------------------------------------------------------------------

// The three control kinds (docs/page-safety.md, "Control convention").
function kindOf(el) {
  if (tagOf(el) === "select") return "select";
  if (tagOf(el) === "button" && el.hasAttribute("data-value")) return "button";
  if (el.hasAttribute("data-control-items")) return "items";
  return "other";
}

async function drive(page, control, value) {
  const { document, window } = page;
  // Matched by comparing the attribute, never by a selector built from a value.
  const matches = Array.from(document.querySelectorAll("[data-control]")).filter(
    (el) => el.getAttribute("data-control") === control,
  );
  const name = '"' + control + '"';
  if (matches.length === 0) throw new PageError("no control named " + name);
  const kinds = new Set(matches.map(kindOf));
  if (kinds.size > 1) throw new PageError("control " + name + " is ambiguous");
  const [kind] = kinds;
  if (kind === "select") {
    const select = matches[0];
    const option = Array.from(select.options).find((o) => o.value === value);
    if (option === undefined) throw new PageError("control " + name + ' has no option "' + value + '"');
    if (select.disabled || option.disabled) throw new PageError("control " + name + " is disabled");
    select.value = value;
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
  } else if (kind === "button") {
    const button = matches.find((b) => b.getAttribute("data-value") === value);
    if (button === undefined) throw new PageError("control " + name + ' has no button "' + value + '"');
    if (button.disabled) throw new PageError("control " + name + " is disabled");
    button.click();
  } else if (kind === "items") {
    const item = controlItem(document, control, value);
    item.dispatchEvent(new window.FocusEvent("focusin", { bubbles: true }));
  } else {
    throw new PageError("control " + name + " is not a select, a button with data-value or a data-control-items container");
  }
  await settle();
  drain(page.errors);
}

// The item of a data-control-items container: shared by drive and leave.
function controlItem(document, control, value) {
  const matches = Array.from(document.querySelectorAll("[data-control]")).filter(
    (el) => el.getAttribute("data-control") === control,
  );
  const name = '"' + control + '"';
  if (matches.length === 0) throw new PageError("no control named " + name);
  if (!matches.every((el) => kindOf(el) === "items")) {
    throw new PageError("control " + name + " is not a data-control-items container");
  }
  const item = matches
    .flatMap((c) => Array.from(c.querySelectorAll("[data-item]")))
    .find((el) => el.getAttribute("data-item") === value);
  if (item === undefined) throw new PageError("control " + name + ' has no item "' + value + '"');
  return item;
}

async function leave(page, control, value) {
  const item = controlItem(page.document, control, value);
  // No relatedTarget: focus leaves for nowhere, as on a blur.
  item.dispatchEvent(new page.window.FocusEvent("focusout", { bubbles: true }));
  await settle();
  drain(page.errors);
}

// A chip: a [data-req] element inside the item of any data-control-items
// container, matched by comparing attributes, never by a built selector.
async function chip(page, item, req, action) {
  const { document, window } = page;
  if (action !== "click" && action !== "focus") throw new PageError("chip action must be click or focus");
  const found = Array.from(document.querySelectorAll("[data-control-items] [data-item]"))
    .filter((el) => el.getAttribute("data-item") === item)
    .flatMap((el) => Array.from(el.querySelectorAll("[data-req]")))
    .find((el) => el.getAttribute("data-req") === req);
  if (found === undefined) throw new PageError('item "' + item + '" has no chip "' + req + '"');
  if (action === "click") {
    found.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  } else {
    found.focus();
    if (document.activeElement !== found) throw new PageError('chip "' + req + '" cannot take focus');
  }
  await settle();
  drain(page.errors);
}

// A key pressed where the focus is: on the active element, else on the body.
async function key(page, name) {
  const { document, window } = page;
  const target = document.activeElement !== null ? document.activeElement : document.body;
  target.dispatchEvent(new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  await settle();
  drain(page.errors);
}

// --- call --------------------------------------------------------------------------

class Unencodable extends Error {
  constructor(path, kind) {
    super(path + ": " + kind);
    this.path = path;
    this.kind = kind;
  }
}

function kindName(value) {
  if (value === undefined) return "undefined";
  if (typeof value === "number") return "non-finite number";
  if (typeof value !== "object") return typeof value;
  return Object.prototype.toString.call(value).slice(8, -1);
}

// The return value as JSON can carry it: null, booleans, finite numbers,
// strings, arrays and plain objects, with a Map as [key, value] pairs.
function encode(value, path, pageObject) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    const out = [];
    for (let i = 0; i < value.length; i++) out.push(encode(value[i], path + "[" + i + "]", pageObject));
    return out;
  }
  if (typeof value === "object") {
    // A page-realm Map is not instanceof this realm's Map (spike.md U5).
    if (Object.prototype.toString.call(value) === "[object Map]") {
      return Array.from(value, ([k, v], i) => [
        encode(k, path + ".key(" + i + ")", pageObject),
        encode(v, path + ".get(" + i + ")", pageObject),
      ]);
    }
    const proto = Object.getPrototypeOf(value);
    if (proto === null || proto === Object.prototype || proto === pageObject) {
      // A null prototype, so an own __proto__ key stays a key.
      const out = Object.create(null);
      for (const key of Object.keys(value)) out[key] = encode(value[key], path + "." + key, pageObject);
      return out;
    }
  }
  throw new Unencodable(path, kindName(value));
}

async function call(path, name, args) {
  const page = await load(path);
  try {
    let parent = null;
    let target = page.window;
    for (const segment of name.split(".")) {
      const owner = target === null || target === undefined ? null : Object(target);
      if (owner === null || !Object.prototype.hasOwnProperty.call(owner, segment)) {
        throw new PageError("run_function: " + segment + " is not defined on the page");
      }
      parent = target;
      target = owner[segment];
    }
    if (typeof target !== "function") throw new PageError("run_function: " + name + " is not a function");
    // Rebuilt in the page realm, so the function sees its own Object and Array.
    const pageArgs = page.window.JSON.parse(JSON.stringify(args));
    let result;
    try {
      result = target.apply(parent, pageArgs);
    } catch (e) {
      throw new PageError("run_function: " + name + " threw " + describe(e));
    }
    // A rejection the call leaves behind is this call's error.
    await settle();
    drain(page.errors);
    try {
      return encode(result, "result", page.window.Object.prototype);
    } catch (e) {
      if (!(e instanceof Unencodable)) throw e;
      throw new PageError("run_function: " + name + " returned a value JSON cannot carry at " + e.path + ": " + e.kind);
    }
  } finally {
    page.window.close();
  }
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
    case "drive":
      await drive(needPage(state), request.control, request.value);
      return null;
    case "leave":
      await leave(needPage(state), request.control, request.value);
      return null;
    case "chip":
      await chip(needPage(state), request.item, request.req, request.action);
      return null;
    case "key":
      await key(needPage(state), request.key);
      return null;
    case "call":
      return await call(request.path, request.name, request.args);
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
    activeErrors = state.page === null ? null : state.page.errors;
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

// The unhandledRejection listener keeps Node alive on a rejection, so a
// fault in main itself must end the driver, not leave Python waiting.
main().catch((e) => {
  process.stderr.write("page-script driver failed: " + describe(e) + "\n");
  process.exit(1);
});
