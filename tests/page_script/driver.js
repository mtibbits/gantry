"use strict";
// The Node side of the page-script harness (#53). It reads one JSON request
// per line on stdin and writes one JSON reply per line on stdout; harness.py
// is the other side, and docs/page-safety.md describes the protocol. It wraps
// navigate in jsdom/lib/jsdom/living/window/navigation.js before the first
// require("jsdom"), which works only while jsdom ships no exports map and
// Location-impl.js destructures navigate at load: re-check both on a bump.

const fs = require("fs");
const http = require("http");
const { createRequire } = require("module");
const readline = require("readline");
const { pathToFileURL } = require("url");

// Each open page's record, by its window: jsdom's own calls name a window,
// and an inline script runs inside the JSDOM constructor, before it returns.
const records = new WeakMap();

// Reload and navigation on a clock page (#63). Location's members are
// unforgeable and jsdom has no public navigation hook, so the driver wraps
// jsdom's internal navigate. The wrapper must be in place before the first
// require("jsdom"): Location-impl.js and HTMLHyperlinkElementUtils-impl.js
// destructure navigate at load, and only jsdom's lack of an exports map lets
// this path resolve. Re-check both on a jsdom bump.
const navigation = require("jsdom/lib/jsdom/living/window/navigation.js");
const { serializeURL } = createRequire(require.resolve("jsdom"))("whatwg-url");
const jsdomNavigate = navigation.navigate;
navigation.navigate = function (window, newURL, flags) {
  const page = records.get(window);
  // Without a clock, or for a window no longer the page's, jsdom's own path.
  if (page === undefined || page.time === null || page.window !== window) return jsdomNavigate.apply(this, arguments);
  if (flags.reloadTriggered) {
    // location.reload() or history.go(0): counted now, carried out by finish.
    page.reloads++;
    page.pendingReload = true;
    return undefined;
  }
  if (newURL.scheme === "javascript") return jsdomNavigate.apply(this, arguments);
  // A change of fragment alone, to a fragment that is there and differs.
  const current = window._document._URL;
  if (newURL.fragment !== null && newURL.fragment !== current.fragment &&
      serializeURL(newURL, true) === serializeURL(current, true)) {
    return jsdomNavigate.apply(this, arguments);
  }
  page.errors.push({ type: "refused", message: "the page-script harness follows no navigation: " + serializeURL(newURL) });
  return undefined;
};
const { JSDOM, VirtualConsole } = require("jsdom");

// A "page" reply: the page failed, not the harness.
class PageError extends Error {}

const NETWORK = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"];
const TIMERS = ["setTimeout", "setInterval", "requestAnimationFrame"];
const TIMER_LIMIT = 10000;

// --- the controlled clock (#63) ----------------------------------------------------

// wall is the time Date reads, in ms since the epoch. span counts the ms
// advanced since open; timers and performance.now() ride it, so set_clock
// moves no due time. start is the span when the current document loaded.
function makeTime(wall) {
  return { wall, span: 0, start: 0, queue: [], nextId: 1, nextSeq: 1 };
}

// Built in the page realm, so the page's Date is a page-realm function and
// new Date() is still an instance of it. Only now() and the no-argument
// forms read the controlled clock; everything else is the realm's own Date.
const DATE_FACTORY = `(function (getNow) {
  const Real = Date;
  function Date_(...args) {
    if (!new.target) return new Real(getNow()).toString();
    return Reflect.construct(Real, args.length === 0 ? [getNow()] : args, new.target);
  }
  Date_.prototype = Real.prototype;
  Real.prototype.constructor = Date_;
  Date_.now = function now() { return getNow(); };
  Date_.parse = Real.parse;
  Date_.UTC = Real.UTC;
  Object.defineProperty(Date_, "name", { value: "Date" });
  Object.defineProperty(Date_, "length", { value: 7 });
  return Date_;
})`;

