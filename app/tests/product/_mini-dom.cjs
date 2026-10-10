// DOM minimo per montare componenti React 18 in node e provarne il CABLAGGIO (effetti, ref, eventi),
// non solo l'HTML statico (10/10/2026, Opus 5.5 — audit Vol Deck MEDIA-7). Nessuna dipendenza nuova:
// solo cio' che react-dom/client usa davvero (createElement/NS, testi, attributi, stile, listener sul
// contenitore). Gli eventi si consegnano ai listener che React registra sul contenitore, col target
// vero: React risale le fibre da li' come nel browser.
const HTML_NS = 'http://www.w3.org/1999/xhtml';

class Node {
  constructor(document, nodeType, nodeName) {
    this.ownerDocument = document; this.nodeType = nodeType; this.nodeName = nodeName;
    this.childNodes = []; this.parentNode = null;
  }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const p = this.parentNode; if (!p) return null; return p.childNodes[p.childNodes.indexOf(this) + 1] || null; }
  appendChild(child) { if (child.parentNode) child.parentNode.removeChild(child); this.childNodes.push(child); child.parentNode = this; return child; }
  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    if (child.parentNode) child.parentNode.removeChild(child);
    this.childNodes.splice(this.childNodes.indexOf(ref), 0, child); child.parentNode = this; return child;
  }
  removeChild(child) { const i = this.childNodes.indexOf(child); if (i >= 0) this.childNodes.splice(i, 1); child.parentNode = null; return child; }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(c => c.textContent).join(''); }
  set textContent(value) {
    if (this.nodeType === 3) { this.nodeValue = String(value); return; }
    this.childNodes.forEach(c => { c.parentNode = null; }); this.childNodes = [];
    if (value !== '' && value != null) this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }
}
class Text extends Node {
  constructor(document, text) { super(document, 3, '#text'); this.nodeValue = text; }
  get data() { return this.nodeValue; }
}
class Element extends Node {
  constructor(document, tag, namespaceURI = HTML_NS) {
    super(document, 1, namespaceURI === HTML_NS ? tag.toUpperCase() : tag);
    this.tagName = this.nodeName; this.localName = tag; this.namespaceURI = namespaceURI;
    this.attributes = new Map(); this.listeners = [];
    const style = {}; Object.defineProperty(style, 'setProperty', { value: (k, v) => { style[k] = v; } });
    Object.defineProperty(style, 'removeProperty', { value: k => { delete style[k]; } });
    this.style = style; this.rect = null;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  setAttributeNS(_ns, name, value) { this.setAttribute(name, value); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  removeAttributeNS(_ns, name) { this.removeAttribute(name); }
  get className() { return this.getAttribute('class') || ''; }
  addEventListener(type, listener, options) { this.listeners.push({ type, listener, capture: options === true || !!options?.capture }); }
  removeEventListener(type, listener) { this.listeners = this.listeners.filter(l => l.type !== type || l.listener !== listener); }
  getBoundingClientRect() {
    const r = this.rect || this.ownerDocument.defaultRect;
    return { left: r.left, top: r.top, width: r.width, height: r.height, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top };
  }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  // ricerca per predicato (al posto dei selettori CSS)
  all(predicate) { const out = []; const walk = n => { for (const c of n.childNodes) { if (c.nodeType === 1) { if (predicate(c)) out.push(c); walk(c); } } }; walk(this); return out; }
  byClass(name) { return this.all(el => el.className.split(/\s+/).includes(name)); }
  byAttr(name, value) { return this.all(el => el.hasAttribute(name) && (value === undefined || el.getAttribute(name) === String(value))); }
  byTag(tag) { return this.all(el => el.localName === tag); }
}

function createDocument() {
  const document = new Node(null, 9, '#document');
  document.ownerDocument = null;
  document.defaultRect = { left: 0, top: 0, width: 800, height: 300 };
  document.createElement = tag => new Element(document, tag);
  document.createElementNS = (ns, tag) => new Element(document, tag, ns);
  document.createTextNode = text => new Text(document, text);
  document.createComment = () => new Node(document, 8, '#comment');
  document.documentElement = new Element(document, 'html'); document.appendChild(document.documentElement);
  document.body = new Element(document, 'body'); document.documentElement.appendChild(document.body);
  document.head = new Element(document, 'head'); document.documentElement.appendChild(document.head);
  document.activeElement = document.body;
  document.listeners = [];
  document.addEventListener = (type, listener) => document.listeners.push({ type, listener });
  document.removeEventListener = () => {};
  document.baseURI = 'http://synthetic.invalid/';
  return document;
}

/** Installa document/window globali per la durata di un test; restituisce il ripristino. */
function installDom() {
  const saved = { document: globalThis.document, window: globalThis.window, getComputedStyle: globalThis.getComputedStyle,
    act: globalThis.IS_REACT_ACT_ENVIRONMENT, HTMLIFrameElement: globalThis.HTMLIFrameElement };
  const document = createDocument();
  const window = { document, HTMLIFrameElement: class {}, getComputedStyle: () => ({ getPropertyValue: () => '' }),
    addEventListener() {}, removeEventListener() {}, location: { href: 'http://synthetic.invalid/' } };
  window.window = window;
  globalThis.document = document; globalThis.window = window;
  globalThis.HTMLIFrameElement = window.HTMLIFrameElement;
  globalThis.getComputedStyle = window.getComputedStyle;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const restore = () => {
    for (const [k, v] of Object.entries({ document: saved.document, window: saved.window, getComputedStyle: saved.getComputedStyle,
      HTMLIFrameElement: saved.HTMLIFrameElement, IS_REACT_ACT_ENVIRONMENT: saved.act })) {
      if (v === undefined) delete globalThis[k]; else globalThis[k] = v;
    }
  };
  return { document, window, restore };
}

/** Consegna un evento nativo come il browser: ai listener che React ha messo sul contenitore. */
function dispatch(container, target, type, init = {}) {
  let defaultPrevented = false, stopped = false;
  const event = { type, target, srcElement: target, bubbles: true, cancelable: true, timeStamp: Date.now(), isTrusted: true,
    button: 0, buttons: 0, pointerType: 'mouse', clientX: 0, clientY: 0, key: undefined, ...init,
    preventDefault() { defaultPrevented = true; this.defaultPrevented = true; }, stopPropagation() { stopped = true; },
    get cancelBubble() { return stopped; }, composedPath: () => [], defaultPrevented: false };
  for (const capture of [true, false]) for (const l of container.listeners.filter(x => x.type === type && x.capture === capture)) l.listener(event);
  return { defaultPrevented };
}

module.exports = { installDom, dispatch };