function installClock(page, window) {
  const time = page.time;
  window.Date = window.eval(DATE_FACTORY)(() => time.wall);
  Object.defineProperty(window.performance, "now", {
    value: () => time.span - time.start, configurable: true, writable: true, enumerable: true,
  });
  // One id sequence for both kinds; a timer runs only inside advance.
  const add = (name, repeat) => function (handler, delay, ...args) {
    if (typeof handler !== "function") {
      throw new window.Error("the page-script harness runs no timer: " + name + " handler is not a function");
    }
    // Whole milliseconds, as a browser converts the delay.
    const ms = Math.max(0, Math.trunc(Number(delay)) || 0);
    const id = time.nextId++;
    time.queue.push({ id, handler, args, due: time.span + ms, period: repeat ? ms : null, seq: time.nextSeq++ });
    return id;
  };
  const clear = function (id) {
    time.queue = time.queue.filter((t) => t.id !== id);
  };
  window.setTimeout = add("setTimeout", false);
  window.setInterval = add("setInterval", true);
  window.clearTimeout = clear;
  window.clearInterval = clear;
}

// --- requests on a clock page (#63) -----------------------------------------------

// A request URL resolved by the page's own URL parser against location.href,
// without its fragment; an unparsable one is kept as written.
function resolveURL(window, url) {
  try {
    const u = new window.URL(String(url), window.location.href);
    u.hash = "";
    return u.href;
  } catch (e) {
    return String(url);
  }
}

// The page's own HEAD is answered from page.status; every other request is
// logged, recorded as a refusal the step's end raises even when the page
// catches it, and thrown into the page.
function installNetwork(page, window, errors) {
  const refuse = (entry, message) => {
    page.requests.push(entry);
    errors.push({ type: "refused", message });
    throw new window.Error(message);
  };
  window.fetch = function (input, init) {
    const options = init === undefined || init === null ? {} : init;
    const method = String(options.method === undefined ? "GET" : options.method).toUpperCase();
    const url = resolveURL(window, input);
    const entry = { method, url, cache: options.cache === undefined ? "default" : String(options.cache), api: "fetch" };
    if (method !== "HEAD" || url !== page.ownURL) {
      refuse(entry, "the page-script harness loads no subresource: fetch " + method + " " + url);
    }
    page.requests.push(entry);
    if (page.status === null) return window.Promise.reject(new window.TypeError("Failed to fetch"));
    // A page-realm object with these three own keys only; a 3xx is answered,
    // not followed.
    const response = new window.Object();
    response.status = page.status;
    response.ok = page.status >= 200 && page.status <= 299;
    response.statusText = "";
    return window.Promise.resolve(response);
  };
  for (const name of ["XMLHttpRequest", "WebSocket", "EventSource"]) {
    window[name] = function (url) {
      const entry = { method: null, url: name === "XMLHttpRequest" ? null : resolveURL(window, url), cache: null, api: name };
      refuse(entry, "the page-script harness loads no subresource: " + name);
    };
  }
}

function moveTo(time, at) {
  time.wall += at - time.span;
  time.span = at;
}

// The timer due first by end: earliest due time, then the order it was set.
function nextDue(time, end) {
  let first = null;
  for (const t of time.queue) {
    if (t.due <= end && (first === null || t.due < first.due || (t.due === first.due && t.seq < first.seq))) first = t;
  }
  return first;
}

// Run each timer due within the span, one at a time, each with the clocks at
// max(due, now) and a settle after it; the clocks end at the span's end, or
// at the run that failed. A hidden page's timers are held: none runs.
async function advance(page, ms) {
  const time = page.time;
  const end = time.span + ms;
  let runs = 0;
  while (page.visible) {
    const timer = nextDue(time, end);
    if (timer === null) break;
    if (runs === TIMER_LIMIT) throw new PageError("timer limit: more than 10000 timer runs in one advance");
    runs++;
    moveTo(time, Math.max(timer.due, time.span));
    if (timer.period === null) {
      time.queue = time.queue.filter((t) => t !== timer);
    } else {
      // Re-queued before its call, so the callback can clear it.
      timer.due = time.span + timer.period;
      timer.seq = time.nextSeq++;
    }
    try {
      timer.handler.apply(page.window, timer.args);
    } catch (e) {
      page.errors.push({ type: "unhandled-exception", cause: e });
    }
    await finish(page);
  }
  moveTo(time, end);
}

function beforeParse(page, window) {
  records.set(window, page);
  page.window = window;
  page.document = window.document;
  // Network and timers both throw. With no timer, the setImmediate after
  // each step is a true settle point.
  for (const [names, refusal] of [[NETWORK, "loads no subresource"], [TIMERS, "runs no timer"]]) {
    for (const name of names) {
      window[name] = function () {
        throw new Error("the page-script harness " + refusal + ": " + name);
      };
    }
  }
  // Own accessors on the document win over pretendToBeVisual (spike U2).
  Object.defineProperty(window.document, "hidden", { get: () => !page.visible, configurable: true });
  Object.defineProperty(window.document, "visibilityState", {
    get: () => (page.visible ? "visible" : "hidden"), configurable: true,
  });
  if (page.time !== null) {
    // The own URL is the URL opened, without its fragment, fixed at open.
    if (page.ownURL === null) page.ownURL = resolveURL(window, page.url);
    installClock(page, window);
    installNetwork(page, window, page.errors);
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
    if (e.type === "refused") throw new PageError(e.message);
    if (e.type === "not-implemented") {
      throw new PageError("page used something jsdom does not implement: " + e.message);
    }
  }
}

const RELOAD_LIMIT = 100;

// Every step that runs page code ends here: settle, then fail on the first
// error the step left; else carry out a pending reload and settle again.
async function finish(page) {
  for (;;) {
    await settle();
    try {
      drain(page.errors);
    } catch (e) {
      // A step ending in an error drops its pending reload; the count stands.
      page.pendingReload = false;
      throw e;
    }
    if (!page.pendingReload) return;
    page.pendingReload = false;
    if (page.carried === RELOAD_LIMIT) throw new PageError("reload limit: more than 100 reloads in one step");
    page.carried++;
    await reload(page);
  }
}

const FETCH_LIMIT_MS = 10000;

// GET a served page: the host checked again, its own Agent (so no proxy the
// environment names), a 3xx not followed, one limit over the whole fetch.
function httpGet(url) {
  let parsed = null;
  try {
    parsed = new URL(url);
  } catch (e) {
    parsed = null;
  }
  if (parsed === null || parsed.protocol !== "http:" || (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "[::1]")) {
    return Promise.reject(new Error("cannot fetch " + url + ": not a loopback http URL"));
  }
  return new Promise((resolve, reject) => {
    const agent = new http.Agent({ keepAlive: false });
    let done = false;
    let request = null;
    const end = () => {
      done = true;
      clearTimeout(timer);
      agent.destroy();
    };
    const fail = (reason) => {
      if (done) return;
      end();
      if (request !== null) request.destroy();
      reject(new Error("cannot fetch " + url + ": " + reason));
    };
    const timer = setTimeout(() => fail("no answer within 10 s"), FETCH_LIMIT_MS);
    request = http.get(parsed, { agent }, (response) => {
      if (response.statusCode < 200 || response.statusCode > 299) {
        response.resume();
        fail("status " + response.statusCode);
        return;
      }
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("error", (e) => fail(e.code || e.message));
      response.on("end", () => {
        if (done) return;
        end();
        resolve(Buffer.concat(chunks).toString("utf8"));
      });
    });
    request.on("error", (e) => fail(e.code || e.message));
  });
}

// source is {path} or {url}: the file read again, or the URL fetched again.
async function readSource(source) {
  if (source.url !== undefined) return httpGet(source.url);
  return fs.readFileSync(source.path, "utf8");
}

// Load the page again from where it was opened, into the same record: the
// clock, visibility, status, request log and counts are kept; the old
// window and its timers are dropped. A failed read closes the page.
async function reload(page) {
  let text;
  try {
    text = await readSource(page.source);
  } catch (e) {
    page.window.close();
    page.closed = true;
    throw e;
  }
  page.window.close();
  page.time.queue = [];
  await build(page, text);
}

// Build a document from source text into the page record. The record exists
// first, so beforeParse can file it under the window before any script runs.
async function build(page, text) {
  if (page.time !== null) page.time.start = page.time.span;
  const errors = [];
  page.errors = errors;
  activeErrors = errors;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => errors.push(e));
  // No resources option (D6): jsdom loads no script, sheet or frame the page
  // names (spike.md U2), and beforeParse stubs the network APIs.
  const dom = new JSDOM(text, {
    url: page.url,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse: (window) => beforeParse(page, window),
  });
  page.dom = dom;
  page.window = dom.window;
  page.document = dom.window.document;
  if (page.document.readyState !== "complete") {
    await new Promise((resolve) => page.window.addEventListener("load", resolve, { once: true }));
  }
}

// source is {path} or {url}; clock is the controlled clock's start in ms, or null.
async function load(source, clock) {
  // remembered: name -> {selector, nodes}, the references remember keeps for
  // same. Nothing is written to a DOM node.
  const page = {
    source,
    url: source.url !== undefined ? source.url : pathToFileURL(source.path).href,
    dom: null,
    window: null,
    document: null,
    errors: [],
    remembered: new Map(),
    visible: true,
    time: clock === null ? null : makeTime(clock),
    // Clock pages only: the own URL, the HEAD answer and the request log.
    ownURL: null,
    status: null,
    requests: [],
    // reloads: location.reload() calls; carried: reloads carried out in this
    // request; closed: a reload failed and the page is gone.
    reloads: 0,
    pendingReload: false,
    carried: 0,
    closed: false,
  };
  try {
    await build(page, await readSource(source));
    await finish(page);
  } catch (e) {
    if (page.window !== null) page.window.close();
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

function matchAll(page, selector) {
  try {
    return Array.from(page.document.querySelectorAll(selector));
  } catch (e) {
    throw new Error("bad selector " + JSON.stringify(selector) + ": " + e.message);
  }
}

function query(page, selector) {
  return matchAll(page, selector).map((el) => ({
    tag: tagOf(el),
    attrs: Array.from(el.attributes, (a) => [a.name, a.value]),
    classes: Array.from(el.classList),
    text: el.textContent,
  }));
}

// An empty set would leave same() true while the match stayed empty, which
// says nothing about identity. The refusal comes before the store, so an
// earlier set under the name stays.
function remember(page, name, selector) {
  const nodes = matchAll(page, selector);
  if (nodes.length === 0) throw new PageError("remember: " + JSON.stringify(selector) + " matches no element");
  page.remembered.set(name, { selector, nodes });
  return nodes.length;
}

function same(page, name) {
  const entry = page.remembered.get(name);
  if (entry === undefined) throw new Error("same: nothing remembered as " + JSON.stringify(name));
  const now = matchAll(page, entry.selector);
  return now.length === entry.nodes.length && now.every((el, i) => el === entry.nodes[i]);
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
  await finish(page);
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
  await finish(page);
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
  await finish(page);
}

// A key pressed where the focus is: on the active element, else on the body.
async function key(page, name) {
  const { document, window } = page;
  const target = document.activeElement !== null ? document.activeElement : document.body;
  target.dispatchEvent(new window.KeyboardEvent("keydown", { key: name, bubbles: true, cancelable: true }));
  await finish(page);
}

// A change of visibility fires visibilitychange on the document; the same
// value fires nothing.
async function setVisible(page, visible) {
  if (page.visible === visible) return;
  page.visible = visible;
  page.document.dispatchEvent(new page.window.Event("visibilitychange", { bubbles: true }));
  await finish(page);
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
  const page = await load({ path }, null);
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

function needClock(state, op) {
  const page = needPage(state);
  if (page.time === null) throw new Error(op + ": the page was opened without a clock");
  return page;
}

async function handle(request, state) {
  switch (request.op) {
    case "open":
      if (state.page !== null) throw new Error("a page is already open");
      state.page = await load(
        request.url !== undefined ? { url: request.url } : { path: request.path },
        request.clock === undefined ? null : request.clock,
      );
      return null;
    case "query":
      return query(needPage(state), request.selector);
    case "remember":
      return remember(needPage(state), request.name, request.selector);
    case "same":
      return same(needPage(state), request.name);
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
    case "advance":
      await advance(needClock(state, "advance"), request.ms);
      return null;
    case "set_visible":
      await setVisible(needPage(state), request.visible);
      return null;
    case "set_status":
      needClock(state, "set_status").status = request.status;
      return null;
    case "requests":
      return needClock(state, "requests").requests;
    case "reloads":
      return needClock(state, "reloads").reloads;
    case "set_clock":
      needClock(state, "set_clock").time.wall = request.ms;
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
    if (state.page !== null) state.page.carried = 0;
    try {
      reply = { ok: true, result: await handle(JSON.parse(line), state) };
    } catch (e) {
      reply = { ok: false, kind: e instanceof PageError ? "page" : "harness", error: String(e && e.message) };
    }
    if (state.page !== null && state.page.closed) state.page = null;
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
