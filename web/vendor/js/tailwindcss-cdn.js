"use strict";
var TailwindCSS = (() => {
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __commonJS = (cb, mod) => function __require() {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  };

  // picocolors/picocolors.browser.js
  var require_picocolors_browser = __commonJS({
    "picocolors/picocolors.browser.js"(exports, module) {
      var x = String;
      var create = function() {
        return { isColorSupported: false, reset: x, bold: x, dim: x, italic: x, underline: x, inverse: x, hidden: x, strikethrough: x, black: x, red: x, green: x, yellow: x, blue: x, magenta: x, cyan: x, white: x, gray: x, bgBlack: x, bgRed: x, bgGreen: x, bgYellow: x, bgBlue: x, bgMagenta: x, bgCyan: x, bgWhite: x, blackBright: x, redBright: x, greenBright: x, yellowBright: x, blueBright: x, magentaBright: x, cyanBright: x, whiteBright: x, bgBlackBright: x, bgRedBright: x, bgGreenBright: x, bgYellowBright: x, bgBlueBright: x, bgMagentaBright: x, bgCyanBright: x, bgWhiteBright: x };
      };
      module.exports = create();
      module.exports.createColors = create;
    }
  });

  // (disabled):postcss/lib/terminal-highlight
  var require_terminal_highlight = __commonJS({
    "(disabled):postcss/lib/terminal-highlight"() {
    }
  });

  // postcss/lib/css-syntax-error.js
  var require_css_syntax_error = __commonJS({
    "postcss/lib/css-syntax-error.js"(exports, module) {
      "use strict";
      var pico = require_picocolors_browser();
      var terminalHighlight = require_terminal_highlight();
      var CssSyntaxError = class _CssSyntaxError extends Error {
        constructor(message, line, column, source, file, plugin2) {
          super(message);
          this.name = "CssSyntaxError";
          this.reason = message;
          if (file) {
            this.file = file;
          }
          if (source) {
            this.source = source;
          }
          if (plugin2) {
            this.plugin = plugin2;
          }
          if (typeof line !== "undefined" && typeof column !== "undefined") {
            if (typeof line === "number") {
              this.line = line;
              this.column = column;
            } else {
              this.line = line.line;
              this.column = line.column;
              this.endLine = column.line;
              this.endColumn = column.column;
            }
          }
          this.setMessage();
          if (Error.captureStackTrace) {
            Error.captureStackTrace(this, _CssSyntaxError);
          }
        }
        setMessage() {
          this.message = this.plugin ? this.plugin + ": " : "";
          this.message += this.file ? this.file : "<css input>";
          if (typeof this.line !== "undefined") {
            this.message += ":" + this.line + ":" + this.column;
          }
          this.message += ": " + this.reason;
        }
        showSourceCode(color) {
          if (!this.source) return "";
          let css = this.source;
          if (color == null) color = pico.isColorSupported;
          let aside = (text) => text;
          let mark = (text) => text;
          let highlight = (text) => text;
          if (color) {
            let { bold, gray, red } = pico.createColors(true);
            mark = (text) => bold(red(text));
            aside = (text) => gray(text);
            if (terminalHighlight) {
              highlight = (text) => terminalHighlight(text);
            }
          }
          let lines = css.split(/\r?\n/);
          let start = Math.max(this.line - 3, 0);
          let end = Math.min(this.line + 2, lines.length);
          let maxWidth = String(end).length;
          return lines.slice(start, end).map((line, index) => {
            let number = start + 1 + index;
            let gutter = " " + (" " + number).slice(-maxWidth) + " | ";
            if (number === this.line) {
              if (line.length > 160) {
                let padding = 20;
                let subLineStart = Math.max(0, this.column - padding);
                let subLineEnd = Math.max(
                  this.column + padding,
                  this.endColumn + padding
                );
                let subLine = line.slice(subLineStart, subLineEnd);
                let spacing2 = aside(gutter.replace(/\d/g, " ")) + line.slice(0, Math.min(this.column - 1, padding - 1)).replace(/[^\t]/g, " ");
                return mark(">") + aside(gutter) + highlight(subLine) + "\n " + spacing2 + mark("^");
              }
              let spacing = aside(gutter.replace(/\d/g, " ")) + line.slice(0, this.column - 1).replace(/[^\t]/g, " ");
              return mark(">") + aside(gutter) + highlight(line) + "\n " + spacing + mark("^");
            }
            return " " + aside(gutter) + highlight(line);
          }).join("\n");
        }
        toString() {
          let code = this.showSourceCode();
          if (code) {
            code = "\n\n" + code + "\n";
          }
          return this.name + ": " + this.message + code;
        }
      };
      module.exports = CssSyntaxError;
      CssSyntaxError.default = CssSyntaxError;
    }
  });

  // postcss/lib/stringifier.js
  var require_stringifier = __commonJS({
    "postcss/lib/stringifier.js"(exports, module) {
      "use strict";
      var DEFAULT_RAW = {
        after: "\n",
        beforeClose: "\n",
        beforeComment: "\n",
        beforeDecl: "\n",
        beforeOpen: " ",
        beforeRule: "\n",
        colon: ": ",
        commentLeft: " ",
        commentRight: " ",
        emptyBody: "",
        indent: "    ",
        semicolon: false
      };
      function capitalize(str) {
        return str[0].toUpperCase() + str.slice(1);
      }
      var Stringifier = class {
        constructor(builder) {
          this.builder = builder;
        }
        atrule(node, semicolon) {
          let name = "@" + node.name;
          let params = node.params ? this.rawValue(node, "params") : "";
          if (typeof node.raws.afterName !== "undefined") {
            name += node.raws.afterName;
          } else if (params) {
            name += " ";
          }
          if (node.nodes) {
            this.block(node, name + params);
          } else {
            let end = (node.raws.between || "") + (semicolon ? ";" : "");
            this.builder(name + params + end, node);
          }
        }
        beforeAfter(node, detect) {
          let value;
          if (node.type === "decl") {
            value = this.raw(node, null, "beforeDecl");
          } else if (node.type === "comment") {
            value = this.raw(node, null, "beforeComment");
          } else if (detect === "before") {
            value = this.raw(node, null, "beforeRule");
          } else {
            value = this.raw(node, null, "beforeClose");
          }
          let buf = node.parent;
          let depth = 0;
          while (buf && buf.type !== "root") {
            depth += 1;
            buf = buf.parent;
          }
          if (value.includes("\n")) {
            let indent = this.raw(node, null, "indent");
            if (indent.length) {
              for (let step = 0; step < depth; step++) value += indent;
            }
          }
          return value;
        }
        block(node, start) {
          let between = this.raw(node, "between", "beforeOpen");
          this.builder(start + between + "{", node, "start");
          let after;
          if (node.nodes && node.nodes.length) {
            this.body(node);
            after = this.raw(node, "after");
          } else {
            after = this.raw(node, "after", "emptyBody");
          }
          if (after) this.builder(after);
          this.builder("}", node, "end");
        }
        body(node) {
          let last = node.nodes.length - 1;
          while (last > 0) {
            if (node.nodes[last].type !== "comment") break;
            last -= 1;
          }
          let semicolon = this.raw(node, "semicolon");
          for (let i = 0; i < node.nodes.length; i++) {
            let child = node.nodes[i];
            let before = this.raw(child, "before");
            if (before) this.builder(before);
            this.stringify(child, last !== i || semicolon);
          }
        }
        comment(node) {
          let left = this.raw(node, "left", "commentLeft");
          let right = this.raw(node, "right", "commentRight");
          this.builder("/*" + left + node.text + right + "*/", node);
        }
        decl(node, semicolon) {
          let between = this.raw(node, "between", "colon");
          let string = node.prop + between + this.rawValue(node, "value");
          if (node.important) {
            string += node.raws.important || " !important";
          }
          if (semicolon) string += ";";
          this.builder(string, node);
        }
        document(node) {
          this.body(node);
        }
        raw(node, own, detect) {
          let value;
          if (!detect) detect = own;
          if (own) {
            value = node.raws[own];
            if (typeof value !== "undefined") return value;
          }
          let parent = node.parent;
          if (detect === "before") {
            if (!parent || parent.type === "root" && parent.first === node) {
              return "";
            }
            if (parent && parent.type === "document") {
              return "";
            }
          }
          if (!parent) return DEFAULT_RAW[detect];
          let root = node.root();
          if (!root.rawCache) root.rawCache = {};
          if (typeof root.rawCache[detect] !== "undefined") {
            return root.rawCache[detect];
          }
          if (detect === "before" || detect === "after") {
            return this.beforeAfter(node, detect);
          } else {
            let method = "raw" + capitalize(detect);
            if (this[method]) {
              value = this[method](root, node);
            } else {
              root.walk((i) => {
                value = i.raws[own];
                if (typeof value !== "undefined") return false;
              });
            }
          }
          if (typeof value === "undefined") value = DEFAULT_RAW[detect];
          root.rawCache[detect] = value;
          return value;
        }
        rawBeforeClose(root) {
          let value;
          root.walk((i) => {
            if (i.nodes && i.nodes.length > 0) {
              if (typeof i.raws.after !== "undefined") {
                value = i.raws.after;
                if (value.includes("\n")) {
                  value = value.replace(/[^\n]+$/, "");
                }
                return false;
              }
            }
          });
          if (value) value = value.replace(/\S/g, "");
          return value;
        }
        rawBeforeComment(root, node) {
          let value;
          root.walkComments((i) => {
            if (typeof i.raws.before !== "undefined") {
              value = i.raws.before;
              if (value.includes("\n")) {
                value = value.replace(/[^\n]+$/, "");
              }
              return false;
            }
          });
          if (typeof value === "undefined") {
            value = this.raw(node, null, "beforeDecl");
          } else if (value) {
            value = value.replace(/\S/g, "");
          }
          return value;
        }
        rawBeforeDecl(root, node) {
          let value;
          root.walkDecls((i) => {
            if (typeof i.raws.before !== "undefined") {
              value = i.raws.before;
              if (value.includes("\n")) {
                value = value.replace(/[^\n]+$/, "");
              }
              return false;
            }
          });
          if (typeof value === "undefined") {
            value = this.raw(node, null, "beforeRule");
          } else if (value) {
            value = value.replace(/\S/g, "");
          }
          return value;
        }
        rawBeforeOpen(root) {
          let value;
          root.walk((i) => {
            if (i.type !== "decl") {
              value = i.raws.between;
              if (typeof value !== "undefined") return false;
            }
          });
          return value;
        }
        rawBeforeRule(root) {
          let value;
          root.walk((i) => {
            if (i.nodes && (i.parent !== root || root.first !== i)) {
              if (typeof i.raws.before !== "undefined") {
                value = i.raws.before;
                if (value.includes("\n")) {
                  value = value.replace(/[^\n]+$/, "");
                }
                return false;
              }
            }
          });
          if (value) value = value.replace(/\S/g, "");
          return value;
        }
        rawColon(root) {
          let value;
          root.walkDecls((i) => {
            if (typeof i.raws.between !== "undefined") {
              value = i.raws.between.replace(/[^\s:]/g, "");
              return false;
            }
          });
          return value;
        }
        rawEmptyBody(root) {
          let value;
          root.walk((i) => {
            if (i.nodes && i.nodes.length === 0) {
              value = i.raws.after;
              if (typeof value !== "undefined") return false;
            }
          });
          return value;
        }
        rawIndent(root) {
          if (root.raws.indent) return root.raws.indent;
          let value;
          root.walk((i) => {
            let p = i.parent;
            if (p && p !== root && p.parent && p.parent === root) {
              if (typeof i.raws.before !== "undefined") {
                let parts = i.raws.before.split("\n");
                value = parts[parts.length - 1];
                value = value.replace(/\S/g, "");
                return false;
              }
            }
          });
          return value;
        }
        rawSemicolon(root) {
          let value;
          root.walk((i) => {
            if (i.nodes && i.nodes.length && i.last.type === "decl") {
              value = i.raws.semicolon;
              if (typeof value !== "undefined") return false;
            }
          });
          return value;
        }
        rawValue(node, prop) {
          let value = node[prop];
          let raw = node.raws[prop];
          if (raw && raw.value === value) {
            return raw.raw;
          }
          return value;
        }
        root(node) {
          this.body(node);
          if (node.raws.after) this.builder(node.raws.after);
        }
        rule(node) {
          this.block(node, this.rawValue(node, "selector"));
          if (node.raws.ownSemicolon) {
            this.builder(node.raws.ownSemicolon, node, "end");
          }
        }
        stringify(node, semicolon) {
          if (!this[node.type]) {
            throw new Error(
              "Unknown AST node type " + node.type + ". Maybe you need to change PostCSS stringifier."
            );
          }
          this[node.type](node, semicolon);
        }
      };
      module.exports = Stringifier;
      Stringifier.default = Stringifier;
    }
  });

  // postcss/lib/stringify.js
  var require_stringify = __commonJS({
    "postcss/lib/stringify.js"(exports, module) {
      "use strict";
      var Stringifier = require_stringifier();
      function stringify(node, builder) {
        let str = new Stringifier(builder);
        str.stringify(node);
      }
      module.exports = stringify;
      stringify.default = stringify;
    }
  });

  // postcss/lib/symbols.js
  var require_symbols = __commonJS({
    "postcss/lib/symbols.js"(exports, module) {
      "use strict";
      module.exports.isClean = Symbol("isClean");
      module.exports.my = Symbol("my");
    }
  });

  // postcss/lib/node.js
  var require_node = __commonJS({
    "postcss/lib/node.js"(exports, module) {
      "use strict";
      var CssSyntaxError = require_css_syntax_error();
      var Stringifier = require_stringifier();
      var stringify = require_stringify();
      var { isClean, my } = require_symbols();
      function cloneNode(obj, parent) {
        let cloned = new obj.constructor();
        for (let i in obj) {
          if (!Object.prototype.hasOwnProperty.call(obj, i)) {
            continue;
          }
          if (i === "proxyCache") continue;
          let value = obj[i];
          let type = typeof value;
          if (i === "parent" && type === "object") {
            if (parent) cloned[i] = parent;
          } else if (i === "source") {
            cloned[i] = value;
          } else if (Array.isArray(value)) {
            cloned[i] = value.map((j) => cloneNode(j, cloned));
          } else {
            if (type === "object" && value !== null) value = cloneNode(value);
            cloned[i] = value;
          }
        }
        return cloned;
      }
      var Node = class {
        constructor(defaults = {}) {
          this.raws = {};
          this[isClean] = false;
          this[my] = true;
          for (let name in defaults) {
            if (name === "nodes") {
              this.nodes = [];
              for (let node of defaults[name]) {
                if (typeof node.clone === "function") {
                  this.append(node.clone());
                } else {
                  this.append(node);
                }
              }
            } else {
              this[name] = defaults[name];
            }
          }
        }
        addToError(error) {
          error.postcssNode = this;
          if (error.stack && this.source && /\n\s{4}at /.test(error.stack)) {
            let s = this.source;
            error.stack = error.stack.replace(
              /\n\s{4}at /,
              `$&${s.input.from}:${s.start.line}:${s.start.column}$&`
            );
          }
          return error;
        }
        after(add) {
          this.parent.insertAfter(this, add);
          return this;
        }
        assign(overrides = {}) {
          for (let name in overrides) {
            this[name] = overrides[name];
          }
          return this;
        }
        before(add) {
          this.parent.insertBefore(this, add);
          return this;
        }
        cleanRaws(keepBetween) {
          delete this.raws.before;
          delete this.raws.after;
          if (!keepBetween) delete this.raws.between;
        }
        clone(overrides = {}) {
          let cloned = cloneNode(this);
          for (let name in overrides) {
            cloned[name] = overrides[name];
          }
          return cloned;
        }
        cloneAfter(overrides = {}) {
          let cloned = this.clone(overrides);
          this.parent.insertAfter(this, cloned);
          return cloned;
        }
        cloneBefore(overrides = {}) {
          let cloned = this.clone(overrides);
          this.parent.insertBefore(this, cloned);
          return cloned;
        }
        error(message, opts = {}) {
          if (this.source) {
            let { end, start } = this.rangeBy(opts);
            return this.source.input.error(
              message,
              { column: start.column, line: start.line },
              { column: end.column, line: end.line },
              opts
            );
          }
          return new CssSyntaxError(message);
        }
        getProxyProcessor() {
          return {
            get(node, prop) {
              if (prop === "proxyOf") {
                return node;
              } else if (prop === "root") {
                return () => node.root().toProxy();
              } else {
                return node[prop];
              }
            },
            set(node, prop, value) {
              if (node[prop] === value) return true;
              node[prop] = value;
              if (prop === "prop" || prop === "value" || prop === "name" || prop === "params" || prop === "important" || /* c8 ignore next */
              prop === "text") {
                node.markDirty();
              }
              return true;
            }
          };
        }
        /* c8 ignore next 3 */
        markClean() {
          this[isClean] = true;
        }
        markDirty() {
          if (this[isClean]) {
            this[isClean] = false;
            let next = this;
            while (next = next.parent) {
              next[isClean] = false;
            }
          }
        }
        next() {
          if (!this.parent) return void 0;
          let index = this.parent.index(this);
          return this.parent.nodes[index + 1];
        }
        positionBy(opts, stringRepresentation) {
          let pos = this.source.start;
          if (opts.index) {
            pos = this.positionInside(opts.index, stringRepresentation);
          } else if (opts.word) {
            stringRepresentation = this.toString();
            let index = stringRepresentation.indexOf(opts.word);
            if (index !== -1) pos = this.positionInside(index, stringRepresentation);
          }
          return pos;
        }
        positionInside(index, stringRepresentation) {
          let string = stringRepresentation || this.toString();
          let column = this.source.start.column;
          let line = this.source.start.line;
          for (let i = 0; i < index; i++) {
            if (string[i] === "\n") {
              column = 1;
              line += 1;
            } else {
              column += 1;
            }
          }
          return { column, line };
        }
        prev() {
          if (!this.parent) return void 0;
          let index = this.parent.index(this);
          return this.parent.nodes[index - 1];
        }
        rangeBy(opts) {
          let start = {
            column: this.source.start.column,
            line: this.source.start.line
          };
          let end = this.source.end ? {
            column: this.source.end.column + 1,
            line: this.source.end.line
          } : {
            column: start.column + 1,
            line: start.line
          };
          if (opts.word) {
            let stringRepresentation = this.toString();
            let index = stringRepresentation.indexOf(opts.word);
            if (index !== -1) {
              start = this.positionInside(index, stringRepresentation);
              end = this.positionInside(
                index + opts.word.length,
                stringRepresentation
              );
            }
          } else {
            if (opts.start) {
              start = {
                column: opts.start.column,
                line: opts.start.line
              };
            } else if (opts.index) {
              start = this.positionInside(opts.index);
            }
            if (opts.end) {
              end = {
                column: opts.end.column,
                line: opts.end.line
              };
            } else if (typeof opts.endIndex === "number") {
              end = this.positionInside(opts.endIndex);
            } else if (opts.index) {
              end = this.positionInside(opts.index + 1);
            }
          }
          if (end.line < start.line || end.line === start.line && end.column <= start.column) {
            end = { column: start.column + 1, line: start.line };
          }
          return { end, start };
        }
        raw(prop, defaultType) {
          let str = new Stringifier();
          return str.raw(this, prop, defaultType);
        }
        remove() {
          if (this.parent) {
            this.parent.removeChild(this);
          }
          this.parent = void 0;
          return this;
        }
        replaceWith(...nodes) {
          if (this.parent) {
            let bookmark = this;
            let foundSelf = false;
            for (let node of nodes) {
              if (node === this) {
                foundSelf = true;
              } else if (foundSelf) {
                this.parent.insertAfter(bookmark, node);
                bookmark = node;
              } else {
                this.parent.insertBefore(bookmark, node);
              }
            }
            if (!foundSelf) {
              this.remove();
            }
          }
          return this;
        }
        root() {
          let result = this;
          while (result.parent && result.parent.type !== "document") {
            result = result.parent;
          }
          return result;
        }
        toJSON(_, inputs) {
          let fixed = {};
          let emitInputs = inputs == null;
          inputs = inputs || /* @__PURE__ */ new Map();
          let inputsNextIndex = 0;
          for (let name in this) {
            if (!Object.prototype.hasOwnProperty.call(this, name)) {
              continue;
            }
            if (name === "parent" || name === "proxyCache") continue;
            let value = this[name];
            if (Array.isArray(value)) {
              fixed[name] = value.map((i) => {
                if (typeof i === "object" && i.toJSON) {
                  return i.toJSON(null, inputs);
                } else {
                  return i;
                }
              });
            } else if (typeof value === "object" && value.toJSON) {
              fixed[name] = value.toJSON(null, inputs);
            } else if (name === "source") {
              let inputId = inputs.get(value.input);
              if (inputId == null) {
                inputId = inputsNextIndex;
                inputs.set(value.input, inputsNextIndex);
                inputsNextIndex++;
              }
              fixed[name] = {
                end: value.end,
                inputId,
                start: value.start
              };
            } else {
              fixed[name] = value;
            }
          }
          if (emitInputs) {
            fixed.inputs = [...inputs.keys()].map((input) => input.toJSON());
          }
          return fixed;
        }
        toProxy() {
          if (!this.proxyCache) {
            this.proxyCache = new Proxy(this, this.getProxyProcessor());
          }
          return this.proxyCache;
        }
        toString(stringifier = stringify) {
          if (stringifier.stringify) stringifier = stringifier.stringify;
          let result = "";
          stringifier(this, (i) => {
            result += i;
          });
          return result;
        }
        warn(result, text, opts) {
          let data = { node: this };
          for (let i in opts) data[i] = opts[i];
          return result.warn(text, data);
        }
        get proxyOf() {
          return this;
        }
      };
      module.exports = Node;
      Node.default = Node;
    }
  });

  // postcss/lib/comment.js
  var require_comment = __commonJS({
    "postcss/lib/comment.js"(exports, module) {
      "use strict";
      var Node = require_node();
      var Comment = class extends Node {
        constructor(defaults) {
          super(defaults);
          this.type = "comment";
        }
      };
      module.exports = Comment;
      Comment.default = Comment;
    }
  });

  // postcss/lib/declaration.js
  var require_declaration = __commonJS({
    "postcss/lib/declaration.js"(exports, module) {
      "use strict";
      var Node = require_node();
      var Declaration = class extends Node {
        constructor(defaults) {
          if (defaults && typeof defaults.value !== "undefined" && typeof defaults.value !== "string") {
            defaults = { ...defaults, value: String(defaults.value) };
          }
          super(defaults);
          this.type = "decl";
        }
        get variable() {
          return this.prop.startsWith("--") || this.prop[0] === "$";
        }
      };
      module.exports = Declaration;
      Declaration.default = Declaration;
    }
  });

  // postcss/lib/container.js
  var require_container = __commonJS({
    "postcss/lib/container.js"(exports, module) {
      "use strict";
      var Comment = require_comment();
      var Declaration = require_declaration();
      var Node = require_node();
      var { isClean, my } = require_symbols();
      var AtRule;
      var parse;
      var Root;
      var Rule;
      function cleanSource(nodes) {
        return nodes.map((i) => {
          if (i.nodes) i.nodes = cleanSource(i.nodes);
          delete i.source;
          return i;
        });
      }
      function markTreeDirty(node) {
        node[isClean] = false;
        if (node.proxyOf.nodes) {
          for (let i of node.proxyOf.nodes) {
            markTreeDirty(i);
          }
        }
      }
      var Container = class _Container extends Node {
        append(...children) {
          for (let child of children) {
            let nodes = this.normalize(child, this.last);
            for (let node of nodes) this.proxyOf.nodes.push(node);
          }
          this.markDirty();
          return this;
        }
        cleanRaws(keepBetween) {
          super.cleanRaws(keepBetween);
          if (this.nodes) {
            for (let node of this.nodes) node.cleanRaws(keepBetween);
          }
        }
        each(callback) {
          if (!this.proxyOf.nodes) return void 0;
          let iterator = this.getIterator();
          let index, result;
          while (this.indexes[iterator] < this.proxyOf.nodes.length) {
            index = this.indexes[iterator];
            result = callback(this.proxyOf.nodes[index], index);
            if (result === false) break;
            this.indexes[iterator] += 1;
          }
          delete this.indexes[iterator];
          return result;
        }
        every(condition) {
          return this.nodes.every(condition);
        }
        getIterator() {
          if (!this.lastEach) this.lastEach = 0;
          if (!this.indexes) this.indexes = {};
          this.lastEach += 1;
          let iterator = this.lastEach;
          this.indexes[iterator] = 0;
          return iterator;
        }
        getProxyProcessor() {
          return {
            get(node, prop) {
              if (prop === "proxyOf") {
                return node;
              } else if (!node[prop]) {
                return node[prop];
              } else if (prop === "each" || typeof prop === "string" && prop.startsWith("walk")) {
                return (...args) => {
                  return node[prop](
                    ...args.map((i) => {
                      if (typeof i === "function") {
                        return (child, index) => i(child.toProxy(), index);
                      } else {
                        return i;
                      }
                    })
                  );
                };
              } else if (prop === "every" || prop === "some") {
                return (cb) => {
                  return node[prop](
                    (child, ...other) => cb(child.toProxy(), ...other)
                  );
                };
              } else if (prop === "root") {
                return () => node.root().toProxy();
              } else if (prop === "nodes") {
                return node.nodes.map((i) => i.toProxy());
              } else if (prop === "first" || prop === "last") {
                return node[prop].toProxy();
              } else {
                return node[prop];
              }
            },
            set(node, prop, value) {
              if (node[prop] === value) return true;
              node[prop] = value;
              if (prop === "name" || prop === "params" || prop === "selector") {
                node.markDirty();
              }
              return true;
            }
          };
        }
        index(child) {
          if (typeof child === "number") return child;
          if (child.proxyOf) child = child.proxyOf;
          return this.proxyOf.nodes.indexOf(child);
        }
        insertAfter(exist, add) {
          let existIndex = this.index(exist);
          let nodes = this.normalize(add, this.proxyOf.nodes[existIndex]).reverse();
          existIndex = this.index(exist);
          for (let node of nodes) this.proxyOf.nodes.splice(existIndex + 1, 0, node);
          let index;
          for (let id in this.indexes) {
            index = this.indexes[id];
            if (existIndex < index) {
              this.indexes[id] = index + nodes.length;
            }
          }
          this.markDirty();
          return this;
        }
        insertBefore(exist, add) {
          let existIndex = this.index(exist);
          let type = existIndex === 0 ? "prepend" : false;
          let nodes = this.normalize(
            add,
            this.proxyOf.nodes[existIndex],
            type
          ).reverse();
          existIndex = this.index(exist);
          for (let node of nodes) this.proxyOf.nodes.splice(existIndex, 0, node);
          let index;
          for (let id in this.indexes) {
            index = this.indexes[id];
            if (existIndex <= index) {
              this.indexes[id] = index + nodes.length;
            }
          }
          this.markDirty();
          return this;
        }
        normalize(nodes, sample) {
          if (typeof nodes === "string") {
            nodes = cleanSource(parse(nodes).nodes);
          } else if (typeof nodes === "undefined") {
            nodes = [];
          } else if (Array.isArray(nodes)) {
            nodes = nodes.slice(0);
            for (let i of nodes) {
              if (i.parent) i.parent.removeChild(i, "ignore");
            }
          } else if (nodes.type === "root" && this.type !== "document") {
            nodes = nodes.nodes.slice(0);
            for (let i of nodes) {
              if (i.parent) i.parent.removeChild(i, "ignore");
            }
          } else if (nodes.type) {
            nodes = [nodes];
          } else if (nodes.prop) {
            if (typeof nodes.value === "undefined") {
              throw new Error("Value field is missed in node creation");
            } else if (typeof nodes.value !== "string") {
              nodes.value = String(nodes.value);
            }
            nodes = [new Declaration(nodes)];
          } else if (nodes.selector || nodes.selectors) {
            nodes = [new Rule(nodes)];
          } else if (nodes.name) {
            nodes = [new AtRule(nodes)];
          } else if (nodes.text) {
            nodes = [new Comment(nodes)];
          } else {
            throw new Error("Unknown node type in node creation");
          }
          let processed = nodes.map((i) => {
            if (!i[my]) _Container.rebuild(i);
            i = i.proxyOf;
            if (i.parent) i.parent.removeChild(i);
            if (i[isClean]) markTreeDirty(i);
            if (!i.raws) i.raws = {};
            if (typeof i.raws.before === "undefined") {
              if (sample && typeof sample.raws.before !== "undefined") {
                i.raws.before = sample.raws.before.replace(/\S/g, "");
              }
            }
            i.parent = this.proxyOf;
            return i;
          });
          return processed;
        }
        prepend(...children) {
          children = children.reverse();
          for (let child of children) {
            let nodes = this.normalize(child, this.first, "prepend").reverse();
            for (let node of nodes) this.proxyOf.nodes.unshift(node);
            for (let id in this.indexes) {
              this.indexes[id] = this.indexes[id] + nodes.length;
            }
          }
          this.markDirty();
          return this;
        }
        push(child) {
          child.parent = this;
          this.proxyOf.nodes.push(child);
          return this;
        }
        removeAll() {
          for (let node of this.proxyOf.nodes) node.parent = void 0;
          this.proxyOf.nodes = [];
          this.markDirty();
          return this;
        }
        removeChild(child) {
          child = this.index(child);
          this.proxyOf.nodes[child].parent = void 0;
          this.proxyOf.nodes.splice(child, 1);
          let index;
          for (let id in this.indexes) {
            index = this.indexes[id];
            if (index >= child) {
              this.indexes[id] = index - 1;
            }
          }
          this.markDirty();
          return this;
        }
        replaceValues(pattern, opts, callback) {
          if (!callback) {
            callback = opts;
            opts = {};
          }
          this.walkDecls((decl) => {
            if (opts.props && !opts.props.includes(decl.prop)) return;
            if (opts.fast && !decl.value.includes(opts.fast)) return;
            decl.value = decl.value.replace(pattern, callback);
          });
          this.markDirty();
          return this;
        }
        some(condition) {
          return this.nodes.some(condition);
        }
        walk(callback) {
          return this.each((child, i) => {
            let result;
            try {
              result = callback(child, i);
            } catch (e) {
              throw child.addToError(e);
            }
            if (result !== false && child.walk) {
              result = child.walk(callback);
            }
            return result;
          });
        }
        walkAtRules(name, callback) {
          if (!callback) {
            callback = name;
            return this.walk((child, i) => {
              if (child.type === "atrule") {
                return callback(child, i);
              }
            });
          }
          if (name instanceof RegExp) {
            return this.walk((child, i) => {
              if (child.type === "atrule" && name.test(child.name)) {
                return callback(child, i);
              }
            });
          }
          return this.walk((child, i) => {
            if (child.type === "atrule" && child.name === name) {
              return callback(child, i);
            }
          });
        }
        walkComments(callback) {
          return this.walk((child, i) => {
            if (child.type === "comment") {
              return callback(child, i);
            }
          });
        }
        walkDecls(prop, callback) {
          if (!callback) {
            callback = prop;
            return this.walk((child, i) => {
              if (child.type === "decl") {
                return callback(child, i);
              }
            });
          }
          if (prop instanceof RegExp) {
            return this.walk((child, i) => {
              if (child.type === "decl" && prop.test(child.prop)) {
                return callback(child, i);
              }
            });
          }
          return this.walk((child, i) => {
            if (child.type === "decl" && child.prop === prop) {
              return callback(child, i);
            }
          });
        }
        walkRules(selector, callback) {
          if (!callback) {
            callback = selector;
            return this.walk((child, i) => {
              if (child.type === "rule") {
                return callback(child, i);
              }
            });
          }
          if (selector instanceof RegExp) {
            return this.walk((child, i) => {
              if (child.type === "rule" && selector.test(child.selector)) {
                return callback(child, i);
              }
            });
          }
          return this.walk((child, i) => {
            if (child.type === "rule" && child.selector === selector) {
              return callback(child, i);
            }
          });
        }
        get first() {
          if (!this.proxyOf.nodes) return void 0;
          return this.proxyOf.nodes[0];
        }
        get last() {
          if (!this.proxyOf.nodes) return void 0;
          return this.proxyOf.nodes[this.proxyOf.nodes.length - 1];
        }
      };
      Container.registerParse = (dependant) => {
        parse = dependant;
      };
      Container.registerRule = (dependant) => {
        Rule = dependant;
      };
      Container.registerAtRule = (dependant) => {
        AtRule = dependant;
      };
      Container.registerRoot = (dependant) => {
        Root = dependant;
      };
      module.exports = Container;
      Container.default = Container;
      Container.rebuild = (node) => {
        if (node.type === "atrule") {
          Object.setPrototypeOf(node, AtRule.prototype);
        } else if (node.type === "rule") {
          Object.setPrototypeOf(node, Rule.prototype);
        } else if (node.type === "decl") {
          Object.setPrototypeOf(node, Declaration.prototype);
        } else if (node.type === "comment") {
          Object.setPrototypeOf(node, Comment.prototype);
        } else if (node.type === "root") {
          Object.setPrototypeOf(node, Root.prototype);
        }
        node[my] = true;
        if (node.nodes) {
          node.nodes.forEach((child) => {
            Container.rebuild(child);
          });
        }
      };
    }
  });

  // postcss/lib/at-rule.js
  var require_at_rule = __commonJS({
    "postcss/lib/at-rule.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var AtRule = class extends Container {
        constructor(defaults) {
          super(defaults);
          this.type = "atrule";
        }
        append(...children) {
          if (!this.proxyOf.nodes) this.nodes = [];
          return super.append(...children);
        }
        prepend(...children) {
          if (!this.proxyOf.nodes) this.nodes = [];
          return super.prepend(...children);
        }
      };
      module.exports = AtRule;
      AtRule.default = AtRule;
      Container.registerAtRule(AtRule);
    }
  });

  // postcss/lib/document.js
  var require_document = __commonJS({
    "postcss/lib/document.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var LazyResult;
      var Processor;
      var Document = class extends Container {
        constructor(defaults) {
          super({ type: "document", ...defaults });
          if (!this.nodes) {
            this.nodes = [];
          }
        }
        toResult(opts = {}) {
          let lazy = new LazyResult(new Processor(), this, opts);
          return lazy.stringify();
        }
      };
      Document.registerLazyResult = (dependant) => {
        LazyResult = dependant;
      };
      Document.registerProcessor = (dependant) => {
        Processor = dependant;
      };
      module.exports = Document;
      Document.default = Document;
    }
  });

  // nanoid/non-secure/index.cjs
  var require_non_secure = __commonJS({
    "nanoid/non-secure/index.cjs"(exports, module) {
      var urlAlphabet = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict";
      var customAlphabet = (alphabet, defaultSize = 21) => {
        return (size = defaultSize) => {
          let id = "";
          let i = size | 0;
          while (i-- > 0) {
            id += alphabet[Math.random() * alphabet.length | 0];
          }
          return id;
        };
      };
      var nanoid = (size = 21) => {
        let id = "";
        let i = size | 0;
        while (i-- > 0) {
          id += urlAlphabet[Math.random() * 64 | 0];
        }
        return id;
      };
      module.exports = { nanoid, customAlphabet };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-input-path.js
  var require_postcss_input_path = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-input-path.js"(exports, module) {
      "use strict";
      function unsupported(operation, value) {
        throw new Error(`PostCSS input.js \u7981\u6B62\u8DEF\u5F84\u64CD\u4F5C ${operation}\uFF1A${String(value)}`);
      }
      module.exports = {
        isAbsolute(value) {
          return unsupported("isAbsolute", value);
        },
        resolve(value) {
          return unsupported("resolve", value);
        }
      };
    }
  });

  // (disabled):source-map-js/source-map.js
  var require_source_map = __commonJS({
    "(disabled):source-map-js/source-map.js"() {
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-input-url.js
  var require_postcss_input_url = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-input-url.js"(exports, module) {
      "use strict";
      function unsupported(operation, value) {
        throw new Error(`PostCSS input.js \u7981\u6B62 URL \u64CD\u4F5C ${operation}\uFF1A${String(value)}`);
      }
      module.exports = {
        fileURLToPath(value) {
          return unsupported("fileURLToPath", value);
        },
        pathToFileURL(value) {
          return unsupported("pathToFileURL", value);
        }
      };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-previous-fs.js
  var require_postcss_previous_fs = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-previous-fs.js"(exports, module) {
      "use strict";
      function existsSync(file) {
        throw new Error(`PostCSS previous-map.js \u7981\u6B62 existsSync\uFF1A${String(file)}`);
      }
      function readFileSync(file, encoding) {
        throw new Error(`PostCSS previous-map.js \u7981\u6B62 readFileSync\uFF1A${String(file)} (${String(encoding)})`);
      }
      module.exports = { existsSync, readFileSync };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-previous-path.js
  var require_postcss_previous_path = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-previous-path.js"(exports, module) {
      "use strict";
      function unsupported(operation, value) {
        throw new Error(`PostCSS previous-map.js \u7981\u6B62\u8DEF\u5F84\u64CD\u4F5C ${operation}\uFF1A${String(value)}`);
      }
      module.exports = {
        dirname(value) {
          return unsupported("dirname", value);
        },
        join(...values) {
          return unsupported("join", values.join("/"));
        }
      };
    }
  });

  // postcss/lib/previous-map.js
  var require_previous_map = __commonJS({
    "postcss/lib/previous-map.js"(exports, module) {
      "use strict";
      var { existsSync, readFileSync } = require_postcss_previous_fs();
      var { dirname, join } = require_postcss_previous_path();
      var { SourceMapConsumer, SourceMapGenerator } = require_source_map();
      function fromBase64(str) {
        if (Buffer) {
          return Buffer.from(str, "base64").toString();
        } else {
          return window.atob(str);
        }
      }
      var PreviousMap = class {
        constructor(css, opts) {
          if (opts.map === false) return;
          this.loadAnnotation(css);
          this.inline = this.startWith(this.annotation, "data:");
          let prev = opts.map ? opts.map.prev : void 0;
          let text = this.loadMap(opts.from, prev);
          if (!this.mapFile && opts.from) {
            this.mapFile = opts.from;
          }
          if (this.mapFile) this.root = dirname(this.mapFile);
          if (text) this.text = text;
        }
        consumer() {
          if (!this.consumerCache) {
            this.consumerCache = new SourceMapConsumer(this.text);
          }
          return this.consumerCache;
        }
        decodeInline(text) {
          let baseCharsetUri = /^data:application\/json;charset=utf-?8;base64,/;
          let baseUri = /^data:application\/json;base64,/;
          let charsetUri = /^data:application\/json;charset=utf-?8,/;
          let uri = /^data:application\/json,/;
          let uriMatch = text.match(charsetUri) || text.match(uri);
          if (uriMatch) {
            return decodeURIComponent(text.substr(uriMatch[0].length));
          }
          let baseUriMatch = text.match(baseCharsetUri) || text.match(baseUri);
          if (baseUriMatch) {
            return fromBase64(text.substr(baseUriMatch[0].length));
          }
          let encoding = text.match(/data:application\/json;([^,]+),/)[1];
          throw new Error("Unsupported source map encoding " + encoding);
        }
        getAnnotationURL(sourceMapString) {
          return sourceMapString.replace(/^\/\*\s*# sourceMappingURL=/, "").trim();
        }
        isMap(map) {
          if (typeof map !== "object") return false;
          return typeof map.mappings === "string" || typeof map._mappings === "string" || Array.isArray(map.sections);
        }
        loadAnnotation(css) {
          let comments = css.match(/\/\*\s*# sourceMappingURL=/g);
          if (!comments) return;
          let start = css.lastIndexOf(comments.pop());
          let end = css.indexOf("*/", start);
          if (start > -1 && end > -1) {
            this.annotation = this.getAnnotationURL(css.substring(start, end));
          }
        }
        loadFile(path) {
          this.root = dirname(path);
          if (existsSync(path)) {
            this.mapFile = path;
            return readFileSync(path, "utf-8").toString().trim();
          }
        }
        loadMap(file, prev) {
          if (prev === false) return false;
          if (prev) {
            if (typeof prev === "string") {
              return prev;
            } else if (typeof prev === "function") {
              let prevPath = prev(file);
              if (prevPath) {
                let map = this.loadFile(prevPath);
                if (!map) {
                  throw new Error(
                    "Unable to load previous source map: " + prevPath.toString()
                  );
                }
                return map;
              }
            } else if (prev instanceof SourceMapConsumer) {
              return SourceMapGenerator.fromSourceMap(prev).toString();
            } else if (prev instanceof SourceMapGenerator) {
              return prev.toString();
            } else if (this.isMap(prev)) {
              return JSON.stringify(prev);
            } else {
              throw new Error(
                "Unsupported previous source map format: " + prev.toString()
              );
            }
          } else if (this.inline) {
            return this.decodeInline(this.annotation);
          } else if (this.annotation) {
            let map = this.annotation;
            if (file) map = join(dirname(file), map);
            return this.loadFile(map);
          }
        }
        startWith(string, start) {
          if (!string) return false;
          return string.substr(0, start.length) === start;
        }
        withContent() {
          return !!(this.consumer().sourcesContent && this.consumer().sourcesContent.length > 0);
        }
      };
      module.exports = PreviousMap;
      PreviousMap.default = PreviousMap;
    }
  });

  // postcss/lib/input.js
  var require_input = __commonJS({
    "postcss/lib/input.js"(exports, module) {
      "use strict";
      var { nanoid } = require_non_secure();
      var { isAbsolute, resolve } = require_postcss_input_path();
      var { SourceMapConsumer, SourceMapGenerator } = require_source_map();
      var { fileURLToPath, pathToFileURL } = require_postcss_input_url();
      var CssSyntaxError = require_css_syntax_error();
      var PreviousMap = require_previous_map();
      var terminalHighlight = require_terminal_highlight();
      var fromOffsetCache = Symbol("fromOffsetCache");
      var sourceMapAvailable = Boolean(SourceMapConsumer && SourceMapGenerator);
      var pathAvailable = Boolean(resolve && isAbsolute);
      var Input = class {
        constructor(css, opts = {}) {
          if (css === null || typeof css === "undefined" || typeof css === "object" && !css.toString) {
            throw new Error(`PostCSS received ${css} instead of CSS string`);
          }
          this.css = css.toString();
          if (this.css[0] === "\uFEFF" || this.css[0] === "\uFFFE") {
            this.hasBOM = true;
            this.css = this.css.slice(1);
          } else {
            this.hasBOM = false;
          }
          if (opts.from) {
            if (!pathAvailable || /^\w+:\/\//.test(opts.from) || isAbsolute(opts.from)) {
              this.file = opts.from;
            } else {
              this.file = resolve(opts.from);
            }
          }
          if (pathAvailable && sourceMapAvailable) {
            let map = new PreviousMap(this.css, opts);
            if (map.text) {
              this.map = map;
              let file = map.consumer().file;
              if (!this.file && file) this.file = this.mapResolve(file);
            }
          }
          if (!this.file) {
            this.id = "<input css " + nanoid(6) + ">";
          }
          if (this.map) this.map.file = this.from;
        }
        error(message, line, column, opts = {}) {
          let endColumn, endLine, result;
          if (line && typeof line === "object") {
            let start = line;
            let end = column;
            if (typeof start.offset === "number") {
              let pos = this.fromOffset(start.offset);
              line = pos.line;
              column = pos.col;
            } else {
              line = start.line;
              column = start.column;
            }
            if (typeof end.offset === "number") {
              let pos = this.fromOffset(end.offset);
              endLine = pos.line;
              endColumn = pos.col;
            } else {
              endLine = end.line;
              endColumn = end.column;
            }
          } else if (!column) {
            let pos = this.fromOffset(line);
            line = pos.line;
            column = pos.col;
          }
          let origin = this.origin(line, column, endLine, endColumn);
          if (origin) {
            result = new CssSyntaxError(
              message,
              origin.endLine === void 0 ? origin.line : { column: origin.column, line: origin.line },
              origin.endLine === void 0 ? origin.column : { column: origin.endColumn, line: origin.endLine },
              origin.source,
              origin.file,
              opts.plugin
            );
          } else {
            result = new CssSyntaxError(
              message,
              endLine === void 0 ? line : { column, line },
              endLine === void 0 ? column : { column: endColumn, line: endLine },
              this.css,
              this.file,
              opts.plugin
            );
          }
          result.input = { column, endColumn, endLine, line, source: this.css };
          if (this.file) {
            if (pathToFileURL) {
              result.input.url = pathToFileURL(this.file).toString();
            }
            result.input.file = this.file;
          }
          return result;
        }
        fromOffset(offset) {
          let lastLine, lineToIndex;
          if (!this[fromOffsetCache]) {
            let lines = this.css.split("\n");
            lineToIndex = new Array(lines.length);
            let prevIndex = 0;
            for (let i = 0, l = lines.length; i < l; i++) {
              lineToIndex[i] = prevIndex;
              prevIndex += lines[i].length + 1;
            }
            this[fromOffsetCache] = lineToIndex;
          } else {
            lineToIndex = this[fromOffsetCache];
          }
          lastLine = lineToIndex[lineToIndex.length - 1];
          let min = 0;
          if (offset >= lastLine) {
            min = lineToIndex.length - 1;
          } else {
            let max = lineToIndex.length - 2;
            let mid;
            while (min < max) {
              mid = min + (max - min >> 1);
              if (offset < lineToIndex[mid]) {
                max = mid - 1;
              } else if (offset >= lineToIndex[mid + 1]) {
                min = mid + 1;
              } else {
                min = mid;
                break;
              }
            }
          }
          return {
            col: offset - lineToIndex[min] + 1,
            line: min + 1
          };
        }
        mapResolve(file) {
          if (/^\w+:\/\//.test(file)) {
            return file;
          }
          return resolve(this.map.consumer().sourceRoot || this.map.root || ".", file);
        }
        origin(line, column, endLine, endColumn) {
          if (!this.map) return false;
          let consumer = this.map.consumer();
          let from = consumer.originalPositionFor({ column, line });
          if (!from.source) return false;
          let to;
          if (typeof endLine === "number") {
            to = consumer.originalPositionFor({ column: endColumn, line: endLine });
          }
          let fromUrl;
          if (isAbsolute(from.source)) {
            fromUrl = pathToFileURL(from.source);
          } else {
            fromUrl = new URL(
              from.source,
              this.map.consumer().sourceRoot || pathToFileURL(this.map.mapFile)
            );
          }
          let result = {
            column: from.column,
            endColumn: to && to.column,
            endLine: to && to.line,
            line: from.line,
            url: fromUrl.toString()
          };
          if (fromUrl.protocol === "file:") {
            if (fileURLToPath) {
              result.file = fileURLToPath(fromUrl);
            } else {
              throw new Error(`file: protocol is not available in this PostCSS build`);
            }
          }
          let source = consumer.sourceContentFor(from.source);
          if (source) result.source = source;
          return result;
        }
        toJSON() {
          let json = {};
          for (let name of ["hasBOM", "css", "file", "id"]) {
            if (this[name] != null) {
              json[name] = this[name];
            }
          }
          if (this.map) {
            json.map = { ...this.map };
            if (json.map.consumerCache) {
              json.map.consumerCache = void 0;
            }
          }
          return json;
        }
        get from() {
          return this.file || this.id;
        }
      };
      module.exports = Input;
      Input.default = Input;
      if (terminalHighlight && terminalHighlight.registerInput) {
        terminalHighlight.registerInput(Input);
      }
    }
  });

  // postcss/lib/root.js
  var require_root = __commonJS({
    "postcss/lib/root.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var LazyResult;
      var Processor;
      var Root = class extends Container {
        constructor(defaults) {
          super(defaults);
          this.type = "root";
          if (!this.nodes) this.nodes = [];
        }
        normalize(child, sample, type) {
          let nodes = super.normalize(child);
          if (sample) {
            if (type === "prepend") {
              if (this.nodes.length > 1) {
                sample.raws.before = this.nodes[1].raws.before;
              } else {
                delete sample.raws.before;
              }
            } else if (this.first !== sample) {
              for (let node of nodes) {
                node.raws.before = sample.raws.before;
              }
            }
          }
          return nodes;
        }
        removeChild(child, ignore) {
          let index = this.index(child);
          if (!ignore && index === 0 && this.nodes.length > 1) {
            this.nodes[1].raws.before = this.nodes[index].raws.before;
          }
          return super.removeChild(child);
        }
        toResult(opts = {}) {
          let lazy = new LazyResult(new Processor(), this, opts);
          return lazy.stringify();
        }
      };
      Root.registerLazyResult = (dependant) => {
        LazyResult = dependant;
      };
      Root.registerProcessor = (dependant) => {
        Processor = dependant;
      };
      module.exports = Root;
      Root.default = Root;
      Container.registerRoot(Root);
    }
  });

  // postcss/lib/list.js
  var require_list = __commonJS({
    "postcss/lib/list.js"(exports, module) {
      "use strict";
      var list = {
        comma(string) {
          return list.split(string, [","], true);
        },
        space(string) {
          let spaces = [" ", "\n", "	"];
          return list.split(string, spaces);
        },
        split(string, separators, last) {
          let array = [];
          let current = "";
          let split = false;
          let func = 0;
          let inQuote = false;
          let prevQuote = "";
          let escape = false;
          for (let letter of string) {
            if (escape) {
              escape = false;
            } else if (letter === "\\") {
              escape = true;
            } else if (inQuote) {
              if (letter === prevQuote) {
                inQuote = false;
              }
            } else if (letter === '"' || letter === "'") {
              inQuote = true;
              prevQuote = letter;
            } else if (letter === "(") {
              func += 1;
            } else if (letter === ")") {
              if (func > 0) func -= 1;
            } else if (func === 0) {
              if (separators.includes(letter)) split = true;
            }
            if (split) {
              if (current !== "") array.push(current.trim());
              current = "";
              split = false;
            } else {
              current += letter;
            }
          }
          if (last || current !== "") array.push(current.trim());
          return array;
        }
      };
      module.exports = list;
      list.default = list;
    }
  });

  // postcss/lib/rule.js
  var require_rule = __commonJS({
    "postcss/lib/rule.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var list = require_list();
      var Rule = class extends Container {
        constructor(defaults) {
          super(defaults);
          this.type = "rule";
          if (!this.nodes) this.nodes = [];
        }
        get selectors() {
          return list.comma(this.selector);
        }
        set selectors(values) {
          let match = this.selector ? this.selector.match(/,\s*/) : null;
          let sep = match ? match[0] : "," + this.raw("between", "beforeOpen");
          this.selector = values.join(sep);
        }
      };
      module.exports = Rule;
      Rule.default = Rule;
      Container.registerRule(Rule);
    }
  });

  // postcss/lib/fromJSON.js
  var require_fromJSON = __commonJS({
    "postcss/lib/fromJSON.js"(exports, module) {
      "use strict";
      var AtRule = require_at_rule();
      var Comment = require_comment();
      var Declaration = require_declaration();
      var Input = require_input();
      var PreviousMap = require_previous_map();
      var Root = require_root();
      var Rule = require_rule();
      function fromJSON(json, inputs) {
        if (Array.isArray(json)) return json.map((n) => fromJSON(n));
        let { inputs: ownInputs, ...defaults } = json;
        if (ownInputs) {
          inputs = [];
          for (let input of ownInputs) {
            let inputHydrated = { ...input, __proto__: Input.prototype };
            if (inputHydrated.map) {
              inputHydrated.map = {
                ...inputHydrated.map,
                __proto__: PreviousMap.prototype
              };
            }
            inputs.push(inputHydrated);
          }
        }
        if (defaults.nodes) {
          defaults.nodes = json.nodes.map((n) => fromJSON(n, inputs));
        }
        if (defaults.source) {
          let { inputId, ...source } = defaults.source;
          defaults.source = source;
          if (inputId != null) {
            defaults.source.input = inputs[inputId];
          }
        }
        if (defaults.type === "root") {
          return new Root(defaults);
        } else if (defaults.type === "decl") {
          return new Declaration(defaults);
        } else if (defaults.type === "rule") {
          return new Rule(defaults);
        } else if (defaults.type === "comment") {
          return new Comment(defaults);
        } else if (defaults.type === "atrule") {
          return new AtRule(defaults);
        } else {
          throw new Error("Unknown node type: " + json.type);
        }
      }
      module.exports = fromJSON;
      fromJSON.default = fromJSON;
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-map-path.js
  var require_postcss_map_path = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-map-path.js"(exports, module) {
      "use strict";
      function unsupported(operation, value) {
        throw new Error(`PostCSS map-generator.js \u7981\u6B62\u8DEF\u5F84\u64CD\u4F5C ${operation}\uFF1A${String(value)}`);
      }
      module.exports = {
        dirname(value) {
          return unsupported("dirname", value);
        },
        relative(from, to) {
          return unsupported("relative", `${String(from)} -> ${String(to)}`);
        },
        resolve(value) {
          return unsupported("resolve", value);
        },
        sep: "/"
      };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/postcss-map-url.js
  var require_postcss_map_url = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/postcss-map-url.js"(exports, module) {
      "use strict";
      function pathToFileURL(value) {
        throw new Error(`PostCSS map-generator.js \u7981\u6B62 file URL \u64CD\u4F5C\uFF1A${String(value)}`);
      }
      module.exports = { pathToFileURL };
    }
  });

  // postcss/lib/map-generator.js
  var require_map_generator = __commonJS({
    "postcss/lib/map-generator.js"(exports, module) {
      "use strict";
      var { dirname, relative, resolve, sep } = require_postcss_map_path();
      var { SourceMapConsumer, SourceMapGenerator } = require_source_map();
      var { pathToFileURL } = require_postcss_map_url();
      var Input = require_input();
      var sourceMapAvailable = Boolean(SourceMapConsumer && SourceMapGenerator);
      var pathAvailable = Boolean(dirname && resolve && relative && sep);
      var MapGenerator = class {
        constructor(stringify, root, opts, cssString) {
          this.stringify = stringify;
          this.mapOpts = opts.map || {};
          this.root = root;
          this.opts = opts;
          this.css = cssString;
          this.originalCSS = cssString;
          this.usesFileUrls = !this.mapOpts.from && this.mapOpts.absolute;
          this.memoizedFileURLs = /* @__PURE__ */ new Map();
          this.memoizedPaths = /* @__PURE__ */ new Map();
          this.memoizedURLs = /* @__PURE__ */ new Map();
        }
        addAnnotation() {
          let content;
          if (this.isInline()) {
            content = "data:application/json;base64," + this.toBase64(this.map.toString());
          } else if (typeof this.mapOpts.annotation === "string") {
            content = this.mapOpts.annotation;
          } else if (typeof this.mapOpts.annotation === "function") {
            content = this.mapOpts.annotation(this.opts.to, this.root);
          } else {
            content = this.outputFile() + ".map";
          }
          let eol = "\n";
          if (this.css.includes("\r\n")) eol = "\r\n";
          this.css += eol + "/*# sourceMappingURL=" + content + " */";
        }
        applyPrevMaps() {
          for (let prev of this.previous()) {
            let from = this.toUrl(this.path(prev.file));
            let root = prev.root || dirname(prev.file);
            let map;
            if (this.mapOpts.sourcesContent === false) {
              map = new SourceMapConsumer(prev.text);
              if (map.sourcesContent) {
                map.sourcesContent = null;
              }
            } else {
              map = prev.consumer();
            }
            this.map.applySourceMap(map, from, this.toUrl(this.path(root)));
          }
        }
        clearAnnotation() {
          if (this.mapOpts.annotation === false) return;
          if (this.root) {
            let node;
            for (let i = this.root.nodes.length - 1; i >= 0; i--) {
              node = this.root.nodes[i];
              if (node.type !== "comment") continue;
              if (node.text.startsWith("# sourceMappingURL=")) {
                this.root.removeChild(i);
              }
            }
          } else if (this.css) {
            this.css = this.css.replace(/\n*\/\*#[\S\s]*?\*\/$/gm, "");
          }
        }
        generate() {
          this.clearAnnotation();
          if (pathAvailable && sourceMapAvailable && this.isMap()) {
            return this.generateMap();
          } else {
            let result = "";
            this.stringify(this.root, (i) => {
              result += i;
            });
            return [result];
          }
        }
        generateMap() {
          if (this.root) {
            this.generateString();
          } else if (this.previous().length === 1) {
            let prev = this.previous()[0].consumer();
            prev.file = this.outputFile();
            this.map = SourceMapGenerator.fromSourceMap(prev, {
              ignoreInvalidMapping: true
            });
          } else {
            this.map = new SourceMapGenerator({
              file: this.outputFile(),
              ignoreInvalidMapping: true
            });
            this.map.addMapping({
              generated: { column: 0, line: 1 },
              original: { column: 0, line: 1 },
              source: this.opts.from ? this.toUrl(this.path(this.opts.from)) : "<no source>"
            });
          }
          if (this.isSourcesContent()) this.setSourcesContent();
          if (this.root && this.previous().length > 0) this.applyPrevMaps();
          if (this.isAnnotation()) this.addAnnotation();
          if (this.isInline()) {
            return [this.css];
          } else {
            return [this.css, this.map];
          }
        }
        generateString() {
          this.css = "";
          this.map = new SourceMapGenerator({
            file: this.outputFile(),
            ignoreInvalidMapping: true
          });
          let line = 1;
          let column = 1;
          let noSource = "<no source>";
          let mapping = {
            generated: { column: 0, line: 0 },
            original: { column: 0, line: 0 },
            source: ""
          };
          let last, lines;
          this.stringify(this.root, (str, node, type) => {
            this.css += str;
            if (node && type !== "end") {
              mapping.generated.line = line;
              mapping.generated.column = column - 1;
              if (node.source && node.source.start) {
                mapping.source = this.sourcePath(node);
                mapping.original.line = node.source.start.line;
                mapping.original.column = node.source.start.column - 1;
                this.map.addMapping(mapping);
              } else {
                mapping.source = noSource;
                mapping.original.line = 1;
                mapping.original.column = 0;
                this.map.addMapping(mapping);
              }
            }
            lines = str.match(/\n/g);
            if (lines) {
              line += lines.length;
              last = str.lastIndexOf("\n");
              column = str.length - last;
            } else {
              column += str.length;
            }
            if (node && type !== "start") {
              let p = node.parent || { raws: {} };
              let childless = node.type === "decl" || node.type === "atrule" && !node.nodes;
              if (!childless || node !== p.last || p.raws.semicolon) {
                if (node.source && node.source.end) {
                  mapping.source = this.sourcePath(node);
                  mapping.original.line = node.source.end.line;
                  mapping.original.column = node.source.end.column - 1;
                  mapping.generated.line = line;
                  mapping.generated.column = column - 2;
                  this.map.addMapping(mapping);
                } else {
                  mapping.source = noSource;
                  mapping.original.line = 1;
                  mapping.original.column = 0;
                  mapping.generated.line = line;
                  mapping.generated.column = column - 1;
                  this.map.addMapping(mapping);
                }
              }
            }
          });
        }
        isAnnotation() {
          if (this.isInline()) {
            return true;
          }
          if (typeof this.mapOpts.annotation !== "undefined") {
            return this.mapOpts.annotation;
          }
          if (this.previous().length) {
            return this.previous().some((i) => i.annotation);
          }
          return true;
        }
        isInline() {
          if (typeof this.mapOpts.inline !== "undefined") {
            return this.mapOpts.inline;
          }
          let annotation = this.mapOpts.annotation;
          if (typeof annotation !== "undefined" && annotation !== true) {
            return false;
          }
          if (this.previous().length) {
            return this.previous().some((i) => i.inline);
          }
          return true;
        }
        isMap() {
          if (typeof this.opts.map !== "undefined") {
            return !!this.opts.map;
          }
          return this.previous().length > 0;
        }
        isSourcesContent() {
          if (typeof this.mapOpts.sourcesContent !== "undefined") {
            return this.mapOpts.sourcesContent;
          }
          if (this.previous().length) {
            return this.previous().some((i) => i.withContent());
          }
          return true;
        }
        outputFile() {
          if (this.opts.to) {
            return this.path(this.opts.to);
          } else if (this.opts.from) {
            return this.path(this.opts.from);
          } else {
            return "to.css";
          }
        }
        path(file) {
          if (this.mapOpts.absolute) return file;
          if (file.charCodeAt(0) === 60) return file;
          if (/^\w+:\/\//.test(file)) return file;
          let cached = this.memoizedPaths.get(file);
          if (cached) return cached;
          let from = this.opts.to ? dirname(this.opts.to) : ".";
          if (typeof this.mapOpts.annotation === "string") {
            from = dirname(resolve(from, this.mapOpts.annotation));
          }
          let path = relative(from, file);
          this.memoizedPaths.set(file, path);
          return path;
        }
        previous() {
          if (!this.previousMaps) {
            this.previousMaps = [];
            if (this.root) {
              this.root.walk((node) => {
                if (node.source && node.source.input.map) {
                  let map = node.source.input.map;
                  if (!this.previousMaps.includes(map)) {
                    this.previousMaps.push(map);
                  }
                }
              });
            } else {
              let input = new Input(this.originalCSS, this.opts);
              if (input.map) this.previousMaps.push(input.map);
            }
          }
          return this.previousMaps;
        }
        setSourcesContent() {
          let already = {};
          if (this.root) {
            this.root.walk((node) => {
              if (node.source) {
                let from = node.source.input.from;
                if (from && !already[from]) {
                  already[from] = true;
                  let fromUrl = this.usesFileUrls ? this.toFileUrl(from) : this.toUrl(this.path(from));
                  this.map.setSourceContent(fromUrl, node.source.input.css);
                }
              }
            });
          } else if (this.css) {
            let from = this.opts.from ? this.toUrl(this.path(this.opts.from)) : "<no source>";
            this.map.setSourceContent(from, this.css);
          }
        }
        sourcePath(node) {
          if (this.mapOpts.from) {
            return this.toUrl(this.mapOpts.from);
          } else if (this.usesFileUrls) {
            return this.toFileUrl(node.source.input.from);
          } else {
            return this.toUrl(this.path(node.source.input.from));
          }
        }
        toBase64(str) {
          if (Buffer) {
            return Buffer.from(str).toString("base64");
          } else {
            return window.btoa(unescape(encodeURIComponent(str)));
          }
        }
        toFileUrl(path) {
          let cached = this.memoizedFileURLs.get(path);
          if (cached) return cached;
          if (pathToFileURL) {
            let fileURL = pathToFileURL(path).toString();
            this.memoizedFileURLs.set(path, fileURL);
            return fileURL;
          } else {
            throw new Error(
              "`map.absolute` option is not available in this PostCSS build"
            );
          }
        }
        toUrl(path) {
          let cached = this.memoizedURLs.get(path);
          if (cached) return cached;
          if (sep === "\\") {
            path = path.replace(/\\/g, "/");
          }
          let url = encodeURI(path).replace(/[#?]/g, encodeURIComponent);
          this.memoizedURLs.set(path, url);
          return url;
        }
      };
      module.exports = MapGenerator;
    }
  });

  // postcss/lib/tokenize.js
  var require_tokenize = __commonJS({
    "postcss/lib/tokenize.js"(exports, module) {
      "use strict";
      var SINGLE_QUOTE = "'".charCodeAt(0);
      var DOUBLE_QUOTE = '"'.charCodeAt(0);
      var BACKSLASH = "\\".charCodeAt(0);
      var SLASH = "/".charCodeAt(0);
      var NEWLINE = "\n".charCodeAt(0);
      var SPACE = " ".charCodeAt(0);
      var FEED = "\f".charCodeAt(0);
      var TAB = "	".charCodeAt(0);
      var CR = "\r".charCodeAt(0);
      var OPEN_SQUARE = "[".charCodeAt(0);
      var CLOSE_SQUARE = "]".charCodeAt(0);
      var OPEN_PARENTHESES = "(".charCodeAt(0);
      var CLOSE_PARENTHESES = ")".charCodeAt(0);
      var OPEN_CURLY = "{".charCodeAt(0);
      var CLOSE_CURLY = "}".charCodeAt(0);
      var SEMICOLON = ";".charCodeAt(0);
      var ASTERISK = "*".charCodeAt(0);
      var COLON = ":".charCodeAt(0);
      var AT = "@".charCodeAt(0);
      var RE_AT_END = /[\t\n\f\r "#'()/;[\\\]{}]/g;
      var RE_WORD_END = /[\t\n\f\r !"#'():;@[\\\]{}]|\/(?=\*)/g;
      var RE_BAD_BRACKET = /.[\r\n"'(/\\]/;
      var RE_HEX_ESCAPE = /[\da-f]/i;
      module.exports = function tokenizer(input, options = {}) {
        let css = input.css.valueOf();
        let ignore = options.ignoreErrors;
        let code, content, escape, next, quote;
        let currentToken, escaped, escapePos, n, prev;
        let length = css.length;
        let pos = 0;
        let buffer = [];
        let returned = [];
        function position() {
          return pos;
        }
        function unclosed(what) {
          throw input.error("Unclosed " + what, pos);
        }
        function endOfFile() {
          return returned.length === 0 && pos >= length;
        }
        function nextToken(opts) {
          if (returned.length) return returned.pop();
          if (pos >= length) return;
          let ignoreUnclosed = opts ? opts.ignoreUnclosed : false;
          code = css.charCodeAt(pos);
          switch (code) {
            case NEWLINE:
            case SPACE:
            case TAB:
            case CR:
            case FEED: {
              next = pos;
              do {
                next += 1;
                code = css.charCodeAt(next);
              } while (code === SPACE || code === NEWLINE || code === TAB || code === CR || code === FEED);
              currentToken = ["space", css.slice(pos, next)];
              pos = next - 1;
              break;
            }
            case OPEN_SQUARE:
            case CLOSE_SQUARE:
            case OPEN_CURLY:
            case CLOSE_CURLY:
            case COLON:
            case SEMICOLON:
            case CLOSE_PARENTHESES: {
              let controlChar = String.fromCharCode(code);
              currentToken = [controlChar, controlChar, pos];
              break;
            }
            case OPEN_PARENTHESES: {
              prev = buffer.length ? buffer.pop()[1] : "";
              n = css.charCodeAt(pos + 1);
              if (prev === "url" && n !== SINGLE_QUOTE && n !== DOUBLE_QUOTE && n !== SPACE && n !== NEWLINE && n !== TAB && n !== FEED && n !== CR) {
                next = pos;
                do {
                  escaped = false;
                  next = css.indexOf(")", next + 1);
                  if (next === -1) {
                    if (ignore || ignoreUnclosed) {
                      next = pos;
                      break;
                    } else {
                      unclosed("bracket");
                    }
                  }
                  escapePos = next;
                  while (css.charCodeAt(escapePos - 1) === BACKSLASH) {
                    escapePos -= 1;
                    escaped = !escaped;
                  }
                } while (escaped);
                currentToken = ["brackets", css.slice(pos, next + 1), pos, next];
                pos = next;
              } else {
                next = css.indexOf(")", pos + 1);
                content = css.slice(pos, next + 1);
                if (next === -1 || RE_BAD_BRACKET.test(content)) {
                  currentToken = ["(", "(", pos];
                } else {
                  currentToken = ["brackets", content, pos, next];
                  pos = next;
                }
              }
              break;
            }
            case SINGLE_QUOTE:
            case DOUBLE_QUOTE: {
              quote = code === SINGLE_QUOTE ? "'" : '"';
              next = pos;
              do {
                escaped = false;
                next = css.indexOf(quote, next + 1);
                if (next === -1) {
                  if (ignore || ignoreUnclosed) {
                    next = pos + 1;
                    break;
                  } else {
                    unclosed("string");
                  }
                }
                escapePos = next;
                while (css.charCodeAt(escapePos - 1) === BACKSLASH) {
                  escapePos -= 1;
                  escaped = !escaped;
                }
              } while (escaped);
              currentToken = ["string", css.slice(pos, next + 1), pos, next];
              pos = next;
              break;
            }
            case AT: {
              RE_AT_END.lastIndex = pos + 1;
              RE_AT_END.test(css);
              if (RE_AT_END.lastIndex === 0) {
                next = css.length - 1;
              } else {
                next = RE_AT_END.lastIndex - 2;
              }
              currentToken = ["at-word", css.slice(pos, next + 1), pos, next];
              pos = next;
              break;
            }
            case BACKSLASH: {
              next = pos;
              escape = true;
              while (css.charCodeAt(next + 1) === BACKSLASH) {
                next += 1;
                escape = !escape;
              }
              code = css.charCodeAt(next + 1);
              if (escape && code !== SLASH && code !== SPACE && code !== NEWLINE && code !== TAB && code !== CR && code !== FEED) {
                next += 1;
                if (RE_HEX_ESCAPE.test(css.charAt(next))) {
                  while (RE_HEX_ESCAPE.test(css.charAt(next + 1))) {
                    next += 1;
                  }
                  if (css.charCodeAt(next + 1) === SPACE) {
                    next += 1;
                  }
                }
              }
              currentToken = ["word", css.slice(pos, next + 1), pos, next];
              pos = next;
              break;
            }
            default: {
              if (code === SLASH && css.charCodeAt(pos + 1) === ASTERISK) {
                next = css.indexOf("*/", pos + 2) + 1;
                if (next === 0) {
                  if (ignore || ignoreUnclosed) {
                    next = css.length;
                  } else {
                    unclosed("comment");
                  }
                }
                currentToken = ["comment", css.slice(pos, next + 1), pos, next];
                pos = next;
              } else {
                RE_WORD_END.lastIndex = pos + 1;
                RE_WORD_END.test(css);
                if (RE_WORD_END.lastIndex === 0) {
                  next = css.length - 1;
                } else {
                  next = RE_WORD_END.lastIndex - 2;
                }
                currentToken = ["word", css.slice(pos, next + 1), pos, next];
                buffer.push(currentToken);
                pos = next;
              }
              break;
            }
          }
          pos++;
          return currentToken;
        }
        function back(token) {
          returned.push(token);
        }
        return {
          back,
          endOfFile,
          nextToken,
          position
        };
      };
    }
  });

  // postcss/lib/parser.js
  var require_parser = __commonJS({
    "postcss/lib/parser.js"(exports, module) {
      "use strict";
      var AtRule = require_at_rule();
      var Comment = require_comment();
      var Declaration = require_declaration();
      var Root = require_root();
      var Rule = require_rule();
      var tokenizer = require_tokenize();
      var SAFE_COMMENT_NEIGHBOR = {
        empty: true,
        space: true
      };
      function findLastWithPosition(tokens) {
        for (let i = tokens.length - 1; i >= 0; i--) {
          let token = tokens[i];
          let pos = token[3] || token[2];
          if (pos) return pos;
        }
      }
      var Parser = class {
        constructor(input) {
          this.input = input;
          this.root = new Root();
          this.current = this.root;
          this.spaces = "";
          this.semicolon = false;
          this.createTokenizer();
          this.root.source = { input, start: { column: 1, line: 1, offset: 0 } };
        }
        atrule(token) {
          let node = new AtRule();
          node.name = token[1].slice(1);
          if (node.name === "") {
            this.unnamedAtrule(node, token);
          }
          this.init(node, token[2]);
          let type;
          let prev;
          let shift;
          let last = false;
          let open = false;
          let params = [];
          let brackets = [];
          while (!this.tokenizer.endOfFile()) {
            token = this.tokenizer.nextToken();
            type = token[0];
            if (type === "(" || type === "[") {
              brackets.push(type === "(" ? ")" : "]");
            } else if (type === "{" && brackets.length > 0) {
              brackets.push("}");
            } else if (type === brackets[brackets.length - 1]) {
              brackets.pop();
            }
            if (brackets.length === 0) {
              if (type === ";") {
                node.source.end = this.getPosition(token[2]);
                node.source.end.offset++;
                this.semicolon = true;
                break;
              } else if (type === "{") {
                open = true;
                break;
              } else if (type === "}") {
                if (params.length > 0) {
                  shift = params.length - 1;
                  prev = params[shift];
                  while (prev && prev[0] === "space") {
                    prev = params[--shift];
                  }
                  if (prev) {
                    node.source.end = this.getPosition(prev[3] || prev[2]);
                    node.source.end.offset++;
                  }
                }
                this.end(token);
                break;
              } else {
                params.push(token);
              }
            } else {
              params.push(token);
            }
            if (this.tokenizer.endOfFile()) {
              last = true;
              break;
            }
          }
          node.raws.between = this.spacesAndCommentsFromEnd(params);
          if (params.length) {
            node.raws.afterName = this.spacesAndCommentsFromStart(params);
            this.raw(node, "params", params);
            if (last) {
              token = params[params.length - 1];
              node.source.end = this.getPosition(token[3] || token[2]);
              node.source.end.offset++;
              this.spaces = node.raws.between;
              node.raws.between = "";
            }
          } else {
            node.raws.afterName = "";
            node.params = "";
          }
          if (open) {
            node.nodes = [];
            this.current = node;
          }
        }
        checkMissedSemicolon(tokens) {
          let colon = this.colon(tokens);
          if (colon === false) return;
          let founded = 0;
          let token;
          for (let j = colon - 1; j >= 0; j--) {
            token = tokens[j];
            if (token[0] !== "space") {
              founded += 1;
              if (founded === 2) break;
            }
          }
          throw this.input.error(
            "Missed semicolon",
            token[0] === "word" ? token[3] + 1 : token[2]
          );
        }
        colon(tokens) {
          let brackets = 0;
          let prev, token, type;
          for (let [i, element] of tokens.entries()) {
            token = element;
            type = token[0];
            if (type === "(") {
              brackets += 1;
            }
            if (type === ")") {
              brackets -= 1;
            }
            if (brackets === 0 && type === ":") {
              if (!prev) {
                this.doubleColon(token);
              } else if (prev[0] === "word" && prev[1] === "progid") {
                continue;
              } else {
                return i;
              }
            }
            prev = token;
          }
          return false;
        }
        comment(token) {
          let node = new Comment();
          this.init(node, token[2]);
          node.source.end = this.getPosition(token[3] || token[2]);
          node.source.end.offset++;
          let text = token[1].slice(2, -2);
          if (/^\s*$/.test(text)) {
            node.text = "";
            node.raws.left = text;
            node.raws.right = "";
          } else {
            let match = text.match(/^(\s*)([^]*\S)(\s*)$/);
            node.text = match[2];
            node.raws.left = match[1];
            node.raws.right = match[3];
          }
        }
        createTokenizer() {
          this.tokenizer = tokenizer(this.input);
        }
        decl(tokens, customProperty) {
          let node = new Declaration();
          this.init(node, tokens[0][2]);
          let last = tokens[tokens.length - 1];
          if (last[0] === ";") {
            this.semicolon = true;
            tokens.pop();
          }
          node.source.end = this.getPosition(
            last[3] || last[2] || findLastWithPosition(tokens)
          );
          node.source.end.offset++;
          while (tokens[0][0] !== "word") {
            if (tokens.length === 1) this.unknownWord(tokens);
            node.raws.before += tokens.shift()[1];
          }
          node.source.start = this.getPosition(tokens[0][2]);
          node.prop = "";
          while (tokens.length) {
            let type = tokens[0][0];
            if (type === ":" || type === "space" || type === "comment") {
              break;
            }
            node.prop += tokens.shift()[1];
          }
          node.raws.between = "";
          let token;
          while (tokens.length) {
            token = tokens.shift();
            if (token[0] === ":") {
              node.raws.between += token[1];
              break;
            } else {
              if (token[0] === "word" && /\w/.test(token[1])) {
                this.unknownWord([token]);
              }
              node.raws.between += token[1];
            }
          }
          if (node.prop[0] === "_" || node.prop[0] === "*") {
            node.raws.before += node.prop[0];
            node.prop = node.prop.slice(1);
          }
          let firstSpaces = [];
          let next;
          while (tokens.length) {
            next = tokens[0][0];
            if (next !== "space" && next !== "comment") break;
            firstSpaces.push(tokens.shift());
          }
          this.precheckMissedSemicolon(tokens);
          for (let i = tokens.length - 1; i >= 0; i--) {
            token = tokens[i];
            if (token[1].toLowerCase() === "!important") {
              node.important = true;
              let string = this.stringFrom(tokens, i);
              string = this.spacesFromEnd(tokens) + string;
              if (string !== " !important") node.raws.important = string;
              break;
            } else if (token[1].toLowerCase() === "important") {
              let cache = tokens.slice(0);
              let str = "";
              for (let j = i; j > 0; j--) {
                let type = cache[j][0];
                if (str.trim().startsWith("!") && type !== "space") {
                  break;
                }
                str = cache.pop()[1] + str;
              }
              if (str.trim().startsWith("!")) {
                node.important = true;
                node.raws.important = str;
                tokens = cache;
              }
            }
            if (token[0] !== "space" && token[0] !== "comment") {
              break;
            }
          }
          let hasWord = tokens.some((i) => i[0] !== "space" && i[0] !== "comment");
          if (hasWord) {
            node.raws.between += firstSpaces.map((i) => i[1]).join("");
            firstSpaces = [];
          }
          this.raw(node, "value", firstSpaces.concat(tokens), customProperty);
          if (node.value.includes(":") && !customProperty) {
            this.checkMissedSemicolon(tokens);
          }
        }
        doubleColon(token) {
          throw this.input.error(
            "Double colon",
            { offset: token[2] },
            { offset: token[2] + token[1].length }
          );
        }
        emptyRule(token) {
          let node = new Rule();
          this.init(node, token[2]);
          node.selector = "";
          node.raws.between = "";
          this.current = node;
        }
        end(token) {
          if (this.current.nodes && this.current.nodes.length) {
            this.current.raws.semicolon = this.semicolon;
          }
          this.semicolon = false;
          this.current.raws.after = (this.current.raws.after || "") + this.spaces;
          this.spaces = "";
          if (this.current.parent) {
            this.current.source.end = this.getPosition(token[2]);
            this.current.source.end.offset++;
            this.current = this.current.parent;
          } else {
            this.unexpectedClose(token);
          }
        }
        endFile() {
          if (this.current.parent) this.unclosedBlock();
          if (this.current.nodes && this.current.nodes.length) {
            this.current.raws.semicolon = this.semicolon;
          }
          this.current.raws.after = (this.current.raws.after || "") + this.spaces;
          this.root.source.end = this.getPosition(this.tokenizer.position());
        }
        freeSemicolon(token) {
          this.spaces += token[1];
          if (this.current.nodes) {
            let prev = this.current.nodes[this.current.nodes.length - 1];
            if (prev && prev.type === "rule" && !prev.raws.ownSemicolon) {
              prev.raws.ownSemicolon = this.spaces;
              this.spaces = "";
            }
          }
        }
        // Helpers
        getPosition(offset) {
          let pos = this.input.fromOffset(offset);
          return {
            column: pos.col,
            line: pos.line,
            offset
          };
        }
        init(node, offset) {
          this.current.push(node);
          node.source = {
            input: this.input,
            start: this.getPosition(offset)
          };
          node.raws.before = this.spaces;
          this.spaces = "";
          if (node.type !== "comment") this.semicolon = false;
        }
        other(start) {
          let end = false;
          let type = null;
          let colon = false;
          let bracket = null;
          let brackets = [];
          let customProperty = start[1].startsWith("--");
          let tokens = [];
          let token = start;
          while (token) {
            type = token[0];
            tokens.push(token);
            if (type === "(" || type === "[") {
              if (!bracket) bracket = token;
              brackets.push(type === "(" ? ")" : "]");
            } else if (customProperty && colon && type === "{") {
              if (!bracket) bracket = token;
              brackets.push("}");
            } else if (brackets.length === 0) {
              if (type === ";") {
                if (colon) {
                  this.decl(tokens, customProperty);
                  return;
                } else {
                  break;
                }
              } else if (type === "{") {
                this.rule(tokens);
                return;
              } else if (type === "}") {
                this.tokenizer.back(tokens.pop());
                end = true;
                break;
              } else if (type === ":") {
                colon = true;
              }
            } else if (type === brackets[brackets.length - 1]) {
              brackets.pop();
              if (brackets.length === 0) bracket = null;
            }
            token = this.tokenizer.nextToken();
          }
          if (this.tokenizer.endOfFile()) end = true;
          if (brackets.length > 0) this.unclosedBracket(bracket);
          if (end && colon) {
            if (!customProperty) {
              while (tokens.length) {
                token = tokens[tokens.length - 1][0];
                if (token !== "space" && token !== "comment") break;
                this.tokenizer.back(tokens.pop());
              }
            }
            this.decl(tokens, customProperty);
          } else {
            this.unknownWord(tokens);
          }
        }
        parse() {
          let token;
          while (!this.tokenizer.endOfFile()) {
            token = this.tokenizer.nextToken();
            switch (token[0]) {
              case "space":
                this.spaces += token[1];
                break;
              case ";":
                this.freeSemicolon(token);
                break;
              case "}":
                this.end(token);
                break;
              case "comment":
                this.comment(token);
                break;
              case "at-word":
                this.atrule(token);
                break;
              case "{":
                this.emptyRule(token);
                break;
              default:
                this.other(token);
                break;
            }
          }
          this.endFile();
        }
        precheckMissedSemicolon() {
        }
        raw(node, prop, tokens, customProperty) {
          let token, type;
          let length = tokens.length;
          let value = "";
          let clean = true;
          let next, prev;
          for (let i = 0; i < length; i += 1) {
            token = tokens[i];
            type = token[0];
            if (type === "space" && i === length - 1 && !customProperty) {
              clean = false;
            } else if (type === "comment") {
              prev = tokens[i - 1] ? tokens[i - 1][0] : "empty";
              next = tokens[i + 1] ? tokens[i + 1][0] : "empty";
              if (!SAFE_COMMENT_NEIGHBOR[prev] && !SAFE_COMMENT_NEIGHBOR[next]) {
                if (value.slice(-1) === ",") {
                  clean = false;
                } else {
                  value += token[1];
                }
              } else {
                clean = false;
              }
            } else {
              value += token[1];
            }
          }
          if (!clean) {
            let raw = tokens.reduce((all, i) => all + i[1], "");
            node.raws[prop] = { raw, value };
          }
          node[prop] = value;
        }
        rule(tokens) {
          tokens.pop();
          let node = new Rule();
          this.init(node, tokens[0][2]);
          node.raws.between = this.spacesAndCommentsFromEnd(tokens);
          this.raw(node, "selector", tokens);
          this.current = node;
        }
        spacesAndCommentsFromEnd(tokens) {
          let lastTokenType;
          let spaces = "";
          while (tokens.length) {
            lastTokenType = tokens[tokens.length - 1][0];
            if (lastTokenType !== "space" && lastTokenType !== "comment") break;
            spaces = tokens.pop()[1] + spaces;
          }
          return spaces;
        }
        // Errors
        spacesAndCommentsFromStart(tokens) {
          let next;
          let spaces = "";
          while (tokens.length) {
            next = tokens[0][0];
            if (next !== "space" && next !== "comment") break;
            spaces += tokens.shift()[1];
          }
          return spaces;
        }
        spacesFromEnd(tokens) {
          let lastTokenType;
          let spaces = "";
          while (tokens.length) {
            lastTokenType = tokens[tokens.length - 1][0];
            if (lastTokenType !== "space") break;
            spaces = tokens.pop()[1] + spaces;
          }
          return spaces;
        }
        stringFrom(tokens, from) {
          let result = "";
          for (let i = from; i < tokens.length; i++) {
            result += tokens[i][1];
          }
          tokens.splice(from, tokens.length - from);
          return result;
        }
        unclosedBlock() {
          let pos = this.current.source.start;
          throw this.input.error("Unclosed block", pos.line, pos.column);
        }
        unclosedBracket(bracket) {
          throw this.input.error(
            "Unclosed bracket",
            { offset: bracket[2] },
            { offset: bracket[2] + 1 }
          );
        }
        unexpectedClose(token) {
          throw this.input.error(
            "Unexpected }",
            { offset: token[2] },
            { offset: token[2] + 1 }
          );
        }
        unknownWord(tokens) {
          throw this.input.error(
            "Unknown word",
            { offset: tokens[0][2] },
            { offset: tokens[0][2] + tokens[0][1].length }
          );
        }
        unnamedAtrule(node, token) {
          throw this.input.error(
            "At-rule without name",
            { offset: token[2] },
            { offset: token[2] + token[1].length }
          );
        }
      };
      module.exports = Parser;
    }
  });

  // postcss/lib/parse.js
  var require_parse = __commonJS({
    "postcss/lib/parse.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var Input = require_input();
      var Parser = require_parser();
      function parse(css, opts) {
        let input = new Input(css, opts);
        let parser = new Parser(input);
        try {
          parser.parse();
        } catch (e) {
          if (false) {
            if (e.name === "CssSyntaxError" && opts && opts.from) {
              if (/\.scss$/i.test(opts.from)) {
                e.message += "\nYou tried to parse SCSS with the standard CSS parser; try again with the postcss-scss parser";
              } else if (/\.sass/i.test(opts.from)) {
                e.message += "\nYou tried to parse Sass with the standard CSS parser; try again with the postcss-sass parser";
              } else if (/\.less$/i.test(opts.from)) {
                e.message += "\nYou tried to parse Less with the standard CSS parser; try again with the postcss-less parser";
              }
            }
          }
          throw e;
        }
        return parser.root;
      }
      module.exports = parse;
      parse.default = parse;
      Container.registerParse(parse);
    }
  });

  // postcss/lib/warning.js
  var require_warning = __commonJS({
    "postcss/lib/warning.js"(exports, module) {
      "use strict";
      var Warning = class {
        constructor(text, opts = {}) {
          this.type = "warning";
          this.text = text;
          if (opts.node && opts.node.source) {
            let range = opts.node.rangeBy(opts);
            this.line = range.start.line;
            this.column = range.start.column;
            this.endLine = range.end.line;
            this.endColumn = range.end.column;
          }
          for (let opt in opts) this[opt] = opts[opt];
        }
        toString() {
          if (this.node) {
            return this.node.error(this.text, {
              index: this.index,
              plugin: this.plugin,
              word: this.word
            }).message;
          }
          if (this.plugin) {
            return this.plugin + ": " + this.text;
          }
          return this.text;
        }
      };
      module.exports = Warning;
      Warning.default = Warning;
    }
  });

  // postcss/lib/result.js
  var require_result = __commonJS({
    "postcss/lib/result.js"(exports, module) {
      "use strict";
      var Warning = require_warning();
      var Result = class {
        constructor(processor, root, opts) {
          this.processor = processor;
          this.messages = [];
          this.root = root;
          this.opts = opts;
          this.css = void 0;
          this.map = void 0;
        }
        toString() {
          return this.css;
        }
        warn(text, opts = {}) {
          if (!opts.plugin) {
            if (this.lastPlugin && this.lastPlugin.postcssPlugin) {
              opts.plugin = this.lastPlugin.postcssPlugin;
            }
          }
          let warning = new Warning(text, opts);
          this.messages.push(warning);
          return warning;
        }
        warnings() {
          return this.messages.filter((i) => i.type === "warning");
        }
        get content() {
          return this.css;
        }
      };
      module.exports = Result;
      Result.default = Result;
    }
  });

  // postcss/lib/warn-once.js
  var require_warn_once = __commonJS({
    "postcss/lib/warn-once.js"(exports, module) {
      "use strict";
      var printed = {};
      module.exports = function warnOnce(message) {
        if (printed[message]) return;
        printed[message] = true;
        if (typeof console !== "undefined" && console.warn) {
          console.warn(message);
        }
      };
    }
  });

  // postcss/lib/lazy-result.js
  var require_lazy_result = __commonJS({
    "postcss/lib/lazy-result.js"(exports, module) {
      "use strict";
      var Container = require_container();
      var Document = require_document();
      var MapGenerator = require_map_generator();
      var parse = require_parse();
      var Result = require_result();
      var Root = require_root();
      var stringify = require_stringify();
      var { isClean, my } = require_symbols();
      var warnOnce = require_warn_once();
      var TYPE_TO_CLASS_NAME = {
        atrule: "AtRule",
        comment: "Comment",
        decl: "Declaration",
        document: "Document",
        root: "Root",
        rule: "Rule"
      };
      var PLUGIN_PROPS = {
        AtRule: true,
        AtRuleExit: true,
        Comment: true,
        CommentExit: true,
        Declaration: true,
        DeclarationExit: true,
        Document: true,
        DocumentExit: true,
        Once: true,
        OnceExit: true,
        postcssPlugin: true,
        prepare: true,
        Root: true,
        RootExit: true,
        Rule: true,
        RuleExit: true
      };
      var NOT_VISITORS = {
        Once: true,
        postcssPlugin: true,
        prepare: true
      };
      var CHILDREN = 0;
      function isPromise(obj) {
        return typeof obj === "object" && typeof obj.then === "function";
      }
      function getEvents(node) {
        let key = false;
        let type = TYPE_TO_CLASS_NAME[node.type];
        if (node.type === "decl") {
          key = node.prop.toLowerCase();
        } else if (node.type === "atrule") {
          key = node.name.toLowerCase();
        }
        if (key && node.append) {
          return [
            type,
            type + "-" + key,
            CHILDREN,
            type + "Exit",
            type + "Exit-" + key
          ];
        } else if (key) {
          return [type, type + "-" + key, type + "Exit", type + "Exit-" + key];
        } else if (node.append) {
          return [type, CHILDREN, type + "Exit"];
        } else {
          return [type, type + "Exit"];
        }
      }
      function toStack(node) {
        let events;
        if (node.type === "document") {
          events = ["Document", CHILDREN, "DocumentExit"];
        } else if (node.type === "root") {
          events = ["Root", CHILDREN, "RootExit"];
        } else {
          events = getEvents(node);
        }
        return {
          eventIndex: 0,
          events,
          iterator: 0,
          node,
          visitorIndex: 0,
          visitors: []
        };
      }
      function cleanMarks(node) {
        node[isClean] = false;
        if (node.nodes) node.nodes.forEach((i) => cleanMarks(i));
        return node;
      }
      var postcss2 = {};
      var LazyResult = class _LazyResult {
        constructor(processor, css, opts) {
          this.stringified = false;
          this.processed = false;
          let root;
          if (typeof css === "object" && css !== null && (css.type === "root" || css.type === "document")) {
            root = cleanMarks(css);
          } else if (css instanceof _LazyResult || css instanceof Result) {
            root = cleanMarks(css.root);
            if (css.map) {
              if (typeof opts.map === "undefined") opts.map = {};
              if (!opts.map.inline) opts.map.inline = false;
              opts.map.prev = css.map;
            }
          } else {
            let parser = parse;
            if (opts.syntax) parser = opts.syntax.parse;
            if (opts.parser) parser = opts.parser;
            if (parser.parse) parser = parser.parse;
            try {
              root = parser(css, opts);
            } catch (error) {
              this.processed = true;
              this.error = error;
            }
            if (root && !root[my]) {
              Container.rebuild(root);
            }
          }
          this.result = new Result(processor, root, opts);
          this.helpers = { ...postcss2, postcss: postcss2, result: this.result };
          this.plugins = this.processor.plugins.map((plugin2) => {
            if (typeof plugin2 === "object" && plugin2.prepare) {
              return { ...plugin2, ...plugin2.prepare(this.result) };
            } else {
              return plugin2;
            }
          });
        }
        async() {
          if (this.error) return Promise.reject(this.error);
          if (this.processed) return Promise.resolve(this.result);
          if (!this.processing) {
            this.processing = this.runAsync();
          }
          return this.processing;
        }
        catch(onRejected) {
          return this.async().catch(onRejected);
        }
        finally(onFinally) {
          return this.async().then(onFinally, onFinally);
        }
        getAsyncError() {
          throw new Error("Use process(css).then(cb) to work with async plugins");
        }
        handleError(error, node) {
          let plugin2 = this.result.lastPlugin;
          try {
            if (node) node.addToError(error);
            this.error = error;
            if (error.name === "CssSyntaxError" && !error.plugin) {
              error.plugin = plugin2.postcssPlugin;
              error.setMessage();
            } else if (plugin2.postcssVersion) {
              if (false) {
                let pluginName = plugin2.postcssPlugin;
                let pluginVer = plugin2.postcssVersion;
                let runtimeVer = this.result.processor.version;
                let a = pluginVer.split(".");
                let b = runtimeVer.split(".");
                if (a[0] !== b[0] || parseInt(a[1]) > parseInt(b[1])) {
                  console.error(
                    "Unknown error from PostCSS plugin. Your current PostCSS version is " + runtimeVer + ", but " + pluginName + " uses " + pluginVer + ". Perhaps this is the source of the error below."
                  );
                }
              }
            }
          } catch (err) {
            if (console && console.error) console.error(err);
          }
          return error;
        }
        prepareVisitors() {
          this.listeners = {};
          let add = (plugin2, type, cb) => {
            if (!this.listeners[type]) this.listeners[type] = [];
            this.listeners[type].push([plugin2, cb]);
          };
          for (let plugin2 of this.plugins) {
            if (typeof plugin2 === "object") {
              for (let event in plugin2) {
                if (!PLUGIN_PROPS[event] && /^[A-Z]/.test(event)) {
                  throw new Error(
                    `Unknown event ${event} in ${plugin2.postcssPlugin}. Try to update PostCSS (${this.processor.version} now).`
                  );
                }
                if (!NOT_VISITORS[event]) {
                  if (typeof plugin2[event] === "object") {
                    for (let filter in plugin2[event]) {
                      if (filter === "*") {
                        add(plugin2, event, plugin2[event][filter]);
                      } else {
                        add(
                          plugin2,
                          event + "-" + filter.toLowerCase(),
                          plugin2[event][filter]
                        );
                      }
                    }
                  } else if (typeof plugin2[event] === "function") {
                    add(plugin2, event, plugin2[event]);
                  }
                }
              }
            }
          }
          this.hasListener = Object.keys(this.listeners).length > 0;
        }
        async runAsync() {
          this.plugin = 0;
          for (let i = 0; i < this.plugins.length; i++) {
            let plugin2 = this.plugins[i];
            let promise = this.runOnRoot(plugin2);
            if (isPromise(promise)) {
              try {
                await promise;
              } catch (error) {
                throw this.handleError(error);
              }
            }
          }
          this.prepareVisitors();
          if (this.hasListener) {
            let root = this.result.root;
            while (!root[isClean]) {
              root[isClean] = true;
              let stack = [toStack(root)];
              while (stack.length > 0) {
                let promise = this.visitTick(stack);
                if (isPromise(promise)) {
                  try {
                    await promise;
                  } catch (e) {
                    let node = stack[stack.length - 1].node;
                    throw this.handleError(e, node);
                  }
                }
              }
            }
            if (this.listeners.OnceExit) {
              for (let [plugin2, visitor] of this.listeners.OnceExit) {
                this.result.lastPlugin = plugin2;
                try {
                  if (root.type === "document") {
                    let roots = root.nodes.map(
                      (subRoot) => visitor(subRoot, this.helpers)
                    );
                    await Promise.all(roots);
                  } else {
                    await visitor(root, this.helpers);
                  }
                } catch (e) {
                  throw this.handleError(e);
                }
              }
            }
          }
          this.processed = true;
          return this.stringify();
        }
        runOnRoot(plugin2) {
          this.result.lastPlugin = plugin2;
          try {
            if (typeof plugin2 === "object" && plugin2.Once) {
              if (this.result.root.type === "document") {
                let roots = this.result.root.nodes.map(
                  (root) => plugin2.Once(root, this.helpers)
                );
                if (isPromise(roots[0])) {
                  return Promise.all(roots);
                }
                return roots;
              }
              return plugin2.Once(this.result.root, this.helpers);
            } else if (typeof plugin2 === "function") {
              return plugin2(this.result.root, this.result);
            }
          } catch (error) {
            throw this.handleError(error);
          }
        }
        stringify() {
          if (this.error) throw this.error;
          if (this.stringified) return this.result;
          this.stringified = true;
          this.sync();
          let opts = this.result.opts;
          let str = stringify;
          if (opts.syntax) str = opts.syntax.stringify;
          if (opts.stringifier) str = opts.stringifier;
          if (str.stringify) str = str.stringify;
          let map = new MapGenerator(str, this.result.root, this.result.opts);
          let data = map.generate();
          this.result.css = data[0];
          this.result.map = data[1];
          return this.result;
        }
        sync() {
          if (this.error) throw this.error;
          if (this.processed) return this.result;
          this.processed = true;
          if (this.processing) {
            throw this.getAsyncError();
          }
          for (let plugin2 of this.plugins) {
            let promise = this.runOnRoot(plugin2);
            if (isPromise(promise)) {
              throw this.getAsyncError();
            }
          }
          this.prepareVisitors();
          if (this.hasListener) {
            let root = this.result.root;
            while (!root[isClean]) {
              root[isClean] = true;
              this.walkSync(root);
            }
            if (this.listeners.OnceExit) {
              if (root.type === "document") {
                for (let subRoot of root.nodes) {
                  this.visitSync(this.listeners.OnceExit, subRoot);
                }
              } else {
                this.visitSync(this.listeners.OnceExit, root);
              }
            }
          }
          return this.result;
        }
        then(onFulfilled, onRejected) {
          if (false) {
            if (!("from" in this.opts)) {
              warnOnce(
                "Without `from` option PostCSS could generate wrong source map and will not find Browserslist config. Set it to CSS file path or to `undefined` to prevent this warning."
              );
            }
          }
          return this.async().then(onFulfilled, onRejected);
        }
        toString() {
          return this.css;
        }
        visitSync(visitors, node) {
          for (let [plugin2, visitor] of visitors) {
            this.result.lastPlugin = plugin2;
            let promise;
            try {
              promise = visitor(node, this.helpers);
            } catch (e) {
              throw this.handleError(e, node.proxyOf);
            }
            if (node.type !== "root" && node.type !== "document" && !node.parent) {
              return true;
            }
            if (isPromise(promise)) {
              throw this.getAsyncError();
            }
          }
        }
        visitTick(stack) {
          let visit = stack[stack.length - 1];
          let { node, visitors } = visit;
          if (node.type !== "root" && node.type !== "document" && !node.parent) {
            stack.pop();
            return;
          }
          if (visitors.length > 0 && visit.visitorIndex < visitors.length) {
            let [plugin2, visitor] = visitors[visit.visitorIndex];
            visit.visitorIndex += 1;
            if (visit.visitorIndex === visitors.length) {
              visit.visitors = [];
              visit.visitorIndex = 0;
            }
            this.result.lastPlugin = plugin2;
            try {
              return visitor(node.toProxy(), this.helpers);
            } catch (e) {
              throw this.handleError(e, node);
            }
          }
          if (visit.iterator !== 0) {
            let iterator = visit.iterator;
            let child;
            while (child = node.nodes[node.indexes[iterator]]) {
              node.indexes[iterator] += 1;
              if (!child[isClean]) {
                child[isClean] = true;
                stack.push(toStack(child));
                return;
              }
            }
            visit.iterator = 0;
            delete node.indexes[iterator];
          }
          let events = visit.events;
          while (visit.eventIndex < events.length) {
            let event = events[visit.eventIndex];
            visit.eventIndex += 1;
            if (event === CHILDREN) {
              if (node.nodes && node.nodes.length) {
                node[isClean] = true;
                visit.iterator = node.getIterator();
              }
              return;
            } else if (this.listeners[event]) {
              visit.visitors = this.listeners[event];
              return;
            }
          }
          stack.pop();
        }
        walkSync(node) {
          node[isClean] = true;
          let events = getEvents(node);
          for (let event of events) {
            if (event === CHILDREN) {
              if (node.nodes) {
                node.each((child) => {
                  if (!child[isClean]) this.walkSync(child);
                });
              }
            } else {
              let visitors = this.listeners[event];
              if (visitors) {
                if (this.visitSync(visitors, node.toProxy())) return;
              }
            }
          }
        }
        warnings() {
          return this.sync().warnings();
        }
        get content() {
          return this.stringify().content;
        }
        get css() {
          return this.stringify().css;
        }
        get map() {
          return this.stringify().map;
        }
        get messages() {
          return this.sync().messages;
        }
        get opts() {
          return this.result.opts;
        }
        get processor() {
          return this.result.processor;
        }
        get root() {
          return this.sync().root;
        }
        get [Symbol.toStringTag]() {
          return "LazyResult";
        }
      };
      LazyResult.registerPostcss = (dependant) => {
        postcss2 = dependant;
      };
      module.exports = LazyResult;
      LazyResult.default = LazyResult;
      Root.registerLazyResult(LazyResult);
      Document.registerLazyResult(LazyResult);
    }
  });

  // postcss/lib/no-work-result.js
  var require_no_work_result = __commonJS({
    "postcss/lib/no-work-result.js"(exports, module) {
      "use strict";
      var MapGenerator = require_map_generator();
      var parse = require_parse();
      var Result = require_result();
      var stringify = require_stringify();
      var warnOnce = require_warn_once();
      var NoWorkResult = class {
        constructor(processor, css, opts) {
          css = css.toString();
          this.stringified = false;
          this._processor = processor;
          this._css = css;
          this._opts = opts;
          this._map = void 0;
          let root;
          let str = stringify;
          this.result = new Result(this._processor, root, this._opts);
          this.result.css = css;
          let self = this;
          Object.defineProperty(this.result, "root", {
            get() {
              return self.root;
            }
          });
          let map = new MapGenerator(str, root, this._opts, css);
          if (map.isMap()) {
            let [generatedCSS, generatedMap] = map.generate();
            if (generatedCSS) {
              this.result.css = generatedCSS;
            }
            if (generatedMap) {
              this.result.map = generatedMap;
            }
          } else {
            map.clearAnnotation();
            this.result.css = map.css;
          }
        }
        async() {
          if (this.error) return Promise.reject(this.error);
          return Promise.resolve(this.result);
        }
        catch(onRejected) {
          return this.async().catch(onRejected);
        }
        finally(onFinally) {
          return this.async().then(onFinally, onFinally);
        }
        sync() {
          if (this.error) throw this.error;
          return this.result;
        }
        then(onFulfilled, onRejected) {
          if (false) {
            if (!("from" in this._opts)) {
              warnOnce(
                "Without `from` option PostCSS could generate wrong source map and will not find Browserslist config. Set it to CSS file path or to `undefined` to prevent this warning."
              );
            }
          }
          return this.async().then(onFulfilled, onRejected);
        }
        toString() {
          return this._css;
        }
        warnings() {
          return [];
        }
        get content() {
          return this.result.css;
        }
        get css() {
          return this.result.css;
        }
        get map() {
          return this.result.map;
        }
        get messages() {
          return [];
        }
        get opts() {
          return this.result.opts;
        }
        get processor() {
          return this.result.processor;
        }
        get root() {
          if (this._root) {
            return this._root;
          }
          let root;
          let parser = parse;
          try {
            root = parser(this._css, this._opts);
          } catch (error) {
            this.error = error;
          }
          if (this.error) {
            throw this.error;
          } else {
            this._root = root;
            return root;
          }
        }
        get [Symbol.toStringTag]() {
          return "NoWorkResult";
        }
      };
      module.exports = NoWorkResult;
      NoWorkResult.default = NoWorkResult;
    }
  });

  // postcss/lib/processor.js
  var require_processor = __commonJS({
    "postcss/lib/processor.js"(exports, module) {
      "use strict";
      var Document = require_document();
      var LazyResult = require_lazy_result();
      var NoWorkResult = require_no_work_result();
      var Root = require_root();
      var Processor = class {
        constructor(plugins = []) {
          this.version = "8.4.47";
          this.plugins = this.normalize(plugins);
        }
        normalize(plugins) {
          let normalized = [];
          for (let i of plugins) {
            if (i.postcss === true) {
              i = i();
            } else if (i.postcss) {
              i = i.postcss;
            }
            if (typeof i === "object" && Array.isArray(i.plugins)) {
              normalized = normalized.concat(i.plugins);
            } else if (typeof i === "object" && i.postcssPlugin) {
              normalized.push(i);
            } else if (typeof i === "function") {
              normalized.push(i);
            } else if (typeof i === "object" && (i.parse || i.stringify)) {
              if (false) {
                throw new Error(
                  "PostCSS syntaxes cannot be used as plugins. Instead, please use one of the syntax/parser/stringifier options as outlined in your PostCSS runner documentation."
                );
              }
            } else {
              throw new Error(i + " is not a PostCSS plugin");
            }
          }
          return normalized;
        }
        process(css, opts = {}) {
          if (!this.plugins.length && !opts.parser && !opts.stringifier && !opts.syntax) {
            return new NoWorkResult(this, css, opts);
          } else {
            return new LazyResult(this, css, opts);
          }
        }
        use(plugin2) {
          this.plugins = this.plugins.concat(this.normalize([plugin2]));
          return this;
        }
      };
      module.exports = Processor;
      Processor.default = Processor;
      Root.registerProcessor(Processor);
      Document.registerProcessor(Processor);
    }
  });

  // postcss/lib/postcss.js
  var require_postcss = __commonJS({
    "postcss/lib/postcss.js"(exports, module) {
      "use strict";
      var AtRule = require_at_rule();
      var Comment = require_comment();
      var Container = require_container();
      var CssSyntaxError = require_css_syntax_error();
      var Declaration = require_declaration();
      var Document = require_document();
      var fromJSON = require_fromJSON();
      var Input = require_input();
      var LazyResult = require_lazy_result();
      var list = require_list();
      var Node = require_node();
      var parse = require_parse();
      var Processor = require_processor();
      var Result = require_result();
      var Root = require_root();
      var Rule = require_rule();
      var stringify = require_stringify();
      var Warning = require_warning();
      function postcss2(...plugins) {
        if (plugins.length === 1 && Array.isArray(plugins[0])) {
          plugins = plugins[0];
        }
        return new Processor(plugins);
      }
      postcss2.plugin = function plugin2(name, initializer) {
        let warningPrinted = false;
        function creator(...args) {
          if (console && console.warn && !warningPrinted) {
            warningPrinted = true;
            console.warn(
              name + ": postcss.plugin was deprecated. Migration guide:\nhttps://evilmartians.com/chronicles/postcss-8-plugin-migration"
            );
            if (void 0) {
              console.warn(
                name + ": \u91CC\u9762 postcss.plugin \u88AB\u5F03\u7528. \u8FC1\u79FB\u6307\u5357:\nhttps://www.w3ctech.com/topic/2226"
              );
            }
          }
          let transformer = initializer(...args);
          transformer.postcssPlugin = name;
          transformer.postcssVersion = new Processor().version;
          return transformer;
        }
        let cache;
        Object.defineProperty(creator, "postcss", {
          get() {
            if (!cache) cache = creator();
            return cache;
          }
        });
        creator.process = function(css, processOpts, pluginOpts) {
          return postcss2([creator(pluginOpts)]).process(css, processOpts);
        };
        return creator;
      };
      postcss2.stringify = stringify;
      postcss2.parse = parse;
      postcss2.fromJSON = fromJSON;
      postcss2.list = list;
      postcss2.comment = (defaults) => new Comment(defaults);
      postcss2.atRule = (defaults) => new AtRule(defaults);
      postcss2.decl = (defaults) => new Declaration(defaults);
      postcss2.rule = (defaults) => new Rule(defaults);
      postcss2.root = (defaults) => new Root(defaults);
      postcss2.document = (defaults) => new Document(defaults);
      postcss2.CssSyntaxError = CssSyntaxError;
      postcss2.Declaration = Declaration;
      postcss2.Container = Container;
      postcss2.Processor = Processor;
      postcss2.Document = Document;
      postcss2.Comment = Comment;
      postcss2.Warning = Warning;
      postcss2.AtRule = AtRule;
      postcss2.Result = Result;
      postcss2.Input = Input;
      postcss2.Rule = Rule;
      postcss2.Root = Root;
      postcss2.Node = Node;
      LazyResult.registerPostcss(postcss2);
      module.exports = postcss2;
      postcss2.default = postcss2;
    }
  });

  // tailwindcss/lib/util/log.js
  var require_log = __commonJS({
    "tailwindcss/lib/util/log.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        dim: function() {
          return dim;
        },
        default: function() {
          return _default;
        }
      });
      var _picocolors = /* @__PURE__ */ _interop_require_default(require_picocolors_browser());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var alreadyShown = /* @__PURE__ */ new Set();
      function log(type, messages, key) {
        if (typeof process !== "undefined" && void 0) return;
        if (key && alreadyShown.has(key)) return;
        if (key) alreadyShown.add(key);
        console.warn("");
        messages.forEach((message) => console.warn(type, "-", message));
      }
      function dim(input) {
        return _picocolors.default.dim(input);
      }
      var _default = {
        info(key, messages) {
          log(_picocolors.default.bold(_picocolors.default.cyan("info")), ...Array.isArray(key) ? [
            key
          ] : [
            messages,
            key
          ]);
        },
        warn(key, messages) {
          log(_picocolors.default.bold(_picocolors.default.yellow("warn")), ...Array.isArray(key) ? [
            key
          ] : [
            messages,
            key
          ]);
        },
        risk(key, messages) {
          log(_picocolors.default.bold(_picocolors.default.magenta("risk")), ...Array.isArray(key) ? [
            key
          ] : [
            messages,
            key
          ]);
        }
      };
    }
  });

  // tailwindcss/lib/lib/normalizeTailwindDirectives.js
  var require_normalizeTailwindDirectives = __commonJS({
    "tailwindcss/lib/lib/normalizeTailwindDirectives.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return normalizeTailwindDirectives;
        }
      });
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function normalizeTailwindDirectives(root) {
        let tailwindDirectives = /* @__PURE__ */ new Set();
        let layerDirectives = /* @__PURE__ */ new Set();
        let applyDirectives = /* @__PURE__ */ new Set();
        root.walkAtRules((atRule) => {
          if (atRule.name === "apply") {
            applyDirectives.add(atRule);
          }
          if (atRule.name === "import") {
            if (atRule.params === '"tailwindcss/base"' || atRule.params === "'tailwindcss/base'") {
              atRule.name = "tailwind";
              atRule.params = "base";
            } else if (atRule.params === '"tailwindcss/components"' || atRule.params === "'tailwindcss/components'") {
              atRule.name = "tailwind";
              atRule.params = "components";
            } else if (atRule.params === '"tailwindcss/utilities"' || atRule.params === "'tailwindcss/utilities'") {
              atRule.name = "tailwind";
              atRule.params = "utilities";
            } else if (atRule.params === '"tailwindcss/screens"' || atRule.params === "'tailwindcss/screens'" || atRule.params === '"tailwindcss/variants"' || atRule.params === "'tailwindcss/variants'") {
              atRule.name = "tailwind";
              atRule.params = "variants";
            }
          }
          if (atRule.name === "tailwind") {
            if (atRule.params === "screens") {
              atRule.params = "variants";
            }
            tailwindDirectives.add(atRule.params);
          }
          if ([
            "layer",
            "responsive",
            "variants"
          ].includes(atRule.name)) {
            if ([
              "responsive",
              "variants"
            ].includes(atRule.name)) {
              _log.default.warn(`${atRule.name}-at-rule-deprecated`, [
                `The \`@${atRule.name}\` directive has been deprecated in Tailwind CSS v3.0.`,
                `Use \`@layer utilities\` or \`@layer components\` instead.`,
                "https://tailwindcss.com/docs/upgrade-guide#replace-variants-with-layer"
              ]);
            }
            layerDirectives.add(atRule);
          }
        });
        if (!tailwindDirectives.has("base") || !tailwindDirectives.has("components") || !tailwindDirectives.has("utilities")) {
          for (let rule of layerDirectives) {
            if (rule.name === "layer" && [
              "base",
              "components",
              "utilities"
            ].includes(rule.params)) {
              if (!tailwindDirectives.has(rule.params)) {
                throw rule.error(`\`@layer ${rule.params}\` is used but no matching \`@tailwind ${rule.params}\` directive is present.`);
              }
            } else if (rule.name === "responsive") {
              if (!tailwindDirectives.has("utilities")) {
                throw rule.error("`@responsive` is used but `@tailwind utilities` is missing.");
              }
            } else if (rule.name === "variants") {
              if (!tailwindDirectives.has("utilities")) {
                throw rule.error("`@variants` is used but `@tailwind utilities` is missing.");
              }
            }
          }
        }
        return {
          tailwindDirectives,
          applyDirectives
        };
      }
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-expand-fs.js
  var require_tailwind_expand_fs = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-expand-fs.js"(exports, module) {
      "use strict";
      function readFile(file, encoding) {
        return Promise.reject(new Error(`expandTailwindAtRules.js \u62D2\u7EDD\u6587\u4EF6\u5185\u5BB9\u8BFB\u53D6\uFF1A${String(file)} (${String(encoding)})`));
      }
      module.exports = { promises: { readFile } };
    }
  });

  // @alloc/quick-lru/index.js
  var require_quick_lru = __commonJS({
    "@alloc/quick-lru/index.js"(exports, module) {
      "use strict";
      var QuickLRU = class {
        constructor(options = {}) {
          if (!(options.maxSize && options.maxSize > 0)) {
            throw new TypeError("`maxSize` must be a number greater than 0");
          }
          if (typeof options.maxAge === "number" && options.maxAge === 0) {
            throw new TypeError("`maxAge` must be a number greater than 0");
          }
          this.maxSize = options.maxSize;
          this.maxAge = options.maxAge || Infinity;
          this.onEviction = options.onEviction;
          this.cache = /* @__PURE__ */ new Map();
          this.oldCache = /* @__PURE__ */ new Map();
          this._size = 0;
        }
        _emitEvictions(cache) {
          if (typeof this.onEviction !== "function") {
            return;
          }
          for (const [key, item] of cache) {
            this.onEviction(key, item.value);
          }
        }
        _deleteIfExpired(key, item) {
          if (typeof item.expiry === "number" && item.expiry <= Date.now()) {
            if (typeof this.onEviction === "function") {
              this.onEviction(key, item.value);
            }
            return this.delete(key);
          }
          return false;
        }
        _getOrDeleteIfExpired(key, item) {
          const deleted = this._deleteIfExpired(key, item);
          if (deleted === false) {
            return item.value;
          }
        }
        _getItemValue(key, item) {
          return item.expiry ? this._getOrDeleteIfExpired(key, item) : item.value;
        }
        _peek(key, cache) {
          const item = cache.get(key);
          return this._getItemValue(key, item);
        }
        _set(key, value) {
          this.cache.set(key, value);
          this._size++;
          if (this._size >= this.maxSize) {
            this._size = 0;
            this._emitEvictions(this.oldCache);
            this.oldCache = this.cache;
            this.cache = /* @__PURE__ */ new Map();
          }
        }
        _moveToRecent(key, item) {
          this.oldCache.delete(key);
          this._set(key, item);
        }
        *_entriesAscending() {
          for (const item of this.oldCache) {
            const [key, value] = item;
            if (!this.cache.has(key)) {
              const deleted = this._deleteIfExpired(key, value);
              if (deleted === false) {
                yield item;
              }
            }
          }
          for (const item of this.cache) {
            const [key, value] = item;
            const deleted = this._deleteIfExpired(key, value);
            if (deleted === false) {
              yield item;
            }
          }
        }
        get(key) {
          if (this.cache.has(key)) {
            const item = this.cache.get(key);
            return this._getItemValue(key, item);
          }
          if (this.oldCache.has(key)) {
            const item = this.oldCache.get(key);
            if (this._deleteIfExpired(key, item) === false) {
              this._moveToRecent(key, item);
              return item.value;
            }
          }
        }
        set(key, value, { maxAge = this.maxAge } = {}) {
          const expiry = typeof maxAge === "number" && maxAge !== Infinity ? Date.now() + maxAge : void 0;
          if (this.cache.has(key)) {
            this.cache.set(key, {
              value,
              expiry
            });
          } else {
            this._set(key, { value, expiry });
          }
          return this;
        }
        has(key) {
          if (this.cache.has(key)) {
            return !this._deleteIfExpired(key, this.cache.get(key));
          }
          if (this.oldCache.has(key)) {
            return !this._deleteIfExpired(key, this.oldCache.get(key));
          }
          return false;
        }
        peek(key) {
          if (this.cache.has(key)) {
            return this._peek(key, this.cache);
          }
          if (this.oldCache.has(key)) {
            return this._peek(key, this.oldCache);
          }
        }
        expiresIn(key) {
          const item = this.cache.get(key) || this.oldCache.get(key);
          if (item) {
            return item.expiry ? item.expiry - Date.now() : Infinity;
          }
        }
        delete(key) {
          const deleted = this.cache.delete(key);
          if (deleted) {
            this._size--;
          }
          return this.oldCache.delete(key) || deleted;
        }
        clear() {
          this.cache.clear();
          this.oldCache.clear();
          this._size = 0;
        }
        resize(newSize) {
          if (!(newSize && newSize > 0)) {
            throw new TypeError("`maxSize` must be a number greater than 0");
          }
          const items = [...this._entriesAscending()];
          const removeCount = items.length - newSize;
          if (removeCount < 0) {
            this.cache = new Map(items);
            this.oldCache = /* @__PURE__ */ new Map();
            this._size = items.length;
          } else {
            if (removeCount > 0) {
              this._emitEvictions(items.slice(0, removeCount));
            }
            this.oldCache = new Map(items.slice(removeCount));
            this.cache = /* @__PURE__ */ new Map();
            this._size = 0;
          }
          this.maxSize = newSize;
        }
        *keys() {
          for (const [key] of this) {
            yield key;
          }
        }
        *values() {
          for (const [, value] of this) {
            yield value;
          }
        }
        *[Symbol.iterator]() {
          for (const item of this.cache) {
            const [key, value] = item;
            const deleted = this._deleteIfExpired(key, value);
            if (deleted === false) {
              yield [key, value.value];
            }
          }
          for (const item of this.oldCache) {
            const [key, value] = item;
            if (!this.cache.has(key)) {
              const deleted = this._deleteIfExpired(key, value);
              if (deleted === false) {
                yield [key, value.value];
              }
            }
          }
        }
        *entriesDescending() {
          let items = [...this.cache];
          for (let i = items.length - 1; i >= 0; --i) {
            const item = items[i];
            const [key, value] = item;
            const deleted = this._deleteIfExpired(key, value);
            if (deleted === false) {
              yield [key, value.value];
            }
          }
          items = [...this.oldCache];
          for (let i = items.length - 1; i >= 0; --i) {
            const item = items[i];
            const [key, value] = item;
            if (!this.cache.has(key)) {
              const deleted = this._deleteIfExpired(key, value);
              if (deleted === false) {
                yield [key, value.value];
              }
            }
          }
        }
        *entriesAscending() {
          for (const [key, value] of this._entriesAscending()) {
            yield [key, value.value];
          }
        }
        get size() {
          if (!this._size) {
            return this.oldCache.size;
          }
          let oldCacheSize = 0;
          for (const key of this.oldCache.keys()) {
            if (!this.cache.has(key)) {
              oldCacheSize++;
            }
          }
          return Math.min(this._size + oldCacheSize, this.maxSize);
        }
      };
      module.exports = QuickLRU;
    }
  });

  // tailwindcss/lib/lib/sharedState.js
  var require_sharedState = __commonJS({
    "tailwindcss/lib/lib/sharedState.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        env: function() {
          return env;
        },
        contextMap: function() {
          return contextMap;
        },
        configContextMap: function() {
          return configContextMap;
        },
        contextSourcesMap: function() {
          return contextSourcesMap;
        },
        sourceHashMap: function() {
          return sourceHashMap;
        },
        NOT_ON_DEMAND: function() {
          return NOT_ON_DEMAND;
        },
        NONE: function() {
          return NONE;
        },
        resolveDebug: function() {
          return resolveDebug;
        }
      });
      var env = typeof process !== "undefined" ? {
        NODE_ENV: "production",
        DEBUG: resolveDebug(void 0)
      } : {
        NODE_ENV: "production",
        DEBUG: false
      };
      var contextMap = /* @__PURE__ */ new Map();
      var configContextMap = /* @__PURE__ */ new Map();
      var contextSourcesMap = /* @__PURE__ */ new Map();
      var sourceHashMap = /* @__PURE__ */ new Map();
      var NOT_ON_DEMAND = new String("*");
      var NONE = Symbol("__NONE__");
      function resolveDebug(debug) {
        if (debug === void 0) {
          return false;
        }
        if (debug === "true" || debug === "1") {
          return true;
        }
        if (debug === "false" || debug === "0") {
          return false;
        }
        if (debug === "*") {
          return true;
        }
        let debuggers = debug.split(",").map((d) => d.split(":")[0]);
        if (debuggers.includes("-tailwindcss")) {
          return false;
        }
        if (debuggers.includes("tailwindcss")) {
          return true;
        }
        return false;
      }
    }
  });

  // postcss-selector-parser/dist/util/unesc.js
  var require_unesc = __commonJS({
    "postcss-selector-parser/dist/util/unesc.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = unesc;
      function gobbleHex(str) {
        var lower = str.toLowerCase();
        var hex = "";
        var spaceTerminated = false;
        for (var i = 0; i < 6 && lower[i] !== void 0; i++) {
          var code = lower.charCodeAt(i);
          var valid = code >= 97 && code <= 102 || code >= 48 && code <= 57;
          spaceTerminated = code === 32;
          if (!valid) {
            break;
          }
          hex += lower[i];
        }
        if (hex.length === 0) {
          return void 0;
        }
        var codePoint = parseInt(hex, 16);
        var isSurrogate = codePoint >= 55296 && codePoint <= 57343;
        if (isSurrogate || codePoint === 0 || codePoint > 1114111) {
          return ["\uFFFD", hex.length + (spaceTerminated ? 1 : 0)];
        }
        return [String.fromCodePoint(codePoint), hex.length + (spaceTerminated ? 1 : 0)];
      }
      var CONTAINS_ESCAPE = /\\/;
      function unesc(str) {
        var needToProcess = CONTAINS_ESCAPE.test(str);
        if (!needToProcess) {
          return str;
        }
        var ret = "";
        for (var i = 0; i < str.length; i++) {
          if (str[i] === "\\") {
            var gobbled = gobbleHex(str.slice(i + 1, i + 7));
            if (gobbled !== void 0) {
              ret += gobbled[0];
              i += gobbled[1];
              continue;
            }
            if (str[i + 1] === "\\") {
              ret += "\\";
              i++;
              continue;
            }
            if (str.length === i + 1) {
              ret += str[i];
            }
            continue;
          }
          ret += str[i];
        }
        return ret;
      }
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/util/getProp.js
  var require_getProp = __commonJS({
    "postcss-selector-parser/dist/util/getProp.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = getProp;
      function getProp(obj) {
        for (var _len = arguments.length, props = new Array(_len > 1 ? _len - 1 : 0), _key = 1; _key < _len; _key++) {
          props[_key - 1] = arguments[_key];
        }
        while (props.length > 0) {
          var prop = props.shift();
          if (!obj[prop]) {
            return void 0;
          }
          obj = obj[prop];
        }
        return obj;
      }
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/util/ensureObject.js
  var require_ensureObject = __commonJS({
    "postcss-selector-parser/dist/util/ensureObject.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = ensureObject;
      function ensureObject(obj) {
        for (var _len = arguments.length, props = new Array(_len > 1 ? _len - 1 : 0), _key = 1; _key < _len; _key++) {
          props[_key - 1] = arguments[_key];
        }
        while (props.length > 0) {
          var prop = props.shift();
          if (!obj[prop]) {
            obj[prop] = {};
          }
          obj = obj[prop];
        }
      }
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/util/stripComments.js
  var require_stripComments = __commonJS({
    "postcss-selector-parser/dist/util/stripComments.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = stripComments;
      function stripComments(str) {
        var s = "";
        var commentStart = str.indexOf("/*");
        var lastEnd = 0;
        while (commentStart >= 0) {
          s = s + str.slice(lastEnd, commentStart);
          var commentEnd = str.indexOf("*/", commentStart + 2);
          if (commentEnd < 0) {
            return s;
          }
          lastEnd = commentEnd + 2;
          commentStart = str.indexOf("/*", lastEnd);
        }
        s = s + str.slice(lastEnd);
        return s;
      }
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/util/maxNestingDepth.js
  var require_maxNestingDepth = __commonJS({
    "postcss-selector-parser/dist/util/maxNestingDepth.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.MAX_NESTING_DEPTH = void 0;
      exports["default"] = resolveMaxNestingDepth;
      var MAX_NESTING_DEPTH = exports.MAX_NESTING_DEPTH = 256;
      function resolveMaxNestingDepth(value) {
        return Number.isSafeInteger(value) && value >= 0 ? value : MAX_NESTING_DEPTH;
      }
    }
  });

  // postcss-selector-parser/dist/util/index.js
  var require_util = __commonJS({
    "postcss-selector-parser/dist/util/index.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.unesc = exports.stripComments = exports.resolveMaxNestingDepth = exports.getProp = exports.ensureObject = exports.MAX_NESTING_DEPTH = void 0;
      var _unesc = _interopRequireDefault(require_unesc());
      exports.unesc = _unesc["default"];
      var _getProp = _interopRequireDefault(require_getProp());
      exports.getProp = _getProp["default"];
      var _ensureObject = _interopRequireDefault(require_ensureObject());
      exports.ensureObject = _ensureObject["default"];
      var _stripComments = _interopRequireDefault(require_stripComments());
      exports.stripComments = _stripComments["default"];
      var _maxNestingDepth = _interopRequireWildcard(require_maxNestingDepth());
      exports.resolveMaxNestingDepth = _maxNestingDepth["default"];
      exports.MAX_NESTING_DEPTH = _maxNestingDepth.MAX_NESTING_DEPTH;
      function _interopRequireWildcard(e, t) {
        if ("function" == typeof WeakMap) var r = /* @__PURE__ */ new WeakMap(), n = /* @__PURE__ */ new WeakMap();
        return (_interopRequireWildcard = function _interopRequireWildcard2(e2, t2) {
          if (!t2 && e2 && e2.__esModule) return e2;
          var o, i, f = { __proto__: null, "default": e2 };
          if (null === e2 || "object" != typeof e2 && "function" != typeof e2) return f;
          if (o = t2 ? n : r) {
            if (o.has(e2)) return o.get(e2);
            o.set(e2, f);
          }
          for (var _t in e2) {
            "default" !== _t && {}.hasOwnProperty.call(e2, _t) && ((i = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e2, _t)) && (i.get || i.set) ? o(f, _t, i) : f[_t] = e2[_t]);
          }
          return f;
        })(e, t);
      }
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
    }
  });

  // postcss-selector-parser/dist/selectors/node.js
  var require_node2 = __commonJS({
    "postcss-selector-parser/dist/selectors/node.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _util = require_util();
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      var cloneNode = function cloneNode2(obj, parent, depth) {
        if (depth === void 0) {
          depth = 0;
        }
        if (depth > _util.MAX_NESTING_DEPTH) {
          throw new Error("Cannot clone selector: nesting depth exceeds the maximum of " + _util.MAX_NESTING_DEPTH + ".");
        }
        if (typeof obj !== "object" || obj === null) {
          return obj;
        }
        var cloned = new obj.constructor();
        for (var i in obj) {
          if (!obj.hasOwnProperty(i)) {
            continue;
          }
          var value = obj[i];
          var type = typeof value;
          if (i === "parent" && type === "object") {
            if (parent) {
              cloned[i] = parent;
            }
          } else if (value instanceof Array) {
            cloned[i] = value.map(function(j) {
              return cloneNode2(j, cloned, depth + 1);
            });
          } else {
            cloned[i] = cloneNode2(value, cloned, depth + 1);
          }
        }
        return cloned;
      };
      var Node = exports["default"] = /* @__PURE__ */ function() {
        function Node2(opts) {
          if (opts === void 0) {
            opts = {};
          }
          Object.assign(this, opts);
          this.spaces = this.spaces || {};
          this.spaces.before = this.spaces.before || "";
          this.spaces.after = this.spaces.after || "";
        }
        var _proto = Node2.prototype;
        _proto.remove = function remove() {
          if (this.parent) {
            this.parent.removeChild(this);
          }
          this.parent = void 0;
          return this;
        };
        _proto.replaceWith = function replaceWith() {
          if (this.parent) {
            for (var index in arguments) {
              this.parent.insertBefore(this, arguments[index]);
            }
            this.remove();
          }
          return this;
        };
        _proto.next = function next() {
          return this.parent.at(this.parent.index(this) + 1);
        };
        _proto.prev = function prev() {
          return this.parent.at(this.parent.index(this) - 1);
        };
        _proto.clone = function clone(overrides) {
          if (overrides === void 0) {
            overrides = {};
          }
          var cloned = cloneNode(this);
          for (var name in overrides) {
            cloned[name] = overrides[name];
          }
          return cloned;
        };
        _proto.appendToPropertyAndEscape = function appendToPropertyAndEscape(name, value, valueEscaped) {
          if (!this.raws) {
            this.raws = {};
          }
          var originalValue = this[name];
          var originalEscaped = this.raws[name];
          this[name] = originalValue + value;
          if (originalEscaped || valueEscaped !== value) {
            this.raws[name] = (originalEscaped || originalValue) + valueEscaped;
          } else {
            delete this.raws[name];
          }
        };
        _proto.setPropertyAndEscape = function setPropertyAndEscape(name, value, valueEscaped) {
          if (!this.raws) {
            this.raws = {};
          }
          this[name] = value;
          this.raws[name] = valueEscaped;
        };
        _proto.setPropertyWithoutEscape = function setPropertyWithoutEscape(name, value) {
          this[name] = value;
          if (this.raws) {
            delete this.raws[name];
          }
        };
        _proto.isAtPosition = function isAtPosition(line, column) {
          if (this.source && this.source.start && this.source.end) {
            if (this.source.start.line > line) {
              return false;
            }
            if (this.source.end.line < line) {
              return false;
            }
            if (this.source.start.line === line && this.source.start.column > column) {
              return false;
            }
            if (this.source.end.line === line && this.source.end.column < column) {
              return false;
            }
            return true;
          }
          return void 0;
        };
        _proto.stringifyProperty = function stringifyProperty(name) {
          return this.raws && this.raws[name] || this[name];
        };
        _proto.valueToString = function valueToString() {
          return String(this.stringifyProperty("value"));
        };
        _proto.toString = function toString() {
          return [this.rawSpaceBefore, this.valueToString(), this.rawSpaceAfter].join("");
        };
        _proto._stringify = function _stringify() {
          return this.toString();
        };
        _createClass(Node2, [{
          key: "rawSpaceBefore",
          get: function get() {
            var rawSpace = this.raws && this.raws.spaces && this.raws.spaces.before;
            if (rawSpace === void 0) {
              rawSpace = this.spaces && this.spaces.before;
            }
            return rawSpace || "";
          },
          set: function set(raw) {
            (0, _util.ensureObject)(this, "raws", "spaces");
            this.raws.spaces.before = raw;
          }
        }, {
          key: "rawSpaceAfter",
          get: function get() {
            var rawSpace = this.raws && this.raws.spaces && this.raws.spaces.after;
            if (rawSpace === void 0) {
              rawSpace = this.spaces.after;
            }
            return rawSpace || "";
          },
          set: function set(raw) {
            (0, _util.ensureObject)(this, "raws", "spaces");
            this.raws.spaces.after = raw;
          }
        }]);
        return Node2;
      }();
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/types.js
  var require_types = __commonJS({
    "postcss-selector-parser/dist/selectors/types.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.UNIVERSAL = exports.TAG = exports.STRING = exports.SELECTOR = exports.ROOT = exports.PSEUDO = exports.NESTING = exports.ID = exports.COMMENT = exports.COMBINATOR = exports.CLASS = exports.ATTRIBUTE = void 0;
      var TAG = exports.TAG = "tag";
      var STRING = exports.STRING = "string";
      var SELECTOR = exports.SELECTOR = "selector";
      var ROOT = exports.ROOT = "root";
      var PSEUDO = exports.PSEUDO = "pseudo";
      var NESTING = exports.NESTING = "nesting";
      var ID = exports.ID = "id";
      var COMMENT = exports.COMMENT = "comment";
      var COMBINATOR = exports.COMBINATOR = "combinator";
      var CLASS = exports.CLASS = "class";
      var ATTRIBUTE = exports.ATTRIBUTE = "attribute";
      var UNIVERSAL = exports.UNIVERSAL = "universal";
    }
  });

  // postcss-selector-parser/dist/selectors/container.js
  var require_container2 = __commonJS({
    "postcss-selector-parser/dist/selectors/container.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _util = require_util();
      var _node = _interopRequireDefault(require_node2());
      var types = _interopRequireWildcard(require_types());
      function _interopRequireWildcard(e, t) {
        if ("function" == typeof WeakMap) var r = /* @__PURE__ */ new WeakMap(), n = /* @__PURE__ */ new WeakMap();
        return (_interopRequireWildcard = function _interopRequireWildcard2(e2, t2) {
          if (!t2 && e2 && e2.__esModule) return e2;
          var o, i, f = { __proto__: null, "default": e2 };
          if (null === e2 || "object" != typeof e2 && "function" != typeof e2) return f;
          if (o = t2 ? n : r) {
            if (o.has(e2)) return o.get(e2);
            o.set(e2, f);
          }
          for (var _t in e2) {
            "default" !== _t && {}.hasOwnProperty.call(e2, _t) && ((i = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e2, _t)) && (i.get || i.set) ? o(f, _t, i) : f[_t] = e2[_t]);
          }
          return f;
        })(e, t);
      }
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _createForOfIteratorHelperLoose(r, e) {
        var t = "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"];
        if (t) return (t = t.call(r)).next.bind(t);
        if (Array.isArray(r) || (t = _unsupportedIterableToArray(r)) || e && r && "number" == typeof r.length) {
          t && (r = t);
          var o = 0;
          return function() {
            return o >= r.length ? { done: true } : { done: false, value: r[o++] };
          };
        }
        throw new TypeError("Invalid attempt to iterate non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.");
      }
      function _unsupportedIterableToArray(r, a) {
        if (r) {
          if ("string" == typeof r) return _arrayLikeToArray(r, a);
          var t = {}.toString.call(r).slice(8, -1);
          return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0;
        }
      }
      function _arrayLikeToArray(r, a) {
        (null == a || a > r.length) && (a = r.length);
        for (var e = 0, n = Array(a); e < a; e++) {
          n[e] = r[e];
        }
        return n;
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Container = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(Container2, _Node);
        function Container2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          if (!_this.nodes) {
            _this.nodes = [];
          }
          return _this;
        }
        var _proto = Container2.prototype;
        _proto.append = function append(selector) {
          selector.parent = this;
          this.nodes.push(selector);
          return this;
        };
        _proto.prepend = function prepend(selector) {
          selector.parent = this;
          this.nodes.unshift(selector);
          return this;
        };
        _proto.at = function at(index) {
          return this.nodes[index];
        };
        _proto.index = function index(child) {
          if (typeof child === "number") {
            return child;
          }
          return this.nodes.indexOf(child);
        };
        _proto.removeChild = function removeChild(child) {
          child = this.index(child);
          this.at(child).parent = void 0;
          this.nodes.splice(child, 1);
          var index;
          for (var id in this.indexes) {
            index = this.indexes[id];
            if (index >= child) {
              this.indexes[id] = index - 1;
            }
          }
          return this;
        };
        _proto.removeAll = function removeAll() {
          for (var _iterator = _createForOfIteratorHelperLoose(this.nodes), _step; !(_step = _iterator()).done; ) {
            var node = _step.value;
            node.parent = void 0;
          }
          this.nodes = [];
          return this;
        };
        _proto.empty = function empty() {
          return this.removeAll();
        };
        _proto.insertAfter = function insertAfter(oldNode, newNode) {
          newNode.parent = this;
          var oldIndex = this.index(oldNode);
          this.nodes.splice(oldIndex + 1, 0, newNode);
          newNode.parent = this;
          var index;
          for (var id in this.indexes) {
            index = this.indexes[id];
            if (oldIndex <= index) {
              this.indexes[id] = index + 1;
            }
          }
          return this;
        };
        _proto.insertBefore = function insertBefore(oldNode, newNode) {
          newNode.parent = this;
          var oldIndex = this.index(oldNode);
          this.nodes.splice(oldIndex, 0, newNode);
          newNode.parent = this;
          var index;
          for (var id in this.indexes) {
            index = this.indexes[id];
            if (index <= oldIndex) {
              this.indexes[id] = index + 1;
            }
          }
          return this;
        };
        _proto._findChildAtPosition = function _findChildAtPosition(line, col) {
          var found = void 0;
          this.each(function(node) {
            if (node.atPosition) {
              var foundChild = node.atPosition(line, col);
              if (foundChild) {
                found = foundChild;
                return false;
              }
            } else if (node.isAtPosition(line, col)) {
              found = node;
              return false;
            }
          });
          return found;
        };
        _proto.atPosition = function atPosition(line, col) {
          if (this.isAtPosition(line, col)) {
            return this._findChildAtPosition(line, col) || this;
          } else {
            return void 0;
          }
        };
        _proto._inferEndPosition = function _inferEndPosition() {
          if (this.last && this.last.source && this.last.source.end) {
            this.source = this.source || {};
            this.source.end = this.source.end || {};
            Object.assign(this.source.end, this.last.source.end);
          }
        };
        _proto.each = function each(callback) {
          if (!this.lastEach) {
            this.lastEach = 0;
          }
          if (!this.indexes) {
            this.indexes = {};
          }
          this.lastEach++;
          var id = this.lastEach;
          this.indexes[id] = 0;
          if (!this.length) {
            return void 0;
          }
          var index, result;
          while (this.indexes[id] < this.length) {
            index = this.indexes[id];
            result = callback(this.at(index), index);
            if (result === false) {
              break;
            }
            this.indexes[id] += 1;
          }
          delete this.indexes[id];
          if (result === false) {
            return false;
          }
        };
        _proto.walk = function walk(callback, depth) {
          if (depth === void 0) {
            depth = 0;
          }
          if (depth > _util.MAX_NESTING_DEPTH) {
            throw new Error("Cannot walk selector: nesting depth exceeds the maximum of " + _util.MAX_NESTING_DEPTH + ".");
          }
          return this.each(function(node, i) {
            var result = callback(node, i);
            if (result !== false && node.length) {
              result = node.walk(callback, depth + 1);
            }
            if (result === false) {
              return false;
            }
          });
        };
        _proto.walkAttributes = function walkAttributes(callback) {
          var _this2 = this;
          return this.walk(function(selector) {
            if (selector.type === types.ATTRIBUTE) {
              return callback.call(_this2, selector);
            }
          });
        };
        _proto.walkClasses = function walkClasses(callback) {
          var _this3 = this;
          return this.walk(function(selector) {
            if (selector.type === types.CLASS) {
              return callback.call(_this3, selector);
            }
          });
        };
        _proto.walkCombinators = function walkCombinators(callback) {
          var _this4 = this;
          return this.walk(function(selector) {
            if (selector.type === types.COMBINATOR) {
              return callback.call(_this4, selector);
            }
          });
        };
        _proto.walkComments = function walkComments(callback) {
          var _this5 = this;
          return this.walk(function(selector) {
            if (selector.type === types.COMMENT) {
              return callback.call(_this5, selector);
            }
          });
        };
        _proto.walkIds = function walkIds(callback) {
          var _this6 = this;
          return this.walk(function(selector) {
            if (selector.type === types.ID) {
              return callback.call(_this6, selector);
            }
          });
        };
        _proto.walkNesting = function walkNesting(callback) {
          var _this7 = this;
          return this.walk(function(selector) {
            if (selector.type === types.NESTING) {
              return callback.call(_this7, selector);
            }
          });
        };
        _proto.walkPseudos = function walkPseudos(callback) {
          var _this8 = this;
          return this.walk(function(selector) {
            if (selector.type === types.PSEUDO) {
              return callback.call(_this8, selector);
            }
          });
        };
        _proto.walkTags = function walkTags(callback) {
          var _this9 = this;
          return this.walk(function(selector) {
            if (selector.type === types.TAG) {
              return callback.call(_this9, selector);
            }
          });
        };
        _proto.walkUniversals = function walkUniversals(callback) {
          var _this0 = this;
          return this.walk(function(selector) {
            if (selector.type === types.UNIVERSAL) {
              return callback.call(_this0, selector);
            }
          });
        };
        _proto.split = function split(callback) {
          var _this1 = this;
          var current = [];
          return this.reduce(function(memo, node, index) {
            var split2 = callback.call(_this1, node);
            current.push(node);
            if (split2) {
              memo.push(current);
              current = [];
            } else if (index === _this1.length - 1) {
              memo.push(current);
            }
            return memo;
          }, []);
        };
        _proto.map = function map(callback) {
          return this.nodes.map(callback);
        };
        _proto.reduce = function reduce(callback, memo) {
          return this.nodes.reduce(callback, memo);
        };
        _proto.every = function every(callback) {
          return this.nodes.every(callback);
        };
        _proto.some = function some(callback) {
          return this.nodes.some(callback);
        };
        _proto.filter = function filter(callback) {
          return this.nodes.filter(callback);
        };
        _proto.sort = function sort(callback) {
          return this.nodes.sort(callback);
        };
        _proto.toString = function toString(options) {
          if (options === void 0) {
            options = {};
          }
          return this._stringify(options, 0, (0, _util.resolveMaxNestingDepth)(options.maxNestingDepth));
        };
        _proto._stringify = function _stringify(options, depth, max) {
          var _this10 = this;
          return this.map(function(child) {
            return _this10._stringifyChild(child, options, depth, max);
          }).join("");
        };
        _proto._stringifyChild = function _stringifyChild(child, options, depth, max) {
          return typeof child._stringify === "function" ? child._stringify(options, depth, max) : String(child);
        };
        _createClass(Container2, [{
          key: "first",
          get: function get() {
            return this.at(0);
          }
        }, {
          key: "last",
          get: function get() {
            return this.at(this.length - 1);
          }
        }, {
          key: "length",
          get: function get() {
            return this.nodes.length;
          }
        }]);
        return Container2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/root.js
  var require_root2 = __commonJS({
    "postcss-selector-parser/dist/selectors/root.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _container = _interopRequireDefault(require_container2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Root = exports["default"] = /* @__PURE__ */ function(_Container) {
        _inheritsLoose(Root2, _Container);
        function Root2(opts) {
          var _this;
          _this = _Container.call(this, opts) || this;
          _this.type = _types.ROOT;
          return _this;
        }
        var _proto = Root2.prototype;
        _proto._stringify = function _stringify(options, depth, max) {
          var _this2 = this;
          var str = this.reduce(function(memo, selector) {
            memo.push(_this2._stringifyChild(selector, options, depth, max));
            return memo;
          }, []).join(",");
          return this.trailingComma ? str + "," : str;
        };
        _proto.error = function error(message, options) {
          if (this._error) {
            return this._error(message, options);
          } else {
            return new Error(message);
          }
        };
        _createClass(Root2, [{
          key: "errorGenerator",
          set: function set(handler) {
            this._error = handler;
          }
        }]);
        return Root2;
      }(_container["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/selector.js
  var require_selector = __commonJS({
    "postcss-selector-parser/dist/selectors/selector.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _container = _interopRequireDefault(require_container2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Selector = exports["default"] = /* @__PURE__ */ function(_Container) {
        _inheritsLoose(Selector2, _Container);
        function Selector2(opts) {
          var _this;
          _this = _Container.call(this, opts) || this;
          _this.type = _types.SELECTOR;
          return _this;
        }
        return Selector2;
      }(_container["default"]);
      module.exports = exports.default;
    }
  });

  // cssesc/cssesc.js
  var require_cssesc = __commonJS({
    "cssesc/cssesc.js"(exports, module) {
      "use strict";
      /*! https://mths.be/cssesc v3.0.0 by @mathias */
      var object = {};
      var hasOwnProperty = object.hasOwnProperty;
      var merge = function merge2(options, defaults) {
        if (!options) {
          return defaults;
        }
        var result = {};
        for (var key in defaults) {
          result[key] = hasOwnProperty.call(options, key) ? options[key] : defaults[key];
        }
        return result;
      };
      var regexAnySingleEscape = /[ -,\.\/:-@\[-\^`\{-~]/;
      var regexSingleEscape = /[ -,\.\/:-@\[\]\^`\{-~]/;
      var regexExcessiveSpaces = /(^|\\+)?(\\[A-F0-9]{1,6})\x20(?![a-fA-F0-9\x20])/g;
      var cssesc = function cssesc2(string, options) {
        options = merge(options, cssesc2.options);
        if (options.quotes != "single" && options.quotes != "double") {
          options.quotes = "single";
        }
        var quote = options.quotes == "double" ? '"' : "'";
        var isIdentifier = options.isIdentifier;
        var firstChar = string.charAt(0);
        var output2 = "";
        var counter = 0;
        var length = string.length;
        while (counter < length) {
          var character = string.charAt(counter++);
          var codePoint = character.charCodeAt();
          var value = void 0;
          if (codePoint < 32 || codePoint > 126) {
            if (codePoint >= 55296 && codePoint <= 56319 && counter < length) {
              var extra = string.charCodeAt(counter++);
              if ((extra & 64512) == 56320) {
                codePoint = ((codePoint & 1023) << 10) + (extra & 1023) + 65536;
              } else {
                counter--;
              }
            }
            value = "\\" + codePoint.toString(16).toUpperCase() + " ";
          } else {
            if (options.escapeEverything) {
              if (regexAnySingleEscape.test(character)) {
                value = "\\" + character;
              } else {
                value = "\\" + codePoint.toString(16).toUpperCase() + " ";
              }
            } else if (/[\t\n\f\r\x0B]/.test(character)) {
              value = "\\" + codePoint.toString(16).toUpperCase() + " ";
            } else if (character == "\\" || !isIdentifier && (character == '"' && quote == character || character == "'" && quote == character) || isIdentifier && regexSingleEscape.test(character)) {
              value = "\\" + character;
            } else {
              value = character;
            }
          }
          output2 += value;
        }
        if (isIdentifier) {
          if (/^-[-\d]/.test(output2)) {
            output2 = "\\-" + output2.slice(1);
          } else if (/\d/.test(firstChar)) {
            output2 = "\\3" + firstChar + " " + output2.slice(1);
          }
        }
        output2 = output2.replace(regexExcessiveSpaces, function($0, $1, $2) {
          if ($1 && $1.length % 2) {
            return $0;
          }
          return ($1 || "") + $2;
        });
        if (!isIdentifier && options.wrap) {
          return quote + output2 + quote;
        }
        return output2;
      };
      cssesc.options = {
        "escapeEverything": false,
        "isIdentifier": false,
        "quotes": "single",
        "wrap": false
      };
      cssesc.version = "3.0.0";
      module.exports = cssesc;
    }
  });

  // postcss-selector-parser/dist/selectors/className.js
  var require_className = __commonJS({
    "postcss-selector-parser/dist/selectors/className.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _cssesc = _interopRequireDefault(require_cssesc());
      var _util = require_util();
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var ClassName = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(ClassName2, _Node);
        function ClassName2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.CLASS;
          _this._constructed = true;
          return _this;
        }
        var _proto = ClassName2.prototype;
        _proto.valueToString = function valueToString() {
          return "." + _Node.prototype.valueToString.call(this);
        };
        _createClass(ClassName2, [{
          key: "value",
          get: function get() {
            return this._value;
          },
          set: function set(v) {
            if (this._constructed) {
              var escaped = (0, _cssesc["default"])(v, {
                isIdentifier: true
              });
              if (escaped !== v) {
                (0, _util.ensureObject)(this, "raws");
                this.raws.value = escaped;
              } else if (this.raws) {
                delete this.raws.value;
              }
            }
            this._value = v;
          }
        }]);
        return ClassName2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/comment.js
  var require_comment2 = __commonJS({
    "postcss-selector-parser/dist/selectors/comment.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Comment = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(Comment2, _Node);
        function Comment2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.COMMENT;
          return _this;
        }
        return Comment2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/id.js
  var require_id = __commonJS({
    "postcss-selector-parser/dist/selectors/id.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var ID = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(ID2, _Node);
        function ID2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.ID;
          return _this;
        }
        var _proto = ID2.prototype;
        _proto.valueToString = function valueToString() {
          return "#" + _Node.prototype.valueToString.call(this);
        };
        return ID2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/namespace.js
  var require_namespace = __commonJS({
    "postcss-selector-parser/dist/selectors/namespace.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _cssesc = _interopRequireDefault(require_cssesc());
      var _util = require_util();
      var _node = _interopRequireDefault(require_node2());
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Namespace = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(Namespace2, _Node);
        function Namespace2() {
          return _Node.apply(this, arguments) || this;
        }
        var _proto = Namespace2.prototype;
        _proto.qualifiedName = function qualifiedName(value) {
          if (this.namespace) {
            return this.namespaceString + "|" + value;
          } else {
            return value;
          }
        };
        _proto.valueToString = function valueToString() {
          return this.qualifiedName(_Node.prototype.valueToString.call(this));
        };
        _createClass(Namespace2, [{
          key: "namespace",
          get: function get() {
            return this._namespace;
          },
          set: function set(namespace) {
            if (namespace === true || namespace === "*" || namespace === "&") {
              this._namespace = namespace;
              if (this.raws) {
                delete this.raws.namespace;
              }
              return;
            }
            var escaped = (0, _cssesc["default"])(namespace, {
              isIdentifier: true
            });
            this._namespace = namespace;
            if (escaped !== namespace) {
              (0, _util.ensureObject)(this, "raws");
              this.raws.namespace = escaped;
            } else if (this.raws) {
              delete this.raws.namespace;
            }
          }
        }, {
          key: "ns",
          get: function get() {
            return this._namespace;
          },
          set: function set(namespace) {
            this.namespace = namespace;
          }
        }, {
          key: "namespaceString",
          get: function get() {
            if (this.namespace) {
              var ns = this.stringifyProperty("namespace");
              if (ns === true) {
                return "";
              } else {
                return ns;
              }
            } else {
              return "";
            }
          }
        }]);
        return Namespace2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/tag.js
  var require_tag = __commonJS({
    "postcss-selector-parser/dist/selectors/tag.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _namespace = _interopRequireDefault(require_namespace());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Tag = exports["default"] = /* @__PURE__ */ function(_Namespace) {
        _inheritsLoose(Tag2, _Namespace);
        function Tag2(opts) {
          var _this;
          _this = _Namespace.call(this, opts) || this;
          _this.type = _types.TAG;
          return _this;
        }
        return Tag2;
      }(_namespace["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/string.js
  var require_string = __commonJS({
    "postcss-selector-parser/dist/selectors/string.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var String2 = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(String3, _Node);
        function String3(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.STRING;
          return _this;
        }
        return String3;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/pseudo.js
  var require_pseudo = __commonJS({
    "postcss-selector-parser/dist/selectors/pseudo.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _container = _interopRequireDefault(require_container2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Pseudo = exports["default"] = /* @__PURE__ */ function(_Container) {
        _inheritsLoose(Pseudo2, _Container);
        function Pseudo2(opts) {
          var _this;
          _this = _Container.call(this, opts) || this;
          _this.type = _types.PSEUDO;
          return _this;
        }
        var _proto = Pseudo2.prototype;
        _proto._stringify = function _stringify(options, depth, max) {
          var _this2 = this;
          if (depth >= max) {
            throw new Error("Cannot serialize selector: nesting depth exceeds the maximum of " + max + ".");
          }
          var params = this.length ? "(" + this.map(function(child) {
            return _this2._stringifyChild(child, options, depth + 1, max);
          }).join(",") + ")" : "";
          return [this.rawSpaceBefore, this.stringifyProperty("value"), params, this.rawSpaceAfter].join("");
        };
        return Pseudo2;
      }(_container["default"]);
      module.exports = exports.default;
    }
  });

  // util-deprecate/browser.js
  var require_browser = __commonJS({
    "util-deprecate/browser.js"(exports, module) {
      module.exports = deprecate;
      function deprecate(fn, msg) {
        if (config("noDeprecation")) {
          return fn;
        }
        var warned = false;
        function deprecated() {
          if (!warned) {
            if (config("throwDeprecation")) {
              throw new Error(msg);
            } else if (config("traceDeprecation")) {
              console.trace(msg);
            } else {
              console.warn(msg);
            }
            warned = true;
          }
          return fn.apply(this, arguments);
        }
        return deprecated;
      }
      function config(name) {
        try {
          if (!global.localStorage) return false;
        } catch (_) {
          return false;
        }
        var val = global.localStorage[name];
        if (null == val) return false;
        return String(val).toLowerCase() === "true";
      }
    }
  });

  // postcss-selector-parser/dist/selectors/attribute.js
  var require_attribute = __commonJS({
    "postcss-selector-parser/dist/selectors/attribute.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      exports.unescapeValue = unescapeValue;
      var _cssesc = _interopRequireDefault(require_cssesc());
      var _unesc = _interopRequireDefault(require_unesc());
      var _namespace = _interopRequireDefault(require_namespace());
      var _types = require_types();
      var _CSSESC_QUOTE_OPTIONS;
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var deprecate = require_browser();
      var WRAPPED_IN_QUOTES = /^('|")([^]*)\1$/;
      var warnOfDeprecatedValueAssignment = deprecate(function() {
      }, "Assigning an attribute a value containing characters that might need to be escaped is deprecated. Call attribute.setValue() instead.");
      var warnOfDeprecatedQuotedAssignment = deprecate(function() {
      }, "Assigning attr.quoted is deprecated and has no effect. Assign to attr.quoteMark instead.");
      var warnOfDeprecatedConstructor = deprecate(function() {
      }, "Constructing an Attribute selector with a value without specifying quoteMark is deprecated. Note: The value should be unescaped now.");
      function unescapeValue(value) {
        var deprecatedUsage = false;
        var quoteMark = null;
        var unescaped = value;
        var m = unescaped.match(WRAPPED_IN_QUOTES);
        if (m) {
          quoteMark = m[1];
          unescaped = m[2];
        }
        unescaped = (0, _unesc["default"])(unescaped);
        if (unescaped !== value) {
          deprecatedUsage = true;
        }
        return {
          deprecatedUsage,
          unescaped,
          quoteMark
        };
      }
      function handleDeprecatedContructorOpts(opts) {
        if (opts.quoteMark !== void 0) {
          return opts;
        }
        if (opts.value === void 0) {
          return opts;
        }
        warnOfDeprecatedConstructor();
        var _unescapeValue = unescapeValue(opts.value), quoteMark = _unescapeValue.quoteMark, unescaped = _unescapeValue.unescaped;
        if (!opts.raws) {
          opts.raws = {};
        }
        if (opts.raws.value === void 0) {
          opts.raws.value = opts.value;
        }
        opts.value = unescaped;
        opts.quoteMark = quoteMark;
        return opts;
      }
      var Attribute = exports["default"] = /* @__PURE__ */ function(_Namespace) {
        _inheritsLoose(Attribute2, _Namespace);
        function Attribute2(opts) {
          var _this;
          if (opts === void 0) {
            opts = {};
          }
          _this = _Namespace.call(this, handleDeprecatedContructorOpts(opts)) || this;
          _this.type = _types.ATTRIBUTE;
          _this.raws = _this.raws || {};
          Object.defineProperty(_this.raws, "unquoted", {
            get: deprecate(function() {
              return _this.value;
            }, "attr.raws.unquoted is deprecated. Call attr.value instead."),
            set: deprecate(function() {
              return _this.value;
            }, "Setting attr.raws.unquoted is deprecated and has no effect. attr.value is unescaped by default now.")
          });
          _this._constructed = true;
          return _this;
        }
        var _proto = Attribute2.prototype;
        _proto.getQuotedValue = function getQuotedValue(options) {
          if (options === void 0) {
            options = {};
          }
          var quoteMark = this._determineQuoteMark(options);
          var cssescopts = CSSESC_QUOTE_OPTIONS[quoteMark];
          var escaped = (0, _cssesc["default"])(this._value, cssescopts);
          return escaped;
        };
        _proto._determineQuoteMark = function _determineQuoteMark(options) {
          return options.smart ? this.smartQuoteMark(options) : this.preferredQuoteMark(options);
        };
        _proto.setValue = function setValue(value, options) {
          if (options === void 0) {
            options = {};
          }
          this._value = value;
          this._quoteMark = this._determineQuoteMark(options);
          this._syncRawValue();
        };
        _proto.smartQuoteMark = function smartQuoteMark(options) {
          var v = this.value;
          var numSingleQuotes = v.replace(/[^']/g, "").length;
          var numDoubleQuotes = v.replace(/[^"]/g, "").length;
          if (numSingleQuotes + numDoubleQuotes === 0) {
            var escaped = (0, _cssesc["default"])(v, {
              isIdentifier: true
            });
            if (escaped === v) {
              return Attribute2.NO_QUOTE;
            } else {
              var pref = this.preferredQuoteMark(options);
              if (pref === Attribute2.NO_QUOTE) {
                var quote = this.quoteMark || options.quoteMark || Attribute2.DOUBLE_QUOTE;
                var opts = CSSESC_QUOTE_OPTIONS[quote];
                var quoteValue = (0, _cssesc["default"])(v, opts);
                if (quoteValue.length < escaped.length) {
                  return quote;
                }
              }
              return pref;
            }
          } else if (numDoubleQuotes === numSingleQuotes) {
            return this.preferredQuoteMark(options);
          } else if (numDoubleQuotes < numSingleQuotes) {
            return Attribute2.DOUBLE_QUOTE;
          } else {
            return Attribute2.SINGLE_QUOTE;
          }
        };
        _proto.preferredQuoteMark = function preferredQuoteMark(options) {
          var quoteMark = options.preferCurrentQuoteMark ? this.quoteMark : options.quoteMark;
          if (quoteMark === void 0) {
            quoteMark = options.preferCurrentQuoteMark ? options.quoteMark : this.quoteMark;
          }
          if (quoteMark === void 0) {
            quoteMark = Attribute2.DOUBLE_QUOTE;
          }
          return quoteMark;
        };
        _proto._syncRawValue = function _syncRawValue() {
          var rawValue = (0, _cssesc["default"])(this._value, CSSESC_QUOTE_OPTIONS[this.quoteMark]);
          if (rawValue === this._value) {
            if (this.raws) {
              delete this.raws.value;
            }
          } else {
            this.raws.value = rawValue;
          }
        };
        _proto._handleEscapes = function _handleEscapes(prop, value) {
          if (this._constructed) {
            var escaped = (0, _cssesc["default"])(value, {
              isIdentifier: true
            });
            if (escaped !== value) {
              this.raws[prop] = escaped;
            } else {
              delete this.raws[prop];
            }
          }
        };
        _proto._spacesFor = function _spacesFor(name) {
          var attrSpaces = {
            before: "",
            after: ""
          };
          var spaces = this.spaces[name] || {};
          var rawSpaces = this.raws.spaces && this.raws.spaces[name] || {};
          return Object.assign(attrSpaces, spaces, rawSpaces);
        };
        _proto._stringFor = function _stringFor(name, spaceName, concat) {
          if (spaceName === void 0) {
            spaceName = name;
          }
          if (concat === void 0) {
            concat = defaultAttrConcat;
          }
          var attrSpaces = this._spacesFor(spaceName);
          return concat(this.stringifyProperty(name), attrSpaces);
        };
        _proto.offsetOf = function offsetOf(name) {
          var count = 1;
          var attributeSpaces = this._spacesFor("attribute");
          count += attributeSpaces.before.length;
          if (name === "namespace" || name === "ns") {
            return this.namespace ? count : -1;
          }
          if (name === "attributeNS") {
            return count;
          }
          count += this.namespaceString.length;
          if (this.namespace) {
            count += 1;
          }
          if (name === "attribute") {
            return count;
          }
          count += this.stringifyProperty("attribute").length;
          count += attributeSpaces.after.length;
          var operatorSpaces = this._spacesFor("operator");
          count += operatorSpaces.before.length;
          var operator = this.stringifyProperty("operator");
          if (name === "operator") {
            return operator ? count : -1;
          }
          count += operator.length;
          count += operatorSpaces.after.length;
          var valueSpaces = this._spacesFor("value");
          count += valueSpaces.before.length;
          var value = this.stringifyProperty("value");
          if (name === "value") {
            return value ? count : -1;
          }
          count += value.length;
          count += valueSpaces.after.length;
          var insensitiveSpaces = this._spacesFor("insensitive");
          count += insensitiveSpaces.before.length;
          if (name === "insensitive") {
            return this.insensitive ? count : -1;
          }
          return -1;
        };
        _proto.toString = function toString() {
          var _this2 = this;
          var selector = [this.rawSpaceBefore, "["];
          selector.push(this._stringFor("qualifiedAttribute", "attribute"));
          if (this.operator && (this.value || this.value === "")) {
            selector.push(this._stringFor("operator"));
            selector.push(this._stringFor("value"));
            selector.push(this._stringFor("insensitiveFlag", "insensitive", function(attrValue, attrSpaces) {
              if (attrValue.length > 0 && !_this2.quoted && attrSpaces.before.length === 0 && !(_this2.spaces.value && _this2.spaces.value.after)) {
                attrSpaces.before = " ";
              }
              return defaultAttrConcat(attrValue, attrSpaces);
            }));
          }
          selector.push("]");
          selector.push(this.rawSpaceAfter);
          return selector.join("");
        };
        _createClass(Attribute2, [{
          key: "quoted",
          get: function get() {
            var qm = this.quoteMark;
            return qm === "'" || qm === '"';
          },
          set: function set(value) {
            warnOfDeprecatedQuotedAssignment();
          }
          /**
           * returns a single (`'`) or double (`"`) quote character if the value is quoted.
           * returns `null` if the value is not quoted.
           * returns `undefined` if the quotation state is unknown (this can happen when
           * the attribute is constructed without specifying a quote mark.)
           */
        }, {
          key: "quoteMark",
          get: function get() {
            return this._quoteMark;
          },
          set: function set(quoteMark) {
            if (!this._constructed) {
              this._quoteMark = quoteMark;
              return;
            }
            if (this._quoteMark !== quoteMark) {
              this._quoteMark = quoteMark;
              this._syncRawValue();
            }
          }
        }, {
          key: "qualifiedAttribute",
          get: function get() {
            return this.qualifiedName(this.raws.attribute || this.attribute);
          }
        }, {
          key: "insensitiveFlag",
          get: function get() {
            return this.insensitive ? "i" : "";
          }
        }, {
          key: "value",
          get: function get() {
            return this._value;
          },
          set: (
            /**
             * Before 3.0, the value had to be set to an escaped value including any wrapped
             * quote marks. In 3.0, the semantics of `Attribute.value` changed so that the value
             * is unescaped during parsing and any quote marks are removed.
             *
             * Because the ambiguity of this semantic change, if you set `attr.value = newValue`,
             * a deprecation warning is raised when the new value contains any characters that would
             * require escaping (including if it contains wrapped quotes).
             *
             * Instead, you should call `attr.setValue(newValue, opts)` and pass options that describe
             * how the new value is quoted.
             */
            function set(v) {
              if (this._constructed) {
                var _unescapeValue2 = unescapeValue(v), deprecatedUsage = _unescapeValue2.deprecatedUsage, unescaped = _unescapeValue2.unescaped, quoteMark = _unescapeValue2.quoteMark;
                if (deprecatedUsage) {
                  warnOfDeprecatedValueAssignment();
                }
                if (unescaped === this._value && quoteMark === this._quoteMark) {
                  return;
                }
                this._value = unescaped;
                this._quoteMark = quoteMark;
                this._syncRawValue();
              } else {
                this._value = v;
              }
            }
          )
        }, {
          key: "insensitive",
          get: function get() {
            return this._insensitive;
          },
          set: function set(insensitive) {
            if (!insensitive) {
              this._insensitive = false;
              if (this.raws && (this.raws.insensitiveFlag === "I" || this.raws.insensitiveFlag === "i")) {
                this.raws.insensitiveFlag = void 0;
              }
            }
            this._insensitive = insensitive;
          }
        }, {
          key: "attribute",
          get: function get() {
            return this._attribute;
          },
          set: function set(name) {
            this._handleEscapes("attribute", name);
            this._attribute = name;
          }
        }]);
        return Attribute2;
      }(_namespace["default"]);
      Attribute.NO_QUOTE = null;
      Attribute.SINGLE_QUOTE = "'";
      Attribute.DOUBLE_QUOTE = '"';
      var CSSESC_QUOTE_OPTIONS = (_CSSESC_QUOTE_OPTIONS = {
        "'": {
          quotes: "single",
          wrap: true
        },
        '"': {
          quotes: "double",
          wrap: true
        }
      }, _CSSESC_QUOTE_OPTIONS[null] = {
        isIdentifier: true
      }, _CSSESC_QUOTE_OPTIONS);
      function defaultAttrConcat(attrValue, attrSpaces) {
        return "" + attrSpaces.before + attrValue + attrSpaces.after;
      }
    }
  });

  // postcss-selector-parser/dist/selectors/universal.js
  var require_universal = __commonJS({
    "postcss-selector-parser/dist/selectors/universal.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _namespace = _interopRequireDefault(require_namespace());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Universal = exports["default"] = /* @__PURE__ */ function(_Namespace) {
        _inheritsLoose(Universal2, _Namespace);
        function Universal2(opts) {
          var _this;
          _this = _Namespace.call(this, opts) || this;
          _this.type = _types.UNIVERSAL;
          _this.value = "*";
          return _this;
        }
        return Universal2;
      }(_namespace["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/combinator.js
  var require_combinator = __commonJS({
    "postcss-selector-parser/dist/selectors/combinator.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Combinator = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(Combinator2, _Node);
        function Combinator2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.COMBINATOR;
          return _this;
        }
        return Combinator2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/nesting.js
  var require_nesting = __commonJS({
    "postcss-selector-parser/dist/selectors/nesting.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _node = _interopRequireDefault(require_node2());
      var _types = require_types();
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _inheritsLoose(t, o) {
        t.prototype = Object.create(o.prototype), t.prototype.constructor = t, _setPrototypeOf(t, o);
      }
      function _setPrototypeOf(t, e) {
        return _setPrototypeOf = Object.setPrototypeOf ? Object.setPrototypeOf.bind() : function(t2, e2) {
          return t2.__proto__ = e2, t2;
        }, _setPrototypeOf(t, e);
      }
      var Nesting = exports["default"] = /* @__PURE__ */ function(_Node) {
        _inheritsLoose(Nesting2, _Node);
        function Nesting2(opts) {
          var _this;
          _this = _Node.call(this, opts) || this;
          _this.type = _types.NESTING;
          _this.value = "&";
          return _this;
        }
        return Nesting2;
      }(_node["default"]);
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/sortAscending.js
  var require_sortAscending = __commonJS({
    "postcss-selector-parser/dist/sortAscending.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = sortAscending;
      function sortAscending(list) {
        return list.sort(function(a, b) {
          return a - b;
        });
      }
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/tokenTypes.js
  var require_tokenTypes = __commonJS({
    "postcss-selector-parser/dist/tokenTypes.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.word = exports.tilde = exports.tab = exports.str = exports.space = exports.slash = exports.singleQuote = exports.semicolon = exports.plus = exports.pipe = exports.openSquare = exports.openParenthesis = exports.newline = exports.greaterThan = exports.feed = exports.equals = exports.doubleQuote = exports.dollar = exports.cr = exports.comment = exports.comma = exports.combinator = exports.colon = exports.closeSquare = exports.closeParenthesis = exports.caret = exports.bang = exports.backslash = exports.at = exports.asterisk = exports.ampersand = void 0;
      var ampersand = exports.ampersand = 38;
      var asterisk = exports.asterisk = 42;
      var at = exports.at = 64;
      var comma = exports.comma = 44;
      var colon = exports.colon = 58;
      var semicolon = exports.semicolon = 59;
      var openParenthesis = exports.openParenthesis = 40;
      var closeParenthesis = exports.closeParenthesis = 41;
      var openSquare = exports.openSquare = 91;
      var closeSquare = exports.closeSquare = 93;
      var dollar = exports.dollar = 36;
      var tilde = exports.tilde = 126;
      var caret = exports.caret = 94;
      var plus = exports.plus = 43;
      var equals = exports.equals = 61;
      var pipe = exports.pipe = 124;
      var greaterThan = exports.greaterThan = 62;
      var space = exports.space = 32;
      var singleQuote = exports.singleQuote = 39;
      var doubleQuote = exports.doubleQuote = 34;
      var slash = exports.slash = 47;
      var bang = exports.bang = 33;
      var backslash = exports.backslash = 92;
      var cr = exports.cr = 13;
      var feed = exports.feed = 12;
      var newline = exports.newline = 10;
      var tab = exports.tab = 9;
      var str = exports.str = singleQuote;
      var comment = exports.comment = -1;
      var word = exports.word = -2;
      var combinator = exports.combinator = -3;
    }
  });

  // postcss-selector-parser/dist/tokenize.js
  var require_tokenize2 = __commonJS({
    "postcss-selector-parser/dist/tokenize.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.FIELDS = void 0;
      exports["default"] = tokenize;
      var t = _interopRequireWildcard(require_tokenTypes());
      var _unescapable;
      var _wordDelimiters;
      function _interopRequireWildcard(e, t2) {
        if ("function" == typeof WeakMap) var r = /* @__PURE__ */ new WeakMap(), n = /* @__PURE__ */ new WeakMap();
        return (_interopRequireWildcard = function _interopRequireWildcard2(e2, t3) {
          if (!t3 && e2 && e2.__esModule) return e2;
          var o, i2, f = { __proto__: null, "default": e2 };
          if (null === e2 || "object" != typeof e2 && "function" != typeof e2) return f;
          if (o = t3 ? n : r) {
            if (o.has(e2)) return o.get(e2);
            o.set(e2, f);
          }
          for (var _t in e2) {
            "default" !== _t && {}.hasOwnProperty.call(e2, _t) && ((i2 = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e2, _t)) && (i2.get || i2.set) ? o(f, _t, i2) : f[_t] = e2[_t]);
          }
          return f;
        })(e, t2);
      }
      var unescapable = (_unescapable = {}, _unescapable[t.tab] = true, _unescapable[t.newline] = true, _unescapable[t.cr] = true, _unescapable[t.feed] = true, _unescapable);
      var wordDelimiters = (_wordDelimiters = {}, _wordDelimiters[t.space] = true, _wordDelimiters[t.tab] = true, _wordDelimiters[t.newline] = true, _wordDelimiters[t.cr] = true, _wordDelimiters[t.feed] = true, _wordDelimiters[t.ampersand] = true, _wordDelimiters[t.asterisk] = true, _wordDelimiters[t.bang] = true, _wordDelimiters[t.comma] = true, _wordDelimiters[t.colon] = true, _wordDelimiters[t.semicolon] = true, _wordDelimiters[t.openParenthesis] = true, _wordDelimiters[t.closeParenthesis] = true, _wordDelimiters[t.openSquare] = true, _wordDelimiters[t.closeSquare] = true, _wordDelimiters[t.singleQuote] = true, _wordDelimiters[t.doubleQuote] = true, _wordDelimiters[t.plus] = true, _wordDelimiters[t.pipe] = true, _wordDelimiters[t.tilde] = true, _wordDelimiters[t.greaterThan] = true, _wordDelimiters[t.equals] = true, _wordDelimiters[t.dollar] = true, _wordDelimiters[t.caret] = true, _wordDelimiters[t.slash] = true, _wordDelimiters);
      var hex = {};
      var hexChars = "0123456789abcdefABCDEF";
      for (i = 0; i < hexChars.length; i++) {
        hex[hexChars.charCodeAt(i)] = true;
      }
      var i;
      function consumeWord(css, start) {
        var next = start;
        var code;
        do {
          code = css.charCodeAt(next);
          if (wordDelimiters[code]) {
            return next - 1;
          } else if (code === t.backslash) {
            next = consumeEscape(css, next) + 1;
          } else {
            next++;
          }
        } while (next < css.length);
        return next - 1;
      }
      function consumeEscape(css, start) {
        var next = start;
        var code = css.charCodeAt(next + 1);
        if (unescapable[code]) {
        } else if (hex[code]) {
          var hexDigits = 0;
          do {
            next++;
            hexDigits++;
            code = css.charCodeAt(next + 1);
          } while (hex[code] && hexDigits < 6);
          if (hexDigits < 6 && code === t.space) {
            next++;
          }
        } else {
          next++;
        }
        return next;
      }
      var FIELDS = exports.FIELDS = {
        TYPE: 0,
        START_LINE: 1,
        START_COL: 2,
        END_LINE: 3,
        END_COL: 4,
        START_POS: 5,
        END_POS: 6
      };
      function tokenize(input) {
        var tokens = [];
        var css = input.css.valueOf();
        var _css = css, length = _css.length;
        var offset = -1;
        var line = 1;
        var start = 0;
        var end = 0;
        var code, content, endColumn, endLine, escaped, escapePos, last, lines, next, nextLine, nextOffset, quote, tokenType;
        function unclosed(what, fix) {
          if (input.safe) {
            css += fix;
            next = css.length - 1;
          } else {
            throw input.error("Unclosed " + what, line, start - offset, start);
          }
        }
        while (start < length) {
          code = css.charCodeAt(start);
          if (code === t.newline) {
            offset = start;
            line += 1;
          }
          switch (code) {
            case t.space:
            case t.tab:
            case t.newline:
            case t.cr:
            case t.feed:
              next = start;
              do {
                next += 1;
                code = css.charCodeAt(next);
                if (code === t.newline) {
                  offset = next;
                  line += 1;
                }
              } while (code === t.space || code === t.newline || code === t.tab || code === t.cr || code === t.feed);
              tokenType = t.space;
              endLine = line;
              endColumn = next - offset - 1;
              end = next;
              break;
            case t.plus:
            case t.greaterThan:
            case t.tilde:
            case t.pipe:
              next = start;
              do {
                next += 1;
                code = css.charCodeAt(next);
              } while (code === t.plus || code === t.greaterThan || code === t.tilde || code === t.pipe);
              tokenType = t.combinator;
              endLine = line;
              endColumn = start - offset;
              end = next;
              break;
            // Consume these characters as single tokens.
            case t.asterisk:
            case t.ampersand:
            case t.bang:
            case t.comma:
            case t.equals:
            case t.dollar:
            case t.caret:
            case t.openSquare:
            case t.closeSquare:
            case t.colon:
            case t.semicolon:
            case t.openParenthesis:
            case t.closeParenthesis:
              next = start;
              tokenType = code;
              endLine = line;
              endColumn = start - offset;
              end = next + 1;
              break;
            case t.singleQuote:
            case t.doubleQuote:
              quote = code === t.singleQuote ? "'" : '"';
              next = start;
              do {
                escaped = false;
                next = css.indexOf(quote, next + 1);
                if (next === -1) {
                  unclosed("quote", quote);
                }
                escapePos = next;
                while (css.charCodeAt(escapePos - 1) === t.backslash) {
                  escapePos -= 1;
                  escaped = !escaped;
                }
              } while (escaped);
              tokenType = t.str;
              endLine = line;
              endColumn = start - offset;
              end = next + 1;
              break;
            default:
              if (code === t.slash && css.charCodeAt(start + 1) === t.asterisk) {
                next = css.indexOf("*/", start + 2) + 1;
                if (next === 0) {
                  unclosed("comment", "*/");
                }
                content = css.slice(start, next + 1);
                lines = content.split("\n");
                last = lines.length - 1;
                if (last > 0) {
                  nextLine = line + last;
                  nextOffset = next - lines[last].length;
                } else {
                  nextLine = line;
                  nextOffset = offset;
                }
                tokenType = t.comment;
                line = nextLine;
                endLine = nextLine;
                endColumn = next - nextOffset;
              } else if (code === t.slash) {
                next = start;
                tokenType = code;
                endLine = line;
                endColumn = start - offset;
                end = next + 1;
              } else {
                next = consumeWord(css, start);
                tokenType = t.word;
                endLine = line;
                endColumn = next - offset;
              }
              end = next + 1;
              break;
          }
          tokens.push([
            tokenType,
            // [0] Token type
            line,
            // [1] Starting line
            start - offset,
            // [2] Starting column
            endLine,
            // [3] Ending line
            endColumn,
            // [4] Ending column
            start,
            // [5] Start position / Source index
            end
            // [6] End position
          ]);
          if (nextOffset) {
            offset = nextOffset;
            nextOffset = null;
          }
          start = end;
        }
        return tokens;
      }
    }
  });

  // postcss-selector-parser/dist/parser.js
  var require_parser2 = __commonJS({
    "postcss-selector-parser/dist/parser.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _root = _interopRequireDefault(require_root2());
      var _selector = _interopRequireDefault(require_selector());
      var _className = _interopRequireDefault(require_className());
      var _comment = _interopRequireDefault(require_comment2());
      var _id = _interopRequireDefault(require_id());
      var _tag = _interopRequireDefault(require_tag());
      var _string = _interopRequireDefault(require_string());
      var _pseudo = _interopRequireDefault(require_pseudo());
      var _attribute = _interopRequireWildcard(require_attribute());
      var _universal = _interopRequireDefault(require_universal());
      var _combinator = _interopRequireDefault(require_combinator());
      var _nesting = _interopRequireDefault(require_nesting());
      var _sortAscending = _interopRequireDefault(require_sortAscending());
      var _tokenize = _interopRequireWildcard(require_tokenize2());
      var tokens = _interopRequireWildcard(require_tokenTypes());
      var types = _interopRequireWildcard(require_types());
      var _util = require_util();
      var _WHITESPACE_TOKENS;
      var _Object$assign;
      function _interopRequireWildcard(e, t) {
        if ("function" == typeof WeakMap) var r = /* @__PURE__ */ new WeakMap(), n = /* @__PURE__ */ new WeakMap();
        return (_interopRequireWildcard = function _interopRequireWildcard2(e2, t2) {
          if (!t2 && e2 && e2.__esModule) return e2;
          var o, i, f = { __proto__: null, "default": e2 };
          if (null === e2 || "object" != typeof e2 && "function" != typeof e2) return f;
          if (o = t2 ? n : r) {
            if (o.has(e2)) return o.get(e2);
            o.set(e2, f);
          }
          for (var _t in e2) {
            "default" !== _t && {}.hasOwnProperty.call(e2, _t) && ((i = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e2, _t)) && (i.get || i.set) ? o(f, _t, i) : f[_t] = e2[_t]);
          }
          return f;
        })(e, t);
      }
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      function _defineProperties(e, r) {
        for (var t = 0; t < r.length; t++) {
          var o = r[t];
          o.enumerable = o.enumerable || false, o.configurable = true, "value" in o && (o.writable = true), Object.defineProperty(e, _toPropertyKey(o.key), o);
        }
      }
      function _createClass(e, r, t) {
        return r && _defineProperties(e.prototype, r), t && _defineProperties(e, t), Object.defineProperty(e, "prototype", { writable: false }), e;
      }
      function _toPropertyKey(t) {
        var i = _toPrimitive(t, "string");
        return "symbol" == typeof i ? i : i + "";
      }
      function _toPrimitive(t, r) {
        if ("object" != typeof t || !t) return t;
        var e = t[Symbol.toPrimitive];
        if (void 0 !== e) {
          var i = e.call(t, r || "default");
          if ("object" != typeof i) return i;
          throw new TypeError("@@toPrimitive must return a primitive value.");
        }
        return ("string" === r ? String : Number)(t);
      }
      var WHITESPACE_TOKENS = (_WHITESPACE_TOKENS = {}, _WHITESPACE_TOKENS[tokens.space] = true, _WHITESPACE_TOKENS[tokens.cr] = true, _WHITESPACE_TOKENS[tokens.feed] = true, _WHITESPACE_TOKENS[tokens.newline] = true, _WHITESPACE_TOKENS[tokens.tab] = true, _WHITESPACE_TOKENS);
      var WHITESPACE_EQUIV_TOKENS = Object.assign({}, WHITESPACE_TOKENS, (_Object$assign = {}, _Object$assign[tokens.comment] = true, _Object$assign));
      function tokenStart(token) {
        return {
          line: token[_tokenize.FIELDS.START_LINE],
          column: token[_tokenize.FIELDS.START_COL]
        };
      }
      function tokenEnd(token) {
        return {
          line: token[_tokenize.FIELDS.END_LINE],
          column: token[_tokenize.FIELDS.END_COL]
        };
      }
      function getSource(startLine, startColumn, endLine, endColumn) {
        return {
          start: {
            line: startLine,
            column: startColumn
          },
          end: {
            line: endLine,
            column: endColumn
          }
        };
      }
      function getTokenSource(token) {
        return getSource(token[_tokenize.FIELDS.START_LINE], token[_tokenize.FIELDS.START_COL], token[_tokenize.FIELDS.END_LINE], token[_tokenize.FIELDS.END_COL]);
      }
      function getTokenSourceSpan(startToken, endToken) {
        if (!startToken) {
          return void 0;
        }
        return getSource(startToken[_tokenize.FIELDS.START_LINE], startToken[_tokenize.FIELDS.START_COL], endToken[_tokenize.FIELDS.END_LINE], endToken[_tokenize.FIELDS.END_COL]);
      }
      function unescapeProp(node, prop) {
        var value = node[prop];
        if (typeof value !== "string") {
          return;
        }
        if (value.indexOf("\\") !== -1) {
          (0, _util.ensureObject)(node, "raws");
          node[prop] = (0, _util.unesc)(value);
          if (node.raws[prop] === void 0) {
            node.raws[prop] = value;
          }
        }
        return node;
      }
      function indexesOf(array, item) {
        var i = -1;
        var indexes = [];
        while ((i = array.indexOf(item, i + 1)) !== -1) {
          indexes.push(i);
        }
        return indexes;
      }
      function uniqs() {
        var list = Array.prototype.concat.apply([], arguments);
        return list.filter(function(item, i) {
          return i === list.indexOf(item);
        });
      }
      var Parser = exports["default"] = /* @__PURE__ */ function() {
        function Parser2(rule, options) {
          if (options === void 0) {
            options = {};
          }
          this.rule = rule;
          this.options = Object.assign({
            lossy: false,
            safe: false
          }, options);
          this.position = 0;
          this.nestingDepth = 0;
          this.maxNestingDepth = (0, _util.resolveMaxNestingDepth)(this.options.maxNestingDepth);
          this.css = typeof this.rule === "string" ? this.rule : this.rule.selector;
          this.tokens = (0, _tokenize["default"])({
            css: this.css,
            error: this._errorGenerator(),
            safe: this.options.safe
          });
          var rootSource = getTokenSourceSpan(this.tokens[0], this.tokens[this.tokens.length - 1]);
          this.root = new _root["default"]({
            source: rootSource
          });
          this.root.errorGenerator = this._errorGenerator();
          var selector = new _selector["default"]({
            source: {
              start: {
                line: 1,
                column: 1
              }
            },
            sourceIndex: 0
          });
          this.root.append(selector);
          this.current = selector;
          this.loop();
        }
        var _proto = Parser2.prototype;
        _proto._errorGenerator = function _errorGenerator() {
          var _this = this;
          return function(message, errorOptions) {
            if (typeof _this.rule === "string") {
              return new Error(message);
            }
            return _this.rule.error(message, errorOptions);
          };
        };
        _proto.attribute = function attribute() {
          var attr = [];
          var startingToken = this.currToken;
          this.position++;
          while (this.position < this.tokens.length && this.currToken[_tokenize.FIELDS.TYPE] !== tokens.closeSquare) {
            attr.push(this.currToken);
            this.position++;
          }
          if (this.currToken[_tokenize.FIELDS.TYPE] !== tokens.closeSquare) {
            return this.expected("closing square bracket", this.currToken[_tokenize.FIELDS.START_POS]);
          }
          var len = attr.length;
          var node = {
            source: getSource(startingToken[1], startingToken[2], this.currToken[3], this.currToken[4]),
            sourceIndex: startingToken[_tokenize.FIELDS.START_POS]
          };
          if (len === 1 && !~[tokens.word].indexOf(attr[0][_tokenize.FIELDS.TYPE])) {
            return this.expected("attribute", attr[0][_tokenize.FIELDS.START_POS]);
          }
          var pos = 0;
          var spaceBefore = "";
          var commentBefore = "";
          var lastAdded = null;
          var spaceAfterMeaningfulToken = false;
          while (pos < len) {
            var token = attr[pos];
            var content = this.content(token);
            var next = attr[pos + 1];
            switch (token[_tokenize.FIELDS.TYPE]) {
              case tokens.space:
                spaceAfterMeaningfulToken = true;
                if (this.options.lossy) {
                  break;
                }
                if (lastAdded) {
                  (0, _util.ensureObject)(node, "spaces", lastAdded);
                  var prevContent = node.spaces[lastAdded].after || "";
                  node.spaces[lastAdded].after = prevContent + content;
                  var existingComment = (0, _util.getProp)(node, "raws", "spaces", lastAdded, "after") || null;
                  if (existingComment) {
                    node.raws.spaces[lastAdded].after = existingComment + content;
                  }
                } else {
                  spaceBefore = spaceBefore + content;
                  commentBefore = commentBefore + content;
                }
                break;
              case tokens.asterisk:
                if (next[_tokenize.FIELDS.TYPE] === tokens.equals) {
                  node.operator = content;
                  lastAdded = "operator";
                } else if ((!node.namespace || lastAdded === "namespace" && !spaceAfterMeaningfulToken) && next) {
                  if (spaceBefore) {
                    (0, _util.ensureObject)(node, "spaces", "attribute");
                    node.spaces.attribute.before = spaceBefore;
                    spaceBefore = "";
                  }
                  if (commentBefore) {
                    (0, _util.ensureObject)(node, "raws", "spaces", "attribute");
                    node.raws.spaces.attribute.before = spaceBefore;
                    commentBefore = "";
                  }
                  node.namespace = (node.namespace || "") + content;
                  var rawValue = (0, _util.getProp)(node, "raws", "namespace") || null;
                  if (rawValue) {
                    node.raws.namespace += content;
                  }
                  lastAdded = "namespace";
                }
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.dollar:
                if (lastAdded === "value") {
                  var oldRawValue = (0, _util.getProp)(node, "raws", "value");
                  node.value += "$";
                  if (oldRawValue) {
                    node.raws.value = oldRawValue + "$";
                  }
                  break;
                }
              // Falls through
              case tokens.caret:
                if (next[_tokenize.FIELDS.TYPE] === tokens.equals) {
                  node.operator = content;
                  lastAdded = "operator";
                }
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.combinator:
                if (content === "~" && next[_tokenize.FIELDS.TYPE] === tokens.equals) {
                  node.operator = content;
                  lastAdded = "operator";
                }
                if (content !== "|") {
                  spaceAfterMeaningfulToken = false;
                  break;
                }
                if (next[_tokenize.FIELDS.TYPE] === tokens.equals) {
                  node.operator = content;
                  lastAdded = "operator";
                } else if (!node.namespace && !node.attribute) {
                  node.namespace = true;
                }
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.word:
                if (next && this.content(next) === "|" && attr[pos + 2] && attr[pos + 2][_tokenize.FIELDS.TYPE] !== tokens.equals && // this look-ahead probably fails with comment nodes involved.
                !node.operator && !node.namespace) {
                  node.namespace = content;
                  lastAdded = "namespace";
                } else if (!node.attribute || lastAdded === "attribute" && !spaceAfterMeaningfulToken) {
                  if (spaceBefore) {
                    (0, _util.ensureObject)(node, "spaces", "attribute");
                    node.spaces.attribute.before = spaceBefore;
                    spaceBefore = "";
                  }
                  if (commentBefore) {
                    (0, _util.ensureObject)(node, "raws", "spaces", "attribute");
                    node.raws.spaces.attribute.before = commentBefore;
                    commentBefore = "";
                  }
                  node.attribute = (node.attribute || "") + content;
                  var _rawValue = (0, _util.getProp)(node, "raws", "attribute") || null;
                  if (_rawValue) {
                    node.raws.attribute += content;
                  }
                  lastAdded = "attribute";
                } else if (!node.value && node.value !== "" || lastAdded === "value" && !(spaceAfterMeaningfulToken || node.quoteMark)) {
                  var _unescaped = (0, _util.unesc)(content);
                  var _oldRawValue = (0, _util.getProp)(node, "raws", "value") || "";
                  var oldValue = node.value || "";
                  node.value = oldValue + _unescaped;
                  node.quoteMark = null;
                  if (_unescaped !== content || _oldRawValue) {
                    (0, _util.ensureObject)(node, "raws");
                    node.raws.value = (_oldRawValue || oldValue) + content;
                  }
                  lastAdded = "value";
                } else {
                  var insensitive = content === "i" || content === "I";
                  if ((node.value || node.value === "") && (node.quoteMark || spaceAfterMeaningfulToken)) {
                    node.insensitive = insensitive;
                    if (!insensitive || content === "I") {
                      (0, _util.ensureObject)(node, "raws");
                      node.raws.insensitiveFlag = content;
                    }
                    lastAdded = "insensitive";
                    if (spaceBefore) {
                      (0, _util.ensureObject)(node, "spaces", "insensitive");
                      node.spaces.insensitive.before = spaceBefore;
                      spaceBefore = "";
                    }
                    if (commentBefore) {
                      (0, _util.ensureObject)(node, "raws", "spaces", "insensitive");
                      node.raws.spaces.insensitive.before = commentBefore;
                      commentBefore = "";
                    }
                  } else if (node.value || node.value === "") {
                    lastAdded = "value";
                    node.value += content;
                    if (node.raws.value) {
                      node.raws.value += content;
                    }
                  }
                }
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.str:
                if (!node.attribute || !node.operator) {
                  return this.error("Expected an attribute followed by an operator preceding the string.", {
                    index: token[_tokenize.FIELDS.START_POS]
                  });
                }
                var _unescapeValue = (0, _attribute.unescapeValue)(content), unescaped = _unescapeValue.unescaped, quoteMark = _unescapeValue.quoteMark;
                node.value = unescaped;
                node.quoteMark = quoteMark;
                lastAdded = "value";
                (0, _util.ensureObject)(node, "raws");
                node.raws.value = content;
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.equals:
                if (!node.attribute) {
                  return this.expected("attribute", token[_tokenize.FIELDS.START_POS], content);
                }
                if (node.value) {
                  return this.error('Unexpected "=" found; an operator was already defined.', {
                    index: token[_tokenize.FIELDS.START_POS]
                  });
                }
                node.operator = node.operator ? node.operator + content : content;
                lastAdded = "operator";
                spaceAfterMeaningfulToken = false;
                break;
              case tokens.comment:
                if (lastAdded) {
                  if (spaceAfterMeaningfulToken || next && next[_tokenize.FIELDS.TYPE] === tokens.space || lastAdded === "insensitive") {
                    var lastComment = (0, _util.getProp)(node, "spaces", lastAdded, "after") || "";
                    var rawLastComment = (0, _util.getProp)(node, "raws", "spaces", lastAdded, "after") || lastComment;
                    (0, _util.ensureObject)(node, "raws", "spaces", lastAdded);
                    node.raws.spaces[lastAdded].after = rawLastComment + content;
                  } else {
                    var lastValue = node[lastAdded] || "";
                    var rawLastValue = (0, _util.getProp)(node, "raws", lastAdded) || lastValue;
                    (0, _util.ensureObject)(node, "raws");
                    node.raws[lastAdded] = rawLastValue + content;
                  }
                } else {
                  commentBefore = commentBefore + content;
                }
                break;
              default:
                return this.error('Unexpected "' + content + '" found.', {
                  index: token[_tokenize.FIELDS.START_POS]
                });
            }
            pos++;
          }
          unescapeProp(node, "attribute");
          unescapeProp(node, "namespace");
          this.newNode(new _attribute["default"](node));
          this.position++;
        };
        _proto.parseWhitespaceEquivalentTokens = function parseWhitespaceEquivalentTokens(stopPosition) {
          if (stopPosition < 0) {
            stopPosition = this.tokens.length;
          }
          var startPosition = this.position;
          var nodes = [];
          var space = "";
          var lastComment = void 0;
          do {
            if (WHITESPACE_TOKENS[this.currToken[_tokenize.FIELDS.TYPE]]) {
              if (!this.options.lossy) {
                space += this.content();
              }
            } else if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.comment) {
              var spaces = {};
              if (space) {
                spaces.before = space;
                space = "";
              }
              lastComment = new _comment["default"]({
                value: this.content(),
                source: getTokenSource(this.currToken),
                sourceIndex: this.currToken[_tokenize.FIELDS.START_POS],
                spaces
              });
              nodes.push(lastComment);
            }
          } while (++this.position < stopPosition);
          if (space) {
            if (lastComment) {
              lastComment.spaces.after = space;
            } else if (!this.options.lossy) {
              var firstToken = this.tokens[startPosition];
              var lastToken = this.tokens[this.position - 1];
              nodes.push(new _string["default"]({
                value: "",
                source: getSource(firstToken[_tokenize.FIELDS.START_LINE], firstToken[_tokenize.FIELDS.START_COL], lastToken[_tokenize.FIELDS.END_LINE], lastToken[_tokenize.FIELDS.END_COL]),
                sourceIndex: firstToken[_tokenize.FIELDS.START_POS],
                spaces: {
                  before: space,
                  after: ""
                }
              }));
            }
          }
          return nodes;
        };
        _proto.convertWhitespaceNodesToSpace = function convertWhitespaceNodesToSpace(nodes, requiredSpace) {
          var _this2 = this;
          if (requiredSpace === void 0) {
            requiredSpace = false;
          }
          var space = "";
          var rawSpace = "";
          nodes.forEach(function(n) {
            var spaceBefore = _this2.lossySpace(n.spaces.before, requiredSpace);
            var rawSpaceBefore = _this2.lossySpace(n.rawSpaceBefore, requiredSpace);
            space += spaceBefore + _this2.lossySpace(n.spaces.after, requiredSpace && spaceBefore.length === 0);
            rawSpace += spaceBefore + n.value + _this2.lossySpace(n.rawSpaceAfter, requiredSpace && rawSpaceBefore.length === 0);
          });
          if (rawSpace === space) {
            rawSpace = void 0;
          }
          var result = {
            space,
            rawSpace
          };
          return result;
        };
        _proto.isNamedCombinator = function isNamedCombinator(position) {
          if (position === void 0) {
            position = this.position;
          }
          return this.tokens[position + 0] && this.tokens[position + 0][_tokenize.FIELDS.TYPE] === tokens.slash && this.tokens[position + 1] && this.tokens[position + 1][_tokenize.FIELDS.TYPE] === tokens.word && this.tokens[position + 2] && this.tokens[position + 2][_tokenize.FIELDS.TYPE] === tokens.slash;
        };
        _proto.namedCombinator = function namedCombinator() {
          if (this.isNamedCombinator()) {
            var nameRaw = this.content(this.tokens[this.position + 1]);
            var name = (0, _util.unesc)(nameRaw).toLowerCase();
            var raws = {};
            if (name !== nameRaw) {
              raws.value = "/" + nameRaw + "/";
            }
            var node = new _combinator["default"]({
              value: "/" + name + "/",
              source: getSource(this.currToken[_tokenize.FIELDS.START_LINE], this.currToken[_tokenize.FIELDS.START_COL], this.tokens[this.position + 2][_tokenize.FIELDS.END_LINE], this.tokens[this.position + 2][_tokenize.FIELDS.END_COL]),
              sourceIndex: this.currToken[_tokenize.FIELDS.START_POS],
              raws
            });
            this.position = this.position + 3;
            return node;
          } else {
            this.unexpected();
          }
        };
        _proto.combinator = function combinator() {
          var _this3 = this;
          if (this.content() === "|") {
            return this.namespace();
          }
          var nextSigTokenPos = this.locateNextMeaningfulToken(this.position);
          if (nextSigTokenPos < 0 || this.tokens[nextSigTokenPos][_tokenize.FIELDS.TYPE] === tokens.comma || this.tokens[nextSigTokenPos][_tokenize.FIELDS.TYPE] === tokens.closeParenthesis) {
            var nodes = this.parseWhitespaceEquivalentTokens(nextSigTokenPos);
            if (nodes.length > 0) {
              var last = this.current.last;
              if (last) {
                var _this$convertWhitespa = this.convertWhitespaceNodesToSpace(nodes), space = _this$convertWhitespa.space, rawSpace = _this$convertWhitespa.rawSpace;
                if (rawSpace !== void 0) {
                  last.rawSpaceAfter += rawSpace;
                }
                last.spaces.after += space;
              } else {
                nodes.forEach(function(n) {
                  return _this3.newNode(n);
                });
              }
            }
            return;
          }
          var firstToken = this.currToken;
          var spaceOrDescendantSelectorNodes = void 0;
          if (nextSigTokenPos > this.position) {
            spaceOrDescendantSelectorNodes = this.parseWhitespaceEquivalentTokens(nextSigTokenPos);
          }
          var node;
          if (this.isNamedCombinator()) {
            node = this.namedCombinator();
          } else if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.combinator) {
            node = new _combinator["default"]({
              value: this.content(),
              source: getTokenSource(this.currToken),
              sourceIndex: this.currToken[_tokenize.FIELDS.START_POS]
            });
            this.position++;
          } else if (WHITESPACE_TOKENS[this.currToken[_tokenize.FIELDS.TYPE]]) {
          } else if (!spaceOrDescendantSelectorNodes) {
            this.unexpected();
          }
          if (node) {
            if (spaceOrDescendantSelectorNodes) {
              var _this$convertWhitespa2 = this.convertWhitespaceNodesToSpace(spaceOrDescendantSelectorNodes), _space = _this$convertWhitespa2.space, _rawSpace = _this$convertWhitespa2.rawSpace;
              node.spaces.before = _space;
              node.rawSpaceBefore = _rawSpace;
            }
          } else {
            var _this$convertWhitespa3 = this.convertWhitespaceNodesToSpace(spaceOrDescendantSelectorNodes, true), _space2 = _this$convertWhitespa3.space, _rawSpace2 = _this$convertWhitespa3.rawSpace;
            if (!_rawSpace2) {
              _rawSpace2 = _space2;
            }
            var spaces = {};
            var raws = {
              spaces: {}
            };
            if (_space2.endsWith(" ") && _rawSpace2.endsWith(" ")) {
              spaces.before = _space2.slice(0, _space2.length - 1);
              raws.spaces.before = _rawSpace2.slice(0, _rawSpace2.length - 1);
            } else if (_space2.startsWith(" ") && _rawSpace2.startsWith(" ")) {
              spaces.after = _space2.slice(1);
              raws.spaces.after = _rawSpace2.slice(1);
            } else {
              raws.value = _rawSpace2;
            }
            node = new _combinator["default"]({
              value: " ",
              source: getTokenSourceSpan(firstToken, this.tokens[this.position - 1]),
              sourceIndex: firstToken[_tokenize.FIELDS.START_POS],
              spaces,
              raws
            });
          }
          if (this.currToken && this.currToken[_tokenize.FIELDS.TYPE] === tokens.space) {
            node.spaces.after = this.optionalSpace(this.content());
            this.position++;
          }
          return this.newNode(node);
        };
        _proto.comma = function comma() {
          if (this.position === this.tokens.length - 1) {
            this.root.trailingComma = true;
            this.position++;
            return;
          }
          this.current._inferEndPosition();
          var selector = new _selector["default"]({
            source: {
              start: tokenStart(this.tokens[this.position + 1])
            },
            sourceIndex: this.tokens[this.position + 1][_tokenize.FIELDS.START_POS]
          });
          this.current.parent.append(selector);
          this.current = selector;
          this.position++;
        };
        _proto.comment = function comment() {
          var current = this.currToken;
          this.newNode(new _comment["default"]({
            value: this.content(),
            source: getTokenSource(current),
            sourceIndex: current[_tokenize.FIELDS.START_POS]
          }));
          this.position++;
        };
        _proto.error = function error(message, opts) {
          throw this.root.error(message, opts);
        };
        _proto.missingBackslash = function missingBackslash() {
          return this.error("Expected a backslash preceding the semicolon.", {
            index: this.currToken[_tokenize.FIELDS.START_POS]
          });
        };
        _proto.missingParenthesis = function missingParenthesis() {
          return this.expected("opening parenthesis", this.currToken[_tokenize.FIELDS.START_POS]);
        };
        _proto.missingSquareBracket = function missingSquareBracket() {
          return this.expected("opening square bracket", this.currToken[_tokenize.FIELDS.START_POS]);
        };
        _proto.unexpected = function unexpected() {
          return this.error("Unexpected '" + this.content() + "'. Escaping special characters with \\ may help.", this.currToken[_tokenize.FIELDS.START_POS]);
        };
        _proto.unexpectedPipe = function unexpectedPipe() {
          return this.error("Unexpected '|'.", this.currToken[_tokenize.FIELDS.START_POS]);
        };
        _proto.namespace = function namespace() {
          var before = this.prevToken && this.content(this.prevToken) || true;
          if (this.nextToken[_tokenize.FIELDS.TYPE] === tokens.word) {
            this.position++;
            return this.word(before);
          } else if (this.nextToken[_tokenize.FIELDS.TYPE] === tokens.asterisk) {
            this.position++;
            return this.universal(before);
          }
          this.unexpectedPipe();
        };
        _proto.nesting = function nesting() {
          if (this.nextToken) {
            var nextContent = this.content(this.nextToken);
            if (nextContent === "|") {
              this.position++;
              return;
            }
          }
          var current = this.currToken;
          this.newNode(new _nesting["default"]({
            value: this.content(),
            source: getTokenSource(current),
            sourceIndex: current[_tokenize.FIELDS.START_POS]
          }));
          this.position++;
        };
        _proto.parentheses = function parentheses() {
          var last = this.current.last;
          var unbalanced = 1;
          this.position++;
          if (last && last.type === types.PSEUDO) {
            var selector = new _selector["default"]({
              source: {
                start: tokenStart(this.tokens[this.position])
              },
              sourceIndex: this.tokens[this.position][_tokenize.FIELDS.START_POS]
            });
            var cache = this.current;
            last.append(selector);
            this.current = selector;
            this.nestingDepth++;
            try {
              if (this.nestingDepth > this.maxNestingDepth) {
                this.error("Cannot parse selector: nesting depth exceeds the maximum of " + this.maxNestingDepth + ".", {
                  index: this.currToken[_tokenize.FIELDS.START_POS]
                });
              }
              while (this.position < this.tokens.length && unbalanced) {
                if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.openParenthesis) {
                  unbalanced++;
                }
                if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.closeParenthesis) {
                  unbalanced--;
                }
                if (unbalanced) {
                  this.parse();
                } else {
                  this.current.source.end = tokenEnd(this.currToken);
                  this.current.parent.source.end = tokenEnd(this.currToken);
                  this.position++;
                }
              }
            } finally {
              this.nestingDepth--;
            }
            this.current = cache;
          } else {
            var parenStart = this.currToken;
            var parenValue = "(";
            var parenEnd;
            while (this.position < this.tokens.length && unbalanced) {
              if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.openParenthesis) {
                unbalanced++;
              }
              if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.closeParenthesis) {
                unbalanced--;
              }
              parenEnd = this.currToken;
              parenValue += this.parseParenthesisToken(this.currToken);
              this.position++;
            }
            if (last) {
              last.appendToPropertyAndEscape("value", parenValue, parenValue);
            } else {
              this.newNode(new _string["default"]({
                value: parenValue,
                source: getSource(parenStart[_tokenize.FIELDS.START_LINE], parenStart[_tokenize.FIELDS.START_COL], parenEnd[_tokenize.FIELDS.END_LINE], parenEnd[_tokenize.FIELDS.END_COL]),
                sourceIndex: parenStart[_tokenize.FIELDS.START_POS]
              }));
            }
          }
          if (unbalanced) {
            return this.expected("closing parenthesis", this.currToken[_tokenize.FIELDS.START_POS]);
          }
        };
        _proto.pseudo = function pseudo() {
          var _this4 = this;
          var pseudoStr = "";
          var startingToken = this.currToken;
          while (this.currToken && this.currToken[_tokenize.FIELDS.TYPE] === tokens.colon) {
            pseudoStr += this.content();
            this.position++;
          }
          if (!this.currToken) {
            return this.expected(["pseudo-class", "pseudo-element"], this.position - 1);
          }
          if (this.currToken[_tokenize.FIELDS.TYPE] === tokens.word) {
            this.splitWord(false, function(first, length) {
              pseudoStr += first;
              _this4.newNode(new _pseudo["default"]({
                value: pseudoStr,
                source: getTokenSourceSpan(startingToken, _this4.currToken),
                sourceIndex: startingToken[_tokenize.FIELDS.START_POS]
              }));
              if (length > 1 && _this4.nextToken && _this4.nextToken[_tokenize.FIELDS.TYPE] === tokens.openParenthesis) {
                _this4.error("Misplaced parenthesis.", {
                  index: _this4.nextToken[_tokenize.FIELDS.START_POS]
                });
              }
            });
          } else {
            return this.expected(["pseudo-class", "pseudo-element"], this.currToken[_tokenize.FIELDS.START_POS]);
          }
        };
        _proto.space = function space() {
          var content = this.content();
          if (this.position === 0 || this.prevToken[_tokenize.FIELDS.TYPE] === tokens.comma || this.prevToken[_tokenize.FIELDS.TYPE] === tokens.openParenthesis || this.current.nodes.every(function(node) {
            return node.type === "comment";
          })) {
            this.spaces = this.optionalSpace(content);
            this.position++;
          } else if (this.position === this.tokens.length - 1 || this.nextToken[_tokenize.FIELDS.TYPE] === tokens.comma || this.nextToken[_tokenize.FIELDS.TYPE] === tokens.closeParenthesis) {
            this.current.last.spaces.after = this.optionalSpace(content);
            this.position++;
          } else {
            this.combinator();
          }
        };
        _proto.string = function string() {
          var current = this.currToken;
          this.newNode(new _string["default"]({
            value: this.content(),
            source: getTokenSource(current),
            sourceIndex: current[_tokenize.FIELDS.START_POS]
          }));
          this.position++;
        };
        _proto.universal = function universal(namespace) {
          var nextToken = this.nextToken;
          if (nextToken && this.content(nextToken) === "|") {
            this.position++;
            return this.namespace();
          }
          var current = this.currToken;
          this.newNode(new _universal["default"]({
            value: this.content(),
            source: getTokenSource(current),
            sourceIndex: current[_tokenize.FIELDS.START_POS]
          }), namespace);
          this.position++;
        };
        _proto.splitWord = function splitWord(namespace, firstCallback) {
          var _this5 = this;
          var nextToken = this.nextToken;
          var word = this.content();
          while (nextToken && ~[tokens.dollar, tokens.caret, tokens.equals, tokens.word].indexOf(nextToken[_tokenize.FIELDS.TYPE])) {
            this.position++;
            var current = this.content();
            word += current;
            if (current.lastIndexOf("\\") === current.length - 1) {
              var next = this.nextToken;
              if (next && next[_tokenize.FIELDS.TYPE] === tokens.space) {
                word += this.requiredSpace(this.content(next));
                this.position++;
              }
            }
            nextToken = this.nextToken;
          }
          var hasClass = indexesOf(word, ".").filter(function(i) {
            var escapedDot = word[i - 1] === "\\";
            var isKeyframesPercent = /^\d+\.\d+%$/.test(word);
            return !escapedDot && !isKeyframesPercent;
          });
          var hasId = indexesOf(word, "#").filter(function(i) {
            return word[i - 1] !== "\\";
          });
          var interpolations = indexesOf(word, "#{");
          if (interpolations.length) {
            hasId = hasId.filter(function(hashIndex) {
              return !~interpolations.indexOf(hashIndex);
            });
          }
          var indices = (0, _sortAscending["default"])(uniqs([0].concat(hasClass, hasId)));
          indices.forEach(function(ind, i) {
            var index = indices[i + 1] || word.length;
            var value = word.slice(ind, index);
            if (i === 0 && firstCallback) {
              return firstCallback.call(_this5, value, indices.length);
            }
            var node;
            var current2 = _this5.currToken;
            var sourceIndex = current2[_tokenize.FIELDS.START_POS] + indices[i];
            var source = getSource(current2[1], current2[2] + ind, current2[3], current2[2] + (index - 1));
            if (~hasClass.indexOf(ind)) {
              var classNameOpts = {
                value: value.slice(1),
                source,
                sourceIndex
              };
              node = new _className["default"](unescapeProp(classNameOpts, "value"));
            } else if (~hasId.indexOf(ind)) {
              var idOpts = {
                value: value.slice(1),
                source,
                sourceIndex
              };
              node = new _id["default"](unescapeProp(idOpts, "value"));
            } else {
              var tagOpts = {
                value,
                source,
                sourceIndex
              };
              unescapeProp(tagOpts, "value");
              node = new _tag["default"](tagOpts);
            }
            _this5.newNode(node, namespace);
            namespace = null;
          });
          this.position++;
        };
        _proto.word = function word(namespace) {
          var nextToken = this.nextToken;
          if (nextToken && this.content(nextToken) === "|") {
            this.position++;
            return this.namespace();
          }
          return this.splitWord(namespace);
        };
        _proto.loop = function loop() {
          while (this.position < this.tokens.length) {
            this.parse(true);
          }
          this.current._inferEndPosition();
          return this.root;
        };
        _proto.parse = function parse(throwOnParenthesis) {
          switch (this.currToken[_tokenize.FIELDS.TYPE]) {
            case tokens.space:
              this.space();
              break;
            case tokens.comment:
              this.comment();
              break;
            case tokens.openParenthesis:
              this.parentheses();
              break;
            case tokens.closeParenthesis:
              if (throwOnParenthesis) {
                this.missingParenthesis();
              }
              break;
            case tokens.openSquare:
              this.attribute();
              break;
            case tokens.dollar:
            case tokens.caret:
            case tokens.equals:
            case tokens.word:
              this.word();
              break;
            case tokens.colon:
              this.pseudo();
              break;
            case tokens.comma:
              this.comma();
              break;
            case tokens.asterisk:
              this.universal();
              break;
            case tokens.ampersand:
              this.nesting();
              break;
            case tokens.slash:
            case tokens.combinator:
              this.combinator();
              break;
            case tokens.str:
              this.string();
              break;
            // These cases throw; no break needed.
            case tokens.closeSquare:
              this.missingSquareBracket();
            case tokens.semicolon:
              this.missingBackslash();
            default:
              this.unexpected();
          }
        };
        _proto.expected = function expected(description, index, found) {
          if (Array.isArray(description)) {
            var last = description.pop();
            description = description.join(", ") + " or " + last;
          }
          var an = /^[aeiou]/.test(description[0]) ? "an" : "a";
          if (!found) {
            return this.error("Expected " + an + " " + description + ".", {
              index
            });
          }
          return this.error("Expected " + an + " " + description + ', found "' + found + '" instead.', {
            index
          });
        };
        _proto.requiredSpace = function requiredSpace(space) {
          return this.options.lossy ? " " : space;
        };
        _proto.optionalSpace = function optionalSpace(space) {
          return this.options.lossy ? "" : space;
        };
        _proto.lossySpace = function lossySpace(space, required) {
          if (this.options.lossy) {
            return required ? " " : "";
          } else {
            return space;
          }
        };
        _proto.parseParenthesisToken = function parseParenthesisToken(token) {
          var content = this.content(token);
          if (token[_tokenize.FIELDS.TYPE] === tokens.space) {
            return this.requiredSpace(content);
          } else {
            return content;
          }
        };
        _proto.newNode = function newNode(node, namespace) {
          if (namespace) {
            if (/^ +$/.test(namespace)) {
              if (!this.options.lossy) {
                this.spaces = (this.spaces || "") + namespace;
              }
              namespace = true;
            }
            node.namespace = namespace;
            unescapeProp(node, "namespace");
          }
          if (this.spaces) {
            node.spaces.before = this.spaces;
            this.spaces = "";
          }
          return this.current.append(node);
        };
        _proto.content = function content(token) {
          if (token === void 0) {
            token = this.currToken;
          }
          return this.css.slice(token[_tokenize.FIELDS.START_POS], token[_tokenize.FIELDS.END_POS]);
        };
        _proto.locateNextMeaningfulToken = function locateNextMeaningfulToken(startPosition) {
          if (startPosition === void 0) {
            startPosition = this.position + 1;
          }
          var searchPosition = startPosition;
          while (searchPosition < this.tokens.length) {
            if (WHITESPACE_EQUIV_TOKENS[this.tokens[searchPosition][_tokenize.FIELDS.TYPE]]) {
              searchPosition++;
              continue;
            } else {
              return searchPosition;
            }
          }
          return -1;
        };
        _createClass(Parser2, [{
          key: "currToken",
          get: function get() {
            return this.tokens[this.position];
          }
        }, {
          key: "nextToken",
          get: function get() {
            return this.tokens[this.position + 1];
          }
        }, {
          key: "prevToken",
          get: function get() {
            return this.tokens[this.position - 1];
          }
        }]);
        return Parser2;
      }();
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/processor.js
  var require_processor2 = __commonJS({
    "postcss-selector-parser/dist/processor.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _parser = _interopRequireDefault(require_parser2());
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      var Processor = exports["default"] = /* @__PURE__ */ function() {
        function Processor2(func, options) {
          this.func = func || function noop() {
          };
          this.funcRes = null;
          this.options = options;
        }
        var _proto = Processor2.prototype;
        _proto._shouldUpdateSelector = function _shouldUpdateSelector(rule, options) {
          if (options === void 0) {
            options = {};
          }
          var merged = Object.assign({}, this.options, options);
          if (merged.updateSelector === false) {
            return false;
          } else {
            return typeof rule !== "string";
          }
        };
        _proto._isLossy = function _isLossy(options) {
          if (options === void 0) {
            options = {};
          }
          var merged = Object.assign({}, this.options, options);
          if (merged.lossless === false) {
            return true;
          } else {
            return false;
          }
        };
        _proto._root = function _root(rule, options) {
          if (options === void 0) {
            options = {};
          }
          var parser = new _parser["default"](rule, this._parseOptions(options));
          return parser.root;
        };
        _proto._parseOptions = function _parseOptions(options) {
          var merged = Object.assign({}, this.options, options);
          return {
            lossy: this._isLossy(merged),
            maxNestingDepth: merged.maxNestingDepth
          };
        };
        _proto._stringifyOptions = function _stringifyOptions(options) {
          var merged = Object.assign({}, this.options, options);
          return {
            maxNestingDepth: merged.maxNestingDepth
          };
        };
        _proto._run = function _run(rule, options) {
          var _this = this;
          if (options === void 0) {
            options = {};
          }
          return new Promise(function(resolve, reject) {
            try {
              var root = _this._root(rule, options);
              Promise.resolve(_this.func(root)).then(function(transform) {
                var string = void 0;
                if (_this._shouldUpdateSelector(rule, options)) {
                  string = root.toString(_this._stringifyOptions(options));
                  rule.selector = string;
                }
                return {
                  transform,
                  root,
                  string
                };
              }).then(resolve, reject);
            } catch (e) {
              reject(e);
              return;
            }
          });
        };
        _proto._runSync = function _runSync(rule, options) {
          if (options === void 0) {
            options = {};
          }
          var root = this._root(rule, options);
          var transform = this.func(root);
          if (transform && typeof transform.then === "function") {
            throw new Error("Selector processor returned a promise to a synchronous call.");
          }
          var string = void 0;
          if (options.updateSelector && typeof rule !== "string") {
            string = root.toString(this._stringifyOptions(options));
            rule.selector = string;
          }
          return {
            transform,
            root,
            string
          };
        };
        _proto.ast = function ast(rule, options) {
          return this._run(rule, options).then(function(result) {
            return result.root;
          });
        };
        _proto.astSync = function astSync(rule, options) {
          return this._runSync(rule, options).root;
        };
        _proto.transform = function transform(rule, options) {
          return this._run(rule, options).then(function(result) {
            return result.transform;
          });
        };
        _proto.transformSync = function transformSync(rule, options) {
          return this._runSync(rule, options).transform;
        };
        _proto.process = function process2(rule, options) {
          var _this2 = this;
          return this._run(rule, options).then(function(result) {
            return result.string || result.root.toString(_this2._stringifyOptions(options));
          });
        };
        _proto.processSync = function processSync(rule, options) {
          var result = this._runSync(rule, options);
          return result.string || result.root.toString(this._stringifyOptions(options));
        };
        return Processor2;
      }();
      module.exports = exports.default;
    }
  });

  // postcss-selector-parser/dist/selectors/constructors.js
  var require_constructors = __commonJS({
    "postcss-selector-parser/dist/selectors/constructors.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.universal = exports.tag = exports.string = exports.selector = exports.root = exports.pseudo = exports.nesting = exports.id = exports.comment = exports.combinator = exports.className = exports.attribute = void 0;
      var _attribute = _interopRequireDefault(require_attribute());
      var _className = _interopRequireDefault(require_className());
      var _combinator = _interopRequireDefault(require_combinator());
      var _comment = _interopRequireDefault(require_comment2());
      var _id = _interopRequireDefault(require_id());
      var _nesting = _interopRequireDefault(require_nesting());
      var _pseudo = _interopRequireDefault(require_pseudo());
      var _root = _interopRequireDefault(require_root2());
      var _selector = _interopRequireDefault(require_selector());
      var _string = _interopRequireDefault(require_string());
      var _tag = _interopRequireDefault(require_tag());
      var _universal = _interopRequireDefault(require_universal());
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      var attribute = exports.attribute = function attribute2(opts) {
        return new _attribute["default"](opts);
      };
      var className = exports.className = function className2(opts) {
        return new _className["default"](opts);
      };
      var combinator = exports.combinator = function combinator2(opts) {
        return new _combinator["default"](opts);
      };
      var comment = exports.comment = function comment2(opts) {
        return new _comment["default"](opts);
      };
      var id = exports.id = function id2(opts) {
        return new _id["default"](opts);
      };
      var nesting = exports.nesting = function nesting2(opts) {
        return new _nesting["default"](opts);
      };
      var pseudo = exports.pseudo = function pseudo2(opts) {
        return new _pseudo["default"](opts);
      };
      var root = exports.root = function root2(opts) {
        return new _root["default"](opts);
      };
      var selector = exports.selector = function selector2(opts) {
        return new _selector["default"](opts);
      };
      var string = exports.string = function string2(opts) {
        return new _string["default"](opts);
      };
      var tag = exports.tag = function tag2(opts) {
        return new _tag["default"](opts);
      };
      var universal = exports.universal = function universal2(opts) {
        return new _universal["default"](opts);
      };
    }
  });

  // postcss-selector-parser/dist/selectors/guards.js
  var require_guards = __commonJS({
    "postcss-selector-parser/dist/selectors/guards.js"(exports) {
      "use strict";
      exports.__esModule = true;
      exports.isComment = exports.isCombinator = exports.isClassName = exports.isAttribute = void 0;
      exports.isContainer = isContainer;
      exports.isIdentifier = void 0;
      exports.isNamespace = isNamespace;
      exports.isNesting = void 0;
      exports.isNode = isNode;
      exports.isPseudo = void 0;
      exports.isPseudoClass = isPseudoClass;
      exports.isPseudoElement = isPseudoElement;
      exports.isUniversal = exports.isTag = exports.isString = exports.isSelector = exports.isRoot = void 0;
      var _types = require_types();
      var _IS_TYPE;
      var IS_TYPE = (_IS_TYPE = {}, _IS_TYPE[_types.ATTRIBUTE] = true, _IS_TYPE[_types.CLASS] = true, _IS_TYPE[_types.COMBINATOR] = true, _IS_TYPE[_types.COMMENT] = true, _IS_TYPE[_types.ID] = true, _IS_TYPE[_types.NESTING] = true, _IS_TYPE[_types.PSEUDO] = true, _IS_TYPE[_types.ROOT] = true, _IS_TYPE[_types.SELECTOR] = true, _IS_TYPE[_types.STRING] = true, _IS_TYPE[_types.TAG] = true, _IS_TYPE[_types.UNIVERSAL] = true, _IS_TYPE);
      function isNode(node) {
        return typeof node === "object" && IS_TYPE[node.type];
      }
      function isNodeType(type, node) {
        return isNode(node) && node.type === type;
      }
      var isAttribute = exports.isAttribute = isNodeType.bind(null, _types.ATTRIBUTE);
      var isClassName = exports.isClassName = isNodeType.bind(null, _types.CLASS);
      var isCombinator = exports.isCombinator = isNodeType.bind(null, _types.COMBINATOR);
      var isComment = exports.isComment = isNodeType.bind(null, _types.COMMENT);
      var isIdentifier = exports.isIdentifier = isNodeType.bind(null, _types.ID);
      var isNesting = exports.isNesting = isNodeType.bind(null, _types.NESTING);
      var isPseudo = exports.isPseudo = isNodeType.bind(null, _types.PSEUDO);
      var isRoot = exports.isRoot = isNodeType.bind(null, _types.ROOT);
      var isSelector = exports.isSelector = isNodeType.bind(null, _types.SELECTOR);
      var isString = exports.isString = isNodeType.bind(null, _types.STRING);
      var isTag = exports.isTag = isNodeType.bind(null, _types.TAG);
      var isUniversal = exports.isUniversal = isNodeType.bind(null, _types.UNIVERSAL);
      function isPseudoElement(node) {
        return isPseudo(node) && node.value && (node.value.startsWith("::") || node.value.toLowerCase() === ":before" || node.value.toLowerCase() === ":after" || node.value.toLowerCase() === ":first-letter" || node.value.toLowerCase() === ":first-line");
      }
      function isPseudoClass(node) {
        return isPseudo(node) && !isPseudoElement(node);
      }
      function isContainer(node) {
        return !!(isNode(node) && node.walk);
      }
      function isNamespace(node) {
        return isAttribute(node) || isTag(node);
      }
    }
  });

  // postcss-selector-parser/dist/selectors/index.js
  var require_selectors = __commonJS({
    "postcss-selector-parser/dist/selectors/index.js"(exports) {
      "use strict";
      exports.__esModule = true;
      var _types = require_types();
      Object.keys(_types).forEach(function(key) {
        if (key === "default" || key === "__esModule") return;
        if (key in exports && exports[key] === _types[key]) return;
        exports[key] = _types[key];
      });
      var _constructors = require_constructors();
      Object.keys(_constructors).forEach(function(key) {
        if (key === "default" || key === "__esModule") return;
        if (key in exports && exports[key] === _constructors[key]) return;
        exports[key] = _constructors[key];
      });
      var _guards = require_guards();
      Object.keys(_guards).forEach(function(key) {
        if (key === "default" || key === "__esModule") return;
        if (key in exports && exports[key] === _guards[key]) return;
        exports[key] = _guards[key];
      });
    }
  });

  // postcss-selector-parser/dist/index.js
  var require_dist = __commonJS({
    "postcss-selector-parser/dist/index.js"(exports, module) {
      "use strict";
      exports.__esModule = true;
      exports["default"] = void 0;
      var _processor = _interopRequireDefault(require_processor2());
      var selectors = _interopRequireWildcard(require_selectors());
      function _interopRequireWildcard(e, t) {
        if ("function" == typeof WeakMap) var r = /* @__PURE__ */ new WeakMap(), n = /* @__PURE__ */ new WeakMap();
        return (_interopRequireWildcard = function _interopRequireWildcard2(e2, t2) {
          if (!t2 && e2 && e2.__esModule) return e2;
          var o, i, f = { __proto__: null, "default": e2 };
          if (null === e2 || "object" != typeof e2 && "function" != typeof e2) return f;
          if (o = t2 ? n : r) {
            if (o.has(e2)) return o.get(e2);
            o.set(e2, f);
          }
          for (var _t in e2) {
            "default" !== _t && {}.hasOwnProperty.call(e2, _t) && ((i = (o = Object.defineProperty) && Object.getOwnPropertyDescriptor(e2, _t)) && (i.get || i.set) ? o(f, _t, i) : f[_t] = e2[_t]);
          }
          return f;
        })(e, t);
      }
      function _interopRequireDefault(e) {
        return e && e.__esModule ? e : { "default": e };
      }
      var parser = function parser2(processor) {
        return new _processor["default"](processor);
      };
      Object.assign(parser, selectors);
      delete parser.__esModule;
      var _default = exports["default"] = parser;
      module.exports = exports.default;
    }
  });

  // postcss-nested/index.js
  var require_postcss_nested = __commonJS({
    "postcss-nested/index.js"(exports, module) {
      var { AtRule, Rule } = require_postcss();
      var parser = require_dist();
      function parse(rawSelector, rule) {
        let nodes;
        try {
          parser((parsed) => {
            nodes = parsed;
          }).processSync(rawSelector);
        } catch (e) {
          if (rawSelector.includes(":")) {
            throw rule ? rule.error("Missed semicolon") : e;
          } else {
            throw rule ? rule.error(e.message) : e;
          }
        }
        return nodes.at(0);
      }
      function interpolateAmpInSelector(nodes, parent) {
        let replaced = false;
        nodes.each((node) => {
          if (node.type === "nesting") {
            let clonedParent = parent.clone({});
            if (node.value !== "&") {
              node.replaceWith(
                parse(node.value.replace("&", clonedParent.toString()))
              );
            } else {
              node.replaceWith(clonedParent);
            }
            replaced = true;
          } else if ("nodes" in node && node.nodes) {
            if (interpolateAmpInSelector(node, parent)) {
              replaced = true;
            }
          }
        });
        return replaced;
      }
      function mergeSelectors(parent, child) {
        let merged = [];
        parent.selectors.forEach((sel) => {
          let parentNode = parse(sel, parent);
          child.selectors.forEach((selector) => {
            if (!selector) {
              return;
            }
            let node = parse(selector, child);
            let replaced = interpolateAmpInSelector(node, parentNode);
            if (!replaced) {
              node.prepend(parser.combinator({ value: " " }));
              node.prepend(parentNode.clone({}));
            }
            merged.push(node.toString());
          });
        });
        return merged;
      }
      function breakOut(child, after) {
        let prev = child.prev();
        after.after(child);
        while (prev && prev.type === "comment") {
          let nextPrev = prev.prev();
          after.after(prev);
          prev = nextPrev;
        }
        return child;
      }
      function createFnAtruleChilds(bubble) {
        return function atruleChilds(rule, atrule, bubbling, mergeSels = bubbling) {
          let children = [];
          atrule.each((child) => {
            if (child.type === "rule" && bubbling) {
              if (mergeSels) {
                child.selectors = mergeSelectors(rule, child);
              }
            } else if (child.type === "atrule" && child.nodes) {
              if (bubble[child.name]) {
                atruleChilds(rule, child, mergeSels);
              } else if (atrule[rootRuleMergeSel] !== false) {
                children.push(child);
              }
            } else {
              children.push(child);
            }
          });
          if (bubbling) {
            if (children.length) {
              let clone = rule.clone({ nodes: [] });
              for (let child of children) {
                clone.append(child);
              }
              atrule.prepend(clone);
            }
          }
        };
      }
      function pickDeclarations(selector, declarations, after) {
        let parent = new Rule({
          nodes: [],
          selector
        });
        parent.append(declarations);
        after.after(parent);
        return parent;
      }
      function atruleNames(defaults, custom) {
        let list = {};
        for (let name of defaults) {
          list[name] = true;
        }
        if (custom) {
          for (let name of custom) {
            list[name.replace(/^@/, "")] = true;
          }
        }
        return list;
      }
      function parseRootRuleParams(params) {
        params = params.trim();
        let braceBlock = params.match(/^\((.*)\)$/);
        if (!braceBlock) {
          return { selector: params, type: "basic" };
        }
        let bits = braceBlock[1].match(/^(with(?:out)?):(.+)$/);
        if (bits) {
          let allowlist = bits[1] === "with";
          let rules = Object.fromEntries(
            bits[2].trim().split(/\s+/).map((name) => [name, true])
          );
          if (allowlist && rules.all) {
            return { type: "noop" };
          }
          let escapes = (rule) => !!rules[rule];
          if (rules.all) {
            escapes = () => true;
          } else if (allowlist) {
            escapes = (rule) => rule === "all" ? false : !rules[rule];
          }
          return {
            escapes,
            type: "withrules"
          };
        }
        return { type: "unknown" };
      }
      function getAncestorRules(leaf) {
        let lineage = [];
        let parent = leaf.parent;
        while (parent && parent instanceof AtRule) {
          lineage.push(parent);
          parent = parent.parent;
        }
        return lineage;
      }
      function unwrapRootRule(rule) {
        let escapes = rule[rootRuleEscapes];
        if (!escapes) {
          rule.after(rule.nodes);
        } else {
          let nodes = rule.nodes;
          let topEscaped;
          let topEscapedIdx = -1;
          let breakoutLeaf;
          let breakoutRoot;
          let clone;
          let lineage = getAncestorRules(rule);
          lineage.forEach((parent, i) => {
            if (escapes(parent.name)) {
              topEscaped = parent;
              topEscapedIdx = i;
              breakoutRoot = clone;
            } else {
              let oldClone = clone;
              clone = parent.clone({ nodes: [] });
              oldClone && clone.append(oldClone);
              breakoutLeaf = breakoutLeaf || clone;
            }
          });
          if (!topEscaped) {
            rule.after(nodes);
          } else if (!breakoutRoot) {
            topEscaped.after(nodes);
          } else {
            let leaf = breakoutLeaf;
            leaf.append(nodes);
            topEscaped.after(breakoutRoot);
          }
          if (rule.next() && topEscaped) {
            let restRoot;
            lineage.slice(0, topEscapedIdx + 1).forEach((parent, i, arr) => {
              let oldRoot = restRoot;
              restRoot = parent.clone({ nodes: [] });
              oldRoot && restRoot.append(oldRoot);
              let nextSibs = [];
              let _child = arr[i - 1] || rule;
              let next = _child.next();
              while (next) {
                nextSibs.push(next);
                next = next.next();
              }
              restRoot.append(nextSibs);
            });
            restRoot && (breakoutRoot || nodes[nodes.length - 1]).after(restRoot);
          }
        }
        rule.remove();
      }
      var rootRuleMergeSel = Symbol("rootRuleMergeSel");
      var rootRuleEscapes = Symbol("rootRuleEscapes");
      function normalizeRootRule(rule) {
        let { params } = rule;
        let { escapes, selector, type } = parseRootRuleParams(params);
        if (type === "unknown") {
          throw rule.error(
            `Unknown @${rule.name} parameter ${JSON.stringify(params)}`
          );
        }
        if (type === "basic" && selector) {
          let selectorBlock = new Rule({ nodes: rule.nodes, selector });
          rule.removeAll();
          rule.append(selectorBlock);
        }
        rule[rootRuleEscapes] = escapes;
        rule[rootRuleMergeSel] = escapes ? !escapes("all") : type === "noop";
      }
      var hasRootRule = Symbol("hasRootRule");
      module.exports = (opts = {}) => {
        let bubble = atruleNames(
          ["media", "supports", "layer", "container", "starting-style"],
          opts.bubble
        );
        let atruleChilds = createFnAtruleChilds(bubble);
        let unwrap = atruleNames(
          [
            "document",
            "font-face",
            "keyframes",
            "-webkit-keyframes",
            "-moz-keyframes"
          ],
          opts.unwrap
        );
        let rootRuleName = (opts.rootRuleName || "at-root").replace(/^@/, "");
        let preserveEmpty = opts.preserveEmpty;
        return {
          Once(root) {
            root.walkAtRules(rootRuleName, (node) => {
              normalizeRootRule(node);
              root[hasRootRule] = true;
            });
          },
          postcssPlugin: "postcss-nested",
          RootExit(root) {
            if (root[hasRootRule]) {
              root.walkAtRules(rootRuleName, unwrapRootRule);
              root[hasRootRule] = false;
            }
          },
          Rule(rule) {
            let unwrapped = false;
            let after = rule;
            let copyDeclarations = false;
            let declarations = [];
            rule.each((child) => {
              if (child.type === "rule") {
                if (declarations.length) {
                  after = pickDeclarations(rule.selector, declarations, after);
                  declarations = [];
                }
                copyDeclarations = true;
                unwrapped = true;
                child.selectors = mergeSelectors(rule, child);
                after = breakOut(child, after);
              } else if (child.type === "atrule") {
                if (declarations.length) {
                  after = pickDeclarations(rule.selector, declarations, after);
                  declarations = [];
                }
                if (child.name === rootRuleName) {
                  unwrapped = true;
                  atruleChilds(rule, child, true, child[rootRuleMergeSel]);
                  after = breakOut(child, after);
                } else if (bubble[child.name]) {
                  copyDeclarations = true;
                  unwrapped = true;
                  atruleChilds(rule, child, true);
                  after = breakOut(child, after);
                } else if (unwrap[child.name]) {
                  copyDeclarations = true;
                  unwrapped = true;
                  atruleChilds(rule, child, false);
                  after = breakOut(child, after);
                } else if (copyDeclarations) {
                  declarations.push(child);
                }
              } else if (child.type === "decl" && copyDeclarations) {
                declarations.push(child);
              }
            });
            if (declarations.length) {
              after = pickDeclarations(rule.selector, declarations, after);
            }
            if (unwrapped && preserveEmpty !== true) {
              rule.raws.semicolon = true;
              if (rule.nodes.length === 0) rule.remove();
            }
          }
        };
      };
      module.exports.postcss = true;
    }
  });

  // postcss-js/parser.js
  var require_parser3 = __commonJS({
    "postcss-js/parser.js"(exports, module) {
      var postcss2 = require_postcss();
      var IMPORTANT = /\s*!important\s*$/i;
      var UNITLESS = {
        "box-flex": true,
        "box-flex-group": true,
        "column-count": true,
        "flex": true,
        "flex-grow": true,
        "flex-positive": true,
        "flex-shrink": true,
        "flex-negative": true,
        "font-weight": true,
        "line-clamp": true,
        "line-height": true,
        "opacity": true,
        "order": true,
        "orphans": true,
        "tab-size": true,
        "widows": true,
        "z-index": true,
        "zoom": true,
        "fill-opacity": true,
        "stroke-dashoffset": true,
        "stroke-opacity": true,
        "stroke-width": true
      };
      function dashify(str) {
        return str.replace(/([A-Z])/g, "-$1").replace(/^ms-/, "-ms-").toLowerCase();
      }
      function decl(parent, name, value) {
        if (value === false || value === null) return;
        if (!name.startsWith("--")) {
          name = dashify(name);
        }
        if (typeof value === "number") {
          if (value === 0 || UNITLESS[name]) {
            value = value.toString();
          } else {
            value += "px";
          }
        }
        if (name === "css-float") name = "float";
        if (IMPORTANT.test(value)) {
          value = value.replace(IMPORTANT, "");
          parent.push(postcss2.decl({ prop: name, value, important: true }));
        } else {
          parent.push(postcss2.decl({ prop: name, value }));
        }
      }
      function atRule(parent, parts, value) {
        let node = postcss2.atRule({ name: parts[1], params: parts[3] || "" });
        if (typeof value === "object") {
          node.nodes = [];
          parse(value, node);
        }
        parent.push(node);
      }
      function parse(obj, parent) {
        let name, node, value;
        for (name in obj) {
          value = obj[name];
          if (value === null || typeof value === "undefined") {
            continue;
          } else if (name[0] === "@") {
            let parts = name.match(/@(\S+)(\s+([\W\w]*)\s*)?/);
            if (Array.isArray(value)) {
              for (let i of value) {
                atRule(parent, parts, i);
              }
            } else {
              atRule(parent, parts, value);
            }
          } else if (Array.isArray(value)) {
            for (let i of value) {
              decl(parent, name, i);
            }
          } else if (typeof value === "object") {
            node = postcss2.rule({ selector: name });
            parse(value, node);
            parent.push(node);
          } else {
            decl(parent, name, value);
          }
        }
      }
      module.exports = function(obj) {
        let root = postcss2.root();
        parse(obj, root);
        return root;
      };
    }
  });

  // camelcase-css/index-es5.js
  var require_index_es5 = __commonJS({
    "camelcase-css/index-es5.js"(exports, module) {
      "use strict";
      var pattern = /-(\w|$)/g;
      var callback = function callback2(dashChar, char) {
        return char.toUpperCase();
      };
      var camelCaseCSS = function camelCaseCSS2(property) {
        property = property.toLowerCase();
        if (property === "float") {
          return "cssFloat";
        } else if (property.charCodeAt(0) === 45 && property.charCodeAt(1) === 109 && property.charCodeAt(2) === 115 && property.charCodeAt(3) === 45) {
          return property.substr(1).replace(pattern, callback);
        } else {
          return property.replace(pattern, callback);
        }
      };
      module.exports = camelCaseCSS;
    }
  });

  // postcss-js/objectifier.js
  var require_objectifier = __commonJS({
    "postcss-js/objectifier.js"(exports, module) {
      var camelcase = require_index_es5();
      var UNITLESS = {
        boxFlex: true,
        boxFlexGroup: true,
        columnCount: true,
        flex: true,
        flexGrow: true,
        flexPositive: true,
        flexShrink: true,
        flexNegative: true,
        fontWeight: true,
        lineClamp: true,
        lineHeight: true,
        opacity: true,
        order: true,
        orphans: true,
        tabSize: true,
        widows: true,
        zIndex: true,
        zoom: true,
        fillOpacity: true,
        strokeDashoffset: true,
        strokeOpacity: true,
        strokeWidth: true
      };
      function atRule(node) {
        if (typeof node.nodes === "undefined") {
          return true;
        } else {
          return process2(node);
        }
      }
      function process2(node, options = {}) {
        let name;
        let result = {};
        let { stringifyImportant } = options;
        node.each((child) => {
          if (child.type === "atrule") {
            name = "@" + child.name;
            if (child.params) name += " " + child.params;
            if (typeof result[name] === "undefined") {
              result[name] = atRule(child);
            } else if (Array.isArray(result[name])) {
              result[name].push(atRule(child));
            } else {
              result[name] = [result[name], atRule(child)];
            }
          } else if (child.type === "rule") {
            let body = process2(child);
            if (result[child.selector]) {
              for (let i in body) {
                let object = result[child.selector];
                if (stringifyImportant && object[i] && object[i].endsWith("!important")) {
                  if (body[i].endsWith("!important")) {
                    object[i] = body[i];
                  }
                } else {
                  object[i] = body[i];
                }
              }
            } else {
              result[child.selector] = body;
            }
          } else if (child.type === "decl") {
            if (child.prop[0] === "-" && child.prop[1] === "-") {
              name = child.prop;
            } else if (child.parent && child.parent.selector === ":export") {
              name = child.prop;
            } else {
              name = camelcase(child.prop);
            }
            let value = child.value;
            if (!isNaN(child.value) && UNITLESS[name]) {
              value = parseFloat(child.value);
            }
            if (child.important) value += " !important";
            if (typeof result[name] === "undefined") {
              result[name] = value;
            } else if (Array.isArray(result[name])) {
              result[name].push(value);
            } else {
              result[name] = [result[name], value];
            }
          }
        });
        return result;
      }
      module.exports = process2;
    }
  });

  // postcss-js/process-result.js
  var require_process_result = __commonJS({
    "postcss-js/process-result.js"(exports, module) {
      var objectify = require_objectifier();
      module.exports = function processResult(result) {
        if (console && console.warn) {
          result.warnings().forEach((warn) => {
            let source = warn.plugin || "PostCSS";
            console.warn(source + ": " + warn.text);
          });
        }
        return objectify(result.root);
      };
    }
  });

  // postcss-js/async.js
  var require_async = __commonJS({
    "postcss-js/async.js"(exports, module) {
      var postcss2 = require_postcss();
      var parse = require_parser3();
      var processResult = require_process_result();
      module.exports = function async(plugins) {
        let processor = postcss2(plugins);
        return async (input) => {
          let result = await processor.process(input, {
            parser: parse,
            from: void 0
          });
          return processResult(result);
        };
      };
    }
  });

  // postcss-js/sync.js
  var require_sync = __commonJS({
    "postcss-js/sync.js"(exports, module) {
      var postcss2 = require_postcss();
      var parse = require_parser3();
      var processResult = require_process_result();
      module.exports = function(plugins) {
        let processor = postcss2(plugins);
        return (input) => {
          let result = processor.process(input, { parser: parse, from: void 0 });
          return processResult(result);
        };
      };
    }
  });

  // postcss-js/index.js
  var require_postcss_js = __commonJS({
    "postcss-js/index.js"(exports, module) {
      var async = require_async();
      var objectify = require_objectifier();
      var parse = require_parser3();
      var sync = require_sync();
      module.exports = {
        objectify,
        parse,
        async,
        sync
      };
    }
  });

  // tailwindcss/lib/util/parseObjectStyles.js
  var require_parseObjectStyles = __commonJS({
    "tailwindcss/lib/util/parseObjectStyles.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return parseObjectStyles;
        }
      });
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _postcssnested = /* @__PURE__ */ _interop_require_default(require_postcss_nested());
      var _postcssjs = /* @__PURE__ */ _interop_require_default(require_postcss_js());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function parseObjectStyles(styles) {
        if (!Array.isArray(styles)) {
          return parseObjectStyles([
            styles
          ]);
        }
        return styles.flatMap((style) => {
          return (0, _postcss.default)([
            (0, _postcssnested.default)({
              bubble: [
                "screen"
              ]
            })
          ]).process(style, {
            parser: _postcssjs.default
          }).root.nodes;
        });
      }
    }
  });

  // tailwindcss/lib/util/isPlainObject.js
  var require_isPlainObject = __commonJS({
    "tailwindcss/lib/util/isPlainObject.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return isPlainObject;
        }
      });
      function isPlainObject(value) {
        if (Object.prototype.toString.call(value) !== "[object Object]") {
          return false;
        }
        const prototype = Object.getPrototypeOf(value);
        return prototype === null || Object.getPrototypeOf(prototype) === null;
      }
    }
  });

  // tailwindcss/lib/util/prefixSelector.js
  var require_prefixSelector = __commonJS({
    "tailwindcss/lib/util/prefixSelector.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(
        exports,
        /**
        * @template {string | import('postcss-selector-parser').Root} T
        *
        * Prefix all classes in the selector with the given prefix
        *
        * It can take either a string or a selector AST and will return the same type
        *
        * @param {string} prefix
        * @param {T} selector
        * @param {boolean} prependNegative
        * @returns {T}
        */
        "default",
        {
          enumerable: true,
          get: function() {
            return _default;
          }
        }
      );
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _default(prefix, selector, prependNegative = false) {
        if (prefix === "") {
          return selector;
        }
        let ast = typeof selector === "string" ? (0, _postcssselectorparser.default)().astSync(selector) : selector;
        ast.walkClasses((classSelector) => {
          let baseClass = classSelector.value;
          let shouldPlaceNegativeBeforePrefix = prependNegative && baseClass.startsWith("-");
          classSelector.value = shouldPlaceNegativeBeforePrefix ? `-${prefix}${baseClass.slice(1)}` : `${prefix}${baseClass}`;
        });
        return typeof selector === "string" ? ast.toString() : ast;
      }
    }
  });

  // tailwindcss/lib/util/escapeCommas.js
  var require_escapeCommas = __commonJS({
    "tailwindcss/lib/util/escapeCommas.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return escapeCommas;
        }
      });
      function escapeCommas(className) {
        return className.replace(/\\,/g, "\\2c ");
      }
    }
  });

  // tailwindcss/lib/util/colorNames.js
  var require_colorNames = __commonJS({
    "tailwindcss/lib/util/colorNames.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _default = {
        aliceblue: [
          240,
          248,
          255
        ],
        antiquewhite: [
          250,
          235,
          215
        ],
        aqua: [
          0,
          255,
          255
        ],
        aquamarine: [
          127,
          255,
          212
        ],
        azure: [
          240,
          255,
          255
        ],
        beige: [
          245,
          245,
          220
        ],
        bisque: [
          255,
          228,
          196
        ],
        black: [
          0,
          0,
          0
        ],
        blanchedalmond: [
          255,
          235,
          205
        ],
        blue: [
          0,
          0,
          255
        ],
        blueviolet: [
          138,
          43,
          226
        ],
        brown: [
          165,
          42,
          42
        ],
        burlywood: [
          222,
          184,
          135
        ],
        cadetblue: [
          95,
          158,
          160
        ],
        chartreuse: [
          127,
          255,
          0
        ],
        chocolate: [
          210,
          105,
          30
        ],
        coral: [
          255,
          127,
          80
        ],
        cornflowerblue: [
          100,
          149,
          237
        ],
        cornsilk: [
          255,
          248,
          220
        ],
        crimson: [
          220,
          20,
          60
        ],
        cyan: [
          0,
          255,
          255
        ],
        darkblue: [
          0,
          0,
          139
        ],
        darkcyan: [
          0,
          139,
          139
        ],
        darkgoldenrod: [
          184,
          134,
          11
        ],
        darkgray: [
          169,
          169,
          169
        ],
        darkgreen: [
          0,
          100,
          0
        ],
        darkgrey: [
          169,
          169,
          169
        ],
        darkkhaki: [
          189,
          183,
          107
        ],
        darkmagenta: [
          139,
          0,
          139
        ],
        darkolivegreen: [
          85,
          107,
          47
        ],
        darkorange: [
          255,
          140,
          0
        ],
        darkorchid: [
          153,
          50,
          204
        ],
        darkred: [
          139,
          0,
          0
        ],
        darksalmon: [
          233,
          150,
          122
        ],
        darkseagreen: [
          143,
          188,
          143
        ],
        darkslateblue: [
          72,
          61,
          139
        ],
        darkslategray: [
          47,
          79,
          79
        ],
        darkslategrey: [
          47,
          79,
          79
        ],
        darkturquoise: [
          0,
          206,
          209
        ],
        darkviolet: [
          148,
          0,
          211
        ],
        deeppink: [
          255,
          20,
          147
        ],
        deepskyblue: [
          0,
          191,
          255
        ],
        dimgray: [
          105,
          105,
          105
        ],
        dimgrey: [
          105,
          105,
          105
        ],
        dodgerblue: [
          30,
          144,
          255
        ],
        firebrick: [
          178,
          34,
          34
        ],
        floralwhite: [
          255,
          250,
          240
        ],
        forestgreen: [
          34,
          139,
          34
        ],
        fuchsia: [
          255,
          0,
          255
        ],
        gainsboro: [
          220,
          220,
          220
        ],
        ghostwhite: [
          248,
          248,
          255
        ],
        gold: [
          255,
          215,
          0
        ],
        goldenrod: [
          218,
          165,
          32
        ],
        gray: [
          128,
          128,
          128
        ],
        green: [
          0,
          128,
          0
        ],
        greenyellow: [
          173,
          255,
          47
        ],
        grey: [
          128,
          128,
          128
        ],
        honeydew: [
          240,
          255,
          240
        ],
        hotpink: [
          255,
          105,
          180
        ],
        indianred: [
          205,
          92,
          92
        ],
        indigo: [
          75,
          0,
          130
        ],
        ivory: [
          255,
          255,
          240
        ],
        khaki: [
          240,
          230,
          140
        ],
        lavender: [
          230,
          230,
          250
        ],
        lavenderblush: [
          255,
          240,
          245
        ],
        lawngreen: [
          124,
          252,
          0
        ],
        lemonchiffon: [
          255,
          250,
          205
        ],
        lightblue: [
          173,
          216,
          230
        ],
        lightcoral: [
          240,
          128,
          128
        ],
        lightcyan: [
          224,
          255,
          255
        ],
        lightgoldenrodyellow: [
          250,
          250,
          210
        ],
        lightgray: [
          211,
          211,
          211
        ],
        lightgreen: [
          144,
          238,
          144
        ],
        lightgrey: [
          211,
          211,
          211
        ],
        lightpink: [
          255,
          182,
          193
        ],
        lightsalmon: [
          255,
          160,
          122
        ],
        lightseagreen: [
          32,
          178,
          170
        ],
        lightskyblue: [
          135,
          206,
          250
        ],
        lightslategray: [
          119,
          136,
          153
        ],
        lightslategrey: [
          119,
          136,
          153
        ],
        lightsteelblue: [
          176,
          196,
          222
        ],
        lightyellow: [
          255,
          255,
          224
        ],
        lime: [
          0,
          255,
          0
        ],
        limegreen: [
          50,
          205,
          50
        ],
        linen: [
          250,
          240,
          230
        ],
        magenta: [
          255,
          0,
          255
        ],
        maroon: [
          128,
          0,
          0
        ],
        mediumaquamarine: [
          102,
          205,
          170
        ],
        mediumblue: [
          0,
          0,
          205
        ],
        mediumorchid: [
          186,
          85,
          211
        ],
        mediumpurple: [
          147,
          112,
          219
        ],
        mediumseagreen: [
          60,
          179,
          113
        ],
        mediumslateblue: [
          123,
          104,
          238
        ],
        mediumspringgreen: [
          0,
          250,
          154
        ],
        mediumturquoise: [
          72,
          209,
          204
        ],
        mediumvioletred: [
          199,
          21,
          133
        ],
        midnightblue: [
          25,
          25,
          112
        ],
        mintcream: [
          245,
          255,
          250
        ],
        mistyrose: [
          255,
          228,
          225
        ],
        moccasin: [
          255,
          228,
          181
        ],
        navajowhite: [
          255,
          222,
          173
        ],
        navy: [
          0,
          0,
          128
        ],
        oldlace: [
          253,
          245,
          230
        ],
        olive: [
          128,
          128,
          0
        ],
        olivedrab: [
          107,
          142,
          35
        ],
        orange: [
          255,
          165,
          0
        ],
        orangered: [
          255,
          69,
          0
        ],
        orchid: [
          218,
          112,
          214
        ],
        palegoldenrod: [
          238,
          232,
          170
        ],
        palegreen: [
          152,
          251,
          152
        ],
        paleturquoise: [
          175,
          238,
          238
        ],
        palevioletred: [
          219,
          112,
          147
        ],
        papayawhip: [
          255,
          239,
          213
        ],
        peachpuff: [
          255,
          218,
          185
        ],
        peru: [
          205,
          133,
          63
        ],
        pink: [
          255,
          192,
          203
        ],
        plum: [
          221,
          160,
          221
        ],
        powderblue: [
          176,
          224,
          230
        ],
        purple: [
          128,
          0,
          128
        ],
        rebeccapurple: [
          102,
          51,
          153
        ],
        red: [
          255,
          0,
          0
        ],
        rosybrown: [
          188,
          143,
          143
        ],
        royalblue: [
          65,
          105,
          225
        ],
        saddlebrown: [
          139,
          69,
          19
        ],
        salmon: [
          250,
          128,
          114
        ],
        sandybrown: [
          244,
          164,
          96
        ],
        seagreen: [
          46,
          139,
          87
        ],
        seashell: [
          255,
          245,
          238
        ],
        sienna: [
          160,
          82,
          45
        ],
        silver: [
          192,
          192,
          192
        ],
        skyblue: [
          135,
          206,
          235
        ],
        slateblue: [
          106,
          90,
          205
        ],
        slategray: [
          112,
          128,
          144
        ],
        slategrey: [
          112,
          128,
          144
        ],
        snow: [
          255,
          250,
          250
        ],
        springgreen: [
          0,
          255,
          127
        ],
        steelblue: [
          70,
          130,
          180
        ],
        tan: [
          210,
          180,
          140
        ],
        teal: [
          0,
          128,
          128
        ],
        thistle: [
          216,
          191,
          216
        ],
        tomato: [
          255,
          99,
          71
        ],
        turquoise: [
          64,
          224,
          208
        ],
        violet: [
          238,
          130,
          238
        ],
        wheat: [
          245,
          222,
          179
        ],
        white: [
          255,
          255,
          255
        ],
        whitesmoke: [
          245,
          245,
          245
        ],
        yellow: [
          255,
          255,
          0
        ],
        yellowgreen: [
          154,
          205,
          50
        ]
      };
    }
  });

  // tailwindcss/lib/util/color.js
  var require_color = __commonJS({
    "tailwindcss/lib/util/color.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        parseColor: function() {
          return parseColor;
        },
        formatColor: function() {
          return formatColor;
        }
      });
      var _colorNames = /* @__PURE__ */ _interop_require_default(require_colorNames());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var HEX = /^#([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})?$/i;
      var SHORT_HEX = /^#([a-f\d])([a-f\d])([a-f\d])([a-f\d])?$/i;
      var VALUE = /(?:\d+|\d*\.\d+)%?/;
      var SEP = /(?:\s*,\s*|\s+)/;
      var ALPHA_SEP = /\s*[,/]\s*/;
      var CUSTOM_PROPERTY = /var\(--(?:[^ )]*?)(?:,(?:[^ )]*?|var\(--[^ )]*?\)))?\)/;
      var RGB = new RegExp(`^(rgba?)\\(\\s*(${VALUE.source}|${CUSTOM_PROPERTY.source})(?:${SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?(?:${SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?(?:${ALPHA_SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?\\s*\\)$`);
      var HSL = new RegExp(`^(hsla?)\\(\\s*((?:${VALUE.source})(?:deg|rad|grad|turn)?|${CUSTOM_PROPERTY.source})(?:${SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?(?:${SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?(?:${ALPHA_SEP.source}(${VALUE.source}|${CUSTOM_PROPERTY.source}))?\\s*\\)$`);
      function parseColor(value, { loose = false } = {}) {
        var _match_, _match__toString;
        if (typeof value !== "string") {
          return null;
        }
        value = value.trim();
        if (value === "transparent") {
          return {
            mode: "rgb",
            color: [
              "0",
              "0",
              "0"
            ],
            alpha: "0"
          };
        }
        if (value in _colorNames.default) {
          return {
            mode: "rgb",
            color: _colorNames.default[value].map((v) => v.toString())
          };
        }
        let hex = value.replace(SHORT_HEX, (_, r, g, b, a) => [
          "#",
          r,
          r,
          g,
          g,
          b,
          b,
          a ? a + a : ""
        ].join("")).match(HEX);
        if (hex !== null) {
          return {
            mode: "rgb",
            color: [
              parseInt(hex[1], 16),
              parseInt(hex[2], 16),
              parseInt(hex[3], 16)
            ].map((v) => v.toString()),
            alpha: hex[4] ? (parseInt(hex[4], 16) / 255).toString() : void 0
          };
        }
        var _value_match;
        let match = (_value_match = value.match(RGB)) !== null && _value_match !== void 0 ? _value_match : value.match(HSL);
        if (match === null) {
          return null;
        }
        let color = [
          match[2],
          match[3],
          match[4]
        ].filter(Boolean).map((v) => v.toString());
        if (color.length === 2 && color[0].startsWith("var(")) {
          return {
            mode: match[1],
            color: [
              color[0]
            ],
            alpha: color[1]
          };
        }
        if (!loose && color.length !== 3) {
          return null;
        }
        if (color.length < 3 && !color.some((part) => /^var\(.*?\)$/.test(part))) {
          return null;
        }
        return {
          mode: match[1],
          color,
          alpha: (_match_ = match[5]) === null || _match_ === void 0 ? void 0 : (_match__toString = _match_.toString) === null || _match__toString === void 0 ? void 0 : _match__toString.call(_match_)
        };
      }
      function formatColor({ mode, color, alpha }) {
        let hasAlpha = alpha !== void 0;
        if (mode === "rgba" || mode === "hsla") {
          return `${mode}(${color.join(", ")}${hasAlpha ? `, ${alpha}` : ""})`;
        }
        return `${mode}(${color.join(" ")}${hasAlpha ? ` / ${alpha}` : ""})`;
      }
    }
  });

  // tailwindcss/lib/util/withAlphaVariable.js
  var require_withAlphaVariable = __commonJS({
    "tailwindcss/lib/util/withAlphaVariable.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        withAlphaValue: function() {
          return withAlphaValue;
        },
        default: function() {
          return withAlphaVariable;
        }
      });
      var _color = require_color();
      function withAlphaValue(color, alphaValue, defaultValue) {
        if (typeof color === "function") {
          return color({
            opacityValue: alphaValue
          });
        }
        let parsed = (0, _color.parseColor)(color, {
          loose: true
        });
        if (parsed === null) {
          return defaultValue;
        }
        return (0, _color.formatColor)({
          ...parsed,
          alpha: alphaValue
        });
      }
      function withAlphaVariable({ color, property, variable }) {
        let properties = [].concat(property);
        if (typeof color === "function") {
          return {
            [variable]: "1",
            ...Object.fromEntries(properties.map((p) => {
              return [
                p,
                color({
                  opacityVariable: variable,
                  opacityValue: `var(${variable}, 1)`
                })
              ];
            }))
          };
        }
        const parsed = (0, _color.parseColor)(color);
        if (parsed === null) {
          return Object.fromEntries(properties.map((p) => [
            p,
            color
          ]));
        }
        if (parsed.alpha !== void 0) {
          return Object.fromEntries(properties.map((p) => [
            p,
            color
          ]));
        }
        return {
          [variable]: "1",
          ...Object.fromEntries(properties.map((p) => {
            return [
              p,
              (0, _color.formatColor)({
                ...parsed,
                alpha: `var(${variable}, 1)`
              })
            ];
          }))
        };
      }
    }
  });

  // tailwindcss/lib/util/splitAtTopLevelOnly.js
  var require_splitAtTopLevelOnly = __commonJS({
    "tailwindcss/lib/util/splitAtTopLevelOnly.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "splitAtTopLevelOnly", {
        enumerable: true,
        get: function() {
          return splitAtTopLevelOnly;
        }
      });
      function splitAtTopLevelOnly(input, separator) {
        let stack = [];
        let parts = [];
        let lastPos = 0;
        let isEscaped = false;
        for (let idx = 0; idx < input.length; idx++) {
          let char = input[idx];
          if (stack.length === 0 && char === separator[0] && !isEscaped) {
            if (separator.length === 1 || input.slice(idx, idx + separator.length) === separator) {
              parts.push(input.slice(lastPos, idx));
              lastPos = idx + separator.length;
            }
          }
          isEscaped = isEscaped ? false : char === "\\";
          if (char === "(" || char === "[" || char === "{") {
            stack.push(char);
          } else if (char === ")" && stack[stack.length - 1] === "(" || char === "]" && stack[stack.length - 1] === "[" || char === "}" && stack[stack.length - 1] === "{") {
            stack.pop();
          }
        }
        parts.push(input.slice(lastPos));
        return parts;
      }
    }
  });

  // tailwindcss/lib/util/parseBoxShadowValue.js
  var require_parseBoxShadowValue = __commonJS({
    "tailwindcss/lib/util/parseBoxShadowValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        parseBoxShadowValue: function() {
          return parseBoxShadowValue;
        },
        formatBoxShadowValue: function() {
          return formatBoxShadowValue;
        }
      });
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      var KEYWORDS = /* @__PURE__ */ new Set([
        "inset",
        "inherit",
        "initial",
        "revert",
        "unset"
      ]);
      var SPACE = /\ +(?![^(]*\))/g;
      var LENGTH = /^-?(\d+|\.\d+)(.*?)$/g;
      function parseBoxShadowValue(input) {
        let shadows = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(input, ",");
        return shadows.map((shadow) => {
          let value = shadow.trim();
          let result = {
            raw: value
          };
          let parts = value.split(SPACE);
          let seen = /* @__PURE__ */ new Set();
          for (let part of parts) {
            LENGTH.lastIndex = 0;
            if (!seen.has("KEYWORD") && KEYWORDS.has(part)) {
              result.keyword = part;
              seen.add("KEYWORD");
            } else if (LENGTH.test(part)) {
              if (!seen.has("X")) {
                result.x = part;
                seen.add("X");
              } else if (!seen.has("Y")) {
                result.y = part;
                seen.add("Y");
              } else if (!seen.has("BLUR")) {
                result.blur = part;
                seen.add("BLUR");
              } else if (!seen.has("SPREAD")) {
                result.spread = part;
                seen.add("SPREAD");
              }
            } else {
              if (!result.color) {
                result.color = part;
              } else {
                if (!result.unknown) result.unknown = [];
                result.unknown.push(part);
              }
            }
          }
          result.valid = result.x !== void 0 && result.y !== void 0;
          return result;
        });
      }
      function formatBoxShadowValue(shadows) {
        return shadows.map((shadow) => {
          if (!shadow.valid) {
            return shadow.raw;
          }
          return [
            shadow.keyword,
            shadow.x,
            shadow.y,
            shadow.blur,
            shadow.spread,
            shadow.color
          ].filter(Boolean).join(" ");
        }).join(", ");
      }
    }
  });

  // tailwindcss/lib/util/dataTypes.js
  var require_dataTypes = __commonJS({
    "tailwindcss/lib/util/dataTypes.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        normalize: function() {
          return normalize;
        },
        normalizeAttributeSelectors: function() {
          return normalizeAttributeSelectors;
        },
        url: function() {
          return url;
        },
        number: function() {
          return number;
        },
        percentage: function() {
          return percentage;
        },
        length: function() {
          return length;
        },
        lineWidth: function() {
          return lineWidth;
        },
        shadow: function() {
          return shadow;
        },
        color: function() {
          return color;
        },
        image: function() {
          return image;
        },
        gradient: function() {
          return gradient;
        },
        position: function() {
          return position;
        },
        familyName: function() {
          return familyName;
        },
        genericName: function() {
          return genericName;
        },
        absoluteSize: function() {
          return absoluteSize;
        },
        relativeSize: function() {
          return relativeSize;
        }
      });
      var _color = require_color();
      var _parseBoxShadowValue = require_parseBoxShadowValue();
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      var cssFunctions = [
        "min",
        "max",
        "clamp",
        "calc"
      ];
      function isCSSFunction(value) {
        return cssFunctions.some((fn) => new RegExp(`^${fn}\\(.*\\)`).test(value));
      }
      var AUTO_VAR_INJECTION_EXCEPTIONS = /* @__PURE__ */ new Set([
        // Concrete properties
        "scroll-timeline-name",
        "timeline-scope",
        "view-timeline-name",
        "font-palette",
        "anchor-name",
        "anchor-scope",
        "position-anchor",
        "position-try-options",
        // Shorthand properties
        "scroll-timeline",
        "animation-timeline",
        "view-timeline",
        "position-try"
      ]);
      function normalize(value, context2 = null, isRoot = true) {
        let isVarException = context2 && AUTO_VAR_INJECTION_EXCEPTIONS.has(context2.property);
        if (value.startsWith("--") && !isVarException) {
          return `var(${value})`;
        }
        if (value.includes("url(")) {
          return value.split(/(url\(.*?\))/g).filter(Boolean).map((part) => {
            if (/^url\(.*?\)$/.test(part)) {
              return part;
            }
            return normalize(part, context2, false);
          }).join("");
        }
        value = value.replace(/([^\\])_+/g, (fullMatch, characterBefore) => characterBefore + " ".repeat(fullMatch.length - 1)).replace(/^_/g, " ").replace(/\\_/g, "_");
        if (isRoot) {
          value = value.trim();
        }
        value = normalizeMathOperatorSpacing(value);
        return value;
      }
      function normalizeAttributeSelectors(value) {
        if (value.includes("=")) {
          value = value.replace(/(=.*)/g, (_fullMatch, match) => {
            if (match[1] === "'" || match[1] === '"') {
              return match;
            }
            if (match.length > 2) {
              let trailingCharacter = match[match.length - 1];
              if (match[match.length - 2] === " " && (trailingCharacter === "i" || trailingCharacter === "I" || trailingCharacter === "s" || trailingCharacter === "S")) {
                return `="${match.slice(1, -2)}" ${match[match.length - 1]}`;
              }
            }
            return `="${match.slice(1)}"`;
          });
        }
        return value;
      }
      function normalizeMathOperatorSpacing(value) {
        let preventFormattingInFunctions = [
          "theme"
        ];
        let preventFormattingKeywords = [
          "min-content",
          "max-content",
          "fit-content",
          // Env
          "safe-area-inset-top",
          "safe-area-inset-right",
          "safe-area-inset-bottom",
          "safe-area-inset-left",
          "titlebar-area-x",
          "titlebar-area-y",
          "titlebar-area-width",
          "titlebar-area-height",
          "keyboard-inset-top",
          "keyboard-inset-right",
          "keyboard-inset-bottom",
          "keyboard-inset-left",
          "keyboard-inset-width",
          "keyboard-inset-height",
          "radial-gradient",
          "linear-gradient",
          "conic-gradient",
          "repeating-radial-gradient",
          "repeating-linear-gradient",
          "repeating-conic-gradient",
          "anchor-size"
        ];
        return value.replace(/(calc|min|max|clamp)\(.+\)/g, (match) => {
          let result = "";
          function lastChar() {
            let char = result.trimEnd();
            return char[char.length - 1];
          }
          for (let i = 0; i < match.length; i++) {
            let peek = function(word) {
              return word.split("").every((char2, j) => match[i + j] === char2);
            }, consumeUntil = function(chars) {
              let minIndex = Infinity;
              for (let char2 of chars) {
                let index = match.indexOf(char2, i);
                if (index !== -1 && index < minIndex) {
                  minIndex = index;
                }
              }
              let result2 = match.slice(i, minIndex);
              i += result2.length - 1;
              return result2;
            };
            let char = match[i];
            if (peek("var")) {
              result += consumeUntil([
                ")",
                ","
              ]);
            } else if (preventFormattingKeywords.some((keyword) => peek(keyword))) {
              let keyword = preventFormattingKeywords.find((keyword2) => peek(keyword2));
              result += keyword;
              i += keyword.length - 1;
            } else if (preventFormattingInFunctions.some((fn) => peek(fn))) {
              result += consumeUntil([
                ")"
              ]);
            } else if (peek("[")) {
              result += consumeUntil([
                "]"
              ]);
            } else if ([
              "+",
              "-",
              "*",
              "/"
            ].includes(char) && ![
              "(",
              "+",
              "-",
              "*",
              "/",
              ","
            ].includes(lastChar())) {
              result += ` ${char} `;
            } else {
              result += char;
            }
          }
          return result.replace(/\s+/g, " ");
        });
      }
      function url(value) {
        return value.startsWith("url(");
      }
      function number(value) {
        return !isNaN(Number(value)) || isCSSFunction(value);
      }
      function percentage(value) {
        return value.endsWith("%") && number(value.slice(0, -1)) || isCSSFunction(value);
      }
      var lengthUnits = [
        "cm",
        "mm",
        "Q",
        "in",
        "pc",
        "pt",
        "px",
        "em",
        "ex",
        "ch",
        "rem",
        "lh",
        "rlh",
        "vw",
        "vh",
        "vmin",
        "vmax",
        "vb",
        "vi",
        "svw",
        "svh",
        "lvw",
        "lvh",
        "dvw",
        "dvh",
        "cqw",
        "cqh",
        "cqi",
        "cqb",
        "cqmin",
        "cqmax"
      ];
      var lengthUnitsPattern = `(?:${lengthUnits.join("|")})`;
      function length(value) {
        return value === "0" || new RegExp(`^[+-]?[0-9]*.?[0-9]+(?:[eE][+-]?[0-9]+)?${lengthUnitsPattern}$`).test(value) || isCSSFunction(value);
      }
      var lineWidths = /* @__PURE__ */ new Set([
        "thin",
        "medium",
        "thick"
      ]);
      function lineWidth(value) {
        return lineWidths.has(value);
      }
      function shadow(value) {
        let parsedShadows = (0, _parseBoxShadowValue.parseBoxShadowValue)(normalize(value));
        for (let parsedShadow of parsedShadows) {
          if (!parsedShadow.valid) {
            return false;
          }
        }
        return true;
      }
      function color(value) {
        let colors2 = 0;
        let result = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(value, "_").every((part) => {
          part = normalize(part);
          if (part.startsWith("var(")) return true;
          if ((0, _color.parseColor)(part, {
            loose: true
          }) !== null) return colors2++, true;
          return false;
        });
        if (!result) return false;
        return colors2 > 0;
      }
      function image(value) {
        let images = 0;
        let result = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(value, ",").every((part) => {
          part = normalize(part);
          if (part.startsWith("var(")) return true;
          if (url(part) || gradient(part) || [
            "element(",
            "image(",
            "cross-fade(",
            "image-set("
          ].some((fn) => part.startsWith(fn))) {
            images++;
            return true;
          }
          return false;
        });
        if (!result) return false;
        return images > 0;
      }
      var gradientTypes = /* @__PURE__ */ new Set([
        "conic-gradient",
        "linear-gradient",
        "radial-gradient",
        "repeating-conic-gradient",
        "repeating-linear-gradient",
        "repeating-radial-gradient"
      ]);
      function gradient(value) {
        value = normalize(value);
        for (let type of gradientTypes) {
          if (value.startsWith(`${type}(`)) {
            return true;
          }
        }
        return false;
      }
      var validPositions = /* @__PURE__ */ new Set([
        "center",
        "top",
        "right",
        "bottom",
        "left"
      ]);
      function position(value) {
        let positions = 0;
        let result = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(value, "_").every((part) => {
          part = normalize(part);
          if (part.startsWith("var(")) return true;
          if (validPositions.has(part) || length(part) || percentage(part)) {
            positions++;
            return true;
          }
          return false;
        });
        if (!result) return false;
        return positions > 0;
      }
      function familyName(value) {
        let fonts = 0;
        let result = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(value, ",").every((part) => {
          part = normalize(part);
          if (part.startsWith("var(")) return true;
          if (part.includes(" ")) {
            if (!/(['"])([^"']+)\1/g.test(part)) {
              return false;
            }
          }
          if (/^\d/g.test(part)) {
            return false;
          }
          fonts++;
          return true;
        });
        if (!result) return false;
        return fonts > 0;
      }
      var genericNames = /* @__PURE__ */ new Set([
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "math",
        "emoji",
        "fangsong"
      ]);
      function genericName(value) {
        return genericNames.has(value);
      }
      var absoluteSizes = /* @__PURE__ */ new Set([
        "xx-small",
        "x-small",
        "small",
        "medium",
        "large",
        "x-large",
        "xx-large",
        "xxx-large"
      ]);
      function absoluteSize(value) {
        return absoluteSizes.has(value);
      }
      var relativeSizes = /* @__PURE__ */ new Set([
        "larger",
        "smaller"
      ]);
      function relativeSize(value) {
        return relativeSizes.has(value);
      }
    }
  });

  // tailwindcss/lib/util/negateValue.js
  var require_negateValue = __commonJS({
    "tailwindcss/lib/util/negateValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return negateValue;
        }
      });
      function negateValue(value) {
        value = `${value}`;
        if (value === "0") {
          return "0";
        }
        if (/^[+-]?(\d+|\d*\.\d+)(e[+-]?\d+)?(%|\w+)?$/.test(value)) {
          return value.replace(/^[+-]?/, (sign) => sign === "-" ? "" : "-");
        }
        let numericFunctions = [
          "var",
          "calc",
          "min",
          "max",
          "clamp"
        ];
        for (const fn of numericFunctions) {
          if (value.includes(`${fn}(`)) {
            return `calc(${value} * -1)`;
          }
        }
      }
    }
  });

  // tailwindcss/lib/util/validateFormalSyntax.js
  var require_validateFormalSyntax = __commonJS({
    "tailwindcss/lib/util/validateFormalSyntax.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "backgroundSize", {
        enumerable: true,
        get: function() {
          return backgroundSize;
        }
      });
      var _dataTypes = require_dataTypes();
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      function backgroundSize(value) {
        let keywordValues = [
          "cover",
          "contain"
        ];
        return (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(value, ",").every((part) => {
          let sizes = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(part, "_").filter(Boolean);
          if (sizes.length === 1 && keywordValues.includes(sizes[0])) return true;
          if (sizes.length !== 1 && sizes.length !== 2) return false;
          return sizes.every((size) => (0, _dataTypes.length)(size) || (0, _dataTypes.percentage)(size) || size === "auto");
        });
      }
    }
  });

  // tailwindcss/lib/featureFlags.js
  var require_featureFlags = __commonJS({
    "tailwindcss/lib/featureFlags.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        flagEnabled: function() {
          return flagEnabled;
        },
        issueFlagNotices: function() {
          return issueFlagNotices;
        },
        default: function() {
          return _default;
        }
      });
      var _picocolors = /* @__PURE__ */ _interop_require_default(require_picocolors_browser());
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var defaults = {
        optimizeUniversalDefaults: false,
        generalizedModifiers: true,
        disableColorOpacityUtilitiesByDefault: false,
        relativeContentPathsByDefault: false
      };
      var featureFlags = {
        future: [
          "hoverOnlyWhenSupported",
          "respectDefaultRingColorOpacity",
          "disableColorOpacityUtilitiesByDefault",
          "relativeContentPathsByDefault"
        ],
        experimental: [
          "optimizeUniversalDefaults",
          "generalizedModifiers"
        ]
      };
      function flagEnabled(config, flag) {
        if (featureFlags.future.includes(flag)) {
          var _config_future;
          var _config_future_flag, _ref;
          return config.future === "all" || ((_ref = (_config_future_flag = config === null || config === void 0 ? void 0 : (_config_future = config.future) === null || _config_future === void 0 ? void 0 : _config_future[flag]) !== null && _config_future_flag !== void 0 ? _config_future_flag : defaults[flag]) !== null && _ref !== void 0 ? _ref : false);
        }
        if (featureFlags.experimental.includes(flag)) {
          var _config_experimental;
          var _config_experimental_flag, _ref1;
          return config.experimental === "all" || ((_ref1 = (_config_experimental_flag = config === null || config === void 0 ? void 0 : (_config_experimental = config.experimental) === null || _config_experimental === void 0 ? void 0 : _config_experimental[flag]) !== null && _config_experimental_flag !== void 0 ? _config_experimental_flag : defaults[flag]) !== null && _ref1 !== void 0 ? _ref1 : false);
        }
        return false;
      }
      function experimentalFlagsEnabled(config) {
        if (config.experimental === "all") {
          return featureFlags.experimental;
        }
        var _config_experimental;
        return Object.keys((_config_experimental = config === null || config === void 0 ? void 0 : config.experimental) !== null && _config_experimental !== void 0 ? _config_experimental : {}).filter((flag) => featureFlags.experimental.includes(flag) && config.experimental[flag]);
      }
      function issueFlagNotices(config) {
        if (false) {
          return;
        }
        if (experimentalFlagsEnabled(config).length > 0) {
          let changes = experimentalFlagsEnabled(config).map((s) => _picocolors.default.yellow(s)).join(", ");
          _log.default.warn("experimental-flags-enabled", [
            `You have enabled experimental features: ${changes}`,
            "Experimental features in Tailwind CSS are not covered by semver, may introduce breaking changes, and can change at any time."
          ]);
        }
      }
      var _default = featureFlags;
    }
  });

  // tailwindcss/lib/util/pluginUtils.js
  var require_pluginUtils = __commonJS({
    "tailwindcss/lib/util/pluginUtils.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        updateAllClasses: function() {
          return updateAllClasses;
        },
        asValue: function() {
          return asValue;
        },
        parseColorFormat: function() {
          return parseColorFormat;
        },
        asColor: function() {
          return asColor;
        },
        asLookupValue: function() {
          return asLookupValue;
        },
        typeMap: function() {
          return typeMap;
        },
        coerceValue: function() {
          return coerceValue;
        },
        getMatchingTypes: function() {
          return getMatchingTypes;
        }
      });
      var _escapeCommas = /* @__PURE__ */ _interop_require_default(require_escapeCommas());
      var _withAlphaVariable = require_withAlphaVariable();
      var _dataTypes = require_dataTypes();
      var _negateValue = /* @__PURE__ */ _interop_require_default(require_negateValue());
      var _validateFormalSyntax = require_validateFormalSyntax();
      var _featureFlags = require_featureFlags();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function updateAllClasses(selectors, updateClass) {
        selectors.walkClasses((sel) => {
          sel.value = updateClass(sel.value);
          if (sel.raws && sel.raws.value) {
            sel.raws.value = (0, _escapeCommas.default)(sel.raws.value);
          }
        });
      }
      function resolveArbitraryValue(modifier, validate) {
        if (!isArbitraryValue(modifier)) {
          return void 0;
        }
        let value = modifier.slice(1, -1);
        if (!validate(value)) {
          return void 0;
        }
        return (0, _dataTypes.normalize)(value);
      }
      function asNegativeValue(modifier, lookup = {}, validate) {
        let positiveValue = lookup[modifier];
        if (positiveValue !== void 0) {
          return (0, _negateValue.default)(positiveValue);
        }
        if (isArbitraryValue(modifier)) {
          let resolved = resolveArbitraryValue(modifier, validate);
          if (resolved === void 0) {
            return void 0;
          }
          return (0, _negateValue.default)(resolved);
        }
      }
      function asValue(modifier, options = {}, { validate = () => true } = {}) {
        var _options_values;
        let value = (_options_values = options.values) === null || _options_values === void 0 ? void 0 : _options_values[modifier];
        if (value !== void 0) {
          return value;
        }
        if (options.supportsNegativeValues && modifier.startsWith("-")) {
          return asNegativeValue(modifier.slice(1), options.values, validate);
        }
        return resolveArbitraryValue(modifier, validate);
      }
      function isArbitraryValue(input) {
        return input.startsWith("[") && input.endsWith("]");
      }
      function splitUtilityModifier(modifier) {
        let slashIdx = modifier.lastIndexOf("/");
        let arbitraryStartIdx = modifier.lastIndexOf("[", slashIdx);
        let arbitraryEndIdx = modifier.indexOf("]", slashIdx);
        let isNextToArbitrary = modifier[slashIdx - 1] === "]" || modifier[slashIdx + 1] === "[";
        if (!isNextToArbitrary) {
          if (arbitraryStartIdx !== -1 && arbitraryEndIdx !== -1) {
            if (arbitraryStartIdx < slashIdx && slashIdx < arbitraryEndIdx) {
              slashIdx = modifier.lastIndexOf("/", arbitraryStartIdx);
            }
          }
        }
        if (slashIdx === -1 || slashIdx === modifier.length - 1) {
          return [
            modifier,
            void 0
          ];
        }
        let arbitrary = isArbitraryValue(modifier);
        if (arbitrary && !modifier.includes("]/[")) {
          return [
            modifier,
            void 0
          ];
        }
        return [
          modifier.slice(0, slashIdx),
          modifier.slice(slashIdx + 1)
        ];
      }
      function parseColorFormat(value) {
        if (typeof value === "string" && value.includes("<alpha-value>")) {
          let oldValue = value;
          return ({ opacityValue = 1 }) => oldValue.replace(/<alpha-value>/g, opacityValue);
        }
        return value;
      }
      function unwrapArbitraryModifier(modifier) {
        return (0, _dataTypes.normalize)(modifier.slice(1, -1));
      }
      function asColor(modifier, options = {}, { tailwindConfig = {} } = {}) {
        var _options_values;
        if (((_options_values = options.values) === null || _options_values === void 0 ? void 0 : _options_values[modifier]) !== void 0) {
          var _options_values1;
          return parseColorFormat((_options_values1 = options.values) === null || _options_values1 === void 0 ? void 0 : _options_values1[modifier]);
        }
        let [color, alpha] = splitUtilityModifier(modifier);
        if (alpha !== void 0) {
          var _options_values2, _tailwindConfig_theme, _tailwindConfig_theme_opacity;
          var _options_values_color;
          let normalizedColor = (_options_values_color = (_options_values2 = options.values) === null || _options_values2 === void 0 ? void 0 : _options_values2[color]) !== null && _options_values_color !== void 0 ? _options_values_color : isArbitraryValue(color) ? color.slice(1, -1) : void 0;
          if (normalizedColor === void 0) {
            return void 0;
          }
          normalizedColor = parseColorFormat(normalizedColor);
          if (isArbitraryValue(alpha)) {
            return (0, _withAlphaVariable.withAlphaValue)(normalizedColor, unwrapArbitraryModifier(alpha));
          }
          if (((_tailwindConfig_theme = tailwindConfig.theme) === null || _tailwindConfig_theme === void 0 ? void 0 : (_tailwindConfig_theme_opacity = _tailwindConfig_theme.opacity) === null || _tailwindConfig_theme_opacity === void 0 ? void 0 : _tailwindConfig_theme_opacity[alpha]) === void 0) {
            return void 0;
          }
          return (0, _withAlphaVariable.withAlphaValue)(normalizedColor, tailwindConfig.theme.opacity[alpha]);
        }
        return asValue(modifier, options, {
          validate: _dataTypes.color
        });
      }
      function asLookupValue(modifier, options = {}) {
        var _options_values;
        return (_options_values = options.values) === null || _options_values === void 0 ? void 0 : _options_values[modifier];
      }
      function guess(validate) {
        return (modifier, options) => {
          return asValue(modifier, options, {
            validate
          });
        };
      }
      var typeMap = {
        any: asValue,
        color: asColor,
        url: guess(_dataTypes.url),
        image: guess(_dataTypes.image),
        length: guess(_dataTypes.length),
        percentage: guess(_dataTypes.percentage),
        position: guess(_dataTypes.position),
        lookup: asLookupValue,
        "generic-name": guess(_dataTypes.genericName),
        "family-name": guess(_dataTypes.familyName),
        number: guess(_dataTypes.number),
        "line-width": guess(_dataTypes.lineWidth),
        "absolute-size": guess(_dataTypes.absoluteSize),
        "relative-size": guess(_dataTypes.relativeSize),
        shadow: guess(_dataTypes.shadow),
        size: guess(_validateFormalSyntax.backgroundSize)
      };
      var supportedTypes = Object.keys(typeMap);
      function splitAtFirst(input, delim) {
        let idx = input.indexOf(delim);
        if (idx === -1) return [
          void 0,
          input
        ];
        return [
          input.slice(0, idx),
          input.slice(idx + 1)
        ];
      }
      function coerceValue(types, modifier, options, tailwindConfig) {
        if (options.values && modifier in options.values) {
          for (let { type } of types !== null && types !== void 0 ? types : []) {
            let result = typeMap[type](modifier, options, {
              tailwindConfig
            });
            if (result === void 0) {
              continue;
            }
            return [
              result,
              type,
              null
            ];
          }
        }
        if (isArbitraryValue(modifier)) {
          let arbitraryValue = modifier.slice(1, -1);
          let [explicitType, value] = splitAtFirst(arbitraryValue, ":");
          if (!/^[\w-_]+$/g.test(explicitType)) {
            value = arbitraryValue;
          } else if (explicitType !== void 0 && !supportedTypes.includes(explicitType)) {
            return [];
          }
          if (value.length > 0 && supportedTypes.includes(explicitType)) {
            return [
              asValue(`[${value}]`, options),
              explicitType,
              null
            ];
          }
        }
        let matches = getMatchingTypes(types, modifier, options, tailwindConfig);
        for (let match of matches) {
          return match;
        }
        return [];
      }
      function* getMatchingTypes(types, rawModifier, options, tailwindConfig) {
        let modifiersEnabled = (0, _featureFlags.flagEnabled)(tailwindConfig, "generalizedModifiers");
        let [modifier, utilityModifier] = splitUtilityModifier(rawModifier);
        let canUseUtilityModifier = modifiersEnabled && options.modifiers != null && (options.modifiers === "any" || typeof options.modifiers === "object" && (utilityModifier && isArbitraryValue(utilityModifier) || utilityModifier in options.modifiers));
        if (!canUseUtilityModifier) {
          modifier = rawModifier;
          utilityModifier = void 0;
        }
        if (utilityModifier !== void 0 && modifier === "") {
          modifier = "DEFAULT";
        }
        if (utilityModifier !== void 0) {
          if (typeof options.modifiers === "object") {
            var _options_modifiers;
            var _options_modifiers_utilityModifier;
            let configValue = (_options_modifiers_utilityModifier = (_options_modifiers = options.modifiers) === null || _options_modifiers === void 0 ? void 0 : _options_modifiers[utilityModifier]) !== null && _options_modifiers_utilityModifier !== void 0 ? _options_modifiers_utilityModifier : null;
            if (configValue !== null) {
              utilityModifier = configValue;
            } else if (isArbitraryValue(utilityModifier)) {
              utilityModifier = unwrapArbitraryModifier(utilityModifier);
            }
          }
        }
        for (let { type } of types !== null && types !== void 0 ? types : []) {
          let result = typeMap[type](modifier, options, {
            tailwindConfig
          });
          if (result === void 0) {
            continue;
          }
          yield [
            result,
            type,
            utilityModifier !== null && utilityModifier !== void 0 ? utilityModifier : null
          ];
        }
      }
    }
  });

  // tailwindcss/lib/util/escapeClassName.js
  var require_escapeClassName = __commonJS({
    "tailwindcss/lib/util/escapeClassName.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return escapeClassName;
        }
      });
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _escapeCommas = /* @__PURE__ */ _interop_require_default(require_escapeCommas());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function escapeClassName(className) {
        var _node_raws;
        let node = _postcssselectorparser.default.className();
        node.value = className;
        var _node_raws_value;
        return (0, _escapeCommas.default)((_node_raws_value = node === null || node === void 0 ? void 0 : (_node_raws = node.raws) === null || _node_raws === void 0 ? void 0 : _node_raws.value) !== null && _node_raws_value !== void 0 ? _node_raws_value : node.value);
      }
    }
  });

  // tailwindcss/lib/util/pseudoElements.js
  var require_pseudoElements = __commonJS({
    "tailwindcss/lib/util/pseudoElements.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "movePseudos", {
        enumerable: true,
        get: function() {
          return movePseudos;
        }
      });
      var elementProperties = {
        // Pseudo elements from the spec
        "::after": [
          "terminal",
          "jumpable"
        ],
        "::backdrop": [
          "terminal",
          "jumpable"
        ],
        "::before": [
          "terminal",
          "jumpable"
        ],
        "::cue": [
          "terminal"
        ],
        "::cue-region": [
          "terminal"
        ],
        "::first-letter": [
          "terminal",
          "jumpable"
        ],
        "::first-line": [
          "terminal",
          "jumpable"
        ],
        "::grammar-error": [
          "terminal"
        ],
        "::marker": [
          "terminal",
          "jumpable"
        ],
        "::part": [
          "terminal",
          "actionable"
        ],
        "::placeholder": [
          "terminal",
          "jumpable"
        ],
        "::selection": [
          "terminal",
          "jumpable"
        ],
        "::slotted": [
          "terminal"
        ],
        "::spelling-error": [
          "terminal"
        ],
        "::target-text": [
          "terminal"
        ],
        // Pseudo elements from the spec with special rules
        "::file-selector-button": [
          "terminal",
          "actionable"
        ],
        // Library-specific pseudo elements used by component libraries
        // These are Shadow DOM-like
        "::deep": [
          "actionable"
        ],
        "::v-deep": [
          "actionable"
        ],
        "::ng-deep": [
          "actionable"
        ],
        // Note: As a rule, double colons (::) should be used instead of a single colon
        // (:). This distinguishes pseudo-classes from pseudo-elements. However, since
        // this distinction was not present in older versions of the W3C spec, most
        // browsers support both syntaxes for the original pseudo-elements.
        ":after": [
          "terminal",
          "jumpable"
        ],
        ":before": [
          "terminal",
          "jumpable"
        ],
        ":first-letter": [
          "terminal",
          "jumpable"
        ],
        ":first-line": [
          "terminal",
          "jumpable"
        ],
        ":where": [],
        ":is": [],
        ":has": [],
        // The default value is used when the pseudo-element is not recognized
        // Because it's not recognized, we don't know if it's terminal or not
        // So we assume it can be moved AND can have user-action pseudo classes attached to it
        __default__: [
          "terminal",
          "actionable"
        ]
      };
      function movePseudos(sel) {
        let [pseudos] = movablePseudos(sel);
        pseudos.forEach(([sel2, pseudo]) => sel2.removeChild(pseudo));
        sel.nodes.push(...pseudos.map(([, pseudo]) => pseudo));
        return sel;
      }
      function movablePseudos(sel) {
        let buffer = [];
        let lastSeenElement = null;
        for (let node of sel.nodes) {
          if (node.type === "combinator") {
            buffer = buffer.filter(([, node2]) => propertiesForPseudo(node2).includes("jumpable"));
            lastSeenElement = null;
          } else if (node.type === "pseudo") {
            if (isMovablePseudoElement(node)) {
              lastSeenElement = node;
              buffer.push([
                sel,
                node,
                null
              ]);
            } else if (lastSeenElement && isAttachablePseudoClass(node, lastSeenElement)) {
              buffer.push([
                sel,
                node,
                lastSeenElement
              ]);
            } else {
              lastSeenElement = null;
            }
            var _node_nodes;
            for (let sub of (_node_nodes = node.nodes) !== null && _node_nodes !== void 0 ? _node_nodes : []) {
              let [movable, lastSeenElementInSub] = movablePseudos(sub);
              lastSeenElement = lastSeenElementInSub || lastSeenElement;
              buffer.push(...movable);
            }
          }
        }
        return [
          buffer,
          lastSeenElement
        ];
      }
      function isPseudoElement(node) {
        return node.value.startsWith("::") || elementProperties[node.value] !== void 0;
      }
      function isMovablePseudoElement(node) {
        return isPseudoElement(node) && propertiesForPseudo(node).includes("terminal");
      }
      function isAttachablePseudoClass(node, pseudo) {
        if (node.type !== "pseudo") return false;
        if (isPseudoElement(node)) return false;
        return propertiesForPseudo(pseudo).includes("actionable");
      }
      function propertiesForPseudo(pseudo) {
        var _elementProperties_pseudo_value;
        return (_elementProperties_pseudo_value = elementProperties[pseudo.value]) !== null && _elementProperties_pseudo_value !== void 0 ? _elementProperties_pseudo_value : elementProperties.__default__;
      }
    }
  });

  // tailwindcss/lib/util/formatVariantSelector.js
  var require_formatVariantSelector = __commonJS({
    "tailwindcss/lib/util/formatVariantSelector.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        formatVariantSelector: function() {
          return formatVariantSelector;
        },
        eliminateIrrelevantSelectors: function() {
          return eliminateIrrelevantSelectors;
        },
        finalizeSelector: function() {
          return finalizeSelector;
        },
        handleMergePseudo: function() {
          return handleMergePseudo;
        }
      });
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _unesc = /* @__PURE__ */ _interop_require_default(require_unesc());
      var _escapeClassName = /* @__PURE__ */ _interop_require_default(require_escapeClassName());
      var _prefixSelector = /* @__PURE__ */ _interop_require_default(require_prefixSelector());
      var _pseudoElements = require_pseudoElements();
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var MERGE = ":merge";
      function formatVariantSelector(formats, { context: context2, candidate }) {
        var _context_tailwindConfig_prefix;
        let prefix = (_context_tailwindConfig_prefix = context2 === null || context2 === void 0 ? void 0 : context2.tailwindConfig.prefix) !== null && _context_tailwindConfig_prefix !== void 0 ? _context_tailwindConfig_prefix : "";
        let parsedFormats = formats.map((format) => {
          let ast = (0, _postcssselectorparser.default)().astSync(format.format);
          return {
            ...format,
            ast: format.respectPrefix ? (0, _prefixSelector.default)(prefix, ast) : ast
          };
        });
        let formatAst = _postcssselectorparser.default.root({
          nodes: [
            _postcssselectorparser.default.selector({
              nodes: [
                _postcssselectorparser.default.className({
                  value: (0, _escapeClassName.default)(candidate)
                })
              ]
            })
          ]
        });
        for (let { ast } of parsedFormats) {
          [formatAst, ast] = handleMergePseudo(formatAst, ast);
          ast.walkNesting((nesting) => nesting.replaceWith(...formatAst.nodes[0].nodes));
          formatAst = ast;
        }
        return formatAst;
      }
      function simpleSelectorForNode(node) {
        let nodes = [];
        while (node.prev() && node.prev().type !== "combinator") {
          node = node.prev();
        }
        while (node && node.type !== "combinator") {
          nodes.push(node);
          node = node.next();
        }
        return nodes;
      }
      function resortSelector(sel) {
        sel.sort((a, b) => {
          if (a.type === "tag" && b.type === "class") {
            return -1;
          } else if (a.type === "class" && b.type === "tag") {
            return 1;
          } else if (a.type === "class" && b.type === "pseudo" && b.value.startsWith("::")) {
            return -1;
          } else if (a.type === "pseudo" && a.value.startsWith("::") && b.type === "class") {
            return 1;
          }
          return sel.index(a) - sel.index(b);
        });
        return sel;
      }
      function eliminateIrrelevantSelectors(sel, base) {
        let hasClassesMatchingCandidate = false;
        sel.walk((child) => {
          if (child.type === "class" && child.value === base) {
            hasClassesMatchingCandidate = true;
            return false;
          }
        });
        if (!hasClassesMatchingCandidate) {
          sel.remove();
        }
      }
      function finalizeSelector(current, formats, { context: context2, candidate, base }) {
        var _context_tailwindConfig;
        var _context_tailwindConfig_separator;
        let separator = (_context_tailwindConfig_separator = context2 === null || context2 === void 0 ? void 0 : (_context_tailwindConfig = context2.tailwindConfig) === null || _context_tailwindConfig === void 0 ? void 0 : _context_tailwindConfig.separator) !== null && _context_tailwindConfig_separator !== void 0 ? _context_tailwindConfig_separator : ":";
        base = base !== null && base !== void 0 ? base : (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(candidate, separator).pop();
        let selector = (0, _postcssselectorparser.default)().astSync(current);
        selector.walkClasses((node) => {
          if (node.raws && node.value.includes(base)) {
            node.raws.value = (0, _escapeClassName.default)((0, _unesc.default)(node.raws.value));
          }
        });
        selector.each((sel) => eliminateIrrelevantSelectors(sel, base));
        if (selector.length === 0) {
          return null;
        }
        let formatAst = Array.isArray(formats) ? formatVariantSelector(formats, {
          context: context2,
          candidate
        }) : formats;
        if (formatAst === null) {
          return selector.toString();
        }
        let simpleStart = _postcssselectorparser.default.comment({
          value: "/*__simple__*/"
        });
        let simpleEnd = _postcssselectorparser.default.comment({
          value: "/*__simple__*/"
        });
        selector.walkClasses((node) => {
          if (node.value !== base) {
            return;
          }
          let parent = node.parent;
          let formatNodes = formatAst.nodes[0].nodes;
          if (parent.nodes.length === 1) {
            node.replaceWith(...formatNodes);
            return;
          }
          let simpleSelector = simpleSelectorForNode(node);
          parent.insertBefore(simpleSelector[0], simpleStart);
          parent.insertAfter(simpleSelector[simpleSelector.length - 1], simpleEnd);
          for (let child of formatNodes) {
            parent.insertBefore(simpleSelector[0], child.clone());
          }
          node.remove();
          simpleSelector = simpleSelectorForNode(simpleStart);
          let firstNode = parent.index(simpleStart);
          parent.nodes.splice(firstNode, simpleSelector.length, ...resortSelector(_postcssselectorparser.default.selector({
            nodes: simpleSelector
          })).nodes);
          simpleStart.remove();
          simpleEnd.remove();
        });
        selector.walkPseudos((p) => {
          if (p.value === MERGE) {
            p.replaceWith(p.nodes);
          }
        });
        selector.each((sel) => (0, _pseudoElements.movePseudos)(sel));
        return selector.toString();
      }
      function handleMergePseudo(selector, format) {
        let merges = [];
        selector.walkPseudos((pseudo) => {
          if (pseudo.value === MERGE) {
            merges.push({
              pseudo,
              value: pseudo.nodes[0].toString()
            });
          }
        });
        format.walkPseudos((pseudo) => {
          if (pseudo.value !== MERGE) {
            return;
          }
          let value = pseudo.nodes[0].toString();
          let existing = merges.find((merge) => merge.value === value);
          if (!existing) {
            return;
          }
          let attachments = [];
          let next = pseudo.next();
          while (next && next.type !== "combinator") {
            attachments.push(next);
            next = next.next();
          }
          let combinator = next;
          existing.pseudo.parent.insertAfter(existing.pseudo, _postcssselectorparser.default.selector({
            nodes: attachments.map((node) => node.clone())
          }));
          pseudo.remove();
          attachments.forEach((node) => node.remove());
          if (combinator && combinator.type === "combinator") {
            combinator.remove();
          }
        });
        return [
          selector,
          format
        ];
      }
    }
  });

  // tailwindcss/lib/util/nameClass.js
  var require_nameClass = __commonJS({
    "tailwindcss/lib/util/nameClass.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        asClass: function() {
          return asClass;
        },
        default: function() {
          return nameClass;
        },
        formatClass: function() {
          return formatClass;
        }
      });
      var _escapeClassName = /* @__PURE__ */ _interop_require_default(require_escapeClassName());
      var _escapeCommas = /* @__PURE__ */ _interop_require_default(require_escapeCommas());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function asClass(name) {
        return (0, _escapeCommas.default)(`.${(0, _escapeClassName.default)(name)}`);
      }
      function nameClass(classPrefix, key) {
        return asClass(formatClass(classPrefix, key));
      }
      function formatClass(classPrefix, key) {
        if (key === "DEFAULT") {
          return classPrefix;
        }
        if (key === "-" || key === "-DEFAULT") {
          return `-${classPrefix}`;
        }
        if (key.startsWith("-")) {
          return `-${classPrefix}${key}`;
        }
        if (key.startsWith("/")) {
          return `${classPrefix}${key}`;
        }
        return `${classPrefix}-${key}`;
      }
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-context-fs.js
  var require_tailwind_context_fs = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-context-fs.js"(exports, module) {
      "use strict";
      function statSync(file) {
        throw new Error(`setupContextUtils.js \u7981\u6B62\u8BBF\u95EE\u6587\u4EF6\u7CFB\u7EDF statSync\uFF1A${String(file)}`);
      }
      module.exports = { statSync };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-context-url.js
  var require_tailwind_context_url = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-context-url.js"(exports, module) {
      "use strict";
      function parse(value) {
        throw new Error(`setupContextUtils.js \u7981\u6B62\u89E3\u6790\u6587\u4EF6 URL\uFF1A${String(value)}`);
      }
      module.exports = { parse };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/owned-path-get.js
  var require_owned_path_get = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/owned-path-get.js"(exports, module) {
      "use strict";
      module.exports = function readPath(root, query, missing) {
        const keys = typeof query === "string" ? query.split(".") : query;
        let value = root;
        for (let offset = 0; offset < keys.length; offset += 1) {
          if (!value) return missing;
          value = value[keys[offset]];
        }
        return value === void 0 ? missing : value;
      };
    }
  });

  // tailwindcss/lib/util/transformThemeValue.js
  var require_transformThemeValue = __commonJS({
    "tailwindcss/lib/util/transformThemeValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return transformThemeValue;
        }
      });
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _isPlainObject = /* @__PURE__ */ _interop_require_default(require_isPlainObject());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function transformThemeValue(themeSection) {
        if ([
          "fontSize",
          "outline"
        ].includes(themeSection)) {
          return (value) => {
            if (typeof value === "function") value = value({});
            if (Array.isArray(value)) value = value[0];
            return value;
          };
        }
        if (themeSection === "fontFamily") {
          return (value) => {
            if (typeof value === "function") value = value({});
            let families = Array.isArray(value) && (0, _isPlainObject.default)(value[1]) ? value[0] : value;
            return Array.isArray(families) ? families.join(", ") : families;
          };
        }
        if ([
          "boxShadow",
          "transitionProperty",
          "transitionDuration",
          "transitionDelay",
          "transitionTimingFunction",
          "backgroundImage",
          "backgroundSize",
          "backgroundColor",
          "cursor",
          "animation"
        ].includes(themeSection)) {
          return (value) => {
            if (typeof value === "function") value = value({});
            if (Array.isArray(value)) value = value.join(", ");
            return value;
          };
        }
        if ([
          "gridTemplateColumns",
          "gridTemplateRows",
          "objectPosition"
        ].includes(themeSection)) {
          return (value) => {
            if (typeof value === "function") value = value({});
            if (typeof value === "string") value = _postcss.default.list.comma(value).join(" ");
            return value;
          };
        }
        return (value, opts = {}) => {
          if (typeof value === "function") {
            value = value(opts);
          }
          return value;
        };
      }
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-preflight-fs.js
  var require_tailwind_preflight_fs = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-preflight-fs.js"(exports, module) {
      "use strict";
      var INLINE_RESOURCE = "__TAILWIND_PREFLIGHT_RESOURCE_R01__";
      var PREFLIGHT_CSS = "/*\n1. Prevent padding and border from affecting element width. (https://github.com/mozdevs/cssremedy/issues/4)\n2. Allow adding a border to an element by just adding a border-width. (https://github.com/tailwindcss/tailwindcss/pull/116)\n*/\n\n*,\n::before,\n::after {\n  box-sizing: border-box; /* 1 */\n  border-width: 0; /* 2 */\n  border-style: solid; /* 2 */\n  border-color: theme('borderColor.DEFAULT', currentColor); /* 2 */\n}\n\n::before,\n::after {\n  --tw-content: '';\n}\n\n/*\n1. Use a consistent sensible line-height in all browsers.\n2. Prevent adjustments of font size after orientation changes in iOS.\n3. Use a more readable tab size.\n4. Use the user's configured `sans` font-family by default.\n5. Use the user's configured `sans` font-feature-settings by default.\n6. Use the user's configured `sans` font-variation-settings by default.\n7. Disable tap highlights on iOS\n*/\n\nhtml,\n:host {\n  line-height: 1.5; /* 1 */\n  -webkit-text-size-adjust: 100%; /* 2 */\n  -moz-tab-size: 4; /* 3 */\n  tab-size: 4; /* 3 */\n  font-family: theme('fontFamily.sans', ui-sans-serif, system-ui, sans-serif, \"Apple Color Emoji\", \"Segoe UI Emoji\", \"Segoe UI Symbol\", \"Noto Color Emoji\"); /* 4 */\n  font-feature-settings: theme('fontFamily.sans[1].fontFeatureSettings', normal); /* 5 */\n  font-variation-settings: theme('fontFamily.sans[1].fontVariationSettings', normal); /* 6 */\n  -webkit-tap-highlight-color: transparent; /* 7 */\n}\n\n/*\n1. Remove the margin in all browsers.\n2. Inherit line-height from `html` so users can set them as a class directly on the `html` element.\n*/\n\nbody {\n  margin: 0; /* 1 */\n  line-height: inherit; /* 2 */\n}\n\n/*\n1. Add the correct height in Firefox.\n2. Correct the inheritance of border color in Firefox. (https://bugzilla.mozilla.org/show_bug.cgi?id=190655)\n3. Ensure horizontal rules are visible by default.\n*/\n\nhr {\n  height: 0; /* 1 */\n  color: inherit; /* 2 */\n  border-top-width: 1px; /* 3 */\n}\n\n/*\nAdd the correct text decoration in Chrome, Edge, and Safari.\n*/\n\nabbr:where([title]) {\n  text-decoration: underline dotted;\n}\n\n/*\nRemove the default font size and weight for headings.\n*/\n\nh1,\nh2,\nh3,\nh4,\nh5,\nh6 {\n  font-size: inherit;\n  font-weight: inherit;\n}\n\n/*\nReset links to optimize for opt-in styling instead of opt-out.\n*/\n\na {\n  color: inherit;\n  text-decoration: inherit;\n}\n\n/*\nAdd the correct font weight in Edge and Safari.\n*/\n\nb,\nstrong {\n  font-weight: bolder;\n}\n\n/*\n1. Use the user's configured `mono` font-family by default.\n2. Use the user's configured `mono` font-feature-settings by default.\n3. Use the user's configured `mono` font-variation-settings by default.\n4. Correct the odd `em` font sizing in all browsers.\n*/\n\ncode,\nkbd,\nsamp,\npre {\n  font-family: theme('fontFamily.mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace); /* 1 */\n  font-feature-settings: theme('fontFamily.mono[1].fontFeatureSettings', normal); /* 2 */\n  font-variation-settings: theme('fontFamily.mono[1].fontVariationSettings', normal); /* 3 */\n  font-size: 1em; /* 4 */\n}\n\n/*\nAdd the correct font size in all browsers.\n*/\n\nsmall {\n  font-size: 80%;\n}\n\n/*\nPrevent `sub` and `sup` elements from affecting the line height in all browsers.\n*/\n\nsub,\nsup {\n  font-size: 75%;\n  line-height: 0;\n  position: relative;\n  vertical-align: baseline;\n}\n\nsub {\n  bottom: -0.25em;\n}\n\nsup {\n  top: -0.5em;\n}\n\n/*\n1. Remove text indentation from table contents in Chrome and Safari. (https://bugs.chromium.org/p/chromium/issues/detail?id=999088, https://bugs.webkit.org/show_bug.cgi?id=201297)\n2. Correct table border color inheritance in all Chrome and Safari. (https://bugs.chromium.org/p/chromium/issues/detail?id=935729, https://bugs.webkit.org/show_bug.cgi?id=195016)\n3. Remove gaps between table borders by default.\n*/\n\ntable {\n  text-indent: 0; /* 1 */\n  border-color: inherit; /* 2 */\n  border-collapse: collapse; /* 3 */\n}\n\n/*\n1. Change the font styles in all browsers.\n2. Remove the margin in Firefox and Safari.\n3. Remove default padding in all browsers.\n*/\n\nbutton,\ninput,\noptgroup,\nselect,\ntextarea {\n  font-family: inherit; /* 1 */\n  font-feature-settings: inherit; /* 1 */\n  font-variation-settings: inherit; /* 1 */\n  font-size: 100%; /* 1 */\n  font-weight: inherit; /* 1 */\n  line-height: inherit; /* 1 */\n  letter-spacing: inherit; /* 1 */\n  color: inherit; /* 1 */\n  margin: 0; /* 2 */\n  padding: 0; /* 3 */\n}\n\n/*\nRemove the inheritance of text transform in Edge and Firefox.\n*/\n\nbutton,\nselect {\n  text-transform: none;\n}\n\n/*\n1. Correct the inability to style clickable types in iOS and Safari.\n2. Remove default button styles.\n*/\n\nbutton,\ninput:where([type='button']),\ninput:where([type='reset']),\ninput:where([type='submit']) {\n  -webkit-appearance: button; /* 1 */\n  background-color: transparent; /* 2 */\n  background-image: none; /* 2 */\n}\n\n/*\nUse the modern Firefox focus style for all focusable elements.\n*/\n\n:-moz-focusring {\n  outline: auto;\n}\n\n/*\nRemove the additional `:invalid` styles in Firefox. (https://github.com/mozilla/gecko-dev/blob/2f9eacd9d3d995c937b4251a5557d95d494c9be1/layout/style/res/forms.css#L728-L737)\n*/\n\n:-moz-ui-invalid {\n  box-shadow: none;\n}\n\n/*\nAdd the correct vertical alignment in Chrome and Firefox.\n*/\n\nprogress {\n  vertical-align: baseline;\n}\n\n/*\nCorrect the cursor style of increment and decrement buttons in Safari.\n*/\n\n::-webkit-inner-spin-button,\n::-webkit-outer-spin-button {\n  height: auto;\n}\n\n/*\n1. Correct the odd appearance in Chrome and Safari.\n2. Correct the outline style in Safari.\n*/\n\n[type='search'] {\n  -webkit-appearance: textfield; /* 1 */\n  outline-offset: -2px; /* 2 */\n}\n\n/*\nRemove the inner padding in Chrome and Safari on macOS.\n*/\n\n::-webkit-search-decoration {\n  -webkit-appearance: none;\n}\n\n/*\n1. Correct the inability to style clickable types in iOS and Safari.\n2. Change font properties to `inherit` in Safari.\n*/\n\n::-webkit-file-upload-button {\n  -webkit-appearance: button; /* 1 */\n  font: inherit; /* 2 */\n}\n\n/*\nAdd the correct display in Chrome and Safari.\n*/\n\nsummary {\n  display: list-item;\n}\n\n/*\nRemoves the default spacing and border for appropriate elements.\n*/\n\nblockquote,\ndl,\ndd,\nh1,\nh2,\nh3,\nh4,\nh5,\nh6,\nhr,\nfigure,\np,\npre {\n  margin: 0;\n}\n\nfieldset {\n  margin: 0;\n  padding: 0;\n}\n\nlegend {\n  padding: 0;\n}\n\nol,\nul,\nmenu {\n  list-style: none;\n  margin: 0;\n  padding: 0;\n}\n\n/*\nReset default styling for dialogs.\n*/\ndialog {\n  padding: 0;\n}\n\n/*\nPrevent resizing textareas horizontally by default.\n*/\n\ntextarea {\n  resize: vertical;\n}\n\n/*\n1. Reset the default placeholder opacity in Firefox. (https://github.com/tailwindlabs/tailwindcss/issues/3300)\n2. Set the default placeholder color to the user's configured gray 400 color.\n*/\n\ninput::placeholder,\ntextarea::placeholder {\n  opacity: 1; /* 1 */\n  color: theme('colors.gray.400', #9ca3af); /* 2 */\n}\n\n/*\nSet the default cursor for buttons.\n*/\n\nbutton,\n[role=\"button\"] {\n  cursor: pointer;\n}\n\n/*\nMake sure disabled buttons don't get the pointer cursor.\n*/\n:disabled {\n  cursor: default;\n}\n\n/*\n1. Make replaced elements `display: block` by default. (https://github.com/mozdevs/cssremedy/issues/14)\n2. Add `vertical-align: middle` to align replaced elements more sensibly by default. (https://github.com/jensimmons/cssremedy/issues/14#issuecomment-634934210)\n   This can trigger a poorly considered lint error in some tools but is included by design.\n*/\n\nimg,\nsvg,\nvideo,\ncanvas,\naudio,\niframe,\nembed,\nobject {\n  display: block; /* 1 */\n  vertical-align: middle; /* 2 */\n}\n\n/*\nConstrain images and videos to the parent width and preserve their intrinsic aspect ratio. (https://github.com/mozdevs/cssremedy/issues/14)\n*/\n\nimg,\nvideo {\n  max-width: 100%;\n  height: auto;\n}\n\n/* Make elements with the HTML hidden attribute stay hidden by default */\n[hidden]:where(:not([hidden=\"until-found\"])) {\n  display: none;\n}\n";
      var EXPECTED_SHA256 = "1d85e5cc0262f68a4f1313c4754a61c7ff56da4cc24866e76bcde9d4e467feac";
      function readFileSync(file, encoding) {
        if (file !== INLINE_RESOURCE) {
          throw new Error(`\u62D2\u7EDD\u8BFB\u53D6\u672A\u6388\u6743\u6587\u4EF6\uFF1A${String(file)}`);
        }
        if (encoding !== "utf8" && encoding !== "utf-8") {
          throw new Error(`\u62D2\u7EDD\u4F7F\u7528\u975E UTF-8 \u7F16\u7801\u8BFB\u53D6 preflight\uFF1A${String(encoding)}`);
        }
        return PREFLIGHT_CSS;
      }
      module.exports = { readFileSync, expectedSha256: EXPECTED_SHA256 };
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-preflight-path.js
  var require_tailwind_preflight_path = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-preflight-path.js"(exports, module) {
      "use strict";
      var INLINE_RESOURCE = "__TAILWIND_PREFLIGHT_RESOURCE_R01__";
      function join(directory, relativePath) {
        if (typeof directory !== "string" || relativePath !== "./css/preflight.css") {
          throw new Error(`\u62D2\u7EDD\u62FC\u63A5\u672A\u6388\u6743\u8DEF\u5F84\uFF1A${String(directory)} / ${String(relativePath)}`);
        }
        return INLINE_RESOURCE;
      }
      module.exports = { join };
    }
  });

  // tailwindcss/lib/util/createUtilityPlugin.js
  var require_createUtilityPlugin = __commonJS({
    "tailwindcss/lib/util/createUtilityPlugin.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return createUtilityPlugin;
        }
      });
      var _transformThemeValue = /* @__PURE__ */ _interop_require_default(require_transformThemeValue());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function createUtilityPlugin(themeKey, utilityVariations = [
        [
          themeKey,
          [
            themeKey
          ]
        ]
      ], { filterDefault = false, ...options } = {}) {
        let transformValue = (0, _transformThemeValue.default)(themeKey);
        return function({ matchUtilities, theme }) {
          for (let utilityVariation of utilityVariations) {
            let group = Array.isArray(utilityVariation[0]) ? utilityVariation : [
              utilityVariation
            ];
            var _theme;
            matchUtilities(group.reduce((obj, [classPrefix, properties]) => {
              return Object.assign(obj, {
                [classPrefix]: (value) => {
                  return properties.reduce((obj2, name) => {
                    if (Array.isArray(name)) {
                      return Object.assign(obj2, {
                        [name[0]]: name[1]
                      });
                    }
                    return Object.assign(obj2, {
                      [name]: transformValue(value)
                    });
                  }, {});
                }
              });
            }, {}), {
              ...options,
              values: filterDefault ? Object.fromEntries(Object.entries((_theme = theme(themeKey)) !== null && _theme !== void 0 ? _theme : {}).filter(([modifier]) => modifier !== "DEFAULT")) : theme(themeKey)
            });
          }
        };
      }
    }
  });

  // tailwindcss/lib/util/buildMediaQuery.js
  var require_buildMediaQuery = __commonJS({
    "tailwindcss/lib/util/buildMediaQuery.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return buildMediaQuery;
        }
      });
      function buildMediaQuery(screens) {
        screens = Array.isArray(screens) ? screens : [
          screens
        ];
        return screens.map((screen) => {
          let values = screen.values.map((screen2) => {
            if (screen2.raw !== void 0) {
              return screen2.raw;
            }
            return [
              screen2.min && `(min-width: ${screen2.min})`,
              screen2.max && `(max-width: ${screen2.max})`
            ].filter(Boolean).join(" and ");
          });
          return screen.not ? `not all and ${values}` : values;
        }).join(", ");
      }
    }
  });

  // tailwindcss/lib/util/parseAnimationValue.js
  var require_parseAnimationValue = __commonJS({
    "tailwindcss/lib/util/parseAnimationValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return parseAnimationValue;
        }
      });
      var DIRECTIONS = /* @__PURE__ */ new Set([
        "normal",
        "reverse",
        "alternate",
        "alternate-reverse"
      ]);
      var PLAY_STATES = /* @__PURE__ */ new Set([
        "running",
        "paused"
      ]);
      var FILL_MODES = /* @__PURE__ */ new Set([
        "none",
        "forwards",
        "backwards",
        "both"
      ]);
      var ITERATION_COUNTS = /* @__PURE__ */ new Set([
        "infinite"
      ]);
      var TIMINGS = /* @__PURE__ */ new Set([
        "linear",
        "ease",
        "ease-in",
        "ease-out",
        "ease-in-out",
        "step-start",
        "step-end"
      ]);
      var TIMING_FNS = [
        "cubic-bezier",
        "steps"
      ];
      var COMMA = /\,(?![^(]*\))/g;
      var SPACE = /\ +(?![^(]*\))/g;
      var TIME = /^(-?[\d.]+m?s)$/;
      var DIGIT = /^(\d+)$/;
      function parseAnimationValue(input) {
        let animations = input.split(COMMA);
        return animations.map((animation) => {
          let value = animation.trim();
          let result = {
            value
          };
          let parts = value.split(SPACE);
          let seen = /* @__PURE__ */ new Set();
          for (let part of parts) {
            if (!seen.has("DIRECTIONS") && DIRECTIONS.has(part)) {
              result.direction = part;
              seen.add("DIRECTIONS");
            } else if (!seen.has("PLAY_STATES") && PLAY_STATES.has(part)) {
              result.playState = part;
              seen.add("PLAY_STATES");
            } else if (!seen.has("FILL_MODES") && FILL_MODES.has(part)) {
              result.fillMode = part;
              seen.add("FILL_MODES");
            } else if (!seen.has("ITERATION_COUNTS") && (ITERATION_COUNTS.has(part) || DIGIT.test(part))) {
              result.iterationCount = part;
              seen.add("ITERATION_COUNTS");
            } else if (!seen.has("TIMING_FUNCTION") && TIMINGS.has(part)) {
              result.timingFunction = part;
              seen.add("TIMING_FUNCTION");
            } else if (!seen.has("TIMING_FUNCTION") && TIMING_FNS.some((f) => part.startsWith(`${f}(`))) {
              result.timingFunction = part;
              seen.add("TIMING_FUNCTION");
            } else if (!seen.has("DURATION") && TIME.test(part)) {
              result.duration = part;
              seen.add("DURATION");
            } else if (!seen.has("DELAY") && TIME.test(part)) {
              result.delay = part;
              seen.add("DELAY");
            } else if (!seen.has("NAME")) {
              result.name = part;
              seen.add("NAME");
            } else {
              if (!result.unknown) result.unknown = [];
              result.unknown.push(part);
            }
          }
          return result;
        });
      }
    }
  });

  // tailwindcss/lib/util/flattenColorPalette.js
  var require_flattenColorPalette = __commonJS({
    "tailwindcss/lib/util/flattenColorPalette.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var flattenColorPalette = (colors2) => Object.assign({}, ...Object.entries(colors2 !== null && colors2 !== void 0 ? colors2 : {}).flatMap(([color, values]) => typeof values == "object" ? Object.entries(flattenColorPalette(values)).map(([number, hex]) => ({
        [color + (number === "DEFAULT" ? "" : `-${number}`)]: hex
      })) : [
        {
          [`${color}`]: values
        }
      ]));
      var _default = flattenColorPalette;
    }
  });

  // tailwindcss/lib/util/toColorValue.js
  var require_toColorValue = __commonJS({
    "tailwindcss/lib/util/toColorValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return toColorValue;
        }
      });
      function toColorValue(maybeFunction) {
        return typeof maybeFunction === "function" ? maybeFunction({}) : maybeFunction;
      }
    }
  });

  // tailwindcss/package.json
  var require_package = __commonJS({
    "tailwindcss/package.json"(exports, module) {
      module.exports = {
        name: "tailwindcss",
        version: "3.4.17",
        description: "A utility-first CSS framework for rapidly building custom user interfaces.",
        license: "MIT",
        main: "lib/index.js",
        types: "types/index.d.ts",
        repository: "https://github.com/tailwindlabs/tailwindcss.git",
        bugs: "https://github.com/tailwindlabs/tailwindcss/issues",
        homepage: "https://tailwindcss.com",
        bin: {
          tailwind: "lib/cli.js",
          tailwindcss: "lib/cli.js"
        },
        scripts: {
          prebuild: "npm run generate && rimraf lib",
          build: "swc src --out-dir lib --copy-files",
          postbuild: "esbuild lib/cli-peer-dependencies.js --bundle --platform=node --outfile=peers/index.js --define:process.env.CSS_TRANSFORMER_WASM=false",
          "rebuild-fixtures": "npm run build && node -r @swc/register scripts/rebuildFixtures.js",
          style: "eslint .",
          pretest: "npm run generate",
          test: "jest",
          "test:integrations": "npm run test --prefix ./integrations",
          "install:integrations": "node scripts/install-integrations.js",
          "generate:plugin-list": "node -r @swc/register scripts/create-plugin-list.js",
          "generate:types": "node -r @swc/register scripts/generate-types.js",
          generate: "npm run generate:plugin-list && npm run generate:types",
          "release-channel": "node ./scripts/release-channel.js",
          "release-notes": "node ./scripts/release-notes.js",
          prepublishOnly: "npm install --force && npm run build"
        },
        files: [
          "src/*",
          "cli/*",
          "lib/*",
          "peers/*",
          "scripts/*.js",
          "stubs/*",
          "nesting/*",
          "types/**/*",
          "*.d.ts",
          "*.css",
          "*.js"
        ],
        devDependencies: {
          "@swc/cli": "0.1.62",
          "@swc/core": "1.3.55",
          "@swc/jest": "0.2.26",
          "@swc/register": "0.1.10",
          autoprefixer: "^10.4.20",
          browserslist: "^4.24.0",
          concurrently: "^8.2.2",
          cssnano: "^6.1.2",
          esbuild: "^0.24.0",
          eslint: "^8.57.1",
          "eslint-config-prettier": "^8.10.0",
          "eslint-plugin-prettier": "^4.2.1",
          jest: "^29.7.0",
          "jest-diff": "^29.7.0",
          lightningcss: "1.27.0",
          prettier: "^2.8.8",
          rimraf: "^5.0.10",
          "source-map-js": "^1.2.1",
          turbo: "^1.13.4"
        },
        dependencies: {
          "@alloc/quick-lru": "^5.2.0",
          arg: "^5.0.2",
          chokidar: "^3.6.0",
          didyoumean: "^1.2.2",
          dlv: "^1.1.3",
          "fast-glob": "^3.3.2",
          "glob-parent": "^6.0.2",
          "is-glob": "^4.0.3",
          jiti: "^1.21.6",
          lilconfig: "^3.1.3",
          micromatch: "^4.0.8",
          "normalize-path": "^3.0.0",
          "object-hash": "^3.0.0",
          picocolors: "^1.1.1",
          postcss: "^8.4.47",
          "postcss-import": "^15.1.0",
          "postcss-js": "^4.0.1",
          "postcss-load-config": "^4.0.2",
          "postcss-nested": "^6.2.0",
          "postcss-selector-parser": "^6.1.2",
          resolve: "^1.22.8",
          sucrase: "^3.35.0"
        },
        browserslist: [
          "> 1%",
          "not edge <= 18",
          "not ie 11",
          "not op_mini all"
        ],
        jest: {
          testTimeout: 3e4,
          setupFilesAfterEnv: [
            "<rootDir>/jest/customMatchers.js"
          ],
          testPathIgnorePatterns: [
            "/node_modules/",
            "/integrations/",
            "/standalone-cli/",
            "\\.test\\.skip\\.js$"
          ],
          transformIgnorePatterns: [
            "node_modules/(?!lightningcss)"
          ],
          transform: {
            "\\.js$": "@swc/jest",
            "\\.ts$": "@swc/jest"
          }
        },
        engines: {
          node: ">=14.0.0"
        }
      };
    }
  });

  // tailwindcss/lib/util/normalizeScreens.js
  var require_normalizeScreens = __commonJS({
    "tailwindcss/lib/util/normalizeScreens.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        normalizeScreens: function() {
          return normalizeScreens;
        },
        isScreenSortable: function() {
          return isScreenSortable;
        },
        compareScreens: function() {
          return compareScreens;
        },
        toScreen: function() {
          return toScreen;
        }
      });
      function normalizeScreens(screens, root = true) {
        if (Array.isArray(screens)) {
          return screens.map((screen) => {
            if (root && Array.isArray(screen)) {
              throw new Error("The tuple syntax is not supported for `screens`.");
            }
            if (typeof screen === "string") {
              return {
                name: screen.toString(),
                not: false,
                values: [
                  {
                    min: screen,
                    max: void 0
                  }
                ]
              };
            }
            let [name, options] = screen;
            name = name.toString();
            if (typeof options === "string") {
              return {
                name,
                not: false,
                values: [
                  {
                    min: options,
                    max: void 0
                  }
                ]
              };
            }
            if (Array.isArray(options)) {
              return {
                name,
                not: false,
                values: options.map((option) => resolveValue(option))
              };
            }
            return {
              name,
              not: false,
              values: [
                resolveValue(options)
              ]
            };
          });
        }
        return normalizeScreens(Object.entries(screens !== null && screens !== void 0 ? screens : {}), false);
      }
      function isScreenSortable(screen) {
        if (screen.values.length !== 1) {
          return {
            result: false,
            reason: "multiple-values"
          };
        } else if (screen.values[0].raw !== void 0) {
          return {
            result: false,
            reason: "raw-values"
          };
        } else if (screen.values[0].min !== void 0 && screen.values[0].max !== void 0) {
          return {
            result: false,
            reason: "min-and-max"
          };
        }
        return {
          result: true,
          reason: null
        };
      }
      function compareScreens(type, a, z) {
        let aScreen = toScreen(a, type);
        let zScreen = toScreen(z, type);
        let aSorting = isScreenSortable(aScreen);
        let bSorting = isScreenSortable(zScreen);
        if (aSorting.reason === "multiple-values" || bSorting.reason === "multiple-values") {
          throw new Error("Attempted to sort a screen with multiple values. This should never happen. Please open a bug report.");
        } else if (aSorting.reason === "raw-values" || bSorting.reason === "raw-values") {
          throw new Error("Attempted to sort a screen with raw values. This should never happen. Please open a bug report.");
        } else if (aSorting.reason === "min-and-max" || bSorting.reason === "min-and-max") {
          throw new Error("Attempted to sort a screen with both min and max values. This should never happen. Please open a bug report.");
        }
        let { min: aMin, max: aMax } = aScreen.values[0];
        let { min: zMin, max: zMax } = zScreen.values[0];
        if (a.not) [aMin, aMax] = [
          aMax,
          aMin
        ];
        if (z.not) [zMin, zMax] = [
          zMax,
          zMin
        ];
        aMin = aMin === void 0 ? aMin : parseFloat(aMin);
        aMax = aMax === void 0 ? aMax : parseFloat(aMax);
        zMin = zMin === void 0 ? zMin : parseFloat(zMin);
        zMax = zMax === void 0 ? zMax : parseFloat(zMax);
        let [aValue, zValue] = type === "min" ? [
          aMin,
          zMin
        ] : [
          zMax,
          aMax
        ];
        return aValue - zValue;
      }
      function toScreen(value, type) {
        if (typeof value === "object") {
          return value;
        }
        return {
          name: "arbitrary-screen",
          values: [
            {
              [type]: value
            }
          ]
        };
      }
      function resolveValue({ "min-width": _minWidth, min = _minWidth, max, raw } = {}) {
        return {
          min,
          max,
          raw
        };
      }
    }
  });

  // tailwindcss/lib/util/removeAlphaVariables.js
  var require_removeAlphaVariables = __commonJS({
    "tailwindcss/lib/util/removeAlphaVariables.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "removeAlphaVariables", {
        enumerable: true,
        get: function() {
          return removeAlphaVariables;
        }
      });
      function removeAlphaVariables(container, toRemove) {
        container.walkDecls((decl) => {
          if (toRemove.includes(decl.prop)) {
            decl.remove();
            return;
          }
          for (let varName of toRemove) {
            if (decl.value.includes(`/ var(${varName})`)) {
              decl.value = decl.value.replace(`/ var(${varName})`, "");
            } else if (decl.value.includes(`/ var(${varName}, 1)`)) {
              decl.value = decl.value.replace(`/ var(${varName}, 1)`, "");
            }
          }
        });
      }
    }
  });

  // tailwindcss/lib/corePlugins.js
  var require_corePlugins = __commonJS({
    "tailwindcss/lib/corePlugins.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        variantPlugins: function() {
          return variantPlugins;
        },
        corePlugins: function() {
          return corePlugins;
        }
      });
      var _fs = /* @__PURE__ */ _interop_require_default(require_tailwind_preflight_fs());
      var _path = /* @__PURE__ */ _interop_require_wildcard(require_tailwind_preflight_path());
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _createUtilityPlugin = /* @__PURE__ */ _interop_require_default(require_createUtilityPlugin());
      var _buildMediaQuery = /* @__PURE__ */ _interop_require_default(require_buildMediaQuery());
      var _escapeClassName = /* @__PURE__ */ _interop_require_default(require_escapeClassName());
      var _parseAnimationValue = /* @__PURE__ */ _interop_require_default(require_parseAnimationValue());
      var _flattenColorPalette = /* @__PURE__ */ _interop_require_default(require_flattenColorPalette());
      var _withAlphaVariable = /* @__PURE__ */ _interop_require_wildcard(require_withAlphaVariable());
      var _toColorValue = /* @__PURE__ */ _interop_require_default(require_toColorValue());
      var _isPlainObject = /* @__PURE__ */ _interop_require_default(require_isPlainObject());
      var _transformThemeValue = /* @__PURE__ */ _interop_require_default(require_transformThemeValue());
      var _packagejson = require_package();
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      var _normalizeScreens = require_normalizeScreens();
      var _parseBoxShadowValue = require_parseBoxShadowValue();
      var _removeAlphaVariables = require_removeAlphaVariables();
      var _featureFlags = require_featureFlags();
      var _dataTypes = require_dataTypes();
      var _setupContextUtils = require_setupContextUtils();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      var variantPlugins = {
        childVariant: ({ addVariant }) => {
          addVariant("*", "& > *");
        },
        pseudoElementVariants: ({ addVariant }) => {
          addVariant("first-letter", "&::first-letter");
          addVariant("first-line", "&::first-line");
          addVariant("marker", [
            ({ container }) => {
              (0, _removeAlphaVariables.removeAlphaVariables)(container, [
                "--tw-text-opacity"
              ]);
              return "& *::marker";
            },
            ({ container }) => {
              (0, _removeAlphaVariables.removeAlphaVariables)(container, [
                "--tw-text-opacity"
              ]);
              return "&::marker";
            }
          ]);
          addVariant("selection", [
            "& *::selection",
            "&::selection"
          ]);
          addVariant("file", "&::file-selector-button");
          addVariant("placeholder", "&::placeholder");
          addVariant("backdrop", "&::backdrop");
          addVariant("before", ({ container }) => {
            container.walkRules((rule) => {
              let foundContent = false;
              rule.walkDecls("content", () => {
                foundContent = true;
              });
              if (!foundContent) {
                rule.prepend(_postcss.default.decl({
                  prop: "content",
                  value: "var(--tw-content)"
                }));
              }
            });
            return "&::before";
          });
          addVariant("after", ({ container }) => {
            container.walkRules((rule) => {
              let foundContent = false;
              rule.walkDecls("content", () => {
                foundContent = true;
              });
              if (!foundContent) {
                rule.prepend(_postcss.default.decl({
                  prop: "content",
                  value: "var(--tw-content)"
                }));
              }
            });
            return "&::after";
          });
        },
        pseudoClassVariants: ({ addVariant, matchVariant, config, prefix }) => {
          let pseudoVariants = [
            // Positional
            [
              "first",
              "&:first-child"
            ],
            [
              "last",
              "&:last-child"
            ],
            [
              "only",
              "&:only-child"
            ],
            [
              "odd",
              "&:nth-child(odd)"
            ],
            [
              "even",
              "&:nth-child(even)"
            ],
            "first-of-type",
            "last-of-type",
            "only-of-type",
            // State
            [
              "visited",
              ({ container }) => {
                (0, _removeAlphaVariables.removeAlphaVariables)(container, [
                  "--tw-text-opacity",
                  "--tw-border-opacity",
                  "--tw-bg-opacity"
                ]);
                return "&:visited";
              }
            ],
            "target",
            [
              "open",
              "&[open]"
            ],
            // Forms
            "default",
            "checked",
            "indeterminate",
            "placeholder-shown",
            "autofill",
            "optional",
            "required",
            "valid",
            "invalid",
            "in-range",
            "out-of-range",
            "read-only",
            // Content
            "empty",
            // Interactive
            "focus-within",
            [
              "hover",
              !(0, _featureFlags.flagEnabled)(config(), "hoverOnlyWhenSupported") ? "&:hover" : "@media (hover: hover) and (pointer: fine) { &:hover }"
            ],
            "focus",
            "focus-visible",
            "active",
            "enabled",
            "disabled"
          ].map((variant) => Array.isArray(variant) ? variant : [
            variant,
            `&:${variant}`
          ]);
          for (let [variantName, state] of pseudoVariants) {
            addVariant(variantName, (ctx) => {
              let result = typeof state === "function" ? state(ctx) : state;
              return result;
            });
          }
          let variants = {
            group: (_, { modifier }) => modifier ? [
              `:merge(${prefix(".group")}\\/${(0, _escapeClassName.default)(modifier)})`,
              " &"
            ] : [
              `:merge(${prefix(".group")})`,
              " &"
            ],
            peer: (_, { modifier }) => modifier ? [
              `:merge(${prefix(".peer")}\\/${(0, _escapeClassName.default)(modifier)})`,
              " ~ &"
            ] : [
              `:merge(${prefix(".peer")})`,
              " ~ &"
            ]
          };
          for (let [name, fn] of Object.entries(variants)) {
            matchVariant(name, (value = "", extra) => {
              let result = (0, _dataTypes.normalize)(typeof value === "function" ? value(extra) : value);
              if (!result.includes("&")) result = "&" + result;
              let [a, b] = fn("", extra);
              let start = null;
              let end = null;
              let quotes = 0;
              for (let i = 0; i < result.length; ++i) {
                let c = result[i];
                if (c === "&") {
                  start = i;
                } else if (c === "'" || c === '"') {
                  quotes += 1;
                } else if (start !== null && c === " " && !quotes) {
                  end = i;
                }
              }
              if (start !== null && end === null) {
                end = result.length;
              }
              return result.slice(0, start) + a + result.slice(start + 1, end) + b + result.slice(end);
            }, {
              values: Object.fromEntries(pseudoVariants),
              [_setupContextUtils.INTERNAL_FEATURES]: {
                respectPrefix: false
              }
            });
          }
        },
        directionVariants: ({ addVariant }) => {
          addVariant("ltr", '&:where([dir="ltr"], [dir="ltr"] *)');
          addVariant("rtl", '&:where([dir="rtl"], [dir="rtl"] *)');
        },
        reducedMotionVariants: ({ addVariant }) => {
          addVariant("motion-safe", "@media (prefers-reduced-motion: no-preference)");
          addVariant("motion-reduce", "@media (prefers-reduced-motion: reduce)");
        },
        darkVariants: ({ config, addVariant }) => {
          let [mode, selector = ".dark"] = [].concat(config("darkMode", "media"));
          if (mode === false) {
            mode = "media";
            _log.default.warn("darkmode-false", [
              "The `darkMode` option in your Tailwind CSS configuration is set to `false`, which now behaves the same as `media`.",
              "Change `darkMode` to `media` or remove it entirely.",
              "https://tailwindcss.com/docs/upgrade-guide#remove-dark-mode-configuration"
            ]);
          }
          if (mode === "variant") {
            let formats;
            if (Array.isArray(selector)) {
              formats = selector;
            } else if (typeof selector === "function") {
              formats = selector;
            } else if (typeof selector === "string") {
              formats = [
                selector
              ];
            }
            if (Array.isArray(formats)) {
              for (let format of formats) {
                if (format === ".dark") {
                  mode = false;
                  _log.default.warn("darkmode-variant-without-selector", [
                    "When using `variant` for `darkMode`, you must provide a selector.",
                    'Example: `darkMode: ["variant", ".your-selector &"]`'
                  ]);
                } else if (!format.includes("&")) {
                  mode = false;
                  _log.default.warn("darkmode-variant-without-ampersand", [
                    "When using `variant` for `darkMode`, your selector must contain `&`.",
                    'Example `darkMode: ["variant", ".your-selector &"]`'
                  ]);
                }
              }
            }
            selector = formats;
          }
          if (mode === "selector") {
            addVariant("dark", `&:where(${selector}, ${selector} *)`);
          } else if (mode === "media") {
            addVariant("dark", "@media (prefers-color-scheme: dark)");
          } else if (mode === "variant") {
            addVariant("dark", selector);
          } else if (mode === "class") {
            addVariant("dark", `&:is(${selector} *)`);
          }
        },
        printVariant: ({ addVariant }) => {
          addVariant("print", "@media print");
        },
        screenVariants: ({ theme, addVariant, matchVariant }) => {
          var _theme;
          let rawScreens = (_theme = theme("screens")) !== null && _theme !== void 0 ? _theme : {};
          let areSimpleScreens = Object.values(rawScreens).every((v) => typeof v === "string");
          let screens = (0, _normalizeScreens.normalizeScreens)(theme("screens"));
          let unitCache = /* @__PURE__ */ new Set([]);
          function units(value) {
            var _value_match;
            var _value_match_;
            return (_value_match_ = (_value_match = value.match(/(\D+)$/)) === null || _value_match === void 0 ? void 0 : _value_match[1]) !== null && _value_match_ !== void 0 ? _value_match_ : "(none)";
          }
          function recordUnits(value) {
            if (value !== void 0) {
              unitCache.add(units(value));
            }
          }
          function canUseUnits(value) {
            recordUnits(value);
            return unitCache.size === 1;
          }
          for (const screen of screens) {
            for (const value of screen.values) {
              recordUnits(value.min);
              recordUnits(value.max);
            }
          }
          let screensUseConsistentUnits = unitCache.size <= 1;
          function buildScreenValues(type) {
            return Object.fromEntries(screens.filter((screen) => (0, _normalizeScreens.isScreenSortable)(screen).result).map((screen) => {
              let { min, max } = screen.values[0];
              if (type === "min" && min !== void 0) {
                return screen;
              } else if (type === "min" && max !== void 0) {
                return {
                  ...screen,
                  not: !screen.not
                };
              } else if (type === "max" && max !== void 0) {
                return screen;
              } else if (type === "max" && min !== void 0) {
                return {
                  ...screen,
                  not: !screen.not
                };
              }
            }).map((screen) => [
              screen.name,
              screen
            ]));
          }
          function buildSort(type) {
            return (a, z) => (0, _normalizeScreens.compareScreens)(type, a.value, z.value);
          }
          let maxSort = buildSort("max");
          let minSort = buildSort("min");
          function buildScreenVariant(type) {
            return (value) => {
              if (!areSimpleScreens) {
                _log.default.warn("complex-screen-config", [
                  "The `min-*` and `max-*` variants are not supported with a `screens` configuration containing objects."
                ]);
                return [];
              } else if (!screensUseConsistentUnits) {
                _log.default.warn("mixed-screen-units", [
                  "The `min-*` and `max-*` variants are not supported with a `screens` configuration containing mixed units."
                ]);
                return [];
              } else if (typeof value === "string" && !canUseUnits(value)) {
                _log.default.warn("minmax-have-mixed-units", [
                  "The `min-*` and `max-*` variants are not supported with a `screens` configuration containing mixed units."
                ]);
                return [];
              }
              return [
                `@media ${(0, _buildMediaQuery.default)((0, _normalizeScreens.toScreen)(value, type))}`
              ];
            };
          }
          matchVariant("max", buildScreenVariant("max"), {
            sort: maxSort,
            values: areSimpleScreens ? buildScreenValues("max") : {}
          });
          let id = "min-screens";
          for (let screen of screens) {
            addVariant(screen.name, `@media ${(0, _buildMediaQuery.default)(screen)}`, {
              id,
              sort: areSimpleScreens && screensUseConsistentUnits ? minSort : void 0,
              value: screen
            });
          }
          matchVariant("min", buildScreenVariant("min"), {
            id,
            sort: minSort
          });
        },
        supportsVariants: ({ matchVariant, theme }) => {
          var _theme;
          matchVariant("supports", (value = "") => {
            let check = (0, _dataTypes.normalize)(value);
            let isRaw = /^\w*\s*\(/.test(check);
            check = isRaw ? check.replace(/\b(and|or|not)\b/g, " $1 ") : check;
            if (isRaw) {
              return `@supports ${check}`;
            }
            if (!check.includes(":")) {
              check = `${check}: var(--tw)`;
            }
            if (!(check.startsWith("(") && check.endsWith(")"))) {
              check = `(${check})`;
            }
            return `@supports ${check}`;
          }, {
            values: (_theme = theme("supports")) !== null && _theme !== void 0 ? _theme : {}
          });
        },
        hasVariants: ({ matchVariant, prefix }) => {
          matchVariant("has", (value) => `&:has(${(0, _dataTypes.normalize)(value)})`, {
            values: {},
            [_setupContextUtils.INTERNAL_FEATURES]: {
              respectPrefix: false
            }
          });
          matchVariant("group-has", (value, { modifier }) => modifier ? `:merge(${prefix(".group")}\\/${modifier}):has(${(0, _dataTypes.normalize)(value)}) &` : `:merge(${prefix(".group")}):has(${(0, _dataTypes.normalize)(value)}) &`, {
            values: {},
            [_setupContextUtils.INTERNAL_FEATURES]: {
              respectPrefix: false
            }
          });
          matchVariant("peer-has", (value, { modifier }) => modifier ? `:merge(${prefix(".peer")}\\/${modifier}):has(${(0, _dataTypes.normalize)(value)}) ~ &` : `:merge(${prefix(".peer")}):has(${(0, _dataTypes.normalize)(value)}) ~ &`, {
            values: {},
            [_setupContextUtils.INTERNAL_FEATURES]: {
              respectPrefix: false
            }
          });
        },
        ariaVariants: ({ matchVariant, theme }) => {
          var _theme;
          matchVariant("aria", (value) => `&[aria-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}]`, {
            values: (_theme = theme("aria")) !== null && _theme !== void 0 ? _theme : {}
          });
          var _theme1;
          matchVariant("group-aria", (value, { modifier }) => modifier ? `:merge(.group\\/${modifier})[aria-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] &` : `:merge(.group)[aria-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] &`, {
            values: (_theme1 = theme("aria")) !== null && _theme1 !== void 0 ? _theme1 : {}
          });
          var _theme2;
          matchVariant("peer-aria", (value, { modifier }) => modifier ? `:merge(.peer\\/${modifier})[aria-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] ~ &` : `:merge(.peer)[aria-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] ~ &`, {
            values: (_theme2 = theme("aria")) !== null && _theme2 !== void 0 ? _theme2 : {}
          });
        },
        dataVariants: ({ matchVariant, theme }) => {
          var _theme;
          matchVariant("data", (value) => `&[data-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}]`, {
            values: (_theme = theme("data")) !== null && _theme !== void 0 ? _theme : {}
          });
          var _theme1;
          matchVariant("group-data", (value, { modifier }) => modifier ? `:merge(.group\\/${modifier})[data-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] &` : `:merge(.group)[data-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] &`, {
            values: (_theme1 = theme("data")) !== null && _theme1 !== void 0 ? _theme1 : {}
          });
          var _theme2;
          matchVariant("peer-data", (value, { modifier }) => modifier ? `:merge(.peer\\/${modifier})[data-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] ~ &` : `:merge(.peer)[data-${(0, _dataTypes.normalizeAttributeSelectors)((0, _dataTypes.normalize)(value))}] ~ &`, {
            values: (_theme2 = theme("data")) !== null && _theme2 !== void 0 ? _theme2 : {}
          });
        },
        orientationVariants: ({ addVariant }) => {
          addVariant("portrait", "@media (orientation: portrait)");
          addVariant("landscape", "@media (orientation: landscape)");
        },
        prefersContrastVariants: ({ addVariant }) => {
          addVariant("contrast-more", "@media (prefers-contrast: more)");
          addVariant("contrast-less", "@media (prefers-contrast: less)");
        },
        forcedColorsVariants: ({ addVariant }) => {
          addVariant("forced-colors", "@media (forced-colors: active)");
        }
      };
      var cssTransformValue = [
        "translate(var(--tw-translate-x), var(--tw-translate-y))",
        "rotate(var(--tw-rotate))",
        "skewX(var(--tw-skew-x))",
        "skewY(var(--tw-skew-y))",
        "scaleX(var(--tw-scale-x))",
        "scaleY(var(--tw-scale-y))"
      ].join(" ");
      var cssFilterValue = [
        "var(--tw-blur)",
        "var(--tw-brightness)",
        "var(--tw-contrast)",
        "var(--tw-grayscale)",
        "var(--tw-hue-rotate)",
        "var(--tw-invert)",
        "var(--tw-saturate)",
        "var(--tw-sepia)",
        "var(--tw-drop-shadow)"
      ].join(" ");
      var cssBackdropFilterValue = [
        "var(--tw-backdrop-blur)",
        "var(--tw-backdrop-brightness)",
        "var(--tw-backdrop-contrast)",
        "var(--tw-backdrop-grayscale)",
        "var(--tw-backdrop-hue-rotate)",
        "var(--tw-backdrop-invert)",
        "var(--tw-backdrop-opacity)",
        "var(--tw-backdrop-saturate)",
        "var(--tw-backdrop-sepia)"
      ].join(" ");
      var corePlugins = {
        preflight: ({ addBase }) => {
          let preflightStyles = _postcss.default.parse(_fs.default.readFileSync(_path.join("/tailwindcss/lib", "./css/preflight.css"), "utf8"));
          addBase([
            _postcss.default.comment({
              text: `! tailwindcss v${_packagejson.version} | MIT License | https://tailwindcss.com`
            }),
            ...preflightStyles.nodes
          ]);
        },
        container: /* @__PURE__ */ (() => {
          function extractMinWidths(breakpoints = []) {
            return breakpoints.flatMap((breakpoint) => breakpoint.values.map((breakpoint2) => breakpoint2.min)).filter((v) => v !== void 0);
          }
          function mapMinWidthsToPadding(minWidths, screens, paddings) {
            if (typeof paddings === "undefined") {
              return [];
            }
            if (!(typeof paddings === "object" && paddings !== null)) {
              return [
                {
                  screen: "DEFAULT",
                  minWidth: 0,
                  padding: paddings
                }
              ];
            }
            let mapping = [];
            if (paddings.DEFAULT) {
              mapping.push({
                screen: "DEFAULT",
                minWidth: 0,
                padding: paddings.DEFAULT
              });
            }
            for (let minWidth of minWidths) {
              for (let screen of screens) {
                for (let { min } of screen.values) {
                  if (min === minWidth) {
                    mapping.push({
                      minWidth,
                      padding: paddings[screen.name]
                    });
                  }
                }
              }
            }
            return mapping;
          }
          return function({ addComponents, theme }) {
            let screens = (0, _normalizeScreens.normalizeScreens)(theme("container.screens", theme("screens")));
            let minWidths = extractMinWidths(screens);
            let paddings = mapMinWidthsToPadding(minWidths, screens, theme("container.padding"));
            let generatePaddingFor = (minWidth) => {
              let paddingConfig = paddings.find((padding) => padding.minWidth === minWidth);
              if (!paddingConfig) {
                return {};
              }
              return {
                paddingRight: paddingConfig.padding,
                paddingLeft: paddingConfig.padding
              };
            };
            let atRules = Array.from(new Set(minWidths.slice().sort((a, z) => parseInt(a) - parseInt(z)))).map((minWidth) => ({
              [`@media (min-width: ${minWidth})`]: {
                ".container": {
                  "max-width": minWidth,
                  ...generatePaddingFor(minWidth)
                }
              }
            }));
            addComponents([
              {
                ".container": Object.assign({
                  width: "100%"
                }, theme("container.center", false) ? {
                  marginRight: "auto",
                  marginLeft: "auto"
                } : {}, generatePaddingFor(0))
              },
              ...atRules
            ]);
          };
        })(),
        accessibility: ({ addUtilities }) => {
          addUtilities({
            ".sr-only": {
              position: "absolute",
              width: "1px",
              height: "1px",
              padding: "0",
              margin: "-1px",
              overflow: "hidden",
              clip: "rect(0, 0, 0, 0)",
              whiteSpace: "nowrap",
              borderWidth: "0"
            },
            ".not-sr-only": {
              position: "static",
              width: "auto",
              height: "auto",
              padding: "0",
              margin: "0",
              overflow: "visible",
              clip: "auto",
              whiteSpace: "normal"
            }
          });
        },
        pointerEvents: ({ addUtilities }) => {
          addUtilities({
            ".pointer-events-none": {
              "pointer-events": "none"
            },
            ".pointer-events-auto": {
              "pointer-events": "auto"
            }
          });
        },
        visibility: ({ addUtilities }) => {
          addUtilities({
            ".visible": {
              visibility: "visible"
            },
            ".invisible": {
              visibility: "hidden"
            },
            ".collapse": {
              visibility: "collapse"
            }
          });
        },
        position: ({ addUtilities }) => {
          addUtilities({
            ".static": {
              position: "static"
            },
            ".fixed": {
              position: "fixed"
            },
            ".absolute": {
              position: "absolute"
            },
            ".relative": {
              position: "relative"
            },
            ".sticky": {
              position: "sticky"
            }
          });
        },
        inset: (0, _createUtilityPlugin.default)("inset", [
          [
            "inset",
            [
              "inset"
            ]
          ],
          [
            [
              "inset-x",
              [
                "left",
                "right"
              ]
            ],
            [
              "inset-y",
              [
                "top",
                "bottom"
              ]
            ]
          ],
          [
            [
              "start",
              [
                "inset-inline-start"
              ]
            ],
            [
              "end",
              [
                "inset-inline-end"
              ]
            ],
            [
              "top",
              [
                "top"
              ]
            ],
            [
              "right",
              [
                "right"
              ]
            ],
            [
              "bottom",
              [
                "bottom"
              ]
            ],
            [
              "left",
              [
                "left"
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        isolation: ({ addUtilities }) => {
          addUtilities({
            ".isolate": {
              isolation: "isolate"
            },
            ".isolation-auto": {
              isolation: "auto"
            }
          });
        },
        zIndex: (0, _createUtilityPlugin.default)("zIndex", [
          [
            "z",
            [
              "zIndex"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        order: (0, _createUtilityPlugin.default)("order", void 0, {
          supportsNegativeValues: true
        }),
        gridColumn: (0, _createUtilityPlugin.default)("gridColumn", [
          [
            "col",
            [
              "gridColumn"
            ]
          ]
        ]),
        gridColumnStart: (0, _createUtilityPlugin.default)("gridColumnStart", [
          [
            "col-start",
            [
              "gridColumnStart"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        gridColumnEnd: (0, _createUtilityPlugin.default)("gridColumnEnd", [
          [
            "col-end",
            [
              "gridColumnEnd"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        gridRow: (0, _createUtilityPlugin.default)("gridRow", [
          [
            "row",
            [
              "gridRow"
            ]
          ]
        ]),
        gridRowStart: (0, _createUtilityPlugin.default)("gridRowStart", [
          [
            "row-start",
            [
              "gridRowStart"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        gridRowEnd: (0, _createUtilityPlugin.default)("gridRowEnd", [
          [
            "row-end",
            [
              "gridRowEnd"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        float: ({ addUtilities }) => {
          addUtilities({
            ".float-start": {
              float: "inline-start"
            },
            ".float-end": {
              float: "inline-end"
            },
            ".float-right": {
              float: "right"
            },
            ".float-left": {
              float: "left"
            },
            ".float-none": {
              float: "none"
            }
          });
        },
        clear: ({ addUtilities }) => {
          addUtilities({
            ".clear-start": {
              clear: "inline-start"
            },
            ".clear-end": {
              clear: "inline-end"
            },
            ".clear-left": {
              clear: "left"
            },
            ".clear-right": {
              clear: "right"
            },
            ".clear-both": {
              clear: "both"
            },
            ".clear-none": {
              clear: "none"
            }
          });
        },
        margin: (0, _createUtilityPlugin.default)("margin", [
          [
            "m",
            [
              "margin"
            ]
          ],
          [
            [
              "mx",
              [
                "margin-left",
                "margin-right"
              ]
            ],
            [
              "my",
              [
                "margin-top",
                "margin-bottom"
              ]
            ]
          ],
          [
            [
              "ms",
              [
                "margin-inline-start"
              ]
            ],
            [
              "me",
              [
                "margin-inline-end"
              ]
            ],
            [
              "mt",
              [
                "margin-top"
              ]
            ],
            [
              "mr",
              [
                "margin-right"
              ]
            ],
            [
              "mb",
              [
                "margin-bottom"
              ]
            ],
            [
              "ml",
              [
                "margin-left"
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        boxSizing: ({ addUtilities }) => {
          addUtilities({
            ".box-border": {
              "box-sizing": "border-box"
            },
            ".box-content": {
              "box-sizing": "content-box"
            }
          });
        },
        lineClamp: ({ matchUtilities, addUtilities, theme }) => {
          matchUtilities({
            "line-clamp": (value) => ({
              overflow: "hidden",
              display: "-webkit-box",
              "-webkit-box-orient": "vertical",
              "-webkit-line-clamp": `${value}`
            })
          }, {
            values: theme("lineClamp")
          });
          addUtilities({
            ".line-clamp-none": {
              overflow: "visible",
              display: "block",
              "-webkit-box-orient": "horizontal",
              "-webkit-line-clamp": "none"
            }
          });
        },
        display: ({ addUtilities }) => {
          addUtilities({
            ".block": {
              display: "block"
            },
            ".inline-block": {
              display: "inline-block"
            },
            ".inline": {
              display: "inline"
            },
            ".flex": {
              display: "flex"
            },
            ".inline-flex": {
              display: "inline-flex"
            },
            ".table": {
              display: "table"
            },
            ".inline-table": {
              display: "inline-table"
            },
            ".table-caption": {
              display: "table-caption"
            },
            ".table-cell": {
              display: "table-cell"
            },
            ".table-column": {
              display: "table-column"
            },
            ".table-column-group": {
              display: "table-column-group"
            },
            ".table-footer-group": {
              display: "table-footer-group"
            },
            ".table-header-group": {
              display: "table-header-group"
            },
            ".table-row-group": {
              display: "table-row-group"
            },
            ".table-row": {
              display: "table-row"
            },
            ".flow-root": {
              display: "flow-root"
            },
            ".grid": {
              display: "grid"
            },
            ".inline-grid": {
              display: "inline-grid"
            },
            ".contents": {
              display: "contents"
            },
            ".list-item": {
              display: "list-item"
            },
            ".hidden": {
              display: "none"
            }
          });
        },
        aspectRatio: (0, _createUtilityPlugin.default)("aspectRatio", [
          [
            "aspect",
            [
              "aspect-ratio"
            ]
          ]
        ]),
        size: (0, _createUtilityPlugin.default)("size", [
          [
            "size",
            [
              "width",
              "height"
            ]
          ]
        ]),
        height: (0, _createUtilityPlugin.default)("height", [
          [
            "h",
            [
              "height"
            ]
          ]
        ]),
        maxHeight: (0, _createUtilityPlugin.default)("maxHeight", [
          [
            "max-h",
            [
              "maxHeight"
            ]
          ]
        ]),
        minHeight: (0, _createUtilityPlugin.default)("minHeight", [
          [
            "min-h",
            [
              "minHeight"
            ]
          ]
        ]),
        width: (0, _createUtilityPlugin.default)("width", [
          [
            "w",
            [
              "width"
            ]
          ]
        ]),
        minWidth: (0, _createUtilityPlugin.default)("minWidth", [
          [
            "min-w",
            [
              "minWidth"
            ]
          ]
        ]),
        maxWidth: (0, _createUtilityPlugin.default)("maxWidth", [
          [
            "max-w",
            [
              "maxWidth"
            ]
          ]
        ]),
        flex: (0, _createUtilityPlugin.default)("flex"),
        flexShrink: (0, _createUtilityPlugin.default)("flexShrink", [
          [
            "flex-shrink",
            [
              "flex-shrink"
            ]
          ],
          [
            "shrink",
            [
              "flex-shrink"
            ]
          ]
        ]),
        flexGrow: (0, _createUtilityPlugin.default)("flexGrow", [
          [
            "flex-grow",
            [
              "flex-grow"
            ]
          ],
          [
            "grow",
            [
              "flex-grow"
            ]
          ]
        ]),
        flexBasis: (0, _createUtilityPlugin.default)("flexBasis", [
          [
            "basis",
            [
              "flex-basis"
            ]
          ]
        ]),
        tableLayout: ({ addUtilities }) => {
          addUtilities({
            ".table-auto": {
              "table-layout": "auto"
            },
            ".table-fixed": {
              "table-layout": "fixed"
            }
          });
        },
        captionSide: ({ addUtilities }) => {
          addUtilities({
            ".caption-top": {
              "caption-side": "top"
            },
            ".caption-bottom": {
              "caption-side": "bottom"
            }
          });
        },
        borderCollapse: ({ addUtilities }) => {
          addUtilities({
            ".border-collapse": {
              "border-collapse": "collapse"
            },
            ".border-separate": {
              "border-collapse": "separate"
            }
          });
        },
        borderSpacing: ({ addDefaults, matchUtilities, theme }) => {
          addDefaults("border-spacing", {
            "--tw-border-spacing-x": 0,
            "--tw-border-spacing-y": 0
          });
          matchUtilities({
            "border-spacing": (value) => {
              return {
                "--tw-border-spacing-x": value,
                "--tw-border-spacing-y": value,
                "@defaults border-spacing": {},
                "border-spacing": "var(--tw-border-spacing-x) var(--tw-border-spacing-y)"
              };
            },
            "border-spacing-x": (value) => {
              return {
                "--tw-border-spacing-x": value,
                "@defaults border-spacing": {},
                "border-spacing": "var(--tw-border-spacing-x) var(--tw-border-spacing-y)"
              };
            },
            "border-spacing-y": (value) => {
              return {
                "--tw-border-spacing-y": value,
                "@defaults border-spacing": {},
                "border-spacing": "var(--tw-border-spacing-x) var(--tw-border-spacing-y)"
              };
            }
          }, {
            values: theme("borderSpacing")
          });
        },
        transformOrigin: (0, _createUtilityPlugin.default)("transformOrigin", [
          [
            "origin",
            [
              "transformOrigin"
            ]
          ]
        ]),
        translate: (0, _createUtilityPlugin.default)("translate", [
          [
            [
              "translate-x",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-translate-x",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ],
            [
              "translate-y",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-translate-y",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        rotate: (0, _createUtilityPlugin.default)("rotate", [
          [
            "rotate",
            [
              [
                "@defaults transform",
                {}
              ],
              "--tw-rotate",
              [
                "transform",
                cssTransformValue
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        skew: (0, _createUtilityPlugin.default)("skew", [
          [
            [
              "skew-x",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-skew-x",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ],
            [
              "skew-y",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-skew-y",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        scale: (0, _createUtilityPlugin.default)("scale", [
          [
            "scale",
            [
              [
                "@defaults transform",
                {}
              ],
              "--tw-scale-x",
              "--tw-scale-y",
              [
                "transform",
                cssTransformValue
              ]
            ]
          ],
          [
            [
              "scale-x",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-scale-x",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ],
            [
              "scale-y",
              [
                [
                  "@defaults transform",
                  {}
                ],
                "--tw-scale-y",
                [
                  "transform",
                  cssTransformValue
                ]
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        transform: ({ addDefaults, addUtilities }) => {
          addDefaults("transform", {
            "--tw-translate-x": "0",
            "--tw-translate-y": "0",
            "--tw-rotate": "0",
            "--tw-skew-x": "0",
            "--tw-skew-y": "0",
            "--tw-scale-x": "1",
            "--tw-scale-y": "1"
          });
          addUtilities({
            ".transform": {
              "@defaults transform": {},
              transform: cssTransformValue
            },
            ".transform-cpu": {
              transform: cssTransformValue
            },
            ".transform-gpu": {
              transform: cssTransformValue.replace("translate(var(--tw-translate-x), var(--tw-translate-y))", "translate3d(var(--tw-translate-x), var(--tw-translate-y), 0)")
            },
            ".transform-none": {
              transform: "none"
            }
          });
        },
        animation: ({ matchUtilities, theme, config }) => {
          let prefixName = (name) => (0, _escapeClassName.default)(config("prefix") + name);
          var _theme;
          let keyframes = Object.fromEntries(Object.entries((_theme = theme("keyframes")) !== null && _theme !== void 0 ? _theme : {}).map(([key, value]) => {
            return [
              key,
              {
                [`@keyframes ${prefixName(key)}`]: value
              }
            ];
          }));
          matchUtilities({
            animate: (value) => {
              let animations = (0, _parseAnimationValue.default)(value);
              return [
                ...animations.flatMap((animation) => keyframes[animation.name]),
                {
                  animation: animations.map(({ name, value: value2 }) => {
                    if (name === void 0 || keyframes[name] === void 0) {
                      return value2;
                    }
                    return value2.replace(name, prefixName(name));
                  }).join(", ")
                }
              ];
            }
          }, {
            values: theme("animation")
          });
        },
        cursor: (0, _createUtilityPlugin.default)("cursor"),
        touchAction: ({ addDefaults, addUtilities }) => {
          addDefaults("touch-action", {
            "--tw-pan-x": " ",
            "--tw-pan-y": " ",
            "--tw-pinch-zoom": " "
          });
          let cssTouchActionValue = "var(--tw-pan-x) var(--tw-pan-y) var(--tw-pinch-zoom)";
          addUtilities({
            ".touch-auto": {
              "touch-action": "auto"
            },
            ".touch-none": {
              "touch-action": "none"
            },
            ".touch-pan-x": {
              "@defaults touch-action": {},
              "--tw-pan-x": "pan-x",
              "touch-action": cssTouchActionValue
            },
            ".touch-pan-left": {
              "@defaults touch-action": {},
              "--tw-pan-x": "pan-left",
              "touch-action": cssTouchActionValue
            },
            ".touch-pan-right": {
              "@defaults touch-action": {},
              "--tw-pan-x": "pan-right",
              "touch-action": cssTouchActionValue
            },
            ".touch-pan-y": {
              "@defaults touch-action": {},
              "--tw-pan-y": "pan-y",
              "touch-action": cssTouchActionValue
            },
            ".touch-pan-up": {
              "@defaults touch-action": {},
              "--tw-pan-y": "pan-up",
              "touch-action": cssTouchActionValue
            },
            ".touch-pan-down": {
              "@defaults touch-action": {},
              "--tw-pan-y": "pan-down",
              "touch-action": cssTouchActionValue
            },
            ".touch-pinch-zoom": {
              "@defaults touch-action": {},
              "--tw-pinch-zoom": "pinch-zoom",
              "touch-action": cssTouchActionValue
            },
            ".touch-manipulation": {
              "touch-action": "manipulation"
            }
          });
        },
        userSelect: ({ addUtilities }) => {
          addUtilities({
            ".select-none": {
              "user-select": "none"
            },
            ".select-text": {
              "user-select": "text"
            },
            ".select-all": {
              "user-select": "all"
            },
            ".select-auto": {
              "user-select": "auto"
            }
          });
        },
        resize: ({ addUtilities }) => {
          addUtilities({
            ".resize-none": {
              resize: "none"
            },
            ".resize-y": {
              resize: "vertical"
            },
            ".resize-x": {
              resize: "horizontal"
            },
            ".resize": {
              resize: "both"
            }
          });
        },
        scrollSnapType: ({ addDefaults, addUtilities }) => {
          addDefaults("scroll-snap-type", {
            "--tw-scroll-snap-strictness": "proximity"
          });
          addUtilities({
            ".snap-none": {
              "scroll-snap-type": "none"
            },
            ".snap-x": {
              "@defaults scroll-snap-type": {},
              "scroll-snap-type": "x var(--tw-scroll-snap-strictness)"
            },
            ".snap-y": {
              "@defaults scroll-snap-type": {},
              "scroll-snap-type": "y var(--tw-scroll-snap-strictness)"
            },
            ".snap-both": {
              "@defaults scroll-snap-type": {},
              "scroll-snap-type": "both var(--tw-scroll-snap-strictness)"
            },
            ".snap-mandatory": {
              "--tw-scroll-snap-strictness": "mandatory"
            },
            ".snap-proximity": {
              "--tw-scroll-snap-strictness": "proximity"
            }
          });
        },
        scrollSnapAlign: ({ addUtilities }) => {
          addUtilities({
            ".snap-start": {
              "scroll-snap-align": "start"
            },
            ".snap-end": {
              "scroll-snap-align": "end"
            },
            ".snap-center": {
              "scroll-snap-align": "center"
            },
            ".snap-align-none": {
              "scroll-snap-align": "none"
            }
          });
        },
        scrollSnapStop: ({ addUtilities }) => {
          addUtilities({
            ".snap-normal": {
              "scroll-snap-stop": "normal"
            },
            ".snap-always": {
              "scroll-snap-stop": "always"
            }
          });
        },
        scrollMargin: (0, _createUtilityPlugin.default)("scrollMargin", [
          [
            "scroll-m",
            [
              "scroll-margin"
            ]
          ],
          [
            [
              "scroll-mx",
              [
                "scroll-margin-left",
                "scroll-margin-right"
              ]
            ],
            [
              "scroll-my",
              [
                "scroll-margin-top",
                "scroll-margin-bottom"
              ]
            ]
          ],
          [
            [
              "scroll-ms",
              [
                "scroll-margin-inline-start"
              ]
            ],
            [
              "scroll-me",
              [
                "scroll-margin-inline-end"
              ]
            ],
            [
              "scroll-mt",
              [
                "scroll-margin-top"
              ]
            ],
            [
              "scroll-mr",
              [
                "scroll-margin-right"
              ]
            ],
            [
              "scroll-mb",
              [
                "scroll-margin-bottom"
              ]
            ],
            [
              "scroll-ml",
              [
                "scroll-margin-left"
              ]
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        scrollPadding: (0, _createUtilityPlugin.default)("scrollPadding", [
          [
            "scroll-p",
            [
              "scroll-padding"
            ]
          ],
          [
            [
              "scroll-px",
              [
                "scroll-padding-left",
                "scroll-padding-right"
              ]
            ],
            [
              "scroll-py",
              [
                "scroll-padding-top",
                "scroll-padding-bottom"
              ]
            ]
          ],
          [
            [
              "scroll-ps",
              [
                "scroll-padding-inline-start"
              ]
            ],
            [
              "scroll-pe",
              [
                "scroll-padding-inline-end"
              ]
            ],
            [
              "scroll-pt",
              [
                "scroll-padding-top"
              ]
            ],
            [
              "scroll-pr",
              [
                "scroll-padding-right"
              ]
            ],
            [
              "scroll-pb",
              [
                "scroll-padding-bottom"
              ]
            ],
            [
              "scroll-pl",
              [
                "scroll-padding-left"
              ]
            ]
          ]
        ]),
        listStylePosition: ({ addUtilities }) => {
          addUtilities({
            ".list-inside": {
              "list-style-position": "inside"
            },
            ".list-outside": {
              "list-style-position": "outside"
            }
          });
        },
        listStyleType: (0, _createUtilityPlugin.default)("listStyleType", [
          [
            "list",
            [
              "listStyleType"
            ]
          ]
        ]),
        listStyleImage: (0, _createUtilityPlugin.default)("listStyleImage", [
          [
            "list-image",
            [
              "listStyleImage"
            ]
          ]
        ]),
        appearance: ({ addUtilities }) => {
          addUtilities({
            ".appearance-none": {
              appearance: "none"
            },
            ".appearance-auto": {
              appearance: "auto"
            }
          });
        },
        columns: (0, _createUtilityPlugin.default)("columns", [
          [
            "columns",
            [
              "columns"
            ]
          ]
        ]),
        breakBefore: ({ addUtilities }) => {
          addUtilities({
            ".break-before-auto": {
              "break-before": "auto"
            },
            ".break-before-avoid": {
              "break-before": "avoid"
            },
            ".break-before-all": {
              "break-before": "all"
            },
            ".break-before-avoid-page": {
              "break-before": "avoid-page"
            },
            ".break-before-page": {
              "break-before": "page"
            },
            ".break-before-left": {
              "break-before": "left"
            },
            ".break-before-right": {
              "break-before": "right"
            },
            ".break-before-column": {
              "break-before": "column"
            }
          });
        },
        breakInside: ({ addUtilities }) => {
          addUtilities({
            ".break-inside-auto": {
              "break-inside": "auto"
            },
            ".break-inside-avoid": {
              "break-inside": "avoid"
            },
            ".break-inside-avoid-page": {
              "break-inside": "avoid-page"
            },
            ".break-inside-avoid-column": {
              "break-inside": "avoid-column"
            }
          });
        },
        breakAfter: ({ addUtilities }) => {
          addUtilities({
            ".break-after-auto": {
              "break-after": "auto"
            },
            ".break-after-avoid": {
              "break-after": "avoid"
            },
            ".break-after-all": {
              "break-after": "all"
            },
            ".break-after-avoid-page": {
              "break-after": "avoid-page"
            },
            ".break-after-page": {
              "break-after": "page"
            },
            ".break-after-left": {
              "break-after": "left"
            },
            ".break-after-right": {
              "break-after": "right"
            },
            ".break-after-column": {
              "break-after": "column"
            }
          });
        },
        gridAutoColumns: (0, _createUtilityPlugin.default)("gridAutoColumns", [
          [
            "auto-cols",
            [
              "gridAutoColumns"
            ]
          ]
        ]),
        gridAutoFlow: ({ addUtilities }) => {
          addUtilities({
            ".grid-flow-row": {
              gridAutoFlow: "row"
            },
            ".grid-flow-col": {
              gridAutoFlow: "column"
            },
            ".grid-flow-dense": {
              gridAutoFlow: "dense"
            },
            ".grid-flow-row-dense": {
              gridAutoFlow: "row dense"
            },
            ".grid-flow-col-dense": {
              gridAutoFlow: "column dense"
            }
          });
        },
        gridAutoRows: (0, _createUtilityPlugin.default)("gridAutoRows", [
          [
            "auto-rows",
            [
              "gridAutoRows"
            ]
          ]
        ]),
        gridTemplateColumns: (0, _createUtilityPlugin.default)("gridTemplateColumns", [
          [
            "grid-cols",
            [
              "gridTemplateColumns"
            ]
          ]
        ]),
        gridTemplateRows: (0, _createUtilityPlugin.default)("gridTemplateRows", [
          [
            "grid-rows",
            [
              "gridTemplateRows"
            ]
          ]
        ]),
        flexDirection: ({ addUtilities }) => {
          addUtilities({
            ".flex-row": {
              "flex-direction": "row"
            },
            ".flex-row-reverse": {
              "flex-direction": "row-reverse"
            },
            ".flex-col": {
              "flex-direction": "column"
            },
            ".flex-col-reverse": {
              "flex-direction": "column-reverse"
            }
          });
        },
        flexWrap: ({ addUtilities }) => {
          addUtilities({
            ".flex-wrap": {
              "flex-wrap": "wrap"
            },
            ".flex-wrap-reverse": {
              "flex-wrap": "wrap-reverse"
            },
            ".flex-nowrap": {
              "flex-wrap": "nowrap"
            }
          });
        },
        placeContent: ({ addUtilities }) => {
          addUtilities({
            ".place-content-center": {
              "place-content": "center"
            },
            ".place-content-start": {
              "place-content": "start"
            },
            ".place-content-end": {
              "place-content": "end"
            },
            ".place-content-between": {
              "place-content": "space-between"
            },
            ".place-content-around": {
              "place-content": "space-around"
            },
            ".place-content-evenly": {
              "place-content": "space-evenly"
            },
            ".place-content-baseline": {
              "place-content": "baseline"
            },
            ".place-content-stretch": {
              "place-content": "stretch"
            }
          });
        },
        placeItems: ({ addUtilities }) => {
          addUtilities({
            ".place-items-start": {
              "place-items": "start"
            },
            ".place-items-end": {
              "place-items": "end"
            },
            ".place-items-center": {
              "place-items": "center"
            },
            ".place-items-baseline": {
              "place-items": "baseline"
            },
            ".place-items-stretch": {
              "place-items": "stretch"
            }
          });
        },
        alignContent: ({ addUtilities }) => {
          addUtilities({
            ".content-normal": {
              "align-content": "normal"
            },
            ".content-center": {
              "align-content": "center"
            },
            ".content-start": {
              "align-content": "flex-start"
            },
            ".content-end": {
              "align-content": "flex-end"
            },
            ".content-between": {
              "align-content": "space-between"
            },
            ".content-around": {
              "align-content": "space-around"
            },
            ".content-evenly": {
              "align-content": "space-evenly"
            },
            ".content-baseline": {
              "align-content": "baseline"
            },
            ".content-stretch": {
              "align-content": "stretch"
            }
          });
        },
        alignItems: ({ addUtilities }) => {
          addUtilities({
            ".items-start": {
              "align-items": "flex-start"
            },
            ".items-end": {
              "align-items": "flex-end"
            },
            ".items-center": {
              "align-items": "center"
            },
            ".items-baseline": {
              "align-items": "baseline"
            },
            ".items-stretch": {
              "align-items": "stretch"
            }
          });
        },
        justifyContent: ({ addUtilities }) => {
          addUtilities({
            ".justify-normal": {
              "justify-content": "normal"
            },
            ".justify-start": {
              "justify-content": "flex-start"
            },
            ".justify-end": {
              "justify-content": "flex-end"
            },
            ".justify-center": {
              "justify-content": "center"
            },
            ".justify-between": {
              "justify-content": "space-between"
            },
            ".justify-around": {
              "justify-content": "space-around"
            },
            ".justify-evenly": {
              "justify-content": "space-evenly"
            },
            ".justify-stretch": {
              "justify-content": "stretch"
            }
          });
        },
        justifyItems: ({ addUtilities }) => {
          addUtilities({
            ".justify-items-start": {
              "justify-items": "start"
            },
            ".justify-items-end": {
              "justify-items": "end"
            },
            ".justify-items-center": {
              "justify-items": "center"
            },
            ".justify-items-stretch": {
              "justify-items": "stretch"
            }
          });
        },
        gap: (0, _createUtilityPlugin.default)("gap", [
          [
            "gap",
            [
              "gap"
            ]
          ],
          [
            [
              "gap-x",
              [
                "columnGap"
              ]
            ],
            [
              "gap-y",
              [
                "rowGap"
              ]
            ]
          ]
        ]),
        space: ({ matchUtilities, addUtilities, theme }) => {
          matchUtilities({
            "space-x": (value) => {
              value = value === "0" ? "0px" : value;
              return {
                "& > :not([hidden]) ~ :not([hidden])": {
                  "--tw-space-x-reverse": "0",
                  "margin-right": `calc(${value} * var(--tw-space-x-reverse))`,
                  "margin-left": `calc(${value} * calc(1 - var(--tw-space-x-reverse)))`
                }
              };
            },
            "space-y": (value) => {
              value = value === "0" ? "0px" : value;
              return {
                "& > :not([hidden]) ~ :not([hidden])": {
                  "--tw-space-y-reverse": "0",
                  "margin-top": `calc(${value} * calc(1 - var(--tw-space-y-reverse)))`,
                  "margin-bottom": `calc(${value} * var(--tw-space-y-reverse))`
                }
              };
            }
          }, {
            values: theme("space"),
            supportsNegativeValues: true
          });
          addUtilities({
            ".space-y-reverse > :not([hidden]) ~ :not([hidden])": {
              "--tw-space-y-reverse": "1"
            },
            ".space-x-reverse > :not([hidden]) ~ :not([hidden])": {
              "--tw-space-x-reverse": "1"
            }
          });
        },
        divideWidth: ({ matchUtilities, addUtilities, theme }) => {
          matchUtilities({
            "divide-x": (value) => {
              value = value === "0" ? "0px" : value;
              return {
                "& > :not([hidden]) ~ :not([hidden])": {
                  "@defaults border-width": {},
                  "--tw-divide-x-reverse": "0",
                  "border-right-width": `calc(${value} * var(--tw-divide-x-reverse))`,
                  "border-left-width": `calc(${value} * calc(1 - var(--tw-divide-x-reverse)))`
                }
              };
            },
            "divide-y": (value) => {
              value = value === "0" ? "0px" : value;
              return {
                "& > :not([hidden]) ~ :not([hidden])": {
                  "@defaults border-width": {},
                  "--tw-divide-y-reverse": "0",
                  "border-top-width": `calc(${value} * calc(1 - var(--tw-divide-y-reverse)))`,
                  "border-bottom-width": `calc(${value} * var(--tw-divide-y-reverse))`
                }
              };
            }
          }, {
            values: theme("divideWidth"),
            type: [
              "line-width",
              "length",
              "any"
            ]
          });
          addUtilities({
            ".divide-y-reverse > :not([hidden]) ~ :not([hidden])": {
              "@defaults border-width": {},
              "--tw-divide-y-reverse": "1"
            },
            ".divide-x-reverse > :not([hidden]) ~ :not([hidden])": {
              "@defaults border-width": {},
              "--tw-divide-x-reverse": "1"
            }
          });
        },
        divideStyle: ({ addUtilities }) => {
          addUtilities({
            ".divide-solid > :not([hidden]) ~ :not([hidden])": {
              "border-style": "solid"
            },
            ".divide-dashed > :not([hidden]) ~ :not([hidden])": {
              "border-style": "dashed"
            },
            ".divide-dotted > :not([hidden]) ~ :not([hidden])": {
              "border-style": "dotted"
            },
            ".divide-double > :not([hidden]) ~ :not([hidden])": {
              "border-style": "double"
            },
            ".divide-none > :not([hidden]) ~ :not([hidden])": {
              "border-style": "none"
            }
          });
        },
        divideColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            divide: (value) => {
              if (!corePlugins2("divideOpacity")) {
                return {
                  ["& > :not([hidden]) ~ :not([hidden])"]: {
                    "border-color": (0, _toColorValue.default)(value)
                  }
                };
              }
              return {
                ["& > :not([hidden]) ~ :not([hidden])"]: (0, _withAlphaVariable.default)({
                  color: value,
                  property: "border-color",
                  variable: "--tw-divide-opacity"
                })
              };
            }
          }, {
            values: (({ DEFAULT: _, ...colors2 }) => colors2)((0, _flattenColorPalette.default)(theme("divideColor"))),
            type: [
              "color",
              "any"
            ]
          });
        },
        divideOpacity: ({ matchUtilities, theme }) => {
          matchUtilities({
            "divide-opacity": (value) => {
              return {
                [`& > :not([hidden]) ~ :not([hidden])`]: {
                  "--tw-divide-opacity": value
                }
              };
            }
          }, {
            values: theme("divideOpacity")
          });
        },
        placeSelf: ({ addUtilities }) => {
          addUtilities({
            ".place-self-auto": {
              "place-self": "auto"
            },
            ".place-self-start": {
              "place-self": "start"
            },
            ".place-self-end": {
              "place-self": "end"
            },
            ".place-self-center": {
              "place-self": "center"
            },
            ".place-self-stretch": {
              "place-self": "stretch"
            }
          });
        },
        alignSelf: ({ addUtilities }) => {
          addUtilities({
            ".self-auto": {
              "align-self": "auto"
            },
            ".self-start": {
              "align-self": "flex-start"
            },
            ".self-end": {
              "align-self": "flex-end"
            },
            ".self-center": {
              "align-self": "center"
            },
            ".self-stretch": {
              "align-self": "stretch"
            },
            ".self-baseline": {
              "align-self": "baseline"
            }
          });
        },
        justifySelf: ({ addUtilities }) => {
          addUtilities({
            ".justify-self-auto": {
              "justify-self": "auto"
            },
            ".justify-self-start": {
              "justify-self": "start"
            },
            ".justify-self-end": {
              "justify-self": "end"
            },
            ".justify-self-center": {
              "justify-self": "center"
            },
            ".justify-self-stretch": {
              "justify-self": "stretch"
            }
          });
        },
        overflow: ({ addUtilities }) => {
          addUtilities({
            ".overflow-auto": {
              overflow: "auto"
            },
            ".overflow-hidden": {
              overflow: "hidden"
            },
            ".overflow-clip": {
              overflow: "clip"
            },
            ".overflow-visible": {
              overflow: "visible"
            },
            ".overflow-scroll": {
              overflow: "scroll"
            },
            ".overflow-x-auto": {
              "overflow-x": "auto"
            },
            ".overflow-y-auto": {
              "overflow-y": "auto"
            },
            ".overflow-x-hidden": {
              "overflow-x": "hidden"
            },
            ".overflow-y-hidden": {
              "overflow-y": "hidden"
            },
            ".overflow-x-clip": {
              "overflow-x": "clip"
            },
            ".overflow-y-clip": {
              "overflow-y": "clip"
            },
            ".overflow-x-visible": {
              "overflow-x": "visible"
            },
            ".overflow-y-visible": {
              "overflow-y": "visible"
            },
            ".overflow-x-scroll": {
              "overflow-x": "scroll"
            },
            ".overflow-y-scroll": {
              "overflow-y": "scroll"
            }
          });
        },
        overscrollBehavior: ({ addUtilities }) => {
          addUtilities({
            ".overscroll-auto": {
              "overscroll-behavior": "auto"
            },
            ".overscroll-contain": {
              "overscroll-behavior": "contain"
            },
            ".overscroll-none": {
              "overscroll-behavior": "none"
            },
            ".overscroll-y-auto": {
              "overscroll-behavior-y": "auto"
            },
            ".overscroll-y-contain": {
              "overscroll-behavior-y": "contain"
            },
            ".overscroll-y-none": {
              "overscroll-behavior-y": "none"
            },
            ".overscroll-x-auto": {
              "overscroll-behavior-x": "auto"
            },
            ".overscroll-x-contain": {
              "overscroll-behavior-x": "contain"
            },
            ".overscroll-x-none": {
              "overscroll-behavior-x": "none"
            }
          });
        },
        scrollBehavior: ({ addUtilities }) => {
          addUtilities({
            ".scroll-auto": {
              "scroll-behavior": "auto"
            },
            ".scroll-smooth": {
              "scroll-behavior": "smooth"
            }
          });
        },
        textOverflow: ({ addUtilities }) => {
          addUtilities({
            ".truncate": {
              overflow: "hidden",
              "text-overflow": "ellipsis",
              "white-space": "nowrap"
            },
            ".overflow-ellipsis": {
              "text-overflow": "ellipsis"
            },
            ".text-ellipsis": {
              "text-overflow": "ellipsis"
            },
            ".text-clip": {
              "text-overflow": "clip"
            }
          });
        },
        hyphens: ({ addUtilities }) => {
          addUtilities({
            ".hyphens-none": {
              hyphens: "none"
            },
            ".hyphens-manual": {
              hyphens: "manual"
            },
            ".hyphens-auto": {
              hyphens: "auto"
            }
          });
        },
        whitespace: ({ addUtilities }) => {
          addUtilities({
            ".whitespace-normal": {
              "white-space": "normal"
            },
            ".whitespace-nowrap": {
              "white-space": "nowrap"
            },
            ".whitespace-pre": {
              "white-space": "pre"
            },
            ".whitespace-pre-line": {
              "white-space": "pre-line"
            },
            ".whitespace-pre-wrap": {
              "white-space": "pre-wrap"
            },
            ".whitespace-break-spaces": {
              "white-space": "break-spaces"
            }
          });
        },
        textWrap: ({ addUtilities }) => {
          addUtilities({
            ".text-wrap": {
              "text-wrap": "wrap"
            },
            ".text-nowrap": {
              "text-wrap": "nowrap"
            },
            ".text-balance": {
              "text-wrap": "balance"
            },
            ".text-pretty": {
              "text-wrap": "pretty"
            }
          });
        },
        wordBreak: ({ addUtilities }) => {
          addUtilities({
            ".break-normal": {
              "overflow-wrap": "normal",
              "word-break": "normal"
            },
            ".break-words": {
              "overflow-wrap": "break-word"
            },
            ".break-all": {
              "word-break": "break-all"
            },
            ".break-keep": {
              "word-break": "keep-all"
            }
          });
        },
        borderRadius: (0, _createUtilityPlugin.default)("borderRadius", [
          [
            "rounded",
            [
              "border-radius"
            ]
          ],
          [
            [
              "rounded-s",
              [
                "border-start-start-radius",
                "border-end-start-radius"
              ]
            ],
            [
              "rounded-e",
              [
                "border-start-end-radius",
                "border-end-end-radius"
              ]
            ],
            [
              "rounded-t",
              [
                "border-top-left-radius",
                "border-top-right-radius"
              ]
            ],
            [
              "rounded-r",
              [
                "border-top-right-radius",
                "border-bottom-right-radius"
              ]
            ],
            [
              "rounded-b",
              [
                "border-bottom-right-radius",
                "border-bottom-left-radius"
              ]
            ],
            [
              "rounded-l",
              [
                "border-top-left-radius",
                "border-bottom-left-radius"
              ]
            ]
          ],
          [
            [
              "rounded-ss",
              [
                "border-start-start-radius"
              ]
            ],
            [
              "rounded-se",
              [
                "border-start-end-radius"
              ]
            ],
            [
              "rounded-ee",
              [
                "border-end-end-radius"
              ]
            ],
            [
              "rounded-es",
              [
                "border-end-start-radius"
              ]
            ],
            [
              "rounded-tl",
              [
                "border-top-left-radius"
              ]
            ],
            [
              "rounded-tr",
              [
                "border-top-right-radius"
              ]
            ],
            [
              "rounded-br",
              [
                "border-bottom-right-radius"
              ]
            ],
            [
              "rounded-bl",
              [
                "border-bottom-left-radius"
              ]
            ]
          ]
        ]),
        borderWidth: (0, _createUtilityPlugin.default)("borderWidth", [
          [
            "border",
            [
              [
                "@defaults border-width",
                {}
              ],
              "border-width"
            ]
          ],
          [
            [
              "border-x",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-left-width",
                "border-right-width"
              ]
            ],
            [
              "border-y",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-top-width",
                "border-bottom-width"
              ]
            ]
          ],
          [
            [
              "border-s",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-inline-start-width"
              ]
            ],
            [
              "border-e",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-inline-end-width"
              ]
            ],
            [
              "border-t",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-top-width"
              ]
            ],
            [
              "border-r",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-right-width"
              ]
            ],
            [
              "border-b",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-bottom-width"
              ]
            ],
            [
              "border-l",
              [
                [
                  "@defaults border-width",
                  {}
                ],
                "border-left-width"
              ]
            ]
          ]
        ], {
          type: [
            "line-width",
            "length"
          ]
        }),
        borderStyle: ({ addUtilities }) => {
          addUtilities({
            ".border-solid": {
              "border-style": "solid"
            },
            ".border-dashed": {
              "border-style": "dashed"
            },
            ".border-dotted": {
              "border-style": "dotted"
            },
            ".border-double": {
              "border-style": "double"
            },
            ".border-hidden": {
              "border-style": "hidden"
            },
            ".border-none": {
              "border-style": "none"
            }
          });
        },
        borderColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            border: (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-color",
                variable: "--tw-border-opacity"
              });
            }
          }, {
            values: (({ DEFAULT: _, ...colors2 }) => colors2)((0, _flattenColorPalette.default)(theme("borderColor"))),
            type: [
              "color",
              "any"
            ]
          });
          matchUtilities({
            "border-x": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-left-color": (0, _toColorValue.default)(value),
                  "border-right-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: [
                  "border-left-color",
                  "border-right-color"
                ],
                variable: "--tw-border-opacity"
              });
            },
            "border-y": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-top-color": (0, _toColorValue.default)(value),
                  "border-bottom-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: [
                  "border-top-color",
                  "border-bottom-color"
                ],
                variable: "--tw-border-opacity"
              });
            }
          }, {
            values: (({ DEFAULT: _, ...colors2 }) => colors2)((0, _flattenColorPalette.default)(theme("borderColor"))),
            type: [
              "color",
              "any"
            ]
          });
          matchUtilities({
            "border-s": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-inline-start-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-inline-start-color",
                variable: "--tw-border-opacity"
              });
            },
            "border-e": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-inline-end-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-inline-end-color",
                variable: "--tw-border-opacity"
              });
            },
            "border-t": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-top-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-top-color",
                variable: "--tw-border-opacity"
              });
            },
            "border-r": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-right-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-right-color",
                variable: "--tw-border-opacity"
              });
            },
            "border-b": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-bottom-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-bottom-color",
                variable: "--tw-border-opacity"
              });
            },
            "border-l": (value) => {
              if (!corePlugins2("borderOpacity")) {
                return {
                  "border-left-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "border-left-color",
                variable: "--tw-border-opacity"
              });
            }
          }, {
            values: (({ DEFAULT: _, ...colors2 }) => colors2)((0, _flattenColorPalette.default)(theme("borderColor"))),
            type: [
              "color",
              "any"
            ]
          });
        },
        borderOpacity: (0, _createUtilityPlugin.default)("borderOpacity", [
          [
            "border-opacity",
            [
              "--tw-border-opacity"
            ]
          ]
        ]),
        backgroundColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            bg: (value) => {
              if (!corePlugins2("backgroundOpacity")) {
                return {
                  "background-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "background-color",
                variable: "--tw-bg-opacity"
              });
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("backgroundColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        backgroundOpacity: (0, _createUtilityPlugin.default)("backgroundOpacity", [
          [
            "bg-opacity",
            [
              "--tw-bg-opacity"
            ]
          ]
        ]),
        backgroundImage: (0, _createUtilityPlugin.default)("backgroundImage", [
          [
            "bg",
            [
              "background-image"
            ]
          ]
        ], {
          type: [
            "lookup",
            "image",
            "url"
          ]
        }),
        gradientColorStops: /* @__PURE__ */ (() => {
          function transparentTo(value) {
            return (0, _withAlphaVariable.withAlphaValue)(value, 0, "rgb(255 255 255 / 0)");
          }
          return function({ matchUtilities, theme, addDefaults }) {
            addDefaults("gradient-color-stops", {
              "--tw-gradient-from-position": " ",
              "--tw-gradient-via-position": " ",
              "--tw-gradient-to-position": " "
            });
            let options = {
              values: (0, _flattenColorPalette.default)(theme("gradientColorStops")),
              type: [
                "color",
                "any"
              ]
            };
            let positionOptions = {
              values: theme("gradientColorStopPositions"),
              type: [
                "length",
                "percentage"
              ]
            };
            matchUtilities({
              from: (value) => {
                let transparentToValue = transparentTo(value);
                return {
                  "@defaults gradient-color-stops": {},
                  "--tw-gradient-from": `${(0, _toColorValue.default)(value)} var(--tw-gradient-from-position)`,
                  "--tw-gradient-to": `${transparentToValue} var(--tw-gradient-to-position)`,
                  "--tw-gradient-stops": `var(--tw-gradient-from), var(--tw-gradient-to)`
                };
              }
            }, options);
            matchUtilities({
              from: (value) => {
                return {
                  "--tw-gradient-from-position": value
                };
              }
            }, positionOptions);
            matchUtilities({
              via: (value) => {
                let transparentToValue = transparentTo(value);
                return {
                  "@defaults gradient-color-stops": {},
                  "--tw-gradient-to": `${transparentToValue}  var(--tw-gradient-to-position)`,
                  "--tw-gradient-stops": `var(--tw-gradient-from), ${(0, _toColorValue.default)(value)} var(--tw-gradient-via-position), var(--tw-gradient-to)`
                };
              }
            }, options);
            matchUtilities({
              via: (value) => {
                return {
                  "--tw-gradient-via-position": value
                };
              }
            }, positionOptions);
            matchUtilities({
              to: (value) => ({
                "@defaults gradient-color-stops": {},
                "--tw-gradient-to": `${(0, _toColorValue.default)(value)} var(--tw-gradient-to-position)`
              })
            }, options);
            matchUtilities({
              to: (value) => {
                return {
                  "--tw-gradient-to-position": value
                };
              }
            }, positionOptions);
          };
        })(),
        boxDecorationBreak: ({ addUtilities }) => {
          addUtilities({
            ".decoration-slice": {
              "box-decoration-break": "slice"
            },
            ".decoration-clone": {
              "box-decoration-break": "clone"
            },
            ".box-decoration-slice": {
              "box-decoration-break": "slice"
            },
            ".box-decoration-clone": {
              "box-decoration-break": "clone"
            }
          });
        },
        backgroundSize: (0, _createUtilityPlugin.default)("backgroundSize", [
          [
            "bg",
            [
              "background-size"
            ]
          ]
        ], {
          type: [
            "lookup",
            "length",
            "percentage",
            "size"
          ]
        }),
        backgroundAttachment: ({ addUtilities }) => {
          addUtilities({
            ".bg-fixed": {
              "background-attachment": "fixed"
            },
            ".bg-local": {
              "background-attachment": "local"
            },
            ".bg-scroll": {
              "background-attachment": "scroll"
            }
          });
        },
        backgroundClip: ({ addUtilities }) => {
          addUtilities({
            ".bg-clip-border": {
              "background-clip": "border-box"
            },
            ".bg-clip-padding": {
              "background-clip": "padding-box"
            },
            ".bg-clip-content": {
              "background-clip": "content-box"
            },
            ".bg-clip-text": {
              "background-clip": "text"
            }
          });
        },
        backgroundPosition: (0, _createUtilityPlugin.default)("backgroundPosition", [
          [
            "bg",
            [
              "background-position"
            ]
          ]
        ], {
          type: [
            "lookup",
            [
              "position",
              {
                preferOnConflict: true
              }
            ]
          ]
        }),
        backgroundRepeat: ({ addUtilities }) => {
          addUtilities({
            ".bg-repeat": {
              "background-repeat": "repeat"
            },
            ".bg-no-repeat": {
              "background-repeat": "no-repeat"
            },
            ".bg-repeat-x": {
              "background-repeat": "repeat-x"
            },
            ".bg-repeat-y": {
              "background-repeat": "repeat-y"
            },
            ".bg-repeat-round": {
              "background-repeat": "round"
            },
            ".bg-repeat-space": {
              "background-repeat": "space"
            }
          });
        },
        backgroundOrigin: ({ addUtilities }) => {
          addUtilities({
            ".bg-origin-border": {
              "background-origin": "border-box"
            },
            ".bg-origin-padding": {
              "background-origin": "padding-box"
            },
            ".bg-origin-content": {
              "background-origin": "content-box"
            }
          });
        },
        fill: ({ matchUtilities, theme }) => {
          matchUtilities({
            fill: (value) => {
              return {
                fill: (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("fill")),
            type: [
              "color",
              "any"
            ]
          });
        },
        stroke: ({ matchUtilities, theme }) => {
          matchUtilities({
            stroke: (value) => {
              return {
                stroke: (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("stroke")),
            type: [
              "color",
              "url",
              "any"
            ]
          });
        },
        strokeWidth: (0, _createUtilityPlugin.default)("strokeWidth", [
          [
            "stroke",
            [
              "stroke-width"
            ]
          ]
        ], {
          type: [
            "length",
            "number",
            "percentage"
          ]
        }),
        objectFit: ({ addUtilities }) => {
          addUtilities({
            ".object-contain": {
              "object-fit": "contain"
            },
            ".object-cover": {
              "object-fit": "cover"
            },
            ".object-fill": {
              "object-fit": "fill"
            },
            ".object-none": {
              "object-fit": "none"
            },
            ".object-scale-down": {
              "object-fit": "scale-down"
            }
          });
        },
        objectPosition: (0, _createUtilityPlugin.default)("objectPosition", [
          [
            "object",
            [
              "object-position"
            ]
          ]
        ]),
        padding: (0, _createUtilityPlugin.default)("padding", [
          [
            "p",
            [
              "padding"
            ]
          ],
          [
            [
              "px",
              [
                "padding-left",
                "padding-right"
              ]
            ],
            [
              "py",
              [
                "padding-top",
                "padding-bottom"
              ]
            ]
          ],
          [
            [
              "ps",
              [
                "padding-inline-start"
              ]
            ],
            [
              "pe",
              [
                "padding-inline-end"
              ]
            ],
            [
              "pt",
              [
                "padding-top"
              ]
            ],
            [
              "pr",
              [
                "padding-right"
              ]
            ],
            [
              "pb",
              [
                "padding-bottom"
              ]
            ],
            [
              "pl",
              [
                "padding-left"
              ]
            ]
          ]
        ]),
        textAlign: ({ addUtilities }) => {
          addUtilities({
            ".text-left": {
              "text-align": "left"
            },
            ".text-center": {
              "text-align": "center"
            },
            ".text-right": {
              "text-align": "right"
            },
            ".text-justify": {
              "text-align": "justify"
            },
            ".text-start": {
              "text-align": "start"
            },
            ".text-end": {
              "text-align": "end"
            }
          });
        },
        textIndent: (0, _createUtilityPlugin.default)("textIndent", [
          [
            "indent",
            [
              "text-indent"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        verticalAlign: ({ addUtilities, matchUtilities }) => {
          addUtilities({
            ".align-baseline": {
              "vertical-align": "baseline"
            },
            ".align-top": {
              "vertical-align": "top"
            },
            ".align-middle": {
              "vertical-align": "middle"
            },
            ".align-bottom": {
              "vertical-align": "bottom"
            },
            ".align-text-top": {
              "vertical-align": "text-top"
            },
            ".align-text-bottom": {
              "vertical-align": "text-bottom"
            },
            ".align-sub": {
              "vertical-align": "sub"
            },
            ".align-super": {
              "vertical-align": "super"
            }
          });
          matchUtilities({
            align: (value) => ({
              "vertical-align": value
            })
          });
        },
        fontFamily: ({ matchUtilities, theme }) => {
          matchUtilities({
            font: (value) => {
              let [families, options = {}] = Array.isArray(value) && (0, _isPlainObject.default)(value[1]) ? value : [
                value
              ];
              let { fontFeatureSettings, fontVariationSettings } = options;
              return {
                "font-family": Array.isArray(families) ? families.join(", ") : families,
                ...fontFeatureSettings === void 0 ? {} : {
                  "font-feature-settings": fontFeatureSettings
                },
                ...fontVariationSettings === void 0 ? {} : {
                  "font-variation-settings": fontVariationSettings
                }
              };
            }
          }, {
            values: theme("fontFamily"),
            type: [
              "lookup",
              "generic-name",
              "family-name"
            ]
          });
        },
        fontSize: ({ matchUtilities, theme }) => {
          matchUtilities({
            text: (value, { modifier }) => {
              let [fontSize, options] = Array.isArray(value) ? value : [
                value
              ];
              if (modifier) {
                return {
                  "font-size": fontSize,
                  "line-height": modifier
                };
              }
              let { lineHeight, letterSpacing, fontWeight } = (0, _isPlainObject.default)(options) ? options : {
                lineHeight: options
              };
              return {
                "font-size": fontSize,
                ...lineHeight === void 0 ? {} : {
                  "line-height": lineHeight
                },
                ...letterSpacing === void 0 ? {} : {
                  "letter-spacing": letterSpacing
                },
                ...fontWeight === void 0 ? {} : {
                  "font-weight": fontWeight
                }
              };
            }
          }, {
            values: theme("fontSize"),
            modifiers: theme("lineHeight"),
            type: [
              "absolute-size",
              "relative-size",
              "length",
              "percentage"
            ]
          });
        },
        fontWeight: (0, _createUtilityPlugin.default)("fontWeight", [
          [
            "font",
            [
              "fontWeight"
            ]
          ]
        ], {
          type: [
            "lookup",
            "number",
            "any"
          ]
        }),
        textTransform: ({ addUtilities }) => {
          addUtilities({
            ".uppercase": {
              "text-transform": "uppercase"
            },
            ".lowercase": {
              "text-transform": "lowercase"
            },
            ".capitalize": {
              "text-transform": "capitalize"
            },
            ".normal-case": {
              "text-transform": "none"
            }
          });
        },
        fontStyle: ({ addUtilities }) => {
          addUtilities({
            ".italic": {
              "font-style": "italic"
            },
            ".not-italic": {
              "font-style": "normal"
            }
          });
        },
        fontVariantNumeric: ({ addDefaults, addUtilities }) => {
          let cssFontVariantNumericValue = "var(--tw-ordinal) var(--tw-slashed-zero) var(--tw-numeric-figure) var(--tw-numeric-spacing) var(--tw-numeric-fraction)";
          addDefaults("font-variant-numeric", {
            "--tw-ordinal": " ",
            "--tw-slashed-zero": " ",
            "--tw-numeric-figure": " ",
            "--tw-numeric-spacing": " ",
            "--tw-numeric-fraction": " "
          });
          addUtilities({
            ".normal-nums": {
              "font-variant-numeric": "normal"
            },
            ".ordinal": {
              "@defaults font-variant-numeric": {},
              "--tw-ordinal": "ordinal",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".slashed-zero": {
              "@defaults font-variant-numeric": {},
              "--tw-slashed-zero": "slashed-zero",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".lining-nums": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-figure": "lining-nums",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".oldstyle-nums": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-figure": "oldstyle-nums",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".proportional-nums": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-spacing": "proportional-nums",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".tabular-nums": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-spacing": "tabular-nums",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".diagonal-fractions": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-fraction": "diagonal-fractions",
              "font-variant-numeric": cssFontVariantNumericValue
            },
            ".stacked-fractions": {
              "@defaults font-variant-numeric": {},
              "--tw-numeric-fraction": "stacked-fractions",
              "font-variant-numeric": cssFontVariantNumericValue
            }
          });
        },
        lineHeight: (0, _createUtilityPlugin.default)("lineHeight", [
          [
            "leading",
            [
              "lineHeight"
            ]
          ]
        ]),
        letterSpacing: (0, _createUtilityPlugin.default)("letterSpacing", [
          [
            "tracking",
            [
              "letterSpacing"
            ]
          ]
        ], {
          supportsNegativeValues: true
        }),
        textColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            text: (value) => {
              if (!corePlugins2("textOpacity")) {
                return {
                  color: (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "color",
                variable: "--tw-text-opacity"
              });
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("textColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        textOpacity: (0, _createUtilityPlugin.default)("textOpacity", [
          [
            "text-opacity",
            [
              "--tw-text-opacity"
            ]
          ]
        ]),
        textDecoration: ({ addUtilities }) => {
          addUtilities({
            ".underline": {
              "text-decoration-line": "underline"
            },
            ".overline": {
              "text-decoration-line": "overline"
            },
            ".line-through": {
              "text-decoration-line": "line-through"
            },
            ".no-underline": {
              "text-decoration-line": "none"
            }
          });
        },
        textDecorationColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            decoration: (value) => {
              return {
                "text-decoration-color": (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("textDecorationColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        textDecorationStyle: ({ addUtilities }) => {
          addUtilities({
            ".decoration-solid": {
              "text-decoration-style": "solid"
            },
            ".decoration-double": {
              "text-decoration-style": "double"
            },
            ".decoration-dotted": {
              "text-decoration-style": "dotted"
            },
            ".decoration-dashed": {
              "text-decoration-style": "dashed"
            },
            ".decoration-wavy": {
              "text-decoration-style": "wavy"
            }
          });
        },
        textDecorationThickness: (0, _createUtilityPlugin.default)("textDecorationThickness", [
          [
            "decoration",
            [
              "text-decoration-thickness"
            ]
          ]
        ], {
          type: [
            "length",
            "percentage"
          ]
        }),
        textUnderlineOffset: (0, _createUtilityPlugin.default)("textUnderlineOffset", [
          [
            "underline-offset",
            [
              "text-underline-offset"
            ]
          ]
        ], {
          type: [
            "length",
            "percentage",
            "any"
          ]
        }),
        fontSmoothing: ({ addUtilities }) => {
          addUtilities({
            ".antialiased": {
              "-webkit-font-smoothing": "antialiased",
              "-moz-osx-font-smoothing": "grayscale"
            },
            ".subpixel-antialiased": {
              "-webkit-font-smoothing": "auto",
              "-moz-osx-font-smoothing": "auto"
            }
          });
        },
        placeholderColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            placeholder: (value) => {
              if (!corePlugins2("placeholderOpacity")) {
                return {
                  "&::placeholder": {
                    color: (0, _toColorValue.default)(value)
                  }
                };
              }
              return {
                "&::placeholder": (0, _withAlphaVariable.default)({
                  color: value,
                  property: "color",
                  variable: "--tw-placeholder-opacity"
                })
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("placeholderColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        placeholderOpacity: ({ matchUtilities, theme }) => {
          matchUtilities({
            "placeholder-opacity": (value) => {
              return {
                ["&::placeholder"]: {
                  "--tw-placeholder-opacity": value
                }
              };
            }
          }, {
            values: theme("placeholderOpacity")
          });
        },
        caretColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            caret: (value) => {
              return {
                "caret-color": (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("caretColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        accentColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            accent: (value) => {
              return {
                "accent-color": (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("accentColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        opacity: (0, _createUtilityPlugin.default)("opacity", [
          [
            "opacity",
            [
              "opacity"
            ]
          ]
        ]),
        backgroundBlendMode: ({ addUtilities }) => {
          addUtilities({
            ".bg-blend-normal": {
              "background-blend-mode": "normal"
            },
            ".bg-blend-multiply": {
              "background-blend-mode": "multiply"
            },
            ".bg-blend-screen": {
              "background-blend-mode": "screen"
            },
            ".bg-blend-overlay": {
              "background-blend-mode": "overlay"
            },
            ".bg-blend-darken": {
              "background-blend-mode": "darken"
            },
            ".bg-blend-lighten": {
              "background-blend-mode": "lighten"
            },
            ".bg-blend-color-dodge": {
              "background-blend-mode": "color-dodge"
            },
            ".bg-blend-color-burn": {
              "background-blend-mode": "color-burn"
            },
            ".bg-blend-hard-light": {
              "background-blend-mode": "hard-light"
            },
            ".bg-blend-soft-light": {
              "background-blend-mode": "soft-light"
            },
            ".bg-blend-difference": {
              "background-blend-mode": "difference"
            },
            ".bg-blend-exclusion": {
              "background-blend-mode": "exclusion"
            },
            ".bg-blend-hue": {
              "background-blend-mode": "hue"
            },
            ".bg-blend-saturation": {
              "background-blend-mode": "saturation"
            },
            ".bg-blend-color": {
              "background-blend-mode": "color"
            },
            ".bg-blend-luminosity": {
              "background-blend-mode": "luminosity"
            }
          });
        },
        mixBlendMode: ({ addUtilities }) => {
          addUtilities({
            ".mix-blend-normal": {
              "mix-blend-mode": "normal"
            },
            ".mix-blend-multiply": {
              "mix-blend-mode": "multiply"
            },
            ".mix-blend-screen": {
              "mix-blend-mode": "screen"
            },
            ".mix-blend-overlay": {
              "mix-blend-mode": "overlay"
            },
            ".mix-blend-darken": {
              "mix-blend-mode": "darken"
            },
            ".mix-blend-lighten": {
              "mix-blend-mode": "lighten"
            },
            ".mix-blend-color-dodge": {
              "mix-blend-mode": "color-dodge"
            },
            ".mix-blend-color-burn": {
              "mix-blend-mode": "color-burn"
            },
            ".mix-blend-hard-light": {
              "mix-blend-mode": "hard-light"
            },
            ".mix-blend-soft-light": {
              "mix-blend-mode": "soft-light"
            },
            ".mix-blend-difference": {
              "mix-blend-mode": "difference"
            },
            ".mix-blend-exclusion": {
              "mix-blend-mode": "exclusion"
            },
            ".mix-blend-hue": {
              "mix-blend-mode": "hue"
            },
            ".mix-blend-saturation": {
              "mix-blend-mode": "saturation"
            },
            ".mix-blend-color": {
              "mix-blend-mode": "color"
            },
            ".mix-blend-luminosity": {
              "mix-blend-mode": "luminosity"
            },
            ".mix-blend-plus-darker": {
              "mix-blend-mode": "plus-darker"
            },
            ".mix-blend-plus-lighter": {
              "mix-blend-mode": "plus-lighter"
            }
          });
        },
        boxShadow: (() => {
          let transformValue = (0, _transformThemeValue.default)("boxShadow");
          let defaultBoxShadow = [
            `var(--tw-ring-offset-shadow, 0 0 #0000)`,
            `var(--tw-ring-shadow, 0 0 #0000)`,
            `var(--tw-shadow)`
          ].join(", ");
          return function({ matchUtilities, addDefaults, theme }) {
            addDefaults("box-shadow", {
              "--tw-ring-offset-shadow": "0 0 #0000",
              "--tw-ring-shadow": "0 0 #0000",
              "--tw-shadow": "0 0 #0000",
              "--tw-shadow-colored": "0 0 #0000"
            });
            matchUtilities({
              shadow: (value) => {
                value = transformValue(value);
                let ast = (0, _parseBoxShadowValue.parseBoxShadowValue)(value);
                for (let shadow of ast) {
                  if (!shadow.valid) {
                    continue;
                  }
                  shadow.color = "var(--tw-shadow-color)";
                }
                return {
                  "@defaults box-shadow": {},
                  "--tw-shadow": value === "none" ? "0 0 #0000" : value,
                  "--tw-shadow-colored": value === "none" ? "0 0 #0000" : (0, _parseBoxShadowValue.formatBoxShadowValue)(ast),
                  "box-shadow": defaultBoxShadow
                };
              }
            }, {
              values: theme("boxShadow"),
              type: [
                "shadow"
              ]
            });
          };
        })(),
        boxShadowColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            shadow: (value) => {
              return {
                "--tw-shadow-color": (0, _toColorValue.default)(value),
                "--tw-shadow": "var(--tw-shadow-colored)"
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("boxShadowColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        outlineStyle: ({ addUtilities }) => {
          addUtilities({
            ".outline-none": {
              outline: "2px solid transparent",
              "outline-offset": "2px"
            },
            ".outline": {
              "outline-style": "solid"
            },
            ".outline-dashed": {
              "outline-style": "dashed"
            },
            ".outline-dotted": {
              "outline-style": "dotted"
            },
            ".outline-double": {
              "outline-style": "double"
            }
          });
        },
        outlineWidth: (0, _createUtilityPlugin.default)("outlineWidth", [
          [
            "outline",
            [
              "outline-width"
            ]
          ]
        ], {
          type: [
            "length",
            "number",
            "percentage"
          ]
        }),
        outlineOffset: (0, _createUtilityPlugin.default)("outlineOffset", [
          [
            "outline-offset",
            [
              "outline-offset"
            ]
          ]
        ], {
          type: [
            "length",
            "number",
            "percentage",
            "any"
          ],
          supportsNegativeValues: true
        }),
        outlineColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            outline: (value) => {
              return {
                "outline-color": (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("outlineColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        ringWidth: ({ matchUtilities, addDefaults, addUtilities, theme, config }) => {
          let ringColorDefault = (() => {
            var _theme, _theme1;
            if ((0, _featureFlags.flagEnabled)(config(), "respectDefaultRingColorOpacity")) {
              return theme("ringColor.DEFAULT");
            }
            let ringOpacityDefault = theme("ringOpacity.DEFAULT", "0.5");
            if (!((_theme = theme("ringColor")) === null || _theme === void 0 ? void 0 : _theme.DEFAULT)) {
              return `rgb(147 197 253 / ${ringOpacityDefault})`;
            }
            return (0, _withAlphaVariable.withAlphaValue)((_theme1 = theme("ringColor")) === null || _theme1 === void 0 ? void 0 : _theme1.DEFAULT, ringOpacityDefault, `rgb(147 197 253 / ${ringOpacityDefault})`);
          })();
          addDefaults("ring-width", {
            "--tw-ring-inset": " ",
            "--tw-ring-offset-width": theme("ringOffsetWidth.DEFAULT", "0px"),
            "--tw-ring-offset-color": theme("ringOffsetColor.DEFAULT", "#fff"),
            "--tw-ring-color": ringColorDefault,
            "--tw-ring-offset-shadow": "0 0 #0000",
            "--tw-ring-shadow": "0 0 #0000",
            "--tw-shadow": "0 0 #0000",
            "--tw-shadow-colored": "0 0 #0000"
          });
          matchUtilities({
            ring: (value) => {
              return {
                "@defaults ring-width": {},
                "--tw-ring-offset-shadow": `var(--tw-ring-inset) 0 0 0 var(--tw-ring-offset-width) var(--tw-ring-offset-color)`,
                "--tw-ring-shadow": `var(--tw-ring-inset) 0 0 0 calc(${value} + var(--tw-ring-offset-width)) var(--tw-ring-color)`,
                "box-shadow": [
                  `var(--tw-ring-offset-shadow)`,
                  `var(--tw-ring-shadow)`,
                  `var(--tw-shadow, 0 0 #0000)`
                ].join(", ")
              };
            }
          }, {
            values: theme("ringWidth"),
            type: "length"
          });
          addUtilities({
            ".ring-inset": {
              "@defaults ring-width": {},
              "--tw-ring-inset": "inset"
            }
          });
        },
        ringColor: ({ matchUtilities, theme, corePlugins: corePlugins2 }) => {
          matchUtilities({
            ring: (value) => {
              if (!corePlugins2("ringOpacity")) {
                return {
                  "--tw-ring-color": (0, _toColorValue.default)(value)
                };
              }
              return (0, _withAlphaVariable.default)({
                color: value,
                property: "--tw-ring-color",
                variable: "--tw-ring-opacity"
              });
            }
          }, {
            values: Object.fromEntries(Object.entries((0, _flattenColorPalette.default)(theme("ringColor"))).filter(([modifier]) => modifier !== "DEFAULT")),
            type: [
              "color",
              "any"
            ]
          });
        },
        ringOpacity: (helpers) => {
          let { config } = helpers;
          return (0, _createUtilityPlugin.default)("ringOpacity", [
            [
              "ring-opacity",
              [
                "--tw-ring-opacity"
              ]
            ]
          ], {
            filterDefault: !(0, _featureFlags.flagEnabled)(config(), "respectDefaultRingColorOpacity")
          })(helpers);
        },
        ringOffsetWidth: (0, _createUtilityPlugin.default)("ringOffsetWidth", [
          [
            "ring-offset",
            [
              "--tw-ring-offset-width"
            ]
          ]
        ], {
          type: "length"
        }),
        ringOffsetColor: ({ matchUtilities, theme }) => {
          matchUtilities({
            "ring-offset": (value) => {
              return {
                "--tw-ring-offset-color": (0, _toColorValue.default)(value)
              };
            }
          }, {
            values: (0, _flattenColorPalette.default)(theme("ringOffsetColor")),
            type: [
              "color",
              "any"
            ]
          });
        },
        blur: ({ matchUtilities, theme }) => {
          matchUtilities({
            blur: (value) => {
              return {
                "--tw-blur": value.trim() === "" ? " " : `blur(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("blur")
          });
        },
        brightness: ({ matchUtilities, theme }) => {
          matchUtilities({
            brightness: (value) => {
              return {
                "--tw-brightness": `brightness(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("brightness")
          });
        },
        contrast: ({ matchUtilities, theme }) => {
          matchUtilities({
            contrast: (value) => {
              return {
                "--tw-contrast": `contrast(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("contrast")
          });
        },
        dropShadow: ({ matchUtilities, theme }) => {
          matchUtilities({
            "drop-shadow": (value) => {
              return {
                "--tw-drop-shadow": Array.isArray(value) ? value.map((v) => `drop-shadow(${v})`).join(" ") : `drop-shadow(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("dropShadow")
          });
        },
        grayscale: ({ matchUtilities, theme }) => {
          matchUtilities({
            grayscale: (value) => {
              return {
                "--tw-grayscale": `grayscale(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("grayscale")
          });
        },
        hueRotate: ({ matchUtilities, theme }) => {
          matchUtilities({
            "hue-rotate": (value) => {
              return {
                "--tw-hue-rotate": `hue-rotate(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("hueRotate"),
            supportsNegativeValues: true
          });
        },
        invert: ({ matchUtilities, theme }) => {
          matchUtilities({
            invert: (value) => {
              return {
                "--tw-invert": `invert(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("invert")
          });
        },
        saturate: ({ matchUtilities, theme }) => {
          matchUtilities({
            saturate: (value) => {
              return {
                "--tw-saturate": `saturate(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("saturate")
          });
        },
        sepia: ({ matchUtilities, theme }) => {
          matchUtilities({
            sepia: (value) => {
              return {
                "--tw-sepia": `sepia(${value})`,
                "@defaults filter": {},
                filter: cssFilterValue
              };
            }
          }, {
            values: theme("sepia")
          });
        },
        filter: ({ addDefaults, addUtilities }) => {
          addDefaults("filter", {
            "--tw-blur": " ",
            "--tw-brightness": " ",
            "--tw-contrast": " ",
            "--tw-grayscale": " ",
            "--tw-hue-rotate": " ",
            "--tw-invert": " ",
            "--tw-saturate": " ",
            "--tw-sepia": " ",
            "--tw-drop-shadow": " "
          });
          addUtilities({
            ".filter": {
              "@defaults filter": {},
              filter: cssFilterValue
            },
            ".filter-none": {
              filter: "none"
            }
          });
        },
        backdropBlur: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-blur": (value) => {
              return {
                "--tw-backdrop-blur": value.trim() === "" ? " " : `blur(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropBlur")
          });
        },
        backdropBrightness: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-brightness": (value) => {
              return {
                "--tw-backdrop-brightness": `brightness(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropBrightness")
          });
        },
        backdropContrast: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-contrast": (value) => {
              return {
                "--tw-backdrop-contrast": `contrast(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropContrast")
          });
        },
        backdropGrayscale: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-grayscale": (value) => {
              return {
                "--tw-backdrop-grayscale": `grayscale(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropGrayscale")
          });
        },
        backdropHueRotate: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-hue-rotate": (value) => {
              return {
                "--tw-backdrop-hue-rotate": `hue-rotate(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropHueRotate"),
            supportsNegativeValues: true
          });
        },
        backdropInvert: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-invert": (value) => {
              return {
                "--tw-backdrop-invert": `invert(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropInvert")
          });
        },
        backdropOpacity: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-opacity": (value) => {
              return {
                "--tw-backdrop-opacity": `opacity(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropOpacity")
          });
        },
        backdropSaturate: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-saturate": (value) => {
              return {
                "--tw-backdrop-saturate": `saturate(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropSaturate")
          });
        },
        backdropSepia: ({ matchUtilities, theme }) => {
          matchUtilities({
            "backdrop-sepia": (value) => {
              return {
                "--tw-backdrop-sepia": `sepia(${value})`,
                "@defaults backdrop-filter": {},
                "-webkit-backdrop-filter": cssBackdropFilterValue,
                "backdrop-filter": cssBackdropFilterValue
              };
            }
          }, {
            values: theme("backdropSepia")
          });
        },
        backdropFilter: ({ addDefaults, addUtilities }) => {
          addDefaults("backdrop-filter", {
            "--tw-backdrop-blur": " ",
            "--tw-backdrop-brightness": " ",
            "--tw-backdrop-contrast": " ",
            "--tw-backdrop-grayscale": " ",
            "--tw-backdrop-hue-rotate": " ",
            "--tw-backdrop-invert": " ",
            "--tw-backdrop-opacity": " ",
            "--tw-backdrop-saturate": " ",
            "--tw-backdrop-sepia": " "
          });
          addUtilities({
            ".backdrop-filter": {
              "@defaults backdrop-filter": {},
              "-webkit-backdrop-filter": cssBackdropFilterValue,
              "backdrop-filter": cssBackdropFilterValue
            },
            ".backdrop-filter-none": {
              "-webkit-backdrop-filter": "none",
              "backdrop-filter": "none"
            }
          });
        },
        transitionProperty: ({ matchUtilities, theme }) => {
          let defaultTimingFunction = theme("transitionTimingFunction.DEFAULT");
          let defaultDuration = theme("transitionDuration.DEFAULT");
          matchUtilities({
            transition: (value) => {
              return {
                "transition-property": value,
                ...value === "none" ? {} : {
                  "transition-timing-function": defaultTimingFunction,
                  "transition-duration": defaultDuration
                }
              };
            }
          }, {
            values: theme("transitionProperty")
          });
        },
        transitionDelay: (0, _createUtilityPlugin.default)("transitionDelay", [
          [
            "delay",
            [
              "transitionDelay"
            ]
          ]
        ]),
        transitionDuration: (0, _createUtilityPlugin.default)("transitionDuration", [
          [
            "duration",
            [
              "transitionDuration"
            ]
          ]
        ], {
          filterDefault: true
        }),
        transitionTimingFunction: (0, _createUtilityPlugin.default)("transitionTimingFunction", [
          [
            "ease",
            [
              "transitionTimingFunction"
            ]
          ]
        ], {
          filterDefault: true
        }),
        willChange: (0, _createUtilityPlugin.default)("willChange", [
          [
            "will-change",
            [
              "will-change"
            ]
          ]
        ]),
        contain: ({ addDefaults, addUtilities }) => {
          let cssContainValue = "var(--tw-contain-size) var(--tw-contain-layout) var(--tw-contain-paint) var(--tw-contain-style)";
          addDefaults("contain", {
            "--tw-contain-size": " ",
            "--tw-contain-layout": " ",
            "--tw-contain-paint": " ",
            "--tw-contain-style": " "
          });
          addUtilities({
            ".contain-none": {
              contain: "none"
            },
            ".contain-content": {
              contain: "content"
            },
            ".contain-strict": {
              contain: "strict"
            },
            ".contain-size": {
              "@defaults contain": {},
              "--tw-contain-size": "size",
              contain: cssContainValue
            },
            ".contain-inline-size": {
              "@defaults contain": {},
              "--tw-contain-size": "inline-size",
              contain: cssContainValue
            },
            ".contain-layout": {
              "@defaults contain": {},
              "--tw-contain-layout": "layout",
              contain: cssContainValue
            },
            ".contain-paint": {
              "@defaults contain": {},
              "--tw-contain-paint": "paint",
              contain: cssContainValue
            },
            ".contain-style": {
              "@defaults contain": {},
              "--tw-contain-style": "style",
              contain: cssContainValue
            }
          });
        },
        content: (0, _createUtilityPlugin.default)("content", [
          [
            "content",
            [
              "--tw-content",
              [
                "content",
                "var(--tw-content)"
              ]
            ]
          ]
        ]),
        forcedColorAdjust: ({ addUtilities }) => {
          addUtilities({
            ".forced-color-adjust-auto": {
              "forced-color-adjust": "auto"
            },
            ".forced-color-adjust-none": {
              "forced-color-adjust": "none"
            }
          });
        }
      };
    }
  });

  // tailwindcss/lib/util/toPath.js
  var require_toPath = __commonJS({
    "tailwindcss/lib/util/toPath.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "toPath", {
        enumerable: true,
        get: function() {
          return toPath;
        }
      });
      function toPath(path) {
        if (Array.isArray(path)) return path;
        let openBrackets = path.split("[").length - 1;
        let closedBrackets = path.split("]").length - 1;
        if (openBrackets !== closedBrackets) {
          throw new Error(`Path is invalid. Has unbalanced brackets: ${path}`);
        }
        return path.split(/\.(?![^\[]*\])|[\[\]]/g).filter(Boolean);
      }
    }
  });

  // tailwindcss/lib/util/isSyntacticallyValidPropertyValue.js
  var require_isSyntacticallyValidPropertyValue = __commonJS({
    "tailwindcss/lib/util/isSyntacticallyValidPropertyValue.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(
        exports,
        // Arbitrary values must contain balanced brackets (), [] and {}. Escaped
        // values don't count, and brackets inside quotes also don't count.
        //
        // E.g.: w-[this-is]w-[weird-and-invalid]
        // E.g.: w-[this-is\\]w-\\[weird-but-valid]
        // E.g.: content-['this-is-also-valid]-weirdly-enough']
        "default",
        {
          enumerable: true,
          get: function() {
            return isSyntacticallyValidPropertyValue;
          }
        }
      );
      var matchingBrackets = /* @__PURE__ */ new Map([
        [
          "{",
          "}"
        ],
        [
          "[",
          "]"
        ],
        [
          "(",
          ")"
        ]
      ]);
      var inverseMatchingBrackets = new Map(Array.from(matchingBrackets.entries()).map(([k, v]) => [
        v,
        k
      ]));
      var quotes = /* @__PURE__ */ new Set([
        '"',
        "'",
        "`"
      ]);
      function isSyntacticallyValidPropertyValue(value) {
        let stack = [];
        let inQuotes = false;
        for (let i = 0; i < value.length; i++) {
          let char = value[i];
          if (char === ":" && !inQuotes && stack.length === 0) {
            return false;
          }
          if (quotes.has(char) && value[i - 1] !== "\\") {
            inQuotes = !inQuotes;
          }
          if (inQuotes) continue;
          if (value[i - 1] === "\\") continue;
          if (matchingBrackets.has(char)) {
            stack.push(char);
          } else if (inverseMatchingBrackets.has(char)) {
            let inverse = inverseMatchingBrackets.get(char);
            if (stack.length <= 0) {
              return false;
            }
            if (stack.pop() !== inverse) {
              return false;
            }
          }
        }
        if (stack.length > 0) {
          return false;
        }
        return true;
      }
    }
  });

  // ../../../stage-b-spike-r01/src/adapters/tailwind-cache-crypto.js
  var require_tailwind_cache_crypto = __commonJS({
    "../../../stage-b-spike-r01/src/adapters/tailwind-cache-crypto.js"(exports, module) {
      "use strict";
      function createHash(algorithm) {
        throw new Error(`cacheInvalidation.js \u7684 Node crypto \u5206\u652F\u672A\u9002\u914D\uFF1A${String(algorithm)}`);
      }
      module.exports = { createHash };
    }
  });

  // tailwindcss/lib/lib/cacheInvalidation.js
  var require_cacheInvalidation = __commonJS({
    "tailwindcss/lib/lib/cacheInvalidation.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "hasContentChanged", {
        enumerable: true,
        get: function() {
          return hasContentChanged;
        }
      });
      var _crypto = /* @__PURE__ */ _interop_require_default(require_tailwind_cache_crypto());
      var _sharedState = /* @__PURE__ */ _interop_require_wildcard(require_sharedState());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      function getHash(str) {
        try {
          return _crypto.default.createHash("md5").update(str, "utf-8").digest("binary");
        } catch (err) {
          return "";
        }
      }
      function hasContentChanged(sourcePath, root) {
        let css = root.toString();
        if (!css.includes("@tailwind")) {
          return false;
        }
        let existingHash = _sharedState.sourceHashMap.get(sourcePath);
        let rootHash = getHash(css);
        let didChange = existingHash !== rootHash;
        _sharedState.sourceHashMap.set(sourcePath, rootHash);
        return didChange;
      }
    }
  });

  // tailwindcss/lib/util/bigSign.js
  var require_bigSign = __commonJS({
    "tailwindcss/lib/util/bigSign.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return bigSign;
        }
      });
      function bigSign(bigIntValue) {
        return (bigIntValue > 0n) - (bigIntValue < 0n);
      }
    }
  });

  // tailwindcss/lib/lib/remap-bitfield.js
  var require_remap_bitfield = __commonJS({
    "tailwindcss/lib/lib/remap-bitfield.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "remapBitfield", {
        enumerable: true,
        get: function() {
          return remapBitfield;
        }
      });
      function remapBitfield(num, mapping) {
        let oldMask = 0n;
        let newMask = 0n;
        for (let [oldBit, newBit] of mapping) {
          if (num & oldBit) {
            oldMask = oldMask | oldBit;
            newMask = newMask | newBit;
          }
        }
        return num & ~oldMask | newMask;
      }
    }
  });

  // tailwindcss/lib/lib/offsets.js
  var require_offsets = __commonJS({
    "tailwindcss/lib/lib/offsets.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "Offsets", {
        enumerable: true,
        get: function() {
          return Offsets;
        }
      });
      var _bigSign = /* @__PURE__ */ _interop_require_default(require_bigSign());
      var _remapbitfield = require_remap_bitfield();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var Offsets = class {
        constructor() {
          this.offsets = {
            defaults: 0n,
            base: 0n,
            components: 0n,
            utilities: 0n,
            variants: 0n,
            user: 0n
          };
          this.layerPositions = {
            defaults: 0n,
            base: 1n,
            components: 2n,
            utilities: 3n,
            // There isn't technically a "user" layer, but we need to give it a position
            // Because it's used for ordering user-css from @apply
            user: 4n,
            variants: 5n
          };
          this.reservedVariantBits = 0n;
          this.variantOffsets = /* @__PURE__ */ new Map();
        }
        /**
        * @param {Layer} layer
        * @returns {RuleOffset}
        */
        create(layer) {
          return {
            layer,
            parentLayer: layer,
            arbitrary: 0n,
            variants: 0n,
            parallelIndex: 0n,
            index: this.offsets[layer]++,
            propertyOffset: 0n,
            property: "",
            options: []
          };
        }
        /**
        * @param {string} name
        * @returns {RuleOffset}
        */
        arbitraryProperty(name) {
          return {
            ...this.create("utilities"),
            arbitrary: 1n,
            property: name
          };
        }
        /**
        * Get the offset for a variant
        *
        * @param {string} variant
        * @param {number} index
        * @returns {RuleOffset}
        */
        forVariant(variant, index = 0) {
          let offset = this.variantOffsets.get(variant);
          if (offset === void 0) {
            throw new Error(`Cannot find offset for unknown variant ${variant}`);
          }
          return {
            ...this.create("variants"),
            variants: offset << BigInt(index)
          };
        }
        /**
        * @param {RuleOffset} rule
        * @param {RuleOffset} variant
        * @param {VariantOption} options
        * @returns {RuleOffset}
        */
        applyVariantOffset(rule, variant, options) {
          options.variant = variant.variants;
          return {
            ...rule,
            layer: "variants",
            parentLayer: rule.layer === "variants" ? rule.parentLayer : rule.layer,
            variants: rule.variants | variant.variants,
            options: options.sort ? [].concat(options, rule.options) : rule.options,
            // TODO: Technically this is wrong. We should be handling parallel index on a per variant basis.
            // We'll take the max of all the parallel indexes for now.
            // @ts-ignore
            parallelIndex: max([
              rule.parallelIndex,
              variant.parallelIndex
            ])
          };
        }
        /**
        * @param {RuleOffset} offset
        * @param {number} parallelIndex
        * @returns {RuleOffset}
        */
        applyParallelOffset(offset, parallelIndex) {
          return {
            ...offset,
            parallelIndex: BigInt(parallelIndex)
          };
        }
        /**
        * Each variant gets 1 bit per function / rule registered.
        * This is because multiple variants can be applied to a single rule and we need to know which ones are present and which ones are not.
        * Additionally, every unique group of variants is grouped together in the stylesheet.
        *
        * This grouping is order-independent. For instance, we do not differentiate between `hover:focus` and `focus:hover`.
        *
        * @param {string[]} variants
        * @param {(name: string) => number} getLength
        */
        recordVariants(variants, getLength) {
          for (let variant of variants) {
            this.recordVariant(variant, getLength(variant));
          }
        }
        /**
        * The same as `recordVariants` but for a single arbitrary variant at runtime.
        * @param {string} variant
        * @param {number} fnCount
        *
        * @returns {RuleOffset} The highest offset for this variant
        */
        recordVariant(variant, fnCount = 1) {
          this.variantOffsets.set(variant, 1n << this.reservedVariantBits);
          this.reservedVariantBits += BigInt(fnCount);
          return {
            ...this.create("variants"),
            variants: this.variantOffsets.get(variant)
          };
        }
        /**
        * @param {RuleOffset} a
        * @param {RuleOffset} b
        * @returns {bigint}
        */
        compare(a, b) {
          if (a.layer !== b.layer) {
            return this.layerPositions[a.layer] - this.layerPositions[b.layer];
          }
          if (a.parentLayer !== b.parentLayer) {
            return this.layerPositions[a.parentLayer] - this.layerPositions[b.parentLayer];
          }
          for (let aOptions of a.options) {
            for (let bOptions of b.options) {
              if (aOptions.id !== bOptions.id) continue;
              if (!aOptions.sort || !bOptions.sort) continue;
              var _max;
              let maxFnVariant = (_max = max([
                aOptions.variant,
                bOptions.variant
              ])) !== null && _max !== void 0 ? _max : 0n;
              let mask = ~(maxFnVariant | maxFnVariant - 1n);
              let aVariantsAfterFn = a.variants & mask;
              let bVariantsAfterFn = b.variants & mask;
              if (aVariantsAfterFn !== bVariantsAfterFn) {
                continue;
              }
              let result = aOptions.sort({
                value: aOptions.value,
                modifier: aOptions.modifier
              }, {
                value: bOptions.value,
                modifier: bOptions.modifier
              });
              if (result !== 0) return result;
            }
          }
          if (a.variants !== b.variants) {
            return a.variants - b.variants;
          }
          if (a.parallelIndex !== b.parallelIndex) {
            return a.parallelIndex - b.parallelIndex;
          }
          if (a.arbitrary !== b.arbitrary) {
            return a.arbitrary - b.arbitrary;
          }
          if (a.propertyOffset !== b.propertyOffset) {
            return a.propertyOffset - b.propertyOffset;
          }
          return a.index - b.index;
        }
        /**
        * Arbitrary variants are recorded in the order they're encountered.
        * This means that the order is not stable between environments and sets of content files.
        *
        * In order to make the order stable, we need to remap the arbitrary variant offsets to
        * be in alphabetical order starting from the offset of the first arbitrary variant.
        */
        recalculateVariantOffsets() {
          let variants = Array.from(this.variantOffsets.entries()).filter(([v]) => v.startsWith("[")).sort(([a], [z]) => fastCompare(a, z));
          let newOffsets = variants.map(([, offset]) => offset).sort((a, z) => (0, _bigSign.default)(a - z));
          let mapping = variants.map(([, oldOffset], i) => [
            oldOffset,
            newOffsets[i]
          ]);
          return mapping.filter(([a, z]) => a !== z);
        }
        /**
        * @template T
        * @param {[RuleOffset, T][]} list
        * @returns {[RuleOffset, T][]}
        */
        remapArbitraryVariantOffsets(list) {
          let mapping = this.recalculateVariantOffsets();
          if (mapping.length === 0) {
            return list;
          }
          return list.map((item) => {
            let [offset, rule] = item;
            offset = {
              ...offset,
              variants: (0, _remapbitfield.remapBitfield)(offset.variants, mapping)
            };
            return [
              offset,
              rule
            ];
          });
        }
        /**
        * @template T
        * @param {[RuleOffset, T][]} list
        * @returns {[RuleOffset, T][]}
        */
        sortArbitraryProperties(list) {
          let known = /* @__PURE__ */ new Set();
          for (let [offset2] of list) {
            if (offset2.arbitrary === 1n) {
              known.add(offset2.property);
            }
          }
          if (known.size === 0) {
            return list;
          }
          let properties = Array.from(known).sort();
          let offsets = /* @__PURE__ */ new Map();
          let offset = 1n;
          for (let property of properties) {
            offsets.set(property, offset++);
          }
          return list.map((item) => {
            let [offset2, rule] = item;
            var _offsets_get;
            offset2 = {
              ...offset2,
              propertyOffset: (_offsets_get = offsets.get(offset2.property)) !== null && _offsets_get !== void 0 ? _offsets_get : 0n
            };
            return [
              offset2,
              rule
            ];
          });
        }
        /**
        * @template T
        * @param {[RuleOffset, T][]} list
        * @returns {[RuleOffset, T][]}
        */
        sort(list) {
          list = this.remapArbitraryVariantOffsets(list);
          list = this.sortArbitraryProperties(list);
          return list.sort(([a], [b]) => (0, _bigSign.default)(this.compare(a, b)));
        }
      };
      function max(nums) {
        let max2 = null;
        for (const num of nums) {
          max2 = max2 !== null && max2 !== void 0 ? max2 : num;
          max2 = max2 > num ? max2 : num;
        }
        return max2;
      }
      function fastCompare(a, b) {
        let aLen = a.length;
        let bLen = b.length;
        let minLen = aLen < bLen ? aLen : bLen;
        for (let i = 0; i < minLen; i++) {
          let cmp = a.charCodeAt(i) - b.charCodeAt(i);
          if (cmp !== 0) return cmp;
        }
        return aLen - bLen;
      }
    }
  });

  // tailwindcss/lib/lib/setupContextUtils.js
  var require_setupContextUtils = __commonJS({
    "tailwindcss/lib/lib/setupContextUtils.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        INTERNAL_FEATURES: function() {
          return INTERNAL_FEATURES;
        },
        isValidVariantFormatString: function() {
          return isValidVariantFormatString;
        },
        parseVariant: function() {
          return parseVariant;
        },
        getFileModifiedMap: function() {
          return getFileModifiedMap;
        },
        createContext: function() {
          return createContext2;
        },
        getContext: function() {
          return getContext;
        }
      });
      var _fs = /* @__PURE__ */ _interop_require_default(require_tailwind_context_fs());
      var _url = /* @__PURE__ */ _interop_require_default(require_tailwind_context_url());
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _dlv = /* @__PURE__ */ _interop_require_default(require_owned_path_get());
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _transformThemeValue = /* @__PURE__ */ _interop_require_default(require_transformThemeValue());
      var _parseObjectStyles = /* @__PURE__ */ _interop_require_default(require_parseObjectStyles());
      var _prefixSelector = /* @__PURE__ */ _interop_require_default(require_prefixSelector());
      var _isPlainObject = /* @__PURE__ */ _interop_require_default(require_isPlainObject());
      var _escapeClassName = /* @__PURE__ */ _interop_require_default(require_escapeClassName());
      var _nameClass = /* @__PURE__ */ _interop_require_wildcard(require_nameClass());
      var _pluginUtils = require_pluginUtils();
      var _corePlugins = require_corePlugins();
      var _sharedState = /* @__PURE__ */ _interop_require_wildcard(require_sharedState());
      var _toPath = require_toPath();
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      var _negateValue = /* @__PURE__ */ _interop_require_default(require_negateValue());
      var _isSyntacticallyValidPropertyValue = /* @__PURE__ */ _interop_require_default(require_isSyntacticallyValidPropertyValue());
      var _generateRules = require_generateRules();
      var _cacheInvalidation = require_cacheInvalidation();
      var _offsets = require_offsets();
      var _featureFlags = require_featureFlags();
      var _formatVariantSelector = require_formatVariantSelector();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      var INTERNAL_FEATURES = Symbol();
      var VARIANT_TYPES = {
        AddVariant: Symbol.for("ADD_VARIANT"),
        MatchVariant: Symbol.for("MATCH_VARIANT")
      };
      var VARIANT_INFO = {
        Base: 1 << 0,
        Dynamic: 1 << 1
      };
      function prefix(context2, selector) {
        let prefix2 = context2.tailwindConfig.prefix;
        return typeof prefix2 === "function" ? prefix2(selector) : prefix2 + selector;
      }
      function normalizeOptionTypes({ type = "any", ...options }) {
        let types = [].concat(type);
        return {
          ...options,
          types: types.map((type2) => {
            if (Array.isArray(type2)) {
              return {
                type: type2[0],
                ...type2[1]
              };
            }
            return {
              type: type2,
              preferOnConflict: false
            };
          })
        };
      }
      function parseVariantFormatString(input) {
        let parts = [];
        let current = "";
        let depth = 0;
        for (let idx = 0; idx < input.length; idx++) {
          let char = input[idx];
          if (char === "\\") {
            current += "\\" + input[++idx];
          } else if (char === "{") {
            ++depth;
            parts.push(current.trim());
            current = "";
          } else if (char === "}") {
            if (--depth < 0) {
              throw new Error(`Your { and } are unbalanced.`);
            }
            parts.push(current.trim());
            current = "";
          } else {
            current += char;
          }
        }
        if (current.length > 0) {
          parts.push(current.trim());
        }
        parts = parts.filter((part) => part !== "");
        return parts;
      }
      function insertInto(list, value, { before = [] } = {}) {
        before = [].concat(before);
        if (before.length <= 0) {
          list.push(value);
          return;
        }
        let idx = list.length - 1;
        for (let other of before) {
          let iidx = list.indexOf(other);
          if (iidx === -1) continue;
          idx = Math.min(idx, iidx);
        }
        list.splice(idx, 0, value);
      }
      function parseStyles(styles) {
        if (!Array.isArray(styles)) {
          return parseStyles([
            styles
          ]);
        }
        return styles.flatMap((style) => {
          let isNode = !Array.isArray(style) && !(0, _isPlainObject.default)(style);
          return isNode ? style : (0, _parseObjectStyles.default)(style);
        });
      }
      function getClasses(selector, mutate) {
        let parser = (0, _postcssselectorparser.default)((selectors) => {
          let allClasses = [];
          if (mutate) {
            mutate(selectors);
          }
          selectors.walkClasses((classNode) => {
            allClasses.push(classNode.value);
          });
          return allClasses;
        });
        return parser.transformSync(selector);
      }
      function ignoreNot(selectors) {
        selectors.walkPseudos((pseudo) => {
          if (pseudo.value === ":not") {
            pseudo.remove();
          }
        });
      }
      function extractCandidates(node, state = {
        containsNonOnDemandable: false
      }, depth = 0) {
        let classes = [];
        let selectors = [];
        if (node.type === "rule") {
          selectors.push(...node.selectors);
        } else if (node.type === "atrule") {
          node.walkRules((rule) => selectors.push(...rule.selectors));
        }
        for (let selector of selectors) {
          let classCandidates = getClasses(selector, ignoreNot);
          if (classCandidates.length === 0) {
            state.containsNonOnDemandable = true;
          }
          for (let classCandidate of classCandidates) {
            classes.push(classCandidate);
          }
        }
        if (depth === 0) {
          return [
            state.containsNonOnDemandable || classes.length === 0,
            classes
          ];
        }
        return classes;
      }
      function withIdentifiers(styles) {
        return parseStyles(styles).flatMap((node) => {
          let nodeMap = /* @__PURE__ */ new Map();
          let [containsNonOnDemandableSelectors, candidates] = extractCandidates(node);
          if (containsNonOnDemandableSelectors) {
            candidates.unshift(_sharedState.NOT_ON_DEMAND);
          }
          return candidates.map((c) => {
            if (!nodeMap.has(node)) {
              nodeMap.set(node, node);
            }
            return [
              c,
              nodeMap.get(node)
            ];
          });
        });
      }
      function isValidVariantFormatString(format) {
        return format.startsWith("@") || format.includes("&");
      }
      function parseVariant(variant) {
        variant = variant.replace(/\n+/g, "").replace(/\s{1,}/g, " ").trim();
        let fns = parseVariantFormatString(variant).map((str) => {
          if (!str.startsWith("@")) {
            return ({ format }) => format(str);
          }
          let [, name, params] = /@(\S*)( .+|[({].*)?/g.exec(str);
          var _params_trim;
          return ({ wrap }) => {
            return wrap(_postcss.default.atRule({
              name,
              params: (_params_trim = params === null || params === void 0 ? void 0 : params.trim()) !== null && _params_trim !== void 0 ? _params_trim : ""
            }));
          };
        }).reverse();
        return (api) => {
          for (let fn of fns) {
            fn(api);
          }
        };
      }
      function buildPluginApi(tailwindConfig, context2, { variantList, variantMap, offsets, classList }) {
        function getConfigValue(path, defaultValue) {
          return path ? (0, _dlv.default)(tailwindConfig, path, defaultValue) : tailwindConfig;
        }
        function applyConfiguredPrefix(selector) {
          return (0, _prefixSelector.default)(tailwindConfig.prefix, selector);
        }
        function prefixIdentifier(identifier, options) {
          if (identifier === _sharedState.NOT_ON_DEMAND) {
            return _sharedState.NOT_ON_DEMAND;
          }
          if (!options.respectPrefix) {
            return identifier;
          }
          return context2.tailwindConfig.prefix + identifier;
        }
        function resolveThemeValue(path, defaultValue, opts = {}) {
          let parts = (0, _toPath.toPath)(path);
          let value = getConfigValue([
            "theme",
            ...parts
          ], defaultValue);
          return (0, _transformThemeValue.default)(parts[0])(value, opts);
        }
        let variantIdentifier = 0;
        let api = {
          postcss: _postcss.default,
          prefix: applyConfiguredPrefix,
          e: _escapeClassName.default,
          config: getConfigValue,
          theme: resolveThemeValue,
          corePlugins: (path) => {
            if (Array.isArray(tailwindConfig.corePlugins)) {
              return tailwindConfig.corePlugins.includes(path);
            }
            return getConfigValue([
              "corePlugins",
              path
            ], true);
          },
          variants: () => {
            return [];
          },
          addBase(base) {
            for (let [identifier, rule] of withIdentifiers(base)) {
              let prefixedIdentifier = prefixIdentifier(identifier, {});
              let offset = offsets.create("base");
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push([
                {
                  sort: offset,
                  layer: "base"
                },
                rule
              ]);
            }
          },
          /**
          * @param {string} group
          * @param {Record<string, string | string[]>} declarations
          */
          addDefaults(group, declarations) {
            const groups = {
              [`@defaults ${group}`]: declarations
            };
            for (let [identifier, rule] of withIdentifiers(groups)) {
              let prefixedIdentifier = prefixIdentifier(identifier, {});
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push([
                {
                  sort: offsets.create("defaults"),
                  layer: "defaults"
                },
                rule
              ]);
            }
          },
          addComponents(components, options) {
            let defaultOptions = {
              preserveSource: false,
              respectPrefix: true,
              respectImportant: false
            };
            options = Object.assign({}, defaultOptions, Array.isArray(options) ? {} : options);
            for (let [identifier, rule] of withIdentifiers(components)) {
              let prefixedIdentifier = prefixIdentifier(identifier, options);
              classList.add(prefixedIdentifier);
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push([
                {
                  sort: offsets.create("components"),
                  layer: "components",
                  options
                },
                rule
              ]);
            }
          },
          addUtilities(utilities, options) {
            let defaultOptions = {
              preserveSource: false,
              respectPrefix: true,
              respectImportant: true
            };
            options = Object.assign({}, defaultOptions, Array.isArray(options) ? {} : options);
            for (let [identifier, rule] of withIdentifiers(utilities)) {
              let prefixedIdentifier = prefixIdentifier(identifier, options);
              classList.add(prefixedIdentifier);
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push([
                {
                  sort: offsets.create("utilities"),
                  layer: "utilities",
                  options
                },
                rule
              ]);
            }
          },
          matchUtilities: function(utilities, options) {
            let defaultOptions = {
              respectPrefix: true,
              respectImportant: true,
              modifiers: false
            };
            options = normalizeOptionTypes({
              ...defaultOptions,
              ...options
            });
            let offset = offsets.create("utilities");
            for (let identifier in utilities) {
              let wrapped = function(modifier, { isOnlyPlugin }) {
                let [value, coercedType, utilityModifier] = (0, _pluginUtils.coerceValue)(options.types, modifier, options, tailwindConfig);
                if (value === void 0) {
                  return [];
                }
                if (!options.types.some(({ type }) => type === coercedType)) {
                  if (isOnlyPlugin) {
                    _log.default.warn([
                      `Unnecessary typehint \`${coercedType}\` in \`${identifier}-${modifier}\`.`,
                      `You can safely update it to \`${identifier}-${modifier.replace(coercedType + ":", "")}\`.`
                    ]);
                  } else {
                    return [];
                  }
                }
                if (!(0, _isSyntacticallyValidPropertyValue.default)(value)) {
                  return [];
                }
                let extras = {
                  get modifier() {
                    if (!options.modifiers) {
                      _log.default.warn(`modifier-used-without-options-for-${identifier}`, [
                        "Your plugin must set `modifiers: true` in its options to support modifiers."
                      ]);
                    }
                    return utilityModifier;
                  }
                };
                let modifiersEnabled = (0, _featureFlags.flagEnabled)(tailwindConfig, "generalizedModifiers");
                let ruleSets = [].concat(modifiersEnabled ? rule(value, extras) : rule(value)).filter(Boolean).map((declaration) => ({
                  [(0, _nameClass.default)(identifier, modifier)]: declaration
                }));
                return ruleSets;
              };
              let prefixedIdentifier = prefixIdentifier(identifier, options);
              let rule = utilities[identifier];
              classList.add([
                prefixedIdentifier,
                options
              ]);
              let withOffsets = [
                {
                  sort: offset,
                  layer: "utilities",
                  options
                },
                wrapped
              ];
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push(withOffsets);
            }
          },
          matchComponents: function(components, options) {
            let defaultOptions = {
              respectPrefix: true,
              respectImportant: false,
              modifiers: false
            };
            options = normalizeOptionTypes({
              ...defaultOptions,
              ...options
            });
            let offset = offsets.create("components");
            for (let identifier in components) {
              let wrapped = function(modifier, { isOnlyPlugin }) {
                let [value, coercedType, utilityModifier] = (0, _pluginUtils.coerceValue)(options.types, modifier, options, tailwindConfig);
                if (value === void 0) {
                  return [];
                }
                if (!options.types.some(({ type }) => type === coercedType)) {
                  if (isOnlyPlugin) {
                    _log.default.warn([
                      `Unnecessary typehint \`${coercedType}\` in \`${identifier}-${modifier}\`.`,
                      `You can safely update it to \`${identifier}-${modifier.replace(coercedType + ":", "")}\`.`
                    ]);
                  } else {
                    return [];
                  }
                }
                if (!(0, _isSyntacticallyValidPropertyValue.default)(value)) {
                  return [];
                }
                let extras = {
                  get modifier() {
                    if (!options.modifiers) {
                      _log.default.warn(`modifier-used-without-options-for-${identifier}`, [
                        "Your plugin must set `modifiers: true` in its options to support modifiers."
                      ]);
                    }
                    return utilityModifier;
                  }
                };
                let modifiersEnabled = (0, _featureFlags.flagEnabled)(tailwindConfig, "generalizedModifiers");
                let ruleSets = [].concat(modifiersEnabled ? rule(value, extras) : rule(value)).filter(Boolean).map((declaration) => ({
                  [(0, _nameClass.default)(identifier, modifier)]: declaration
                }));
                return ruleSets;
              };
              let prefixedIdentifier = prefixIdentifier(identifier, options);
              let rule = components[identifier];
              classList.add([
                prefixedIdentifier,
                options
              ]);
              let withOffsets = [
                {
                  sort: offset,
                  layer: "components",
                  options
                },
                wrapped
              ];
              if (!context2.candidateRuleMap.has(prefixedIdentifier)) {
                context2.candidateRuleMap.set(prefixedIdentifier, []);
              }
              context2.candidateRuleMap.get(prefixedIdentifier).push(withOffsets);
            }
          },
          addVariant(variantName, variantFunctions, options = {}) {
            variantFunctions = [].concat(variantFunctions).map((variantFunction) => {
              if (typeof variantFunction !== "string") {
                return (api2 = {}) => {
                  let { args, modifySelectors, container, separator, wrap, format } = api2;
                  let result = variantFunction(Object.assign({
                    modifySelectors,
                    container,
                    separator
                  }, options.type === VARIANT_TYPES.MatchVariant && {
                    args,
                    wrap,
                    format
                  }));
                  if (typeof result === "string" && !isValidVariantFormatString(result)) {
                    throw new Error(`Your custom variant \`${variantName}\` has an invalid format string. Make sure it's an at-rule or contains a \`&\` placeholder.`);
                  }
                  if (Array.isArray(result)) {
                    return result.filter((variant) => typeof variant === "string").map((variant) => parseVariant(variant));
                  }
                  return result && typeof result === "string" && parseVariant(result)(api2);
                };
              }
              if (!isValidVariantFormatString(variantFunction)) {
                throw new Error(`Your custom variant \`${variantName}\` has an invalid format string. Make sure it's an at-rule or contains a \`&\` placeholder.`);
              }
              return parseVariant(variantFunction);
            });
            insertInto(variantList, variantName, options);
            variantMap.set(variantName, variantFunctions);
            context2.variantOptions.set(variantName, options);
          },
          matchVariant(variant, variantFn, options) {
            var _options_id;
            let id = (_options_id = options === null || options === void 0 ? void 0 : options.id) !== null && _options_id !== void 0 ? _options_id : ++variantIdentifier;
            let isSpecial = variant === "@";
            let modifiersEnabled = (0, _featureFlags.flagEnabled)(tailwindConfig, "generalizedModifiers");
            var _options_values;
            for (let [key, value] of Object.entries((_options_values = options === null || options === void 0 ? void 0 : options.values) !== null && _options_values !== void 0 ? _options_values : {})) {
              if (key === "DEFAULT") continue;
              api.addVariant(isSpecial ? `${variant}${key}` : `${variant}-${key}`, ({ args, container }) => {
                return variantFn(value, modifiersEnabled ? {
                  modifier: args === null || args === void 0 ? void 0 : args.modifier,
                  container
                } : {
                  container
                });
              }, {
                ...options,
                value,
                id,
                type: VARIANT_TYPES.MatchVariant,
                variantInfo: VARIANT_INFO.Base
              });
            }
            var _options_values1;
            let hasDefault = "DEFAULT" in ((_options_values1 = options === null || options === void 0 ? void 0 : options.values) !== null && _options_values1 !== void 0 ? _options_values1 : {});
            api.addVariant(variant, ({ args, container }) => {
              if ((args === null || args === void 0 ? void 0 : args.value) === _sharedState.NONE && !hasDefault) {
                return null;
              }
              var _args_value;
              return variantFn((args === null || args === void 0 ? void 0 : args.value) === _sharedState.NONE ? options.values.DEFAULT : (_args_value = args === null || args === void 0 ? void 0 : args.value) !== null && _args_value !== void 0 ? _args_value : typeof args === "string" ? args : "", modifiersEnabled ? {
                modifier: args === null || args === void 0 ? void 0 : args.modifier,
                container
              } : {
                container
              });
            }, {
              ...options,
              id,
              type: VARIANT_TYPES.MatchVariant,
              variantInfo: VARIANT_INFO.Dynamic
            });
          }
        };
        return api;
      }
      var fileModifiedMapCache = /* @__PURE__ */ new WeakMap();
      function getFileModifiedMap(context2) {
        if (!fileModifiedMapCache.has(context2)) {
          fileModifiedMapCache.set(context2, /* @__PURE__ */ new Map());
        }
        return fileModifiedMapCache.get(context2);
      }
      function trackModified(files, fileModifiedMap) {
        let changed = false;
        let mtimesToCommit = /* @__PURE__ */ new Map();
        for (let file of files) {
          var _fs_statSync;
          if (!file) continue;
          let parsed = _url.default.parse(file);
          let pathname = parsed.hash ? parsed.href.replace(parsed.hash, "") : parsed.href;
          pathname = parsed.search ? pathname.replace(parsed.search, "") : pathname;
          let newModified = (_fs_statSync = _fs.default.statSync(decodeURIComponent(pathname), {
            throwIfNoEntry: false
          })) === null || _fs_statSync === void 0 ? void 0 : _fs_statSync.mtimeMs;
          if (!newModified) {
            continue;
          }
          if (!fileModifiedMap.has(file) || newModified > fileModifiedMap.get(file)) {
            changed = true;
          }
          mtimesToCommit.set(file, newModified);
        }
        return [
          changed,
          mtimesToCommit
        ];
      }
      function extractVariantAtRules(node) {
        node.walkAtRules((atRule) => {
          if ([
            "responsive",
            "variants"
          ].includes(atRule.name)) {
            extractVariantAtRules(atRule);
            atRule.before(atRule.nodes);
            atRule.remove();
          }
        });
      }
      function collectLayerPlugins(root) {
        let layerPlugins = [];
        root.each((node) => {
          if (node.type === "atrule" && [
            "responsive",
            "variants"
          ].includes(node.name)) {
            node.name = "layer";
            node.params = "utilities";
          }
        });
        root.walkAtRules("layer", (layerRule) => {
          extractVariantAtRules(layerRule);
          if (layerRule.params === "base") {
            for (let node of layerRule.nodes) {
              layerPlugins.push(function({ addBase }) {
                addBase(node, {
                  respectPrefix: false
                });
              });
            }
            layerRule.remove();
          } else if (layerRule.params === "components") {
            for (let node of layerRule.nodes) {
              layerPlugins.push(function({ addComponents }) {
                addComponents(node, {
                  respectPrefix: false,
                  preserveSource: true
                });
              });
            }
            layerRule.remove();
          } else if (layerRule.params === "utilities") {
            for (let node of layerRule.nodes) {
              layerPlugins.push(function({ addUtilities }) {
                addUtilities(node, {
                  respectPrefix: false,
                  preserveSource: true
                });
              });
            }
            layerRule.remove();
          }
        });
        return layerPlugins;
      }
      function resolvePlugins(context2, root) {
        let corePluginList = Object.entries({
          ..._corePlugins.variantPlugins,
          ..._corePlugins.corePlugins
        }).map(([name, plugin2]) => {
          if (!context2.tailwindConfig.corePlugins.includes(name)) {
            return null;
          }
          return plugin2;
        }).filter(Boolean);
        let userPlugins = context2.tailwindConfig.plugins.map((plugin2) => {
          if (plugin2.__isOptionsFunction) {
            plugin2 = plugin2();
          }
          return typeof plugin2 === "function" ? plugin2 : plugin2.handler;
        });
        let layerPlugins = collectLayerPlugins(root);
        let beforeVariants = [
          _corePlugins.variantPlugins["childVariant"],
          _corePlugins.variantPlugins["pseudoElementVariants"],
          _corePlugins.variantPlugins["pseudoClassVariants"],
          _corePlugins.variantPlugins["hasVariants"],
          _corePlugins.variantPlugins["ariaVariants"],
          _corePlugins.variantPlugins["dataVariants"]
        ];
        let afterVariants = [
          _corePlugins.variantPlugins["supportsVariants"],
          _corePlugins.variantPlugins["reducedMotionVariants"],
          _corePlugins.variantPlugins["prefersContrastVariants"],
          _corePlugins.variantPlugins["screenVariants"],
          _corePlugins.variantPlugins["orientationVariants"],
          _corePlugins.variantPlugins["directionVariants"],
          _corePlugins.variantPlugins["darkVariants"],
          _corePlugins.variantPlugins["forcedColorsVariants"],
          _corePlugins.variantPlugins["printVariant"]
        ];
        let isLegacyDarkMode = context2.tailwindConfig.darkMode === "class" || Array.isArray(context2.tailwindConfig.darkMode) && context2.tailwindConfig.darkMode[0] === "class";
        if (isLegacyDarkMode) {
          afterVariants = [
            _corePlugins.variantPlugins["supportsVariants"],
            _corePlugins.variantPlugins["reducedMotionVariants"],
            _corePlugins.variantPlugins["prefersContrastVariants"],
            _corePlugins.variantPlugins["darkVariants"],
            _corePlugins.variantPlugins["screenVariants"],
            _corePlugins.variantPlugins["orientationVariants"],
            _corePlugins.variantPlugins["directionVariants"],
            _corePlugins.variantPlugins["forcedColorsVariants"],
            _corePlugins.variantPlugins["printVariant"]
          ];
        }
        return [
          ...corePluginList,
          ...beforeVariants,
          ...userPlugins,
          ...afterVariants,
          ...layerPlugins
        ];
      }
      function registerPlugins(plugins, context2) {
        let variantList = [];
        let variantMap = /* @__PURE__ */ new Map();
        context2.variantMap = variantMap;
        let offsets = new _offsets.Offsets();
        context2.offsets = offsets;
        let classList = /* @__PURE__ */ new Set();
        let pluginApi = buildPluginApi(context2.tailwindConfig, context2, {
          variantList,
          variantMap,
          offsets,
          classList
        });
        for (let plugin2 of plugins) {
          if (Array.isArray(plugin2)) {
            for (let pluginItem of plugin2) {
              pluginItem(pluginApi);
            }
          } else {
            plugin2 === null || plugin2 === void 0 ? void 0 : plugin2(pluginApi);
          }
        }
        offsets.recordVariants(variantList, (variant) => variantMap.get(variant).length);
        for (let [variantName, variantFunctions] of variantMap.entries()) {
          context2.variantMap.set(variantName, variantFunctions.map((variantFunction, idx) => [
            offsets.forVariant(variantName, idx),
            variantFunction
          ]));
        }
        var _context_tailwindConfig_safelist;
        let safelist = ((_context_tailwindConfig_safelist = context2.tailwindConfig.safelist) !== null && _context_tailwindConfig_safelist !== void 0 ? _context_tailwindConfig_safelist : []).filter(Boolean);
        if (safelist.length > 0) {
          let checks = [];
          for (let value of safelist) {
            if (typeof value === "string") {
              context2.changedContent.push({
                content: value,
                extension: "html"
              });
              continue;
            }
            if (value instanceof RegExp) {
              _log.default.warn("root-regex", [
                "Regular expressions in `safelist` work differently in Tailwind CSS v3.0.",
                "Update your `safelist` configuration to eliminate this warning.",
                "https://tailwindcss.com/docs/content-configuration#safelisting-classes"
              ]);
              continue;
            }
            checks.push(value);
          }
          if (checks.length > 0) {
            let patternMatchingCount = /* @__PURE__ */ new Map();
            let prefixLength = context2.tailwindConfig.prefix.length;
            let checkImportantUtils = checks.some((check) => check.pattern.source.includes("!"));
            for (let util of classList) {
              let utils = Array.isArray(util) ? (() => {
                let [utilName, options] = util;
                var _options_values;
                let values = Object.keys((_options_values = options === null || options === void 0 ? void 0 : options.values) !== null && _options_values !== void 0 ? _options_values : {});
                let classes = values.map((value) => (0, _nameClass.formatClass)(utilName, value));
                if (options === null || options === void 0 ? void 0 : options.supportsNegativeValues) {
                  classes = [
                    ...classes,
                    ...classes.map((cls) => "-" + cls)
                  ];
                  classes = [
                    ...classes,
                    ...classes.map((cls) => cls.slice(0, prefixLength) + "-" + cls.slice(prefixLength))
                  ];
                }
                if (options.types.some(({ type }) => type === "color")) {
                  classes = [
                    ...classes,
                    ...classes.flatMap((cls) => Object.keys(context2.tailwindConfig.theme.opacity).map((opacity) => `${cls}/${opacity}`))
                  ];
                }
                if (checkImportantUtils && (options === null || options === void 0 ? void 0 : options.respectImportant)) {
                  classes = [
                    ...classes,
                    ...classes.map((cls) => "!" + cls)
                  ];
                }
                return classes;
              })() : [
                util
              ];
              for (let util2 of utils) {
                for (let { pattern, variants = [] } of checks) {
                  pattern.lastIndex = 0;
                  if (!patternMatchingCount.has(pattern)) {
                    patternMatchingCount.set(pattern, 0);
                  }
                  if (!pattern.test(util2)) continue;
                  patternMatchingCount.set(pattern, patternMatchingCount.get(pattern) + 1);
                  context2.changedContent.push({
                    content: util2,
                    extension: "html"
                  });
                  for (let variant of variants) {
                    context2.changedContent.push({
                      content: variant + context2.tailwindConfig.separator + util2,
                      extension: "html"
                    });
                  }
                }
              }
            }
            for (let [regex, count] of patternMatchingCount.entries()) {
              if (count !== 0) continue;
              _log.default.warn([
                `The safelist pattern \`${regex}\` doesn't match any Tailwind CSS classes.`,
                "Fix this pattern or remove it from your `safelist` configuration.",
                "https://tailwindcss.com/docs/content-configuration#safelisting-classes"
              ]);
            }
          }
        }
        var _context_tailwindConfig_darkMode, _concat_;
        let darkClassName = (_concat_ = [].concat((_context_tailwindConfig_darkMode = context2.tailwindConfig.darkMode) !== null && _context_tailwindConfig_darkMode !== void 0 ? _context_tailwindConfig_darkMode : "media")[1]) !== null && _concat_ !== void 0 ? _concat_ : "dark";
        let parasiteUtilities = [
          prefix(context2, darkClassName),
          prefix(context2, "group"),
          prefix(context2, "peer")
        ];
        context2.getClassOrder = function getClassOrder(classes) {
          let sorted = [
            ...classes
          ].sort((a, z) => {
            if (a === z) return 0;
            if (a < z) return -1;
            return 1;
          });
          let sortedClassNames = new Map(sorted.map((className) => [
            className,
            null
          ]));
          let rules = (0, _generateRules.generateRules)(new Set(sorted), context2, true);
          rules = context2.offsets.sort(rules);
          let idx = BigInt(parasiteUtilities.length);
          for (const [, rule] of rules) {
            let candidate = rule.raws.tailwind.candidate;
            var _sortedClassNames_get;
            sortedClassNames.set(candidate, (_sortedClassNames_get = sortedClassNames.get(candidate)) !== null && _sortedClassNames_get !== void 0 ? _sortedClassNames_get : idx++);
          }
          return classes.map((className) => {
            var _sortedClassNames_get2;
            let order = (_sortedClassNames_get2 = sortedClassNames.get(className)) !== null && _sortedClassNames_get2 !== void 0 ? _sortedClassNames_get2 : null;
            let parasiteIndex = parasiteUtilities.indexOf(className);
            if (order === null && parasiteIndex !== -1) {
              order = BigInt(parasiteIndex);
            }
            return [
              className,
              order
            ];
          });
        };
        context2.getClassList = function getClassList(options = {}) {
          let output2 = [];
          for (let util of classList) {
            if (Array.isArray(util)) {
              var _utilOptions_types;
              let [utilName, utilOptions] = util;
              let negativeClasses = [];
              var _utilOptions_modifiers;
              let modifiers = Object.keys((_utilOptions_modifiers = utilOptions === null || utilOptions === void 0 ? void 0 : utilOptions.modifiers) !== null && _utilOptions_modifiers !== void 0 ? _utilOptions_modifiers : {});
              if (utilOptions === null || utilOptions === void 0 ? void 0 : (_utilOptions_types = utilOptions.types) === null || _utilOptions_types === void 0 ? void 0 : _utilOptions_types.some(({ type }) => type === "color")) {
                var _context_tailwindConfig_theme_opacity;
                modifiers.push(...Object.keys((_context_tailwindConfig_theme_opacity = context2.tailwindConfig.theme.opacity) !== null && _context_tailwindConfig_theme_opacity !== void 0 ? _context_tailwindConfig_theme_opacity : {}));
              }
              let metadata = {
                modifiers
              };
              let includeMetadata = options.includeMetadata && modifiers.length > 0;
              var _utilOptions_values;
              for (let [key, value] of Object.entries((_utilOptions_values = utilOptions === null || utilOptions === void 0 ? void 0 : utilOptions.values) !== null && _utilOptions_values !== void 0 ? _utilOptions_values : {})) {
                if (value == null) {
                  continue;
                }
                let cls = (0, _nameClass.formatClass)(utilName, key);
                output2.push(includeMetadata ? [
                  cls,
                  metadata
                ] : cls);
                if ((utilOptions === null || utilOptions === void 0 ? void 0 : utilOptions.supportsNegativeValues) && (0, _negateValue.default)(value)) {
                  let cls2 = (0, _nameClass.formatClass)(utilName, `-${key}`);
                  negativeClasses.push(includeMetadata ? [
                    cls2,
                    metadata
                  ] : cls2);
                }
              }
              output2.push(...negativeClasses);
            } else {
              output2.push(util);
            }
          }
          return output2;
        };
        context2.getVariants = function getVariants() {
          let id = Math.random().toString(36).substring(7).toUpperCase();
          let result = [];
          for (let [name, options] of context2.variantOptions.entries()) {
            if (options.variantInfo === VARIANT_INFO.Base) continue;
            var _options_values;
            result.push({
              name,
              isArbitrary: options.type === Symbol.for("MATCH_VARIANT"),
              values: Object.keys((_options_values = options.values) !== null && _options_values !== void 0 ? _options_values : {}),
              hasDash: name !== "@",
              selectors({ modifier, value } = {}) {
                let candidate = `TAILWINDPLACEHOLDER${id}`;
                let rule = _postcss.default.rule({
                  selector: `.${candidate}`
                });
                let container = _postcss.default.root({
                  nodes: [
                    rule.clone()
                  ]
                });
                let before = container.toString();
                var _context_variantMap_get;
                let fns = ((_context_variantMap_get = context2.variantMap.get(name)) !== null && _context_variantMap_get !== void 0 ? _context_variantMap_get : []).flatMap(([_, fn]) => fn);
                let formatStrings = [];
                for (let fn of fns) {
                  var _options_values2;
                  let localFormatStrings = [];
                  var _options_values_value;
                  let api = {
                    args: {
                      modifier,
                      value: (_options_values_value = (_options_values2 = options.values) === null || _options_values2 === void 0 ? void 0 : _options_values2[value]) !== null && _options_values_value !== void 0 ? _options_values_value : value
                    },
                    separator: context2.tailwindConfig.separator,
                    modifySelectors(modifierFunction) {
                      container.each((rule2) => {
                        if (rule2.type !== "rule") {
                          return;
                        }
                        rule2.selectors = rule2.selectors.map((selector) => {
                          return modifierFunction({
                            get className() {
                              return (0, _generateRules.getClassNameFromSelector)(selector);
                            },
                            selector
                          });
                        });
                      });
                      return container;
                    },
                    format(str) {
                      localFormatStrings.push(str);
                    },
                    wrap(wrapper) {
                      localFormatStrings.push(`@${wrapper.name} ${wrapper.params} { & }`);
                    },
                    container
                  };
                  let ruleWithVariant = fn(api);
                  if (localFormatStrings.length > 0) {
                    formatStrings.push(localFormatStrings);
                  }
                  if (Array.isArray(ruleWithVariant)) {
                    for (let variantFunction of ruleWithVariant) {
                      localFormatStrings = [];
                      variantFunction(api);
                      formatStrings.push(localFormatStrings);
                    }
                  }
                }
                let manualFormatStrings = [];
                let after = container.toString();
                if (before !== after) {
                  container.walkRules((rule2) => {
                    let modified = rule2.selector;
                    let rebuiltBase = (0, _postcssselectorparser.default)((selectors) => {
                      selectors.walkClasses((classNode) => {
                        classNode.value = `${name}${context2.tailwindConfig.separator}${classNode.value}`;
                      });
                    }).processSync(modified);
                    manualFormatStrings.push(modified.replace(rebuiltBase, "&").replace(candidate, "&"));
                  });
                  container.walkAtRules((atrule) => {
                    manualFormatStrings.push(`@${atrule.name} (${atrule.params}) { & }`);
                  });
                }
                var _options_values1;
                let isArbitraryVariant = !(value in ((_options_values1 = options.values) !== null && _options_values1 !== void 0 ? _options_values1 : {}));
                var _options_INTERNAL_FEATURES;
                let internalFeatures = (_options_INTERNAL_FEATURES = options[INTERNAL_FEATURES]) !== null && _options_INTERNAL_FEATURES !== void 0 ? _options_INTERNAL_FEATURES : {};
                let respectPrefix = (() => {
                  if (isArbitraryVariant) return false;
                  if (internalFeatures.respectPrefix === false) return false;
                  return true;
                })();
                formatStrings = formatStrings.map((format) => format.map((str) => ({
                  format: str,
                  respectPrefix
                })));
                manualFormatStrings = manualFormatStrings.map((format) => ({
                  format,
                  respectPrefix
                }));
                let opts = {
                  candidate,
                  context: context2
                };
                let result2 = formatStrings.map((formats) => (0, _formatVariantSelector.finalizeSelector)(`.${candidate}`, (0, _formatVariantSelector.formatVariantSelector)(formats, opts), opts).replace(`.${candidate}`, "&").replace("{ & }", "").trim());
                if (manualFormatStrings.length > 0) {
                  result2.push((0, _formatVariantSelector.formatVariantSelector)(manualFormatStrings, opts).toString().replace(`.${candidate}`, "&"));
                }
                return result2;
              }
            });
          }
          return result;
        };
      }
      function markInvalidUtilityCandidate(context2, candidate) {
        if (!context2.classCache.has(candidate)) {
          return;
        }
        context2.notClassCache.add(candidate);
        context2.classCache.delete(candidate);
        context2.applyClassCache.delete(candidate);
        context2.candidateRuleMap.delete(candidate);
        context2.candidateRuleCache.delete(candidate);
        context2.stylesheetCache = null;
      }
      function markInvalidUtilityNode(context2, node) {
        let candidate = node.raws.tailwind.candidate;
        if (!candidate) {
          return;
        }
        for (const entry of context2.ruleCache) {
          if (entry[1].raws.tailwind.candidate === candidate) {
            context2.ruleCache.delete(entry);
          }
        }
        markInvalidUtilityCandidate(context2, candidate);
      }
      function createContext2(tailwindConfig, changedContent = [], root = _postcss.default.root()) {
        var _tailwindConfig_blocklist;
        let context2 = {
          disposables: [],
          ruleCache: /* @__PURE__ */ new Set(),
          candidateRuleCache: /* @__PURE__ */ new Map(),
          classCache: /* @__PURE__ */ new Map(),
          applyClassCache: /* @__PURE__ */ new Map(),
          // Seed the not class cache with the blocklist (which is only strings)
          notClassCache: new Set((_tailwindConfig_blocklist = tailwindConfig.blocklist) !== null && _tailwindConfig_blocklist !== void 0 ? _tailwindConfig_blocklist : []),
          postCssNodeCache: /* @__PURE__ */ new Map(),
          candidateRuleMap: /* @__PURE__ */ new Map(),
          tailwindConfig,
          changedContent,
          variantMap: /* @__PURE__ */ new Map(),
          stylesheetCache: null,
          variantOptions: /* @__PURE__ */ new Map(),
          markInvalidUtilityCandidate: (candidate) => markInvalidUtilityCandidate(context2, candidate),
          markInvalidUtilityNode: (node) => markInvalidUtilityNode(context2, node)
        };
        let resolvedPlugins = resolvePlugins(context2, root);
        registerPlugins(resolvedPlugins, context2);
        return context2;
      }
      var contextMap = _sharedState.contextMap;
      var configContextMap = _sharedState.configContextMap;
      var contextSourcesMap = _sharedState.contextSourcesMap;
      function getContext(root, result, tailwindConfig, userConfigPath, tailwindConfigHash, contextDependencies) {
        let sourcePath = result.opts.from;
        let isConfigFile = userConfigPath !== null;
        _sharedState.env.DEBUG && console.log("Source path:", sourcePath);
        let existingContext;
        if (isConfigFile && contextMap.has(sourcePath)) {
          existingContext = contextMap.get(sourcePath);
        } else if (configContextMap.has(tailwindConfigHash)) {
          let context3 = configContextMap.get(tailwindConfigHash);
          contextSourcesMap.get(context3).add(sourcePath);
          contextMap.set(sourcePath, context3);
          existingContext = context3;
        }
        let cssDidChange = (0, _cacheInvalidation.hasContentChanged)(sourcePath, root);
        if (existingContext) {
          let [contextDependenciesChanged, mtimesToCommit2] = trackModified([
            ...contextDependencies
          ], getFileModifiedMap(existingContext));
          if (!contextDependenciesChanged && !cssDidChange) {
            return [
              existingContext,
              false,
              mtimesToCommit2
            ];
          }
        }
        if (contextMap.has(sourcePath)) {
          let oldContext = contextMap.get(sourcePath);
          if (contextSourcesMap.has(oldContext)) {
            contextSourcesMap.get(oldContext).delete(sourcePath);
            if (contextSourcesMap.get(oldContext).size === 0) {
              contextSourcesMap.delete(oldContext);
              for (let [tailwindConfigHash2, context3] of configContextMap) {
                if (context3 === oldContext) {
                  configContextMap.delete(tailwindConfigHash2);
                }
              }
              for (let disposable of oldContext.disposables.splice(0)) {
                disposable(oldContext);
              }
            }
          }
        }
        _sharedState.env.DEBUG && console.log("Setting up new context...");
        let context2 = createContext2(tailwindConfig, [], root);
        Object.assign(context2, {
          userConfigPath
        });
        let [, mtimesToCommit] = trackModified([
          ...contextDependencies
        ], getFileModifiedMap(context2));
        configContextMap.set(tailwindConfigHash, context2);
        contextMap.set(sourcePath, context2);
        if (!contextSourcesMap.has(context2)) {
          contextSourcesMap.set(context2, /* @__PURE__ */ new Set());
        }
        contextSourcesMap.get(context2).add(sourcePath);
        return [
          context2,
          true,
          mtimesToCommit
        ];
      }
    }
  });

  // tailwindcss/lib/util/applyImportantSelector.js
  var require_applyImportantSelector = __commonJS({
    "tailwindcss/lib/util/applyImportantSelector.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "applyImportantSelector", {
        enumerable: true,
        get: function() {
          return applyImportantSelector;
        }
      });
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _pseudoElements = require_pseudoElements();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function applyImportantSelector(selector, important) {
        let sel = (0, _postcssselectorparser.default)().astSync(selector);
        sel.each((sel2) => {
          let shouldWrap = sel2.nodes.some((node) => node.type === "combinator");
          if (shouldWrap) {
            sel2.nodes = [
              _postcssselectorparser.default.pseudo({
                value: ":is",
                nodes: [
                  sel2.clone()
                ]
              })
            ];
          }
          (0, _pseudoElements.movePseudos)(sel2);
        });
        return `${important} ${sel.toString()}`;
      }
    }
  });

  // tailwindcss/lib/lib/generateRules.js
  var require_generateRules = __commonJS({
    "tailwindcss/lib/lib/generateRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        getClassNameFromSelector: function() {
          return getClassNameFromSelector;
        },
        resolveMatches: function() {
          return resolveMatches;
        },
        generateRules: function() {
          return generateRules;
        }
      });
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _parseObjectStyles = /* @__PURE__ */ _interop_require_default(require_parseObjectStyles());
      var _isPlainObject = /* @__PURE__ */ _interop_require_default(require_isPlainObject());
      var _prefixSelector = /* @__PURE__ */ _interop_require_default(require_prefixSelector());
      var _pluginUtils = require_pluginUtils();
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      var _sharedState = /* @__PURE__ */ _interop_require_wildcard(require_sharedState());
      var _formatVariantSelector = require_formatVariantSelector();
      var _nameClass = require_nameClass();
      var _dataTypes = require_dataTypes();
      var _setupContextUtils = require_setupContextUtils();
      var _isSyntacticallyValidPropertyValue = /* @__PURE__ */ _interop_require_default(require_isSyntacticallyValidPropertyValue());
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      var _featureFlags = require_featureFlags();
      var _applyImportantSelector = require_applyImportantSelector();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      var classNameParser = (0, _postcssselectorparser.default)((selectors) => {
        return selectors.first.filter(({ type }) => type === "class").pop().value;
      });
      function getClassNameFromSelector(selector) {
        return classNameParser.transformSync(selector);
      }
      function* candidatePermutations(candidate) {
        let lastIndex = Infinity;
        while (lastIndex >= 0) {
          let dashIdx;
          let wasSlash = false;
          if (lastIndex === Infinity && candidate.endsWith("]")) {
            let bracketIdx = candidate.indexOf("[");
            if (candidate[bracketIdx - 1] === "-") {
              dashIdx = bracketIdx - 1;
            } else if (candidate[bracketIdx - 1] === "/") {
              dashIdx = bracketIdx - 1;
              wasSlash = true;
            } else {
              dashIdx = -1;
            }
          } else if (lastIndex === Infinity && candidate.includes("/")) {
            dashIdx = candidate.lastIndexOf("/");
            wasSlash = true;
          } else {
            dashIdx = candidate.lastIndexOf("-", lastIndex);
          }
          if (dashIdx < 0) {
            break;
          }
          let prefix = candidate.slice(0, dashIdx);
          let modifier = candidate.slice(wasSlash ? dashIdx : dashIdx + 1);
          lastIndex = dashIdx - 1;
          if (prefix === "" || modifier === "/") {
            continue;
          }
          yield [
            prefix,
            modifier
          ];
        }
      }
      function applyPrefix(matches, context2) {
        if (matches.length === 0 || context2.tailwindConfig.prefix === "") {
          return matches;
        }
        for (let match of matches) {
          let [meta] = match;
          if (meta.options.respectPrefix) {
            let container = _postcss.default.root({
              nodes: [
                match[1].clone()
              ]
            });
            let classCandidate = match[1].raws.tailwind.classCandidate;
            container.walkRules((r) => {
              let shouldPrependNegative = classCandidate.startsWith("-");
              r.selector = (0, _prefixSelector.default)(context2.tailwindConfig.prefix, r.selector, shouldPrependNegative);
            });
            match[1] = container.nodes[0];
          }
        }
        return matches;
      }
      function applyImportant(matches, classCandidate) {
        if (matches.length === 0) {
          return matches;
        }
        let result = [];
        function isInKeyframes(rule) {
          return rule.parent && rule.parent.type === "atrule" && rule.parent.name === "keyframes";
        }
        for (let [meta, rule] of matches) {
          let container = _postcss.default.root({
            nodes: [
              rule.clone()
            ]
          });
          container.walkRules((r) => {
            if (isInKeyframes(r)) {
              return;
            }
            let ast = (0, _postcssselectorparser.default)().astSync(r.selector);
            ast.each((sel) => (0, _formatVariantSelector.eliminateIrrelevantSelectors)(sel, classCandidate));
            (0, _pluginUtils.updateAllClasses)(ast, (className) => className === classCandidate ? `!${className}` : className);
            r.selector = ast.toString();
            r.walkDecls((d) => d.important = true);
          });
          result.push([
            {
              ...meta,
              important: true
            },
            container.nodes[0]
          ]);
        }
        return result;
      }
      function applyVariant(variant, matches, context2) {
        if (matches.length === 0) {
          return matches;
        }
        let args = {
          modifier: null,
          value: _sharedState.NONE
        };
        {
          let [baseVariant, ...modifiers] = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(variant, "/");
          if (modifiers.length > 1) {
            baseVariant = baseVariant + "/" + modifiers.slice(0, -1).join("/");
            modifiers = modifiers.slice(-1);
          }
          if (modifiers.length && !context2.variantMap.has(variant)) {
            variant = baseVariant;
            args.modifier = modifiers[0];
            if (!(0, _featureFlags.flagEnabled)(context2.tailwindConfig, "generalizedModifiers")) {
              return [];
            }
          }
        }
        if (variant.endsWith("]") && !variant.startsWith("[")) {
          let match = /(.)(-?)\[(.*)\]/g.exec(variant);
          if (match) {
            let [, char, separator, value] = match;
            if (char === "@" && separator === "-") return [];
            if (char !== "@" && separator === "") return [];
            variant = variant.replace(`${separator}[${value}]`, "");
            args.value = value;
          }
        }
        if (isArbitraryValue(variant) && !context2.variantMap.has(variant)) {
          let sort = context2.offsets.recordVariant(variant);
          let selector = (0, _dataTypes.normalize)(variant.slice(1, -1));
          let selectors = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(selector, ",");
          if (selectors.length > 1) {
            return [];
          }
          if (!selectors.every(_setupContextUtils.isValidVariantFormatString)) {
            return [];
          }
          let records = selectors.map((sel, idx) => [
            context2.offsets.applyParallelOffset(sort, idx),
            (0, _setupContextUtils.parseVariant)(sel.trim())
          ]);
          context2.variantMap.set(variant, records);
        }
        if (context2.variantMap.has(variant)) {
          var _context_variantOptions_get;
          let isArbitraryVariant = isArbitraryValue(variant);
          var _context_variantOptions_get_INTERNAL_FEATURES;
          let internalFeatures = (_context_variantOptions_get_INTERNAL_FEATURES = (_context_variantOptions_get = context2.variantOptions.get(variant)) === null || _context_variantOptions_get === void 0 ? void 0 : _context_variantOptions_get[_setupContextUtils.INTERNAL_FEATURES]) !== null && _context_variantOptions_get_INTERNAL_FEATURES !== void 0 ? _context_variantOptions_get_INTERNAL_FEATURES : {};
          let variantFunctionTuples = context2.variantMap.get(variant).slice();
          let result = [];
          let respectPrefix = (() => {
            if (isArbitraryVariant) return false;
            if (internalFeatures.respectPrefix === false) return false;
            return true;
          })();
          for (let [meta, rule] of matches) {
            if (meta.layer === "user") {
              continue;
            }
            let container = _postcss.default.root({
              nodes: [
                rule.clone()
              ]
            });
            for (let [variantSort, variantFunction, containerFromArray] of variantFunctionTuples) {
              let prepareBackup = function() {
                if (clone.raws.neededBackup) {
                  return;
                }
                clone.raws.neededBackup = true;
                clone.walkRules((rule2) => rule2.raws.originalSelector = rule2.selector);
              }, modifySelectors = function(modifierFunction) {
                prepareBackup();
                clone.each((rule2) => {
                  if (rule2.type !== "rule") {
                    return;
                  }
                  rule2.selectors = rule2.selectors.map((selector) => {
                    return modifierFunction({
                      get className() {
                        return getClassNameFromSelector(selector);
                      },
                      selector
                    });
                  });
                });
                return clone;
              };
              let clone = (containerFromArray !== null && containerFromArray !== void 0 ? containerFromArray : container).clone();
              let collectedFormats = [];
              let ruleWithVariant = variantFunction({
                // Public API
                get container() {
                  prepareBackup();
                  return clone;
                },
                separator: context2.tailwindConfig.separator,
                modifySelectors,
                // Private API for now
                wrap(wrapper) {
                  let nodes = clone.nodes;
                  clone.removeAll();
                  wrapper.append(nodes);
                  clone.append(wrapper);
                },
                format(selectorFormat) {
                  collectedFormats.push({
                    format: selectorFormat,
                    respectPrefix
                  });
                },
                args
              });
              if (Array.isArray(ruleWithVariant)) {
                for (let [idx, variantFunction2] of ruleWithVariant.entries()) {
                  variantFunctionTuples.push([
                    context2.offsets.applyParallelOffset(variantSort, idx),
                    variantFunction2,
                    // If the clone has been modified we have to pass that back
                    // though so each rule can use the modified container
                    clone.clone()
                  ]);
                }
                continue;
              }
              if (typeof ruleWithVariant === "string") {
                collectedFormats.push({
                  format: ruleWithVariant,
                  respectPrefix
                });
              }
              if (ruleWithVariant === null) {
                continue;
              }
              if (clone.raws.neededBackup) {
                delete clone.raws.neededBackup;
                clone.walkRules((rule2) => {
                  let before = rule2.raws.originalSelector;
                  if (!before) return;
                  delete rule2.raws.originalSelector;
                  if (before === rule2.selector) return;
                  let modified = rule2.selector;
                  let rebuiltBase = (0, _postcssselectorparser.default)((selectors) => {
                    selectors.walkClasses((classNode) => {
                      classNode.value = `${variant}${context2.tailwindConfig.separator}${classNode.value}`;
                    });
                  }).processSync(before);
                  collectedFormats.push({
                    format: modified.replace(rebuiltBase, "&"),
                    respectPrefix
                  });
                  rule2.selector = before;
                });
              }
              clone.nodes[0].raws.tailwind = {
                ...clone.nodes[0].raws.tailwind,
                parentLayer: meta.layer
              };
              var _meta_collectedFormats;
              let withOffset = [
                {
                  ...meta,
                  sort: context2.offsets.applyVariantOffset(meta.sort, variantSort, Object.assign(args, context2.variantOptions.get(variant))),
                  collectedFormats: ((_meta_collectedFormats = meta.collectedFormats) !== null && _meta_collectedFormats !== void 0 ? _meta_collectedFormats : []).concat(collectedFormats)
                },
                clone.nodes[0]
              ];
              result.push(withOffset);
            }
          }
          return result;
        }
        return [];
      }
      function parseRules(rule, cache, options = {}) {
        if (!(0, _isPlainObject.default)(rule) && !Array.isArray(rule)) {
          return [
            [
              rule
            ],
            options
          ];
        }
        if (Array.isArray(rule)) {
          return parseRules(rule[0], cache, rule[1]);
        }
        if (!cache.has(rule)) {
          cache.set(rule, (0, _parseObjectStyles.default)(rule));
        }
        return [
          cache.get(rule),
          options
        ];
      }
      var IS_VALID_PROPERTY_NAME = /^[a-z_-]/;
      function isValidPropName(name) {
        return IS_VALID_PROPERTY_NAME.test(name);
      }
      function looksLikeUri(declaration) {
        if (!declaration.includes("://")) {
          return false;
        }
        try {
          const url = new URL(declaration);
          return url.scheme !== "" && url.host !== "";
        } catch (err) {
          return false;
        }
      }
      function isParsableNode(node) {
        let isParsable = true;
        node.walkDecls((decl) => {
          if (!isParsableCssValue(decl.prop, decl.value)) {
            isParsable = false;
            return false;
          }
        });
        return isParsable;
      }
      function isParsableCssValue(property, value) {
        if (looksLikeUri(`${property}:${value}`)) {
          return false;
        }
        try {
          _postcss.default.parse(`a{${property}:${value}}`).toResult();
          return true;
        } catch (err) {
          return false;
        }
      }
      function extractArbitraryProperty(classCandidate, context2) {
        var _classCandidate_match;
        let [, property, value] = (_classCandidate_match = classCandidate.match(/^\[([a-zA-Z0-9-_]+):(\S+)\]$/)) !== null && _classCandidate_match !== void 0 ? _classCandidate_match : [];
        if (value === void 0) {
          return null;
        }
        if (!isValidPropName(property)) {
          return null;
        }
        if (!(0, _isSyntacticallyValidPropertyValue.default)(value)) {
          return null;
        }
        let normalized = (0, _dataTypes.normalize)(value, {
          property
        });
        if (!isParsableCssValue(property, normalized)) {
          return null;
        }
        let sort = context2.offsets.arbitraryProperty(classCandidate);
        return [
          [
            {
              sort,
              layer: "utilities",
              options: {
                respectImportant: true
              }
            },
            () => ({
              [(0, _nameClass.asClass)(classCandidate)]: {
                [property]: normalized
              }
            })
          ]
        ];
      }
      function* resolveMatchedPlugins(classCandidate, context2) {
        if (context2.candidateRuleMap.has(classCandidate)) {
          yield [
            context2.candidateRuleMap.get(classCandidate),
            "DEFAULT"
          ];
        }
        yield* function* (arbitraryPropertyRule) {
          if (arbitraryPropertyRule !== null) {
            yield [
              arbitraryPropertyRule,
              "DEFAULT"
            ];
          }
        }(extractArbitraryProperty(classCandidate, context2));
        let candidatePrefix = classCandidate;
        let negative = false;
        const twConfigPrefix = context2.tailwindConfig.prefix;
        const twConfigPrefixLen = twConfigPrefix.length;
        const hasMatchingPrefix = candidatePrefix.startsWith(twConfigPrefix) || candidatePrefix.startsWith(`-${twConfigPrefix}`);
        if (candidatePrefix[twConfigPrefixLen] === "-" && hasMatchingPrefix) {
          negative = true;
          candidatePrefix = twConfigPrefix + candidatePrefix.slice(twConfigPrefixLen + 1);
        }
        if (negative && context2.candidateRuleMap.has(candidatePrefix)) {
          yield [
            context2.candidateRuleMap.get(candidatePrefix),
            "-DEFAULT"
          ];
        }
        for (let [prefix, modifier] of candidatePermutations(candidatePrefix)) {
          if (context2.candidateRuleMap.has(prefix)) {
            yield [
              context2.candidateRuleMap.get(prefix),
              negative ? `-${modifier}` : modifier
            ];
          }
        }
      }
      function splitWithSeparator(input, separator) {
        if (input === _sharedState.NOT_ON_DEMAND) {
          return [
            _sharedState.NOT_ON_DEMAND
          ];
        }
        return (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(input, separator);
      }
      function* recordCandidates(matches, classCandidate) {
        for (const match of matches) {
          var _match__options;
          var _match__options_preserveSource;
          match[1].raws.tailwind = {
            ...match[1].raws.tailwind,
            classCandidate,
            preserveSource: (_match__options_preserveSource = (_match__options = match[0].options) === null || _match__options === void 0 ? void 0 : _match__options.preserveSource) !== null && _match__options_preserveSource !== void 0 ? _match__options_preserveSource : false
          };
          yield match;
        }
      }
      function* resolveMatches(candidate, context2) {
        let separator = context2.tailwindConfig.separator;
        let [classCandidate, ...variants] = splitWithSeparator(candidate, separator).reverse();
        let important = false;
        if (classCandidate.startsWith("!")) {
          important = true;
          classCandidate = classCandidate.slice(1);
        }
        for (let matchedPlugins of resolveMatchedPlugins(classCandidate, context2)) {
          let matches = [];
          let typesByMatches = /* @__PURE__ */ new Map();
          let [plugins, modifier] = matchedPlugins;
          let isOnlyPlugin = plugins.length === 1;
          for (let [sort, plugin2] of plugins) {
            let matchesPerPlugin = [];
            if (typeof plugin2 === "function") {
              for (let ruleSet of [].concat(plugin2(modifier, {
                isOnlyPlugin
              }))) {
                let [rules, options] = parseRules(ruleSet, context2.postCssNodeCache);
                for (let rule of rules) {
                  matchesPerPlugin.push([
                    {
                      ...sort,
                      options: {
                        ...sort.options,
                        ...options
                      }
                    },
                    rule
                  ]);
                }
              }
            } else if (modifier === "DEFAULT" || modifier === "-DEFAULT") {
              let ruleSet = plugin2;
              let [rules, options] = parseRules(ruleSet, context2.postCssNodeCache);
              for (let rule of rules) {
                matchesPerPlugin.push([
                  {
                    ...sort,
                    options: {
                      ...sort.options,
                      ...options
                    }
                  },
                  rule
                ]);
              }
            }
            if (matchesPerPlugin.length > 0) {
              var _sort_options;
              var _sort_options_types, _sort_options1;
              let matchingTypes = Array.from((0, _pluginUtils.getMatchingTypes)((_sort_options_types = (_sort_options = sort.options) === null || _sort_options === void 0 ? void 0 : _sort_options.types) !== null && _sort_options_types !== void 0 ? _sort_options_types : [], modifier, (_sort_options1 = sort.options) !== null && _sort_options1 !== void 0 ? _sort_options1 : {}, context2.tailwindConfig)).map(([_, type]) => type);
              if (matchingTypes.length > 0) {
                typesByMatches.set(matchesPerPlugin, matchingTypes);
              }
              matches.push(matchesPerPlugin);
            }
          }
          if (isArbitraryValue(modifier)) {
            if (matches.length > 1) {
              let findFallback = function(matches2) {
                if (matches2.length === 1) {
                  return matches2[0];
                }
                return matches2.find((rules) => {
                  let matchingTypes = typesByMatches.get(rules);
                  return rules.some(([{ options }, rule]) => {
                    if (!isParsableNode(rule)) {
                      return false;
                    }
                    return options.types.some(({ type, preferOnConflict }) => matchingTypes.includes(type) && preferOnConflict);
                  });
                });
              };
              let [withAny, withoutAny] = matches.reduce((group, plugin2) => {
                let hasAnyType = plugin2.some(([{ options }]) => options.types.some(({ type }) => type === "any"));
                if (hasAnyType) {
                  group[0].push(plugin2);
                } else {
                  group[1].push(plugin2);
                }
                return group;
              }, [
                [],
                []
              ]);
              var _findFallback;
              let fallback = (_findFallback = findFallback(withoutAny)) !== null && _findFallback !== void 0 ? _findFallback : findFallback(withAny);
              if (fallback) {
                matches = [
                  fallback
                ];
              } else {
                var _typesByMatches_get;
                let typesPerPlugin = matches.map((match) => /* @__PURE__ */ new Set([
                  ...(_typesByMatches_get = typesByMatches.get(match)) !== null && _typesByMatches_get !== void 0 ? _typesByMatches_get : []
                ]));
                for (let pluginTypes of typesPerPlugin) {
                  for (let type of pluginTypes) {
                    let removeFromOwnGroup = false;
                    for (let otherGroup of typesPerPlugin) {
                      if (pluginTypes === otherGroup) continue;
                      if (otherGroup.has(type)) {
                        otherGroup.delete(type);
                        removeFromOwnGroup = true;
                      }
                    }
                    if (removeFromOwnGroup) pluginTypes.delete(type);
                  }
                }
                let messages = [];
                for (let [idx, group] of typesPerPlugin.entries()) {
                  for (let type of group) {
                    let rules = matches[idx].map(([, rule]) => rule).flat().map((rule) => rule.toString().split("\n").slice(1, -1).map((line) => line.trim()).map((x) => `      ${x}`).join("\n")).join("\n\n");
                    messages.push(`  Use \`${candidate.replace("[", `[${type}:`)}\` for \`${rules.trim()}\``);
                    break;
                  }
                }
                _log.default.warn([
                  `The class \`${candidate}\` is ambiguous and matches multiple utilities.`,
                  ...messages,
                  `If this is content and not a class, replace it with \`${candidate.replace("[", "&lsqb;").replace("]", "&rsqb;")}\` to silence this warning.`
                ]);
                continue;
              }
            }
            matches = matches.map((list) => list.filter((match) => isParsableNode(match[1])));
          }
          matches = matches.flat();
          matches = Array.from(recordCandidates(matches, classCandidate));
          matches = applyPrefix(matches, context2);
          if (important) {
            matches = applyImportant(matches, classCandidate);
          }
          for (let variant of variants) {
            matches = applyVariant(variant, matches, context2);
          }
          for (let match of matches) {
            match[1].raws.tailwind = {
              ...match[1].raws.tailwind,
              candidate
            };
            match = applyFinalFormat(match, {
              context: context2,
              candidate
            });
            if (match === null) {
              continue;
            }
            yield match;
          }
        }
      }
      function applyFinalFormat(match, { context: context2, candidate }) {
        if (!match[0].collectedFormats) {
          return match;
        }
        let isValid = true;
        let finalFormat;
        try {
          finalFormat = (0, _formatVariantSelector.formatVariantSelector)(match[0].collectedFormats, {
            context: context2,
            candidate
          });
        } catch {
          return null;
        }
        let container = _postcss.default.root({
          nodes: [
            match[1].clone()
          ]
        });
        container.walkRules((rule) => {
          if (inKeyframes(rule)) {
            return;
          }
          try {
            let selector = (0, _formatVariantSelector.finalizeSelector)(rule.selector, finalFormat, {
              candidate,
              context: context2
            });
            if (selector === null) {
              rule.remove();
              return;
            }
            rule.selector = selector;
          } catch {
            isValid = false;
            return false;
          }
        });
        if (!isValid) {
          return null;
        }
        if (container.nodes.length === 0) {
          return null;
        }
        match[1] = container.nodes[0];
        return match;
      }
      function inKeyframes(rule) {
        return rule.parent && rule.parent.type === "atrule" && rule.parent.name === "keyframes";
      }
      function getImportantStrategy(important) {
        if (important === true) {
          return (rule) => {
            if (inKeyframes(rule)) {
              return;
            }
            rule.walkDecls((d) => {
              if (d.parent.type === "rule" && !inKeyframes(d.parent)) {
                d.important = true;
              }
            });
          };
        }
        if (typeof important === "string") {
          return (rule) => {
            if (inKeyframes(rule)) {
              return;
            }
            rule.selectors = rule.selectors.map((selector) => {
              return (0, _applyImportantSelector.applyImportantSelector)(selector, important);
            });
          };
        }
      }
      function generateRules(candidates, context2, isSorting = false) {
        let allRules = [];
        let strategy = getImportantStrategy(context2.tailwindConfig.important);
        for (let candidate of candidates) {
          if (context2.notClassCache.has(candidate)) {
            continue;
          }
          if (context2.candidateRuleCache.has(candidate)) {
            allRules = allRules.concat(Array.from(context2.candidateRuleCache.get(candidate)));
            continue;
          }
          let matches = Array.from(resolveMatches(candidate, context2));
          if (matches.length === 0) {
            context2.notClassCache.add(candidate);
            continue;
          }
          context2.classCache.set(candidate, matches);
          var _context_candidateRuleCache_get;
          let rules = (_context_candidateRuleCache_get = context2.candidateRuleCache.get(candidate)) !== null && _context_candidateRuleCache_get !== void 0 ? _context_candidateRuleCache_get : /* @__PURE__ */ new Set();
          context2.candidateRuleCache.set(candidate, rules);
          for (const match of matches) {
            let [{ sort, options }, rule] = match;
            if (options.respectImportant && strategy) {
              let container = _postcss.default.root({
                nodes: [
                  rule.clone()
                ]
              });
              container.walkRules(strategy);
              rule = container.nodes[0];
            }
            let newEntry = [
              sort,
              isSorting ? rule.clone() : rule
            ];
            rules.add(newEntry);
            context2.ruleCache.add(newEntry);
            allRules.push(newEntry);
          }
        }
        return allRules;
      }
      function isArbitraryValue(input) {
        return input.startsWith("[") && input.endsWith("]");
      }
    }
  });

  // tailwindcss/lib/util/cloneNodes.js
  var require_cloneNodes = __commonJS({
    "tailwindcss/lib/util/cloneNodes.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return cloneNodes;
        }
      });
      function cloneNodes(nodes, source = void 0, raws = void 0) {
        return nodes.map((node) => {
          let cloned = node.clone();
          if (raws !== void 0) {
            cloned.raws.tailwind = {
              ...cloned.raws.tailwind,
              ...raws
            };
          }
          if (source !== void 0) {
            traverse(cloned, (node2) => {
              var _node_raws_tailwind;
              let shouldPreserveSource = ((_node_raws_tailwind = node2.raws.tailwind) === null || _node_raws_tailwind === void 0 ? void 0 : _node_raws_tailwind.preserveSource) === true && node2.source;
              if (shouldPreserveSource) {
                return false;
              }
              node2.source = source;
            });
          }
          return cloned;
        });
      }
      function traverse(node, onNode) {
        if (onNode(node) !== false) {
          var _node_each;
          (_node_each = node.each) === null || _node_each === void 0 ? void 0 : _node_each.call(node, (child) => traverse(child, onNode));
        }
      }
    }
  });

  // tailwindcss/lib/lib/regex.js
  var require_regex = __commonJS({
    "tailwindcss/lib/lib/regex.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        pattern: function() {
          return pattern;
        },
        withoutCapturing: function() {
          return withoutCapturing;
        },
        any: function() {
          return any;
        },
        optional: function() {
          return optional;
        },
        zeroOrMore: function() {
          return zeroOrMore;
        },
        nestedBrackets: function() {
          return nestedBrackets;
        },
        escape: function() {
          return escape;
        }
      });
      var REGEX_SPECIAL = /[\\^$.*+?()[\]{}|]/g;
      var REGEX_HAS_SPECIAL = RegExp(REGEX_SPECIAL.source);
      function toSource(source) {
        source = Array.isArray(source) ? source : [
          source
        ];
        source = source.map((item) => item instanceof RegExp ? item.source : item);
        return source.join("");
      }
      function pattern(source) {
        return new RegExp(toSource(source), "g");
      }
      function withoutCapturing(source) {
        return new RegExp(`(?:${toSource(source)})`, "g");
      }
      function any(sources) {
        return `(?:${sources.map(toSource).join("|")})`;
      }
      function optional(source) {
        return `(?:${toSource(source)})?`;
      }
      function zeroOrMore(source) {
        return `(?:${toSource(source)})*`;
      }
      function nestedBrackets(open, close, depth = 1) {
        return withoutCapturing([
          escape(open),
          /[^\s]*/,
          depth === 1 ? `[^${escape(open)}${escape(close)}s]*` : any([
            `[^${escape(open)}${escape(close)}s]*`,
            nestedBrackets(open, close, depth - 1)
          ]),
          /[^\s]*/,
          escape(close)
        ]);
      }
      function escape(string) {
        return string && REGEX_HAS_SPECIAL.test(string) ? string.replace(REGEX_SPECIAL, "\\$&") : string || "";
      }
    }
  });

  // tailwindcss/lib/lib/defaultExtractor.js
  var require_defaultExtractor = __commonJS({
    "tailwindcss/lib/lib/defaultExtractor.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "defaultExtractor", {
        enumerable: true,
        get: function() {
          return defaultExtractor;
        }
      });
      var _regex = /* @__PURE__ */ _interop_require_wildcard(require_regex());
      var _splitAtTopLevelOnly = require_splitAtTopLevelOnly();
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      function defaultExtractor(context2) {
        let patterns = Array.from(buildRegExps(context2));
        return (content) => {
          let results = [];
          for (let pattern of patterns) {
            var _content_match;
            for (let result of (_content_match = content.match(pattern)) !== null && _content_match !== void 0 ? _content_match : []) {
              results.push(clipAtBalancedParens(result));
            }
          }
          for (let result of results.slice()) {
            let segments = (0, _splitAtTopLevelOnly.splitAtTopLevelOnly)(result, ".");
            for (let idx = 0; idx < segments.length; idx++) {
              let segment = segments[idx];
              if (idx >= segments.length - 1) {
                results.push(segment);
                continue;
              }
              let next = Number(segments[idx + 1]);
              if (isNaN(next)) {
                results.push(segment);
              } else {
                idx++;
              }
            }
          }
          return results;
        };
      }
      function* buildRegExps(context2) {
        let separator = context2.tailwindConfig.separator;
        let prefix = context2.tailwindConfig.prefix !== "" ? _regex.optional(_regex.pattern([
          /-?/,
          _regex.escape(context2.tailwindConfig.prefix)
        ])) : "";
        let utility = _regex.any([
          // Arbitrary properties (without square brackets)
          /\[[^\s:'"`]+:[^\s\[\]]+\]/,
          // Arbitrary properties with balanced square brackets
          // This is a targeted fix to continue to allow theme()
          // with square brackets to work in arbitrary properties
          // while fixing a problem with the regex matching too much
          /\[[^\s:'"`\]]+:[^\s]+?\[[^\s]+\][^\s]+?\]/,
          // Utilities
          _regex.pattern([
            // Utility Name / Group Name
            _regex.any([
              /-?(?:\w+)/,
              // This is here to make sure @container supports everything that other utilities do
              /@(?:\w+)/
            ]),
            // Normal/Arbitrary values
            _regex.optional(_regex.any([
              _regex.pattern([
                // Arbitrary values
                _regex.any([
                  /-(?:\w+-)*\['[^\s]+'\]/,
                  /-(?:\w+-)*\["[^\s]+"\]/,
                  /-(?:\w+-)*\[`[^\s]+`\]/,
                  /-(?:\w+-)*\[(?:[^\s\[\]]+\[[^\s\[\]]+\])*[^\s:\[\]]+\]/
                ]),
                // Not immediately followed by an `{[(`
                /(?![{([]])/,
                // optionally followed by an opacity modifier
                /(?:\/[^\s'"`\\><$]*)?/
              ]),
              _regex.pattern([
                // Arbitrary values
                _regex.any([
                  /-(?:\w+-)*\['[^\s]+'\]/,
                  /-(?:\w+-)*\["[^\s]+"\]/,
                  /-(?:\w+-)*\[`[^\s]+`\]/,
                  /-(?:\w+-)*\[(?:[^\s\[\]]+\[[^\s\[\]]+\])*[^\s\[\]]+\]/
                ]),
                // Not immediately followed by an `{[(`
                /(?![{([]])/,
                // optionally followed by an opacity modifier
                /(?:\/[^\s'"`\\$]*)?/
              ]),
              // Normal values w/o quotes — may include an opacity modifier
              /[-\/][^\s'"`\\$={><]*/
            ]))
          ])
        ]);
        let variantPatterns = [
          // Without quotes
          _regex.any([
            // This is here to provide special support for the `@` variant
            _regex.pattern([
              /@\[[^\s"'`]+\](\/[^\s"'`]+)?/,
              separator
            ]),
            // With variant modifier (e.g.: group-[..]/modifier)
            _regex.pattern([
              /([^\s"'`\[\\]+-)?\[[^\s"'`]+\]\/[\w_-]+/,
              separator
            ]),
            _regex.pattern([
              /([^\s"'`\[\\]+-)?\[[^\s"'`]+\]/,
              separator
            ]),
            _regex.pattern([
              /[^\s"'`\[\\]+/,
              separator
            ])
          ]),
          // With quotes allowed
          _regex.any([
            // With variant modifier (e.g.: group-[..]/modifier)
            _regex.pattern([
              /([^\s"'`\[\\]+-)?\[[^\s`]+\]\/[\w_-]+/,
              separator
            ]),
            _regex.pattern([
              /([^\s"'`\[\\]+-)?\[[^\s`]+\]/,
              separator
            ]),
            _regex.pattern([
              /[^\s`\[\\]+/,
              separator
            ])
          ])
        ];
        for (const variantPattern of variantPatterns) {
          yield _regex.pattern([
            // Variants
            "((?=((",
            variantPattern,
            ")+))\\2)?",
            // Important (optional)
            /!?/,
            prefix,
            utility
          ]);
        }
        yield /[^<>"'`\s.(){}[\]#=%$][^<>"'`\s(){}[\]#=%$]*[^<>"'`\s.(){}[\]#=%:$]/g;
      }
      var SPECIALS = /([\[\]'"`])([^\[\]'"`])?/g;
      var ALLOWED_CLASS_CHARACTERS = /[^"'`\s<>\]]+/;
      function clipAtBalancedParens(input) {
        if (!input.includes("-[")) {
          return input;
        }
        let depth = 0;
        let openStringTypes = [];
        let matches = input.matchAll(SPECIALS);
        matches = Array.from(matches).flatMap((match) => {
          const [, ...groups] = match;
          return groups.map((group, idx) => Object.assign([], match, {
            index: match.index + idx,
            0: group
          }));
        });
        for (let match of matches) {
          let char = match[0];
          let inStringType = openStringTypes[openStringTypes.length - 1];
          if (char === inStringType) {
            openStringTypes.pop();
          } else if (char === "'" || char === '"' || char === "`") {
            openStringTypes.push(char);
          }
          if (inStringType) {
            continue;
          } else if (char === "[") {
            depth++;
            continue;
          } else if (char === "]") {
            depth--;
            continue;
          }
          if (depth < 0) {
            return input.substring(0, match.index - 1);
          }
          if (depth === 0 && !ALLOWED_CLASS_CHARACTERS.test(char)) {
            return input.substring(0, match.index);
          }
        }
        return input;
      }
    }
  });

  // tailwindcss/lib/lib/expandTailwindAtRules.js
  var require_expandTailwindAtRules = __commonJS({
    "tailwindcss/lib/lib/expandTailwindAtRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return expandTailwindAtRules;
        }
      });
      var _fs = /* @__PURE__ */ _interop_require_default(require_tailwind_expand_fs());
      var _quicklru = /* @__PURE__ */ _interop_require_default(require_quick_lru());
      var _sharedState = /* @__PURE__ */ _interop_require_wildcard(require_sharedState());
      var _generateRules = require_generateRules();
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      var _cloneNodes = /* @__PURE__ */ _interop_require_default(require_cloneNodes());
      var _defaultExtractor = require_defaultExtractor();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      var env = _sharedState.env;
      var builtInExtractors = {
        DEFAULT: _defaultExtractor.defaultExtractor
      };
      var builtInTransformers = {
        DEFAULT: (content) => content,
        svelte: (content) => content.replace(/(?:^|\s)class:/g, " ")
      };
      function getExtractor(context2, fileExtension) {
        let extractors = context2.tailwindConfig.content.extract;
        return extractors[fileExtension] || extractors.DEFAULT || builtInExtractors[fileExtension] || builtInExtractors.DEFAULT(context2);
      }
      function getTransformer(tailwindConfig, fileExtension) {
        let transformers = tailwindConfig.content.transform;
        return transformers[fileExtension] || transformers.DEFAULT || builtInTransformers[fileExtension] || builtInTransformers.DEFAULT;
      }
      var extractorCache = /* @__PURE__ */ new WeakMap();
      function getClassCandidates(content, extractor, candidates, seen) {
        if (!extractorCache.has(extractor)) {
          extractorCache.set(extractor, new _quicklru.default({
            maxSize: 25e3
          }));
        }
        for (let line of content.split("\n")) {
          line = line.trim();
          if (seen.has(line)) {
            continue;
          }
          seen.add(line);
          if (extractorCache.get(extractor).has(line)) {
            for (let match of extractorCache.get(extractor).get(line)) {
              candidates.add(match);
            }
          } else {
            let extractorMatches = extractor(line).filter((s) => s !== "!*");
            let lineMatchesSet = new Set(extractorMatches);
            for (let match of lineMatchesSet) {
              candidates.add(match);
            }
            extractorCache.get(extractor).set(line, lineMatchesSet);
          }
        }
      }
      function buildStylesheet(rules, context2) {
        let sortedRules = context2.offsets.sort(rules);
        let returnValue = {
          base: /* @__PURE__ */ new Set(),
          defaults: /* @__PURE__ */ new Set(),
          components: /* @__PURE__ */ new Set(),
          utilities: /* @__PURE__ */ new Set(),
          variants: /* @__PURE__ */ new Set()
        };
        for (let [sort, rule] of sortedRules) {
          returnValue[sort.layer].add(rule);
        }
        return returnValue;
      }
      function expandTailwindAtRules(context2) {
        return async (root) => {
          let layerNodes = {
            base: null,
            components: null,
            utilities: null,
            variants: null
          };
          root.walkAtRules((rule) => {
            if (rule.name === "tailwind") {
              if (Object.keys(layerNodes).includes(rule.params)) {
                layerNodes[rule.params] = rule;
              }
            }
          });
          if (Object.values(layerNodes).every((n) => n === null)) {
            return root;
          }
          var _context_candidates;
          let candidates = /* @__PURE__ */ new Set([
            ...(_context_candidates = context2.candidates) !== null && _context_candidates !== void 0 ? _context_candidates : [],
            _sharedState.NOT_ON_DEMAND
          ]);
          let seen = /* @__PURE__ */ new Set();
          env.DEBUG && console.time("Reading changed files");
          let regexParserContent = [];
          for (let item of context2.changedContent) {
            let transformer = getTransformer(context2.tailwindConfig, item.extension);
            let extractor = getExtractor(context2, item.extension);
            regexParserContent.push([
              item,
              {
                transformer,
                extractor
              }
            ]);
          }
          const BATCH_SIZE = 500;
          for (let i = 0; i < regexParserContent.length; i += BATCH_SIZE) {
            let batch = regexParserContent.slice(i, i + BATCH_SIZE);
            await Promise.all(batch.map(async ([{ file, content }, { transformer, extractor }]) => {
              content = file ? await _fs.default.promises.readFile(file, "utf8") : content;
              getClassCandidates(transformer(content), extractor, candidates, seen);
            }));
          }
          env.DEBUG && console.timeEnd("Reading changed files");
          let classCacheCount = context2.classCache.size;
          env.DEBUG && console.time("Generate rules");
          env.DEBUG && console.time("Sorting candidates");
          let sortedCandidates = new Set([
            ...candidates
          ].sort((a, z) => {
            if (a === z) return 0;
            if (a < z) return -1;
            return 1;
          }));
          env.DEBUG && console.timeEnd("Sorting candidates");
          (0, _generateRules.generateRules)(sortedCandidates, context2);
          env.DEBUG && console.timeEnd("Generate rules");
          env.DEBUG && console.time("Build stylesheet");
          if (context2.stylesheetCache === null || context2.classCache.size !== classCacheCount) {
            context2.stylesheetCache = buildStylesheet([
              ...context2.ruleCache
            ], context2);
          }
          env.DEBUG && console.timeEnd("Build stylesheet");
          let { defaults: defaultNodes, base: baseNodes, components: componentNodes, utilities: utilityNodes, variants: screenNodes } = context2.stylesheetCache;
          if (layerNodes.base) {
            layerNodes.base.before((0, _cloneNodes.default)([
              ...defaultNodes,
              ...baseNodes
            ], layerNodes.base.source, {
              layer: "base"
            }));
            layerNodes.base.remove();
          }
          if (layerNodes.components) {
            layerNodes.components.before((0, _cloneNodes.default)([
              ...componentNodes
            ], layerNodes.components.source, {
              layer: "components"
            }));
            layerNodes.components.remove();
          }
          if (layerNodes.utilities) {
            layerNodes.utilities.before((0, _cloneNodes.default)([
              ...utilityNodes
            ], layerNodes.utilities.source, {
              layer: "utilities"
            }));
            layerNodes.utilities.remove();
          }
          const variantNodes = Array.from(screenNodes).filter((node) => {
            var _node_raws_tailwind;
            const parentLayer = (_node_raws_tailwind = node.raws.tailwind) === null || _node_raws_tailwind === void 0 ? void 0 : _node_raws_tailwind.parentLayer;
            if (parentLayer === "components") {
              return layerNodes.components !== null;
            }
            if (parentLayer === "utilities") {
              return layerNodes.utilities !== null;
            }
            return true;
          });
          if (layerNodes.variants) {
            layerNodes.variants.before((0, _cloneNodes.default)(variantNodes, layerNodes.variants.source, {
              layer: "variants"
            }));
            layerNodes.variants.remove();
          } else if (variantNodes.length > 0) {
            root.append((0, _cloneNodes.default)(variantNodes, root.source, {
              layer: "variants"
            }));
          }
          var _root_source_end;
          root.source.end = (_root_source_end = root.source.end) !== null && _root_source_end !== void 0 ? _root_source_end : root.source.start;
          const hasUtilityVariants = variantNodes.some((node) => {
            var _node_raws_tailwind;
            return ((_node_raws_tailwind = node.raws.tailwind) === null || _node_raws_tailwind === void 0 ? void 0 : _node_raws_tailwind.parentLayer) === "utilities";
          });
          if (layerNodes.utilities && utilityNodes.size === 0 && !hasUtilityVariants) {
            _log.default.warn("content-problems", [
              "No utility classes were detected in your source files. If this is unexpected, double-check the `content` option in your Tailwind CSS configuration.",
              "https://tailwindcss.com/docs/content-configuration"
            ]);
          }
          if (env.DEBUG) {
            console.log("Potential classes: ", candidates.size);
            console.log("Active contexts: ", _sharedState.contextSourcesMap.size);
          }
          context2.changedContent = [];
          root.walkAtRules("layer", (rule) => {
            if (Object.keys(layerNodes).includes(rule.params)) {
              rule.remove();
            }
          });
        };
      }
    }
  });

  // tailwindcss/lib/lib/expandApplyAtRules.js
  var require_expandApplyAtRules = __commonJS({
    "tailwindcss/lib/lib/expandApplyAtRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return expandApplyAtRules;
        }
      });
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _generateRules = require_generateRules();
      var _escapeClassName = /* @__PURE__ */ _interop_require_default(require_escapeClassName());
      var _applyImportantSelector = require_applyImportantSelector();
      var _pseudoElements = require_pseudoElements();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function extractClasses(node) {
        let groups = /* @__PURE__ */ new Map();
        let container = _postcss.default.root({
          nodes: [
            node.clone()
          ]
        });
        container.walkRules((rule) => {
          (0, _postcssselectorparser.default)((selectors) => {
            selectors.walkClasses((classSelector) => {
              let parentSelector = classSelector.parent.toString();
              let classes2 = groups.get(parentSelector);
              if (!classes2) {
                groups.set(parentSelector, classes2 = /* @__PURE__ */ new Set());
              }
              classes2.add(classSelector.value);
            });
          }).processSync(rule.selector);
        });
        let normalizedGroups = Array.from(groups.values(), (classes2) => Array.from(classes2));
        let classes = normalizedGroups.flat();
        return Object.assign(classes, {
          groups: normalizedGroups
        });
      }
      var selectorExtractor = (0, _postcssselectorparser.default)();
      function extractSelectors(ruleSelectors) {
        return selectorExtractor.astSync(ruleSelectors);
      }
      function extractBaseCandidates(candidates, separator) {
        let baseClasses = /* @__PURE__ */ new Set();
        for (let candidate of candidates) {
          baseClasses.add(candidate.split(separator).pop());
        }
        return Array.from(baseClasses);
      }
      function prefix(context2, selector) {
        let prefix2 = context2.tailwindConfig.prefix;
        return typeof prefix2 === "function" ? prefix2(selector) : prefix2 + selector;
      }
      function* pathToRoot(node) {
        yield node;
        while (node.parent) {
          yield node.parent;
          node = node.parent;
        }
      }
      function shallowClone(node, overrides = {}) {
        let children = node.nodes;
        node.nodes = [];
        let tmp = node.clone(overrides);
        node.nodes = children;
        return tmp;
      }
      function nestedClone(node) {
        for (let parent of pathToRoot(node)) {
          if (node === parent) {
            continue;
          }
          if (parent.type === "root") {
            break;
          }
          node = shallowClone(parent, {
            nodes: [
              node
            ]
          });
        }
        return node;
      }
      function buildLocalApplyCache(root, context2) {
        let cache = /* @__PURE__ */ new Map();
        root.walkRules((rule) => {
          for (let node of pathToRoot(rule)) {
            var _node_raws_tailwind;
            if (((_node_raws_tailwind = node.raws.tailwind) === null || _node_raws_tailwind === void 0 ? void 0 : _node_raws_tailwind.layer) !== void 0) {
              return;
            }
          }
          let container = nestedClone(rule);
          let sort = context2.offsets.create("user");
          for (let className of extractClasses(rule)) {
            let list = cache.get(className) || [];
            cache.set(className, list);
            list.push([
              {
                layer: "user",
                sort,
                important: false
              },
              container
            ]);
          }
        });
        return cache;
      }
      function buildApplyCache(applyCandidates, context2) {
        for (let candidate of applyCandidates) {
          if (context2.notClassCache.has(candidate) || context2.applyClassCache.has(candidate)) {
            continue;
          }
          if (context2.classCache.has(candidate)) {
            context2.applyClassCache.set(candidate, context2.classCache.get(candidate).map(([meta, rule]) => [
              meta,
              rule.clone()
            ]));
            continue;
          }
          let matches = Array.from((0, _generateRules.resolveMatches)(candidate, context2));
          if (matches.length === 0) {
            context2.notClassCache.add(candidate);
            continue;
          }
          context2.applyClassCache.set(candidate, matches);
        }
        return context2.applyClassCache;
      }
      function lazyCache(buildCacheFn) {
        let cache = null;
        return {
          get: (name) => {
            cache = cache || buildCacheFn();
            return cache.get(name);
          },
          has: (name) => {
            cache = cache || buildCacheFn();
            return cache.has(name);
          }
        };
      }
      function combineCaches(caches) {
        return {
          get: (name) => caches.flatMap((cache) => cache.get(name) || []),
          has: (name) => caches.some((cache) => cache.has(name))
        };
      }
      function extractApplyCandidates(params) {
        let candidates = params.split(/[\s\t\n]+/g);
        if (candidates[candidates.length - 1] === "!important") {
          return [
            candidates.slice(0, -1),
            true
          ];
        }
        return [
          candidates,
          false
        ];
      }
      function processApply(root, context2, localCache) {
        let applyCandidates = /* @__PURE__ */ new Set();
        let applies = [];
        root.walkAtRules("apply", (rule) => {
          let [candidates] = extractApplyCandidates(rule.params);
          for (let util of candidates) {
            applyCandidates.add(util);
          }
          applies.push(rule);
        });
        if (applies.length === 0) {
          return;
        }
        let applyClassCache = combineCaches([
          localCache,
          buildApplyCache(applyCandidates, context2)
        ]);
        function replaceSelector(selector, utilitySelectors, candidate) {
          let selectorList = extractSelectors(selector);
          let utilitySelectorsList = extractSelectors(utilitySelectors);
          let candidateList = extractSelectors(`.${(0, _escapeClassName.default)(candidate)}`);
          let candidateClass = candidateList.nodes[0].nodes[0];
          selectorList.each((sel) => {
            let replaced = /* @__PURE__ */ new Set();
            utilitySelectorsList.each((utilitySelector) => {
              let hasReplaced = false;
              utilitySelector = utilitySelector.clone();
              utilitySelector.walkClasses((node) => {
                if (node.value !== candidateClass.value) {
                  return;
                }
                if (hasReplaced) {
                  return;
                }
                node.replaceWith(...sel.nodes.map((node2) => node2.clone()));
                replaced.add(utilitySelector);
                hasReplaced = true;
              });
            });
            for (let sel2 of replaced) {
              let groups = [
                []
              ];
              for (let node of sel2.nodes) {
                if (node.type === "combinator") {
                  groups.push(node);
                  groups.push([]);
                } else {
                  let last = groups[groups.length - 1];
                  last.push(node);
                }
              }
              sel2.nodes = [];
              for (let group of groups) {
                if (Array.isArray(group)) {
                  group.sort((a, b) => {
                    if (a.type === "tag" && b.type === "class") {
                      return -1;
                    } else if (a.type === "class" && b.type === "tag") {
                      return 1;
                    } else if (a.type === "class" && b.type === "pseudo" && b.value.startsWith("::")) {
                      return -1;
                    } else if (a.type === "pseudo" && a.value.startsWith("::") && b.type === "class") {
                      return 1;
                    }
                    return 0;
                  });
                }
                sel2.nodes = sel2.nodes.concat(group);
              }
            }
            sel.replaceWith(...replaced);
          });
          return selectorList.toString();
        }
        let perParentApplies = /* @__PURE__ */ new Map();
        for (let apply of applies) {
          let [candidates] = perParentApplies.get(apply.parent) || [
            [],
            apply.source
          ];
          perParentApplies.set(apply.parent, [
            candidates,
            apply.source
          ]);
          let [applyCandidates2, important] = extractApplyCandidates(apply.params);
          if (apply.parent.type === "atrule") {
            if (apply.parent.name === "screen") {
              let screenType = apply.parent.params;
              throw apply.error(`@apply is not supported within nested at-rules like @screen. We suggest you write this as @apply ${applyCandidates2.map((c) => `${screenType}:${c}`).join(" ")} instead.`);
            }
            throw apply.error(`@apply is not supported within nested at-rules like @${apply.parent.name}. You can fix this by un-nesting @${apply.parent.name}.`);
          }
          for (let applyCandidate of applyCandidates2) {
            if ([
              prefix(context2, "group"),
              prefix(context2, "peer")
            ].includes(applyCandidate)) {
              throw apply.error(`@apply should not be used with the '${applyCandidate}' utility`);
            }
            if (!applyClassCache.has(applyCandidate)) {
              throw apply.error(`The \`${applyCandidate}\` class does not exist. If \`${applyCandidate}\` is a custom class, make sure it is defined within a \`@layer\` directive.`);
            }
            let rules = applyClassCache.get(applyCandidate);
            for (let [, rule] of rules) {
              if (rule.type === "atrule") {
                continue;
              }
              rule.walkRules(() => {
                throw apply.error([
                  `The \`${applyCandidate}\` class cannot be used with \`@apply\` because \`@apply\` does not currently support nested CSS.`,
                  "Rewrite the selector without nesting or configure the `tailwindcss/nesting` plugin:",
                  "https://tailwindcss.com/docs/using-with-preprocessors#nesting"
                ].join("\n"));
              });
            }
            candidates.push([
              applyCandidate,
              important,
              rules
            ]);
          }
        }
        for (let [parent, [candidates, atApplySource]] of perParentApplies) {
          let siblings = [];
          for (let [applyCandidate, important, rules] of candidates) {
            let potentialApplyCandidates = [
              applyCandidate,
              ...extractBaseCandidates([
                applyCandidate
              ], context2.tailwindConfig.separator)
            ];
            for (let [meta, node] of rules) {
              let parentClasses = extractClasses(parent);
              let nodeClasses = extractClasses(node);
              nodeClasses = nodeClasses.groups.filter((classList) => classList.some((className) => potentialApplyCandidates.includes(className))).flat();
              nodeClasses = nodeClasses.concat(extractBaseCandidates(nodeClasses, context2.tailwindConfig.separator));
              let intersects = parentClasses.some((selector) => nodeClasses.includes(selector));
              if (intersects) {
                throw node.error(`You cannot \`@apply\` the \`${applyCandidate}\` utility here because it creates a circular dependency.`);
              }
              let root2 = _postcss.default.root({
                nodes: [
                  node.clone()
                ]
              });
              root2.walk((node2) => {
                node2.source = atApplySource;
              });
              let canRewriteSelector = node.type !== "atrule" || node.type === "atrule" && node.name !== "keyframes";
              if (canRewriteSelector) {
                root2.walkRules((rule) => {
                  if (!extractClasses(rule).some((candidate) => candidate === applyCandidate)) {
                    rule.remove();
                    return;
                  }
                  let importantSelector = typeof context2.tailwindConfig.important === "string" ? context2.tailwindConfig.important : null;
                  let isGenerated = parent.raws.tailwind !== void 0;
                  let parentSelector = isGenerated && importantSelector && parent.selector.indexOf(importantSelector) === 0 ? parent.selector.slice(importantSelector.length) : parent.selector;
                  if (parentSelector === "") {
                    parentSelector = parent.selector;
                  }
                  rule.selector = replaceSelector(parentSelector, rule.selector, applyCandidate);
                  if (importantSelector && parentSelector !== parent.selector) {
                    rule.selector = (0, _applyImportantSelector.applyImportantSelector)(rule.selector, importantSelector);
                  }
                  rule.walkDecls((d) => {
                    d.important = meta.important || important;
                  });
                  let selector = (0, _postcssselectorparser.default)().astSync(rule.selector);
                  selector.each((sel) => (0, _pseudoElements.movePseudos)(sel));
                  rule.selector = selector.toString();
                });
              }
              if (!root2.nodes[0]) {
                continue;
              }
              siblings.push([
                meta.sort,
                root2.nodes[0]
              ]);
            }
          }
          let nodes = context2.offsets.sort(siblings).map((s) => s[1]);
          parent.after(nodes);
        }
        for (let apply of applies) {
          if (apply.parent.nodes.length > 1) {
            apply.remove();
          } else {
            apply.parent.remove();
          }
        }
        processApply(root, context2, localCache);
      }
      function expandApplyAtRules(context2) {
        return (root) => {
          let localCache = lazyCache(() => buildLocalApplyCache(root, context2));
          processApply(root, context2, localCache);
        };
      }
    }
  });

  // didyoumean/didYouMean-1.2.1.js
  var require_didYouMean_1_2_1 = __commonJS({
    "didyoumean/didYouMean-1.2.1.js"(exports, module) {
      (function() {
        "use strict";
        function didYouMean(str, list, key) {
          if (!str) return null;
          if (!didYouMean.caseSensitive) {
            str = str.toLowerCase();
          }
          var thresholdRelative = didYouMean.threshold === null ? null : didYouMean.threshold * str.length, thresholdAbsolute = didYouMean.thresholdAbsolute, winningVal;
          if (thresholdRelative !== null && thresholdAbsolute !== null) winningVal = Math.min(thresholdRelative, thresholdAbsolute);
          else if (thresholdRelative !== null) winningVal = thresholdRelative;
          else if (thresholdAbsolute !== null) winningVal = thresholdAbsolute;
          else winningVal = null;
          var winner, candidate, testCandidate, val, i, len = list.length;
          for (i = 0; i < len; i++) {
            candidate = list[i];
            if (key) {
              candidate = candidate[key];
            }
            if (!candidate) {
              continue;
            }
            if (!didYouMean.caseSensitive) {
              testCandidate = candidate.toLowerCase();
            } else {
              testCandidate = candidate;
            }
            val = getEditDistance(str, testCandidate, winningVal);
            if (winningVal === null || val < winningVal) {
              winningVal = val;
              if (key && didYouMean.returnWinningObject) winner = list[i];
              else winner = candidate;
              if (didYouMean.returnFirstMatch) return winner;
            }
          }
          return winner || didYouMean.nullResultValue;
        }
        didYouMean.threshold = 0.4;
        didYouMean.thresholdAbsolute = 20;
        didYouMean.caseSensitive = false;
        didYouMean.nullResultValue = null;
        didYouMean.returnWinningObject = null;
        didYouMean.returnFirstMatch = false;
        if (typeof module !== "undefined" && module.exports) {
          module.exports = didYouMean;
        } else {
          window.didYouMean = didYouMean;
        }
        var MAX_INT = Math.pow(2, 32) - 1;
        function getEditDistance(a, b, max) {
          max = max || max === 0 ? max : MAX_INT;
          var lena = a.length;
          var lenb = b.length;
          if (lena === 0) return Math.min(max + 1, lenb);
          if (lenb === 0) return Math.min(max + 1, lena);
          if (Math.abs(lena - lenb) > max) return max + 1;
          var matrix = [], i, j, colMin, minJ, maxJ;
          for (i = 0; i <= lenb; i++) {
            matrix[i] = [i];
          }
          for (j = 0; j <= lena; j++) {
            matrix[0][j] = j;
          }
          for (i = 1; i <= lenb; i++) {
            colMin = MAX_INT;
            minJ = 1;
            if (i > max) minJ = i - max;
            maxJ = lenb + 1;
            if (maxJ > max + i) maxJ = max + i;
            for (j = 1; j <= lena; j++) {
              if (j < minJ || j > maxJ) {
                matrix[i][j] = max + 1;
              } else {
                if (b.charAt(i - 1) === a.charAt(j - 1)) {
                  matrix[i][j] = matrix[i - 1][j - 1];
                } else {
                  matrix[i][j] = Math.min(
                    matrix[i - 1][j - 1] + 1,
                    // Substitute
                    Math.min(
                      matrix[i][j - 1] + 1,
                      // Insert
                      matrix[i - 1][j] + 1
                    )
                  );
                }
              }
              if (matrix[i][j] < colMin) colMin = matrix[i][j];
            }
            if (colMin > max) return max + 1;
          }
          return matrix[lenb][lena];
        }
      })();
    }
  });

  // tailwindcss/lib/value-parser/parse.js
  var require_parse2 = __commonJS({
    "tailwindcss/lib/value-parser/parse.js"(exports, module) {
      "use strict";
      var openParentheses = "(".charCodeAt(0);
      var closeParentheses = ")".charCodeAt(0);
      var singleQuote = "'".charCodeAt(0);
      var doubleQuote = '"'.charCodeAt(0);
      var backslash = "\\".charCodeAt(0);
      var slash = "/".charCodeAt(0);
      var comma = ",".charCodeAt(0);
      var colon = ":".charCodeAt(0);
      var star = "*".charCodeAt(0);
      var uLower = "u".charCodeAt(0);
      var uUpper = "U".charCodeAt(0);
      var plus = "+".charCodeAt(0);
      var isUnicodeRange = /^[a-f0-9?-]+$/i;
      module.exports = function(input) {
        var tokens = [];
        var value = input;
        var next, quote, prev, token, escape, escapePos, whitespacePos, parenthesesOpenPos;
        var pos = 0;
        var code = value.charCodeAt(pos);
        var max = value.length;
        var stack = [
          {
            nodes: tokens
          }
        ];
        var balanced = 0;
        var parent;
        var name = "";
        var before = "";
        var after = "";
        while (pos < max) {
          if (code <= 32) {
            next = pos;
            do {
              next += 1;
              code = value.charCodeAt(next);
            } while (code <= 32);
            token = value.slice(pos, next);
            prev = tokens[tokens.length - 1];
            if (code === closeParentheses && balanced) {
              after = token;
            } else if (prev && prev.type === "div") {
              prev.after = token;
              prev.sourceEndIndex += token.length;
            } else if (code === comma || code === colon || code === slash && value.charCodeAt(next + 1) !== star && (!parent || parent && parent.type === "function" && false)) {
              before = token;
            } else {
              tokens.push({
                type: "space",
                sourceIndex: pos,
                sourceEndIndex: next,
                value: token
              });
            }
            pos = next;
          } else if (code === singleQuote || code === doubleQuote) {
            next = pos;
            quote = code === singleQuote ? "'" : '"';
            token = {
              type: "string",
              sourceIndex: pos,
              quote
            };
            do {
              escape = false;
              next = value.indexOf(quote, next + 1);
              if (~next) {
                escapePos = next;
                while (value.charCodeAt(escapePos - 1) === backslash) {
                  escapePos -= 1;
                  escape = !escape;
                }
              } else {
                value += quote;
                next = value.length - 1;
                token.unclosed = true;
              }
            } while (escape);
            token.value = value.slice(pos + 1, next);
            token.sourceEndIndex = token.unclosed ? next : next + 1;
            tokens.push(token);
            pos = next + 1;
            code = value.charCodeAt(pos);
          } else if (code === slash && value.charCodeAt(pos + 1) === star) {
            next = value.indexOf("*/", pos);
            token = {
              type: "comment",
              sourceIndex: pos,
              sourceEndIndex: next + 2
            };
            if (next === -1) {
              token.unclosed = true;
              next = value.length;
              token.sourceEndIndex = next;
            }
            token.value = value.slice(pos + 2, next);
            tokens.push(token);
            pos = next + 2;
            code = value.charCodeAt(pos);
          } else if ((code === slash || code === star) && parent && parent.type === "function" && true) {
            token = value[pos];
            tokens.push({
              type: "word",
              sourceIndex: pos - before.length,
              sourceEndIndex: pos + token.length,
              value: token
            });
            pos += 1;
            code = value.charCodeAt(pos);
          } else if (code === slash || code === comma || code === colon) {
            token = value[pos];
            tokens.push({
              type: "div",
              sourceIndex: pos - before.length,
              sourceEndIndex: pos + token.length,
              value: token,
              before,
              after: ""
            });
            before = "";
            pos += 1;
            code = value.charCodeAt(pos);
          } else if (openParentheses === code) {
            next = pos;
            do {
              next += 1;
              code = value.charCodeAt(next);
            } while (code <= 32);
            parenthesesOpenPos = pos;
            token = {
              type: "function",
              sourceIndex: pos - name.length,
              value: name,
              before: value.slice(parenthesesOpenPos + 1, next)
            };
            pos = next;
            if (name === "url" && code !== singleQuote && code !== doubleQuote) {
              next -= 1;
              do {
                escape = false;
                next = value.indexOf(")", next + 1);
                if (~next) {
                  escapePos = next;
                  while (value.charCodeAt(escapePos - 1) === backslash) {
                    escapePos -= 1;
                    escape = !escape;
                  }
                } else {
                  value += ")";
                  next = value.length - 1;
                  token.unclosed = true;
                }
              } while (escape);
              whitespacePos = next;
              do {
                whitespacePos -= 1;
                code = value.charCodeAt(whitespacePos);
              } while (code <= 32);
              if (parenthesesOpenPos < whitespacePos) {
                if (pos !== whitespacePos + 1) {
                  token.nodes = [
                    {
                      type: "word",
                      sourceIndex: pos,
                      sourceEndIndex: whitespacePos + 1,
                      value: value.slice(pos, whitespacePos + 1)
                    }
                  ];
                } else {
                  token.nodes = [];
                }
                if (token.unclosed && whitespacePos + 1 !== next) {
                  token.after = "";
                  token.nodes.push({
                    type: "space",
                    sourceIndex: whitespacePos + 1,
                    sourceEndIndex: next,
                    value: value.slice(whitespacePos + 1, next)
                  });
                } else {
                  token.after = value.slice(whitespacePos + 1, next);
                  token.sourceEndIndex = next;
                }
              } else {
                token.after = "";
                token.nodes = [];
              }
              pos = next + 1;
              token.sourceEndIndex = token.unclosed ? next : pos;
              code = value.charCodeAt(pos);
              tokens.push(token);
            } else {
              balanced += 1;
              token.after = "";
              token.sourceEndIndex = pos + 1;
              tokens.push(token);
              stack.push(token);
              tokens = token.nodes = [];
              parent = token;
            }
            name = "";
          } else if (closeParentheses === code && balanced) {
            pos += 1;
            code = value.charCodeAt(pos);
            parent.after = after;
            parent.sourceEndIndex += after.length;
            after = "";
            balanced -= 1;
            stack[stack.length - 1].sourceEndIndex = pos;
            stack.pop();
            parent = stack[balanced];
            tokens = parent.nodes;
          } else {
            next = pos;
            do {
              if (code === backslash) {
                next += 1;
              }
              next += 1;
              code = value.charCodeAt(next);
            } while (next < max && !(code <= 32 || code === singleQuote || code === doubleQuote || code === comma || code === colon || code === slash || code === openParentheses || code === star && parent && parent.type === "function" && true || code === slash && parent.type === "function" && true || code === closeParentheses && balanced));
            token = value.slice(pos, next);
            if (openParentheses === code) {
              name = token;
            } else if ((uLower === token.charCodeAt(0) || uUpper === token.charCodeAt(0)) && plus === token.charCodeAt(1) && isUnicodeRange.test(token.slice(2))) {
              tokens.push({
                type: "unicode-range",
                sourceIndex: pos,
                sourceEndIndex: next,
                value: token
              });
            } else {
              tokens.push({
                type: "word",
                sourceIndex: pos,
                sourceEndIndex: next,
                value: token
              });
            }
            pos = next;
          }
        }
        for (pos = stack.length - 1; pos; pos -= 1) {
          stack[pos].unclosed = true;
          stack[pos].sourceEndIndex = value.length;
        }
        return stack[0].nodes;
      };
    }
  });

  // tailwindcss/lib/value-parser/walk.js
  var require_walk = __commonJS({
    "tailwindcss/lib/value-parser/walk.js"(exports, module) {
      "use strict";
      module.exports = function walk(nodes, cb, bubble) {
        var i, max, node, result;
        for (i = 0, max = nodes.length; i < max; i += 1) {
          node = nodes[i];
          if (!bubble) {
            result = cb(node, i, nodes);
          }
          if (result !== false && node.type === "function" && Array.isArray(node.nodes)) {
            walk(node.nodes, cb, bubble);
          }
          if (bubble) {
            cb(node, i, nodes);
          }
        }
      };
    }
  });

  // tailwindcss/lib/value-parser/stringify.js
  var require_stringify2 = __commonJS({
    "tailwindcss/lib/value-parser/stringify.js"(exports, module) {
      "use strict";
      function stringifyNode(node, custom) {
        var type = node.type;
        var value = node.value;
        var buf;
        var customResult;
        if (custom && (customResult = custom(node)) !== void 0) {
          return customResult;
        } else if (type === "word" || type === "space") {
          return value;
        } else if (type === "string") {
          buf = node.quote || "";
          return buf + value + (node.unclosed ? "" : buf);
        } else if (type === "comment") {
          return "/*" + value + (node.unclosed ? "" : "*/");
        } else if (type === "div") {
          return (node.before || "") + value + (node.after || "");
        } else if (Array.isArray(node.nodes)) {
          buf = stringify(node.nodes, custom);
          if (type !== "function") {
            return buf;
          }
          return value + "(" + (node.before || "") + buf + (node.after || "") + (node.unclosed ? "" : ")");
        }
        return value;
      }
      function stringify(nodes, custom) {
        var result, i;
        if (Array.isArray(nodes)) {
          result = "";
          for (i = nodes.length - 1; ~i; i -= 1) {
            result = stringifyNode(nodes[i], custom) + result;
          }
          return result;
        }
        return stringifyNode(nodes, custom);
      }
      module.exports = stringify;
    }
  });

  // tailwindcss/lib/value-parser/unit.js
  var require_unit = __commonJS({
    "tailwindcss/lib/value-parser/unit.js"(exports, module) {
      "use strict";
      var minus = "-".charCodeAt(0);
      var plus = "+".charCodeAt(0);
      var dot = ".".charCodeAt(0);
      var exp = "e".charCodeAt(0);
      var EXP = "E".charCodeAt(0);
      function likeNumber(value) {
        var code = value.charCodeAt(0);
        var nextCode;
        if (code === plus || code === minus) {
          nextCode = value.charCodeAt(1);
          if (nextCode >= 48 && nextCode <= 57) {
            return true;
          }
          var nextNextCode = value.charCodeAt(2);
          if (nextCode === dot && nextNextCode >= 48 && nextNextCode <= 57) {
            return true;
          }
          return false;
        }
        if (code === dot) {
          nextCode = value.charCodeAt(1);
          if (nextCode >= 48 && nextCode <= 57) {
            return true;
          }
          return false;
        }
        if (code >= 48 && code <= 57) {
          return true;
        }
        return false;
      }
      module.exports = function(value) {
        var pos = 0;
        var length = value.length;
        var code;
        var nextCode;
        var nextNextCode;
        if (length === 0 || !likeNumber(value)) {
          return false;
        }
        code = value.charCodeAt(pos);
        if (code === plus || code === minus) {
          pos++;
        }
        while (pos < length) {
          code = value.charCodeAt(pos);
          if (code < 48 || code > 57) {
            break;
          }
          pos += 1;
        }
        code = value.charCodeAt(pos);
        nextCode = value.charCodeAt(pos + 1);
        if (code === dot && nextCode >= 48 && nextCode <= 57) {
          pos += 2;
          while (pos < length) {
            code = value.charCodeAt(pos);
            if (code < 48 || code > 57) {
              break;
            }
            pos += 1;
          }
        }
        code = value.charCodeAt(pos);
        nextCode = value.charCodeAt(pos + 1);
        nextNextCode = value.charCodeAt(pos + 2);
        if ((code === exp || code === EXP) && (nextCode >= 48 && nextCode <= 57 || (nextCode === plus || nextCode === minus) && nextNextCode >= 48 && nextNextCode <= 57)) {
          pos += nextCode === plus || nextCode === minus ? 3 : 2;
          while (pos < length) {
            code = value.charCodeAt(pos);
            if (code < 48 || code > 57) {
              break;
            }
            pos += 1;
          }
        }
        return {
          number: value.slice(0, pos),
          unit: value.slice(pos)
        };
      };
    }
  });

  // tailwindcss/lib/value-parser/index.js
  var require_value_parser = __commonJS({
    "tailwindcss/lib/value-parser/index.js"(exports, module) {
      "use strict";
      var parse = require_parse2();
      var walk = require_walk();
      var stringify = require_stringify2();
      function ValueParser(value) {
        if (this instanceof ValueParser) {
          this.nodes = parse(value);
          return this;
        }
        return new ValueParser(value);
      }
      ValueParser.prototype.toString = function() {
        return Array.isArray(this.nodes) ? stringify(this.nodes) : "";
      };
      ValueParser.prototype.walk = function(cb, bubble) {
        walk(this.nodes, cb, bubble);
        return this;
      };
      ValueParser.unit = require_unit();
      ValueParser.walk = walk;
      ValueParser.stringify = stringify;
      module.exports = ValueParser;
    }
  });

  // tailwindcss/lib/lib/evaluateTailwindFunctions.js
  var require_evaluateTailwindFunctions = __commonJS({
    "tailwindcss/lib/lib/evaluateTailwindFunctions.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _dlv = /* @__PURE__ */ _interop_require_default(require_owned_path_get());
      var _didyoumean = /* @__PURE__ */ _interop_require_default(require_didYouMean_1_2_1());
      var _transformThemeValue = /* @__PURE__ */ _interop_require_default(require_transformThemeValue());
      var _index = /* @__PURE__ */ _interop_require_default(require_value_parser());
      var _normalizeScreens = require_normalizeScreens();
      var _buildMediaQuery = /* @__PURE__ */ _interop_require_default(require_buildMediaQuery());
      var _toPath = require_toPath();
      var _withAlphaVariable = require_withAlphaVariable();
      var _pluginUtils = require_pluginUtils();
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function isObject(input) {
        return typeof input === "object" && input !== null;
      }
      function findClosestExistingPath(theme, path) {
        let parts = (0, _toPath.toPath)(path);
        do {
          parts.pop();
          if ((0, _dlv.default)(theme, parts) !== void 0) break;
        } while (parts.length);
        return parts.length ? parts : void 0;
      }
      function pathToString(path) {
        if (typeof path === "string") return path;
        return path.reduce((acc, cur, i) => {
          if (cur.includes(".")) return `${acc}[${cur}]`;
          return i === 0 ? cur : `${acc}.${cur}`;
        }, "");
      }
      function list(items) {
        return items.map((key) => `'${key}'`).join(", ");
      }
      function listKeys(obj) {
        return list(Object.keys(obj));
      }
      function validatePath(config, path, defaultValue, themeOpts = {}) {
        const pathString = Array.isArray(path) ? pathToString(path) : path.replace(/^['"]+|['"]+$/g, "");
        const pathSegments = Array.isArray(path) ? path : (0, _toPath.toPath)(pathString);
        const value = (0, _dlv.default)(config.theme, pathSegments, defaultValue);
        if (value === void 0) {
          let error = `'${pathString}' does not exist in your theme config.`;
          const parentSegments = pathSegments.slice(0, -1);
          const parentValue = (0, _dlv.default)(config.theme, parentSegments);
          if (isObject(parentValue)) {
            const validKeys = Object.keys(parentValue).filter((key) => validatePath(config, [
              ...parentSegments,
              key
            ]).isValid);
            const suggestion = (0, _didyoumean.default)(pathSegments[pathSegments.length - 1], validKeys);
            if (suggestion) {
              error += ` Did you mean '${pathToString([
                ...parentSegments,
                suggestion
              ])}'?`;
            } else if (validKeys.length > 0) {
              error += ` '${pathToString(parentSegments)}' has the following valid keys: ${list(validKeys)}`;
            }
          } else {
            const closestPath = findClosestExistingPath(config.theme, pathString);
            if (closestPath) {
              const closestValue = (0, _dlv.default)(config.theme, closestPath);
              if (isObject(closestValue)) {
                error += ` '${pathToString(closestPath)}' has the following keys: ${listKeys(closestValue)}`;
              } else {
                error += ` '${pathToString(closestPath)}' is not an object.`;
              }
            } else {
              error += ` Your theme has the following top-level keys: ${listKeys(config.theme)}`;
            }
          }
          return {
            isValid: false,
            error
          };
        }
        if (!(typeof value === "string" || typeof value === "number" || typeof value === "function" || value instanceof String || value instanceof Number || Array.isArray(value))) {
          let error = `'${pathString}' was found but does not resolve to a string.`;
          if (isObject(value)) {
            let validKeys = Object.keys(value).filter((key) => validatePath(config, [
              ...pathSegments,
              key
            ]).isValid);
            if (validKeys.length) {
              error += ` Did you mean something like '${pathToString([
                ...pathSegments,
                validKeys[0]
              ])}'?`;
            }
          }
          return {
            isValid: false,
            error
          };
        }
        const [themeSection] = pathSegments;
        return {
          isValid: true,
          value: (0, _transformThemeValue.default)(themeSection)(value, themeOpts)
        };
      }
      function extractArgs(node, vNodes, functions) {
        vNodes = vNodes.map((vNode) => resolveVNode(node, vNode, functions));
        let args = [
          ""
        ];
        for (let vNode of vNodes) {
          if (vNode.type === "div" && vNode.value === ",") {
            args.push("");
          } else {
            args[args.length - 1] += _index.default.stringify(vNode);
          }
        }
        return args;
      }
      function resolveVNode(node, vNode, functions) {
        if (vNode.type === "function" && functions[vNode.value] !== void 0) {
          let args = extractArgs(node, vNode.nodes, functions);
          vNode.type = "word";
          vNode.value = functions[vNode.value](node, ...args);
        }
        return vNode;
      }
      function resolveFunctions(node, input, functions) {
        let hasAnyFn = Object.keys(functions).some((fn) => input.includes(`${fn}(`));
        if (!hasAnyFn) return input;
        return (0, _index.default)(input).walk((vNode) => {
          resolveVNode(node, vNode, functions);
        }).toString();
      }
      var nodeTypePropertyMap = {
        atrule: "params",
        decl: "value"
      };
      function* toPaths(path) {
        path = path.replace(/^['"]+|['"]+$/g, "");
        let matches = path.match(/^([^\s]+)(?![^\[]*\])(?:\s*\/\s*([^\/\s]+))$/);
        let alpha = void 0;
        yield [
          path,
          void 0
        ];
        if (matches) {
          path = matches[1];
          alpha = matches[2];
          yield [
            path,
            alpha
          ];
        }
      }
      function resolvePath(config, path, defaultValue) {
        const results = Array.from(toPaths(path)).map(([path2, alpha]) => {
          return Object.assign(validatePath(config, path2, defaultValue, {
            opacityValue: alpha
          }), {
            resolvedPath: path2,
            alpha
          });
        });
        var _results_find;
        return (_results_find = results.find((result) => result.isValid)) !== null && _results_find !== void 0 ? _results_find : results[0];
      }
      function _default(context2) {
        let config = context2.tailwindConfig;
        let functions = {
          theme: (node, path, ...defaultValue) => {
            let { isValid, value, error, alpha } = resolvePath(config, path, defaultValue.length ? defaultValue : void 0);
            if (!isValid) {
              var _parentNode_raws_tailwind;
              let parentNode = node.parent;
              let candidate = (_parentNode_raws_tailwind = parentNode === null || parentNode === void 0 ? void 0 : parentNode.raws.tailwind) === null || _parentNode_raws_tailwind === void 0 ? void 0 : _parentNode_raws_tailwind.candidate;
              if (parentNode && candidate !== void 0) {
                context2.markInvalidUtilityNode(parentNode);
                parentNode.remove();
                _log.default.warn("invalid-theme-key-in-class", [
                  `The utility \`${candidate}\` contains an invalid theme value and was not generated.`
                ]);
                return;
              }
              throw node.error(error);
            }
            let maybeColor = (0, _pluginUtils.parseColorFormat)(value);
            let isColorFunction = maybeColor !== void 0 && typeof maybeColor === "function";
            if (alpha !== void 0 || isColorFunction) {
              if (alpha === void 0) {
                alpha = 1;
              }
              value = (0, _withAlphaVariable.withAlphaValue)(maybeColor, alpha, maybeColor);
            }
            return value;
          },
          screen: (node, screen) => {
            screen = screen.replace(/^['"]+/g, "").replace(/['"]+$/g, "");
            let screens = (0, _normalizeScreens.normalizeScreens)(config.theme.screens);
            let screenDefinition = screens.find(({ name }) => name === screen);
            if (!screenDefinition) {
              throw node.error(`The '${screen}' screen does not exist in your theme.`);
            }
            return (0, _buildMediaQuery.default)(screenDefinition);
          }
        };
        return (root) => {
          root.walk((node) => {
            let property = nodeTypePropertyMap[node.type];
            if (property === void 0) {
              return;
            }
            node[property] = resolveFunctions(node, node[property], functions);
          });
        };
      }
    }
  });

  // tailwindcss/lib/lib/substituteScreenAtRules.js
  var require_substituteScreenAtRules = __commonJS({
    "tailwindcss/lib/lib/substituteScreenAtRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _normalizeScreens = require_normalizeScreens();
      var _buildMediaQuery = /* @__PURE__ */ _interop_require_default(require_buildMediaQuery());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function _default({ tailwindConfig: { theme } }) {
        return function(css) {
          css.walkAtRules("screen", (atRule) => {
            let screen = atRule.params;
            let screens = (0, _normalizeScreens.normalizeScreens)(theme.screens);
            let screenDefinition = screens.find(({ name }) => name === screen);
            if (!screenDefinition) {
              throw atRule.error(`No \`${screen}\` screen found.`);
            }
            atRule.name = "media";
            atRule.params = (0, _buildMediaQuery.default)(screenDefinition);
          });
        };
      }
    }
  });

  // tailwindcss/lib/lib/resolveDefaultsAtRules.js
  var require_resolveDefaultsAtRules = __commonJS({
    "tailwindcss/lib/lib/resolveDefaultsAtRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      function _export(target, all) {
        for (var name in all) Object.defineProperty(target, name, {
          enumerable: true,
          get: all[name]
        });
      }
      _export(exports, {
        elementSelectorParser: function() {
          return elementSelectorParser;
        },
        default: function() {
          return resolveDefaultsAtRules;
        }
      });
      var _postcss = /* @__PURE__ */ _interop_require_default(require_postcss());
      var _postcssselectorparser = /* @__PURE__ */ _interop_require_default(require_dist());
      var _featureFlags = require_featureFlags();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var getNode = {
        id(node) {
          return _postcssselectorparser.default.attribute({
            attribute: "id",
            operator: "=",
            value: node.value,
            quoteMark: '"'
          });
        }
      };
      function minimumImpactSelector(nodes) {
        let rest = nodes.filter((node2) => {
          if (node2.type !== "pseudo") return true;
          if (node2.nodes.length > 0) return true;
          return node2.value.startsWith("::") || [
            ":before",
            ":after",
            ":first-line",
            ":first-letter"
          ].includes(node2.value);
        }).reverse();
        let searchFor = /* @__PURE__ */ new Set([
          "tag",
          "class",
          "id",
          "attribute"
        ]);
        let splitPointIdx = rest.findIndex((n) => searchFor.has(n.type));
        if (splitPointIdx === -1) return rest.reverse().join("").trim();
        let node = rest[splitPointIdx];
        let bestNode = getNode[node.type] ? getNode[node.type](node) : node;
        rest = rest.slice(0, splitPointIdx);
        let combinatorIdx = rest.findIndex((n) => n.type === "combinator" && n.value === ">");
        if (combinatorIdx !== -1) {
          rest.splice(0, combinatorIdx);
          rest.unshift(_postcssselectorparser.default.universal());
        }
        return [
          bestNode,
          ...rest.reverse()
        ].join("").trim();
      }
      var elementSelectorParser = (0, _postcssselectorparser.default)((selectors) => {
        return selectors.map((s) => {
          let nodes = s.split((n) => n.type === "combinator" && n.value === " ").pop();
          return minimumImpactSelector(nodes);
        });
      });
      var cache = /* @__PURE__ */ new Map();
      function extractElementSelector(selector) {
        if (!cache.has(selector)) {
          cache.set(selector, elementSelectorParser.transformSync(selector));
        }
        return cache.get(selector);
      }
      function resolveDefaultsAtRules({ tailwindConfig }) {
        return (root) => {
          let variableNodeMap = /* @__PURE__ */ new Map();
          let universals = /* @__PURE__ */ new Set();
          root.walkAtRules("defaults", (rule) => {
            if (rule.nodes && rule.nodes.length > 0) {
              universals.add(rule);
              return;
            }
            let variable = rule.params;
            if (!variableNodeMap.has(variable)) {
              variableNodeMap.set(variable, /* @__PURE__ */ new Set());
            }
            variableNodeMap.get(variable).add(rule.parent);
            rule.remove();
          });
          if ((0, _featureFlags.flagEnabled)(tailwindConfig, "optimizeUniversalDefaults")) {
            for (let universal of universals) {
              let selectorGroups = /* @__PURE__ */ new Map();
              var _variableNodeMap_get;
              let rules = (_variableNodeMap_get = variableNodeMap.get(universal.params)) !== null && _variableNodeMap_get !== void 0 ? _variableNodeMap_get : [];
              for (let rule of rules) {
                for (let selector of extractElementSelector(rule.selector)) {
                  let selectorGroupName = selector.includes(":-") || selector.includes("::-") || selector.includes(":has") ? selector : "__DEFAULT__";
                  var _selectorGroups_get;
                  let selectors = (_selectorGroups_get = selectorGroups.get(selectorGroupName)) !== null && _selectorGroups_get !== void 0 ? _selectorGroups_get : /* @__PURE__ */ new Set();
                  selectorGroups.set(selectorGroupName, selectors);
                  selectors.add(selector);
                }
              }
              if (selectorGroups.size === 0) {
                universal.remove();
                continue;
              }
              for (let [, selectors] of selectorGroups) {
                let universalRule = _postcss.default.rule({
                  source: universal.source
                });
                universalRule.selectors = [
                  ...selectors
                ];
                universalRule.append(universal.nodes.map((node) => node.clone()));
                universal.before(universalRule);
              }
              universal.remove();
            }
          } else if (universals.size) {
            let universalRule = _postcss.default.rule({
              selectors: [
                "*",
                "::before",
                "::after"
              ]
            });
            for (let universal of universals) {
              universalRule.append(universal.nodes);
              if (!universalRule.parent) {
                universal.before(universalRule);
              }
              if (!universalRule.source) {
                universalRule.source = universal.source;
              }
              universal.remove();
            }
            let backdropRule = universalRule.clone({
              selectors: [
                "::backdrop"
              ]
            });
            universalRule.after(backdropRule);
          }
        };
      }
    }
  });

  // tailwindcss/lib/lib/collapseAdjacentRules.js
  var require_collapseAdjacentRules = __commonJS({
    "tailwindcss/lib/lib/collapseAdjacentRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return collapseAdjacentRules;
        }
      });
      var comparisonMap = {
        atrule: [
          "name",
          "params"
        ],
        rule: [
          "selector"
        ]
      };
      var types = new Set(Object.keys(comparisonMap));
      function collapseAdjacentRules() {
        function collapseRulesIn(root) {
          let currentRule = null;
          root.each((node) => {
            if (!types.has(node.type)) {
              currentRule = null;
              return;
            }
            if (currentRule === null) {
              currentRule = node;
              return;
            }
            let properties = comparisonMap[node.type];
            var _node_property, _currentRule_property;
            if (node.type === "atrule" && node.name === "font-face") {
              currentRule = node;
            } else if (properties.every((property) => ((_node_property = node[property]) !== null && _node_property !== void 0 ? _node_property : "").replace(/\s+/g, " ") === ((_currentRule_property = currentRule[property]) !== null && _currentRule_property !== void 0 ? _currentRule_property : "").replace(/\s+/g, " "))) {
              if (node.nodes) {
                currentRule.append(node.nodes);
              }
              node.remove();
            } else {
              currentRule = node;
            }
          });
          root.each((node) => {
            if (node.type === "atrule") {
              collapseRulesIn(node);
            }
          });
        }
        return (root) => {
          collapseRulesIn(root);
        };
      }
    }
  });

  // tailwindcss/lib/lib/collapseDuplicateDeclarations.js
  var require_collapseDuplicateDeclarations = __commonJS({
    "tailwindcss/lib/lib/collapseDuplicateDeclarations.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return collapseDuplicateDeclarations;
        }
      });
      function collapseDuplicateDeclarations() {
        return (root) => {
          root.walkRules((node) => {
            let seen = /* @__PURE__ */ new Map();
            let droppable = /* @__PURE__ */ new Set([]);
            let byProperty = /* @__PURE__ */ new Map();
            node.walkDecls((decl) => {
              if (decl.parent !== node) {
                return;
              }
              if (seen.has(decl.prop)) {
                if (seen.get(decl.prop).value === decl.value) {
                  droppable.add(seen.get(decl.prop));
                  seen.set(decl.prop, decl);
                  return;
                }
                if (!byProperty.has(decl.prop)) {
                  byProperty.set(decl.prop, /* @__PURE__ */ new Set());
                }
                byProperty.get(decl.prop).add(seen.get(decl.prop));
                byProperty.get(decl.prop).add(decl);
              }
              seen.set(decl.prop, decl);
            });
            for (let decl of droppable) {
              decl.remove();
            }
            for (let declarations of byProperty.values()) {
              let byUnit = /* @__PURE__ */ new Map();
              for (let decl of declarations) {
                let unit = resolveUnit(decl.value);
                if (unit === null) {
                  continue;
                }
                if (!byUnit.has(unit)) {
                  byUnit.set(unit, /* @__PURE__ */ new Set());
                }
                byUnit.get(unit).add(decl);
              }
              for (let declarations2 of byUnit.values()) {
                let removableDeclarations = Array.from(declarations2).slice(0, -1);
                for (let decl of removableDeclarations) {
                  decl.remove();
                }
              }
            }
          });
        };
      }
      var UNITLESS_NUMBER = Symbol("unitless-number");
      function resolveUnit(input) {
        let result = /^-?\d*.?\d+([\w%]+)?$/g.exec(input);
        if (result) {
          var _result_;
          return (_result_ = result[1]) !== null && _result_ !== void 0 ? _result_ : UNITLESS_NUMBER;
        }
        return null;
      }
    }
  });

  // tailwindcss/lib/lib/partitionApplyAtRules.js
  var require_partitionApplyAtRules = __commonJS({
    "tailwindcss/lib/lib/partitionApplyAtRules.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return expandApplyAtRules;
        }
      });
      function partitionRules(root) {
        if (!root.walkAtRules) return;
        let applyParents = /* @__PURE__ */ new Set();
        root.walkAtRules("apply", (rule) => {
          applyParents.add(rule.parent);
        });
        if (applyParents.size === 0) {
          return;
        }
        for (let rule of applyParents) {
          let nodeGroups = [];
          let lastGroup = [];
          for (let node of rule.nodes) {
            if (node.type === "atrule" && node.name === "apply") {
              if (lastGroup.length > 0) {
                nodeGroups.push(lastGroup);
                lastGroup = [];
              }
              nodeGroups.push([
                node
              ]);
            } else {
              lastGroup.push(node);
            }
          }
          if (lastGroup.length > 0) {
            nodeGroups.push(lastGroup);
          }
          if (nodeGroups.length === 1) {
            continue;
          }
          for (let group of [
            ...nodeGroups
          ].reverse()) {
            let clone = rule.clone({
              nodes: []
            });
            clone.append(group);
            rule.after(clone);
          }
          rule.remove();
        }
      }
      function expandApplyAtRules() {
        return (root) => {
          partitionRules(root);
        };
      }
    }
  });

  // tailwindcss/lib/processTailwindFeatures.js
  var require_processTailwindFeatures = __commonJS({
    "tailwindcss/lib/processTailwindFeatures.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return processTailwindFeatures;
        }
      });
      var _normalizeTailwindDirectives = /* @__PURE__ */ _interop_require_default(require_normalizeTailwindDirectives());
      var _expandTailwindAtRules = /* @__PURE__ */ _interop_require_default(require_expandTailwindAtRules());
      var _expandApplyAtRules = /* @__PURE__ */ _interop_require_default(require_expandApplyAtRules());
      var _evaluateTailwindFunctions = /* @__PURE__ */ _interop_require_default(require_evaluateTailwindFunctions());
      var _substituteScreenAtRules = /* @__PURE__ */ _interop_require_default(require_substituteScreenAtRules());
      var _resolveDefaultsAtRules = /* @__PURE__ */ _interop_require_default(require_resolveDefaultsAtRules());
      var _collapseAdjacentRules = /* @__PURE__ */ _interop_require_default(require_collapseAdjacentRules());
      var _collapseDuplicateDeclarations = /* @__PURE__ */ _interop_require_default(require_collapseDuplicateDeclarations());
      var _partitionApplyAtRules = /* @__PURE__ */ _interop_require_default(require_partitionApplyAtRules());
      var _setupContextUtils = require_setupContextUtils();
      var _featureFlags = require_featureFlags();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function processTailwindFeatures(setupContext) {
        return async function(root, result) {
          let { tailwindDirectives, applyDirectives } = (0, _normalizeTailwindDirectives.default)(root);
          (0, _partitionApplyAtRules.default)()(root, result);
          let context2 = setupContext({
            tailwindDirectives,
            applyDirectives,
            registerDependency(dependency) {
              result.messages.push({
                plugin: "tailwindcss",
                parent: result.opts.from,
                ...dependency
              });
            },
            createContext(tailwindConfig, changedContent) {
              return (0, _setupContextUtils.createContext)(tailwindConfig, changedContent, root);
            }
          })(root, result);
          if (context2.tailwindConfig.separator === "-") {
            throw new Error("The '-' character cannot be used as a custom separator in JIT mode due to parsing ambiguity. Please use another character like '_' instead.");
          }
          (0, _featureFlags.issueFlagNotices)(context2.tailwindConfig);
          await (0, _expandTailwindAtRules.default)(context2)(root, result);
          (0, _partitionApplyAtRules.default)()(root, result);
          (0, _expandApplyAtRules.default)(context2)(root, result);
          (0, _evaluateTailwindFunctions.default)(context2)(root, result);
          (0, _substituteScreenAtRules.default)(context2)(root, result);
          (0, _resolveDefaultsAtRules.default)(context2)(root, result);
          (0, _collapseAdjacentRules.default)(context2)(root, result);
          (0, _collapseDuplicateDeclarations.default)(context2)(root, result);
        };
      }
    }
  });

  // tailwindcss/lib/corePluginList.js
  var require_corePluginList = __commonJS({
    "tailwindcss/lib/corePluginList.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _default = [
        "preflight",
        "container",
        "accessibility",
        "pointerEvents",
        "visibility",
        "position",
        "inset",
        "isolation",
        "zIndex",
        "order",
        "gridColumn",
        "gridColumnStart",
        "gridColumnEnd",
        "gridRow",
        "gridRowStart",
        "gridRowEnd",
        "float",
        "clear",
        "margin",
        "boxSizing",
        "lineClamp",
        "display",
        "aspectRatio",
        "size",
        "height",
        "maxHeight",
        "minHeight",
        "width",
        "minWidth",
        "maxWidth",
        "flex",
        "flexShrink",
        "flexGrow",
        "flexBasis",
        "tableLayout",
        "captionSide",
        "borderCollapse",
        "borderSpacing",
        "transformOrigin",
        "translate",
        "rotate",
        "skew",
        "scale",
        "transform",
        "animation",
        "cursor",
        "touchAction",
        "userSelect",
        "resize",
        "scrollSnapType",
        "scrollSnapAlign",
        "scrollSnapStop",
        "scrollMargin",
        "scrollPadding",
        "listStylePosition",
        "listStyleType",
        "listStyleImage",
        "appearance",
        "columns",
        "breakBefore",
        "breakInside",
        "breakAfter",
        "gridAutoColumns",
        "gridAutoFlow",
        "gridAutoRows",
        "gridTemplateColumns",
        "gridTemplateRows",
        "flexDirection",
        "flexWrap",
        "placeContent",
        "placeItems",
        "alignContent",
        "alignItems",
        "justifyContent",
        "justifyItems",
        "gap",
        "space",
        "divideWidth",
        "divideStyle",
        "divideColor",
        "divideOpacity",
        "placeSelf",
        "alignSelf",
        "justifySelf",
        "overflow",
        "overscrollBehavior",
        "scrollBehavior",
        "textOverflow",
        "hyphens",
        "whitespace",
        "textWrap",
        "wordBreak",
        "borderRadius",
        "borderWidth",
        "borderStyle",
        "borderColor",
        "borderOpacity",
        "backgroundColor",
        "backgroundOpacity",
        "backgroundImage",
        "gradientColorStops",
        "boxDecorationBreak",
        "backgroundSize",
        "backgroundAttachment",
        "backgroundClip",
        "backgroundPosition",
        "backgroundRepeat",
        "backgroundOrigin",
        "fill",
        "stroke",
        "strokeWidth",
        "objectFit",
        "objectPosition",
        "padding",
        "textAlign",
        "textIndent",
        "verticalAlign",
        "fontFamily",
        "fontSize",
        "fontWeight",
        "textTransform",
        "fontStyle",
        "fontVariantNumeric",
        "lineHeight",
        "letterSpacing",
        "textColor",
        "textOpacity",
        "textDecoration",
        "textDecorationColor",
        "textDecorationStyle",
        "textDecorationThickness",
        "textUnderlineOffset",
        "fontSmoothing",
        "placeholderColor",
        "placeholderOpacity",
        "caretColor",
        "accentColor",
        "opacity",
        "backgroundBlendMode",
        "mixBlendMode",
        "boxShadow",
        "boxShadowColor",
        "outlineStyle",
        "outlineWidth",
        "outlineOffset",
        "outlineColor",
        "ringWidth",
        "ringColor",
        "ringOpacity",
        "ringOffsetWidth",
        "ringOffsetColor",
        "blur",
        "brightness",
        "contrast",
        "dropShadow",
        "grayscale",
        "hueRotate",
        "invert",
        "saturate",
        "sepia",
        "filter",
        "backdropBlur",
        "backdropBrightness",
        "backdropContrast",
        "backdropGrayscale",
        "backdropHueRotate",
        "backdropInvert",
        "backdropOpacity",
        "backdropSaturate",
        "backdropSepia",
        "backdropFilter",
        "transitionProperty",
        "transitionDelay",
        "transitionDuration",
        "transitionTimingFunction",
        "willChange",
        "contain",
        "content",
        "forcedColorAdjust"
      ];
    }
  });

  // tailwindcss/lib/util/configurePlugins.js
  var require_configurePlugins = __commonJS({
    "tailwindcss/lib/util/configurePlugins.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      function _default(pluginConfig, plugins) {
        if (pluginConfig === void 0) {
          return plugins;
        }
        const pluginNames = Array.isArray(pluginConfig) ? pluginConfig : [
          ...new Set(plugins.filter((pluginName) => {
            return pluginConfig !== false && pluginConfig[pluginName] !== false;
          }).concat(Object.keys(pluginConfig).filter((pluginName) => {
            return pluginConfig[pluginName] !== false;
          })))
        ];
        return pluginNames;
      }
    }
  });

  // tailwindcss/lib/public/colors.js
  var require_colors = __commonJS({
    "tailwindcss/lib/public/colors.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _log = /* @__PURE__ */ _interop_require_default(require_log());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function warn({ version, from, to }) {
        _log.default.warn(`${from}-color-renamed`, [
          `As of Tailwind CSS ${version}, \`${from}\` has been renamed to \`${to}\`.`,
          "Update your configuration file to silence this warning."
        ]);
      }
      var _default = {
        inherit: "inherit",
        current: "currentColor",
        transparent: "transparent",
        black: "#000",
        white: "#fff",
        slate: {
          50: "#f8fafc",
          100: "#f1f5f9",
          200: "#e2e8f0",
          300: "#cbd5e1",
          400: "#94a3b8",
          500: "#64748b",
          600: "#475569",
          700: "#334155",
          800: "#1e293b",
          900: "#0f172a",
          950: "#020617"
        },
        gray: {
          50: "#f9fafb",
          100: "#f3f4f6",
          200: "#e5e7eb",
          300: "#d1d5db",
          400: "#9ca3af",
          500: "#6b7280",
          600: "#4b5563",
          700: "#374151",
          800: "#1f2937",
          900: "#111827",
          950: "#030712"
        },
        zinc: {
          50: "#fafafa",
          100: "#f4f4f5",
          200: "#e4e4e7",
          300: "#d4d4d8",
          400: "#a1a1aa",
          500: "#71717a",
          600: "#52525b",
          700: "#3f3f46",
          800: "#27272a",
          900: "#18181b",
          950: "#09090b"
        },
        neutral: {
          50: "#fafafa",
          100: "#f5f5f5",
          200: "#e5e5e5",
          300: "#d4d4d4",
          400: "#a3a3a3",
          500: "#737373",
          600: "#525252",
          700: "#404040",
          800: "#262626",
          900: "#171717",
          950: "#0a0a0a"
        },
        stone: {
          50: "#fafaf9",
          100: "#f5f5f4",
          200: "#e7e5e4",
          300: "#d6d3d1",
          400: "#a8a29e",
          500: "#78716c",
          600: "#57534e",
          700: "#44403c",
          800: "#292524",
          900: "#1c1917",
          950: "#0c0a09"
        },
        red: {
          50: "#fef2f2",
          100: "#fee2e2",
          200: "#fecaca",
          300: "#fca5a5",
          400: "#f87171",
          500: "#ef4444",
          600: "#dc2626",
          700: "#b91c1c",
          800: "#991b1b",
          900: "#7f1d1d",
          950: "#450a0a"
        },
        orange: {
          50: "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          800: "#9a3412",
          900: "#7c2d12",
          950: "#431407"
        },
        amber: {
          50: "#fffbeb",
          100: "#fef3c7",
          200: "#fde68a",
          300: "#fcd34d",
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
          700: "#b45309",
          800: "#92400e",
          900: "#78350f",
          950: "#451a03"
        },
        yellow: {
          50: "#fefce8",
          100: "#fef9c3",
          200: "#fef08a",
          300: "#fde047",
          400: "#facc15",
          500: "#eab308",
          600: "#ca8a04",
          700: "#a16207",
          800: "#854d0e",
          900: "#713f12",
          950: "#422006"
        },
        lime: {
          50: "#f7fee7",
          100: "#ecfccb",
          200: "#d9f99d",
          300: "#bef264",
          400: "#a3e635",
          500: "#84cc16",
          600: "#65a30d",
          700: "#4d7c0f",
          800: "#3f6212",
          900: "#365314",
          950: "#1a2e05"
        },
        green: {
          50: "#f0fdf4",
          100: "#dcfce7",
          200: "#bbf7d0",
          300: "#86efac",
          400: "#4ade80",
          500: "#22c55e",
          600: "#16a34a",
          700: "#15803d",
          800: "#166534",
          900: "#14532d",
          950: "#052e16"
        },
        emerald: {
          50: "#ecfdf5",
          100: "#d1fae5",
          200: "#a7f3d0",
          300: "#6ee7b7",
          400: "#34d399",
          500: "#10b981",
          600: "#059669",
          700: "#047857",
          800: "#065f46",
          900: "#064e3b",
          950: "#022c22"
        },
        teal: {
          50: "#f0fdfa",
          100: "#ccfbf1",
          200: "#99f6e4",
          300: "#5eead4",
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#0d9488",
          700: "#0f766e",
          800: "#115e59",
          900: "#134e4a",
          950: "#042f2e"
        },
        cyan: {
          50: "#ecfeff",
          100: "#cffafe",
          200: "#a5f3fc",
          300: "#67e8f9",
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
          700: "#0e7490",
          800: "#155e75",
          900: "#164e63",
          950: "#083344"
        },
        sky: {
          50: "#f0f9ff",
          100: "#e0f2fe",
          200: "#bae6fd",
          300: "#7dd3fc",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
          800: "#075985",
          900: "#0c4a6e",
          950: "#082f49"
        },
        blue: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40af",
          900: "#1e3a8a",
          950: "#172554"
        },
        indigo: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
          950: "#1e1b4b"
        },
        violet: {
          50: "#f5f3ff",
          100: "#ede9fe",
          200: "#ddd6fe",
          300: "#c4b5fd",
          400: "#a78bfa",
          500: "#8b5cf6",
          600: "#7c3aed",
          700: "#6d28d9",
          800: "#5b21b6",
          900: "#4c1d95",
          950: "#2e1065"
        },
        purple: {
          50: "#faf5ff",
          100: "#f3e8ff",
          200: "#e9d5ff",
          300: "#d8b4fe",
          400: "#c084fc",
          500: "#a855f7",
          600: "#9333ea",
          700: "#7e22ce",
          800: "#6b21a8",
          900: "#581c87",
          950: "#3b0764"
        },
        fuchsia: {
          50: "#fdf4ff",
          100: "#fae8ff",
          200: "#f5d0fe",
          300: "#f0abfc",
          400: "#e879f9",
          500: "#d946ef",
          600: "#c026d3",
          700: "#a21caf",
          800: "#86198f",
          900: "#701a75",
          950: "#4a044e"
        },
        pink: {
          50: "#fdf2f8",
          100: "#fce7f3",
          200: "#fbcfe8",
          300: "#f9a8d4",
          400: "#f472b6",
          500: "#ec4899",
          600: "#db2777",
          700: "#be185d",
          800: "#9d174d",
          900: "#831843",
          950: "#500724"
        },
        rose: {
          50: "#fff1f2",
          100: "#ffe4e6",
          200: "#fecdd3",
          300: "#fda4af",
          400: "#fb7185",
          500: "#f43f5e",
          600: "#e11d48",
          700: "#be123c",
          800: "#9f1239",
          900: "#881337",
          950: "#4c0519"
        },
        get lightBlue() {
          warn({
            version: "v2.2",
            from: "lightBlue",
            to: "sky"
          });
          return this.sky;
        },
        get warmGray() {
          warn({
            version: "v3.0",
            from: "warmGray",
            to: "stone"
          });
          return this.stone;
        },
        get trueGray() {
          warn({
            version: "v3.0",
            from: "trueGray",
            to: "neutral"
          });
          return this.neutral;
        },
        get coolGray() {
          warn({
            version: "v3.0",
            from: "coolGray",
            to: "gray"
          });
          return this.gray;
        },
        get blueGray() {
          warn({
            version: "v3.0",
            from: "blueGray",
            to: "slate"
          });
          return this.slate;
        }
      };
    }
  });

  // tailwindcss/lib/util/defaults.js
  var require_defaults = __commonJS({
    "tailwindcss/lib/util/defaults.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "defaults", {
        enumerable: true,
        get: function() {
          return defaults;
        }
      });
      function defaults(target, ...sources) {
        for (let source of sources) {
          for (let k in source) {
            var _target_hasOwnProperty;
            if (!(target === null || target === void 0 ? void 0 : (_target_hasOwnProperty = target.hasOwnProperty) === null || _target_hasOwnProperty === void 0 ? void 0 : _target_hasOwnProperty.call(target, k))) {
              target[k] = source[k];
            }
          }
          for (let k of Object.getOwnPropertySymbols(source)) {
            var _target_hasOwnProperty1;
            if (!(target === null || target === void 0 ? void 0 : (_target_hasOwnProperty1 = target.hasOwnProperty) === null || _target_hasOwnProperty1 === void 0 ? void 0 : _target_hasOwnProperty1.call(target, k))) {
              target[k] = source[k];
            }
          }
        }
        return target;
      }
    }
  });

  // tailwindcss/lib/util/normalizeConfig.js
  var require_normalizeConfig = __commonJS({
    "tailwindcss/lib/util/normalizeConfig.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "normalizeConfig", {
        enumerable: true,
        get: function() {
          return normalizeConfig;
        }
      });
      var _featureFlags = require_featureFlags();
      var _log = /* @__PURE__ */ _interop_require_wildcard(require_log());
      function _getRequireWildcardCache(nodeInterop) {
        if (typeof WeakMap !== "function") return null;
        var cacheBabelInterop = /* @__PURE__ */ new WeakMap();
        var cacheNodeInterop = /* @__PURE__ */ new WeakMap();
        return (_getRequireWildcardCache = function(nodeInterop2) {
          return nodeInterop2 ? cacheNodeInterop : cacheBabelInterop;
        })(nodeInterop);
      }
      function _interop_require_wildcard(obj, nodeInterop) {
        if (!nodeInterop && obj && obj.__esModule) {
          return obj;
        }
        if (obj === null || typeof obj !== "object" && typeof obj !== "function") {
          return {
            default: obj
          };
        }
        var cache = _getRequireWildcardCache(nodeInterop);
        if (cache && cache.has(obj)) {
          return cache.get(obj);
        }
        var newObj = {};
        var hasPropertyDescriptor = Object.defineProperty && Object.getOwnPropertyDescriptor;
        for (var key in obj) {
          if (key !== "default" && Object.prototype.hasOwnProperty.call(obj, key)) {
            var desc = hasPropertyDescriptor ? Object.getOwnPropertyDescriptor(obj, key) : null;
            if (desc && (desc.get || desc.set)) {
              Object.defineProperty(newObj, key, desc);
            } else {
              newObj[key] = obj[key];
            }
          }
        }
        newObj.default = obj;
        if (cache) {
          cache.set(obj, newObj);
        }
        return newObj;
      }
      function normalizeConfig(config) {
        let valid = (() => {
          if (config.purge) {
            return false;
          }
          if (!config.content) {
            return false;
          }
          if (!Array.isArray(config.content) && !(typeof config.content === "object" && config.content !== null)) {
            return false;
          }
          if (Array.isArray(config.content)) {
            return config.content.every((path) => {
              if (typeof path === "string") return true;
              if (typeof (path === null || path === void 0 ? void 0 : path.raw) !== "string") return false;
              if ((path === null || path === void 0 ? void 0 : path.extension) && typeof (path === null || path === void 0 ? void 0 : path.extension) !== "string") {
                return false;
              }
              return true;
            });
          }
          if (typeof config.content === "object" && config.content !== null) {
            if (Object.keys(config.content).some((key) => ![
              "files",
              "relative",
              "extract",
              "transform"
            ].includes(key))) {
              return false;
            }
            if (Array.isArray(config.content.files)) {
              if (!config.content.files.every((path) => {
                if (typeof path === "string") return true;
                if (typeof (path === null || path === void 0 ? void 0 : path.raw) !== "string") return false;
                if ((path === null || path === void 0 ? void 0 : path.extension) && typeof (path === null || path === void 0 ? void 0 : path.extension) !== "string") {
                  return false;
                }
                return true;
              })) {
                return false;
              }
              if (typeof config.content.extract === "object") {
                for (let value of Object.values(config.content.extract)) {
                  if (typeof value !== "function") {
                    return false;
                  }
                }
              } else if (!(config.content.extract === void 0 || typeof config.content.extract === "function")) {
                return false;
              }
              if (typeof config.content.transform === "object") {
                for (let value of Object.values(config.content.transform)) {
                  if (typeof value !== "function") {
                    return false;
                  }
                }
              } else if (!(config.content.transform === void 0 || typeof config.content.transform === "function")) {
                return false;
              }
              if (typeof config.content.relative !== "boolean" && typeof config.content.relative !== "undefined") {
                return false;
              }
            }
            return true;
          }
          return false;
        })();
        if (!valid) {
          _log.default.warn("purge-deprecation", [
            "The `purge`/`content` options have changed in Tailwind CSS v3.0.",
            "Update your configuration file to eliminate this warning.",
            "https://tailwindcss.com/docs/upgrade-guide#configure-content-sources"
          ]);
        }
        config.safelist = (() => {
          var _purge_options;
          let { content, purge, safelist } = config;
          if (Array.isArray(safelist)) return safelist;
          if (Array.isArray(content === null || content === void 0 ? void 0 : content.safelist)) return content.safelist;
          if (Array.isArray(purge === null || purge === void 0 ? void 0 : purge.safelist)) return purge.safelist;
          if (Array.isArray(purge === null || purge === void 0 ? void 0 : (_purge_options = purge.options) === null || _purge_options === void 0 ? void 0 : _purge_options.safelist)) return purge.options.safelist;
          return [];
        })();
        config.blocklist = (() => {
          let { blocklist } = config;
          if (Array.isArray(blocklist)) {
            if (blocklist.every((item) => typeof item === "string")) {
              return blocklist;
            }
            _log.default.warn("blocklist-invalid", [
              "The `blocklist` option must be an array of strings.",
              "https://tailwindcss.com/docs/content-configuration#discarding-classes"
            ]);
          }
          return [];
        })();
        if (typeof config.prefix === "function") {
          _log.default.warn("prefix-function", [
            "As of Tailwind CSS v3.0, `prefix` cannot be a function.",
            "Update `prefix` in your configuration to be a string to eliminate this warning.",
            "https://tailwindcss.com/docs/upgrade-guide#prefix-cannot-be-a-function"
          ]);
          config.prefix = "";
        } else {
          var _config_prefix;
          config.prefix = (_config_prefix = config.prefix) !== null && _config_prefix !== void 0 ? _config_prefix : "";
        }
        config.content = {
          relative: (() => {
            let { content } = config;
            if (content === null || content === void 0 ? void 0 : content.relative) {
              return content.relative;
            }
            return (0, _featureFlags.flagEnabled)(config, "relativeContentPathsByDefault");
          })(),
          files: (() => {
            let { content, purge } = config;
            if (Array.isArray(purge)) return purge;
            if (Array.isArray(purge === null || purge === void 0 ? void 0 : purge.content)) return purge.content;
            if (Array.isArray(content)) return content;
            if (Array.isArray(content === null || content === void 0 ? void 0 : content.content)) return content.content;
            if (Array.isArray(content === null || content === void 0 ? void 0 : content.files)) return content.files;
            return [];
          })(),
          extract: (() => {
            let extract = (() => {
              var _config_purge, _config_content, _config_purge1, _config_purge_extract, _config_content1, _config_content_extract, _config_purge2, _config_purge_options, _config_content2, _config_content_options;
              if ((_config_purge = config.purge) === null || _config_purge === void 0 ? void 0 : _config_purge.extract) return config.purge.extract;
              if ((_config_content = config.content) === null || _config_content === void 0 ? void 0 : _config_content.extract) return config.content.extract;
              if ((_config_purge1 = config.purge) === null || _config_purge1 === void 0 ? void 0 : (_config_purge_extract = _config_purge1.extract) === null || _config_purge_extract === void 0 ? void 0 : _config_purge_extract.DEFAULT) return config.purge.extract.DEFAULT;
              if ((_config_content1 = config.content) === null || _config_content1 === void 0 ? void 0 : (_config_content_extract = _config_content1.extract) === null || _config_content_extract === void 0 ? void 0 : _config_content_extract.DEFAULT) return config.content.extract.DEFAULT;
              if ((_config_purge2 = config.purge) === null || _config_purge2 === void 0 ? void 0 : (_config_purge_options = _config_purge2.options) === null || _config_purge_options === void 0 ? void 0 : _config_purge_options.extractors) return config.purge.options.extractors;
              if ((_config_content2 = config.content) === null || _config_content2 === void 0 ? void 0 : (_config_content_options = _config_content2.options) === null || _config_content_options === void 0 ? void 0 : _config_content_options.extractors) return config.content.options.extractors;
              return {};
            })();
            let extractors = {};
            let defaultExtractor = (() => {
              var _config_purge, _config_purge_options, _config_content, _config_content_options;
              if ((_config_purge = config.purge) === null || _config_purge === void 0 ? void 0 : (_config_purge_options = _config_purge.options) === null || _config_purge_options === void 0 ? void 0 : _config_purge_options.defaultExtractor) {
                return config.purge.options.defaultExtractor;
              }
              if ((_config_content = config.content) === null || _config_content === void 0 ? void 0 : (_config_content_options = _config_content.options) === null || _config_content_options === void 0 ? void 0 : _config_content_options.defaultExtractor) {
                return config.content.options.defaultExtractor;
              }
              return void 0;
            })();
            if (defaultExtractor !== void 0) {
              extractors.DEFAULT = defaultExtractor;
            }
            if (typeof extract === "function") {
              extractors.DEFAULT = extract;
            } else if (Array.isArray(extract)) {
              for (let { extensions, extractor } of extract !== null && extract !== void 0 ? extract : []) {
                for (let extension of extensions) {
                  extractors[extension] = extractor;
                }
              }
            } else if (typeof extract === "object" && extract !== null) {
              Object.assign(extractors, extract);
            }
            return extractors;
          })(),
          transform: (() => {
            let transform = (() => {
              var _config_purge, _config_content, _config_purge1, _config_purge_transform, _config_content1, _config_content_transform;
              if ((_config_purge = config.purge) === null || _config_purge === void 0 ? void 0 : _config_purge.transform) return config.purge.transform;
              if ((_config_content = config.content) === null || _config_content === void 0 ? void 0 : _config_content.transform) return config.content.transform;
              if ((_config_purge1 = config.purge) === null || _config_purge1 === void 0 ? void 0 : (_config_purge_transform = _config_purge1.transform) === null || _config_purge_transform === void 0 ? void 0 : _config_purge_transform.DEFAULT) return config.purge.transform.DEFAULT;
              if ((_config_content1 = config.content) === null || _config_content1 === void 0 ? void 0 : (_config_content_transform = _config_content1.transform) === null || _config_content_transform === void 0 ? void 0 : _config_content_transform.DEFAULT) return config.content.transform.DEFAULT;
              return {};
            })();
            let transformers = {};
            if (typeof transform === "function") {
              transformers.DEFAULT = transform;
            } else if (typeof transform === "object" && transform !== null) {
              Object.assign(transformers, transform);
            }
            return transformers;
          })()
        };
        for (let file of config.content.files) {
          if (typeof file === "string" && /{([^,]*?)}/g.test(file)) {
            _log.default.warn("invalid-glob-braces", [
              `The glob pattern ${(0, _log.dim)(file)} in your Tailwind CSS configuration is invalid.`,
              `Update it to ${(0, _log.dim)(file.replace(/{([^,]*?)}/g, "$1"))} to silence this warning.`
            ]);
            break;
          }
        }
        return config;
      }
    }
  });

  // tailwindcss/lib/util/cloneDeep.js
  var require_cloneDeep = __commonJS({
    "tailwindcss/lib/util/cloneDeep.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "cloneDeep", {
        enumerable: true,
        get: function() {
          return cloneDeep;
        }
      });
      function cloneDeep(value) {
        if (Array.isArray(value)) {
          return value.map((child) => cloneDeep(child));
        }
        if (typeof value === "object" && value !== null) {
          return Object.fromEntries(Object.entries(value).map(([k, v]) => [
            k,
            cloneDeep(v)
          ]));
        }
        return value;
      }
    }
  });

  // tailwindcss/lib/util/resolveConfig.js
  var require_resolveConfig = __commonJS({
    "tailwindcss/lib/util/resolveConfig.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return resolveConfig2;
        }
      });
      var _negateValue = /* @__PURE__ */ _interop_require_default(require_negateValue());
      var _corePluginList = /* @__PURE__ */ _interop_require_default(require_corePluginList());
      var _configurePlugins = /* @__PURE__ */ _interop_require_default(require_configurePlugins());
      var _colors = /* @__PURE__ */ _interop_require_default(require_colors());
      var _defaults = require_defaults();
      var _toPath = require_toPath();
      var _normalizeConfig = require_normalizeConfig();
      var _isPlainObject = /* @__PURE__ */ _interop_require_default(require_isPlainObject());
      var _cloneDeep = require_cloneDeep();
      var _pluginUtils = require_pluginUtils();
      var _withAlphaVariable = require_withAlphaVariable();
      var _toColorValue = /* @__PURE__ */ _interop_require_default(require_toColorValue());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function isFunction(input) {
        return typeof input === "function";
      }
      function mergeWith(target, ...sources) {
        let customizer = sources.pop();
        for (let source of sources) {
          for (let k in source) {
            let merged = customizer(target[k], source[k]);
            if (merged === void 0) {
              if ((0, _isPlainObject.default)(target[k]) && (0, _isPlainObject.default)(source[k])) {
                target[k] = mergeWith({}, target[k], source[k], customizer);
              } else {
                target[k] = source[k];
              }
            } else {
              target[k] = merged;
            }
          }
        }
        return target;
      }
      var configUtils = {
        colors: _colors.default,
        negative(scale) {
          return Object.keys(scale).filter((key) => scale[key] !== "0").reduce((negativeScale, key) => {
            let negativeValue = (0, _negateValue.default)(scale[key]);
            if (negativeValue !== void 0) {
              negativeScale[`-${key}`] = negativeValue;
            }
            return negativeScale;
          }, {});
        },
        breakpoints(screens) {
          return Object.keys(screens).filter((key) => typeof screens[key] === "string").reduce((breakpoints, key) => ({
            ...breakpoints,
            [`screen-${key}`]: screens[key]
          }), {});
        }
      };
      function value(valueToResolve, ...args) {
        return isFunction(valueToResolve) ? valueToResolve(...args) : valueToResolve;
      }
      function collectExtends(items) {
        return items.reduce((merged, { extend }) => {
          return mergeWith(merged, extend, (mergedValue, extendValue) => {
            if (mergedValue === void 0) {
              return [
                extendValue
              ];
            }
            if (Array.isArray(mergedValue)) {
              return [
                extendValue,
                ...mergedValue
              ];
            }
            return [
              extendValue,
              mergedValue
            ];
          });
        }, {});
      }
      function mergeThemes(themes) {
        return {
          ...themes.reduce((merged, theme) => (0, _defaults.defaults)(merged, theme), {}),
          // In order to resolve n config objects, we combine all of their `extend` properties
          // into arrays instead of objects so they aren't overridden.
          extend: collectExtends(themes)
        };
      }
      function mergeExtensionCustomizer(merged, value2) {
        if (Array.isArray(merged) && (0, _isPlainObject.default)(merged[0])) {
          return merged.concat(value2);
        }
        if (Array.isArray(value2) && (0, _isPlainObject.default)(value2[0]) && (0, _isPlainObject.default)(merged)) {
          return [
            merged,
            ...value2
          ];
        }
        if (Array.isArray(value2)) {
          return value2;
        }
        return void 0;
      }
      function mergeExtensions({ extend, ...theme }) {
        return mergeWith(theme, extend, (themeValue, extensions) => {
          if (!isFunction(themeValue) && !extensions.some(isFunction)) {
            return mergeWith({}, themeValue, ...extensions, mergeExtensionCustomizer);
          }
          return (resolveThemePath, utils) => mergeWith({}, ...[
            themeValue,
            ...extensions
          ].map((e) => value(e, resolveThemePath, utils)), mergeExtensionCustomizer);
        });
      }
      function* toPaths(key) {
        let path = (0, _toPath.toPath)(key);
        if (path.length === 0) {
          return;
        }
        yield path;
        if (Array.isArray(key)) {
          return;
        }
        let pattern = /^(.*?)\s*\/\s*([^/]+)$/;
        let matches = key.match(pattern);
        if (matches !== null) {
          let [, prefix, alpha] = matches;
          let newPath = (0, _toPath.toPath)(prefix);
          newPath.alpha = alpha;
          yield newPath;
        }
      }
      function resolveFunctionKeys(object) {
        const resolvePath = (key, defaultValue) => {
          for (const path of toPaths(key)) {
            let index = 0;
            let val = object;
            while (val !== void 0 && val !== null && index < path.length) {
              val = val[path[index++]];
              let shouldResolveAsFn = isFunction(val) && (path.alpha === void 0 || index <= path.length - 1);
              val = shouldResolveAsFn ? val(resolvePath, configUtils) : val;
            }
            if (val !== void 0) {
              if (path.alpha !== void 0) {
                let normalized = (0, _pluginUtils.parseColorFormat)(val);
                return (0, _withAlphaVariable.withAlphaValue)(normalized, path.alpha, (0, _toColorValue.default)(normalized));
              }
              if ((0, _isPlainObject.default)(val)) {
                return (0, _cloneDeep.cloneDeep)(val);
              }
              return val;
            }
          }
          return defaultValue;
        };
        Object.assign(resolvePath, {
          theme: resolvePath,
          ...configUtils
        });
        return Object.keys(object).reduce((resolved, key) => {
          resolved[key] = isFunction(object[key]) ? object[key](resolvePath, configUtils) : object[key];
          return resolved;
        }, {});
      }
      function extractPluginConfigs(configs) {
        let allConfigs = [];
        configs.forEach((config) => {
          allConfigs = [
            ...allConfigs,
            config
          ];
          var _config_plugins;
          const plugins = (_config_plugins = config === null || config === void 0 ? void 0 : config.plugins) !== null && _config_plugins !== void 0 ? _config_plugins : [];
          if (plugins.length === 0) {
            return;
          }
          plugins.forEach((plugin2) => {
            if (plugin2.__isOptionsFunction) {
              plugin2 = plugin2();
            }
            var _plugin_config;
            allConfigs = [
              ...allConfigs,
              ...extractPluginConfigs([
                (_plugin_config = plugin2 === null || plugin2 === void 0 ? void 0 : plugin2.config) !== null && _plugin_config !== void 0 ? _plugin_config : {}
              ])
            ];
          });
        });
        return allConfigs;
      }
      function resolveCorePlugins(corePluginConfigs) {
        const result = [
          ...corePluginConfigs
        ].reduceRight((resolved, corePluginConfig) => {
          if (isFunction(corePluginConfig)) {
            return corePluginConfig({
              corePlugins: resolved
            });
          }
          return (0, _configurePlugins.default)(corePluginConfig, resolved);
        }, _corePluginList.default);
        return result;
      }
      function resolvePluginLists(pluginLists) {
        const result = [
          ...pluginLists
        ].reduceRight((resolved, pluginList) => {
          return [
            ...resolved,
            ...pluginList
          ];
        }, []);
        return result;
      }
      function resolveConfig2(configs) {
        let allConfigs = [
          ...extractPluginConfigs(configs),
          {
            prefix: "",
            important: false,
            separator: ":"
          }
        ];
        var _t_theme, _c_plugins;
        return (0, _normalizeConfig.normalizeConfig)((0, _defaults.defaults)({
          theme: resolveFunctionKeys(mergeExtensions(mergeThemes(allConfigs.map((t) => {
            return (_t_theme = t === null || t === void 0 ? void 0 : t.theme) !== null && _t_theme !== void 0 ? _t_theme : {};
          })))),
          corePlugins: resolveCorePlugins(allConfigs.map((c) => c.corePlugins)),
          plugins: resolvePluginLists(configs.map((c) => {
            return (_c_plugins = c === null || c === void 0 ? void 0 : c.plugins) !== null && _c_plugins !== void 0 ? _c_plugins : [];
          }))
        }, ...allConfigs));
      }
    }
  });

  // tailwindcss/stubs/config.full.js
  var require_config_full = __commonJS({
    "tailwindcss/stubs/config.full.js"(exports, module) {
      module.exports = {
        content: [],
        presets: [],
        darkMode: "media",
        // or 'class'
        theme: {
          accentColor: ({ theme }) => ({
            ...theme("colors"),
            auto: "auto"
          }),
          animation: {
            none: "none",
            spin: "spin 1s linear infinite",
            ping: "ping 1s cubic-bezier(0, 0, 0.2, 1) infinite",
            pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
            bounce: "bounce 1s infinite"
          },
          aria: {
            busy: 'busy="true"',
            checked: 'checked="true"',
            disabled: 'disabled="true"',
            expanded: 'expanded="true"',
            hidden: 'hidden="true"',
            pressed: 'pressed="true"',
            readonly: 'readonly="true"',
            required: 'required="true"',
            selected: 'selected="true"'
          },
          aspectRatio: {
            auto: "auto",
            square: "1 / 1",
            video: "16 / 9"
          },
          backdropBlur: ({ theme }) => theme("blur"),
          backdropBrightness: ({ theme }) => theme("brightness"),
          backdropContrast: ({ theme }) => theme("contrast"),
          backdropGrayscale: ({ theme }) => theme("grayscale"),
          backdropHueRotate: ({ theme }) => theme("hueRotate"),
          backdropInvert: ({ theme }) => theme("invert"),
          backdropOpacity: ({ theme }) => theme("opacity"),
          backdropSaturate: ({ theme }) => theme("saturate"),
          backdropSepia: ({ theme }) => theme("sepia"),
          backgroundColor: ({ theme }) => theme("colors"),
          backgroundImage: {
            none: "none",
            "gradient-to-t": "linear-gradient(to top, var(--tw-gradient-stops))",
            "gradient-to-tr": "linear-gradient(to top right, var(--tw-gradient-stops))",
            "gradient-to-r": "linear-gradient(to right, var(--tw-gradient-stops))",
            "gradient-to-br": "linear-gradient(to bottom right, var(--tw-gradient-stops))",
            "gradient-to-b": "linear-gradient(to bottom, var(--tw-gradient-stops))",
            "gradient-to-bl": "linear-gradient(to bottom left, var(--tw-gradient-stops))",
            "gradient-to-l": "linear-gradient(to left, var(--tw-gradient-stops))",
            "gradient-to-tl": "linear-gradient(to top left, var(--tw-gradient-stops))"
          },
          backgroundOpacity: ({ theme }) => theme("opacity"),
          backgroundPosition: {
            bottom: "bottom",
            center: "center",
            left: "left",
            "left-bottom": "left bottom",
            "left-top": "left top",
            right: "right",
            "right-bottom": "right bottom",
            "right-top": "right top",
            top: "top"
          },
          backgroundSize: {
            auto: "auto",
            cover: "cover",
            contain: "contain"
          },
          blur: {
            0: "0",
            none: "",
            sm: "4px",
            DEFAULT: "8px",
            md: "12px",
            lg: "16px",
            xl: "24px",
            "2xl": "40px",
            "3xl": "64px"
          },
          borderColor: ({ theme }) => ({
            ...theme("colors"),
            DEFAULT: theme("colors.gray.200", "currentColor")
          }),
          borderOpacity: ({ theme }) => theme("opacity"),
          borderRadius: {
            none: "0px",
            sm: "0.125rem",
            DEFAULT: "0.25rem",
            md: "0.375rem",
            lg: "0.5rem",
            xl: "0.75rem",
            "2xl": "1rem",
            "3xl": "1.5rem",
            full: "9999px"
          },
          borderSpacing: ({ theme }) => ({
            ...theme("spacing")
          }),
          borderWidth: {
            DEFAULT: "1px",
            0: "0px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          boxShadow: {
            sm: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
            DEFAULT: "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
            md: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
            lg: "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)",
            xl: "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)",
            "2xl": "0 25px 50px -12px rgb(0 0 0 / 0.25)",
            inner: "inset 0 2px 4px 0 rgb(0 0 0 / 0.05)",
            none: "none"
          },
          boxShadowColor: ({ theme }) => theme("colors"),
          brightness: {
            0: "0",
            50: ".5",
            75: ".75",
            90: ".9",
            95: ".95",
            100: "1",
            105: "1.05",
            110: "1.1",
            125: "1.25",
            150: "1.5",
            200: "2"
          },
          caretColor: ({ theme }) => theme("colors"),
          colors: ({ colors: colors2 }) => ({
            inherit: colors2.inherit,
            current: colors2.current,
            transparent: colors2.transparent,
            black: colors2.black,
            white: colors2.white,
            slate: colors2.slate,
            gray: colors2.gray,
            zinc: colors2.zinc,
            neutral: colors2.neutral,
            stone: colors2.stone,
            red: colors2.red,
            orange: colors2.orange,
            amber: colors2.amber,
            yellow: colors2.yellow,
            lime: colors2.lime,
            green: colors2.green,
            emerald: colors2.emerald,
            teal: colors2.teal,
            cyan: colors2.cyan,
            sky: colors2.sky,
            blue: colors2.blue,
            indigo: colors2.indigo,
            violet: colors2.violet,
            purple: colors2.purple,
            fuchsia: colors2.fuchsia,
            pink: colors2.pink,
            rose: colors2.rose
          }),
          columns: {
            auto: "auto",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12",
            "3xs": "16rem",
            "2xs": "18rem",
            xs: "20rem",
            sm: "24rem",
            md: "28rem",
            lg: "32rem",
            xl: "36rem",
            "2xl": "42rem",
            "3xl": "48rem",
            "4xl": "56rem",
            "5xl": "64rem",
            "6xl": "72rem",
            "7xl": "80rem"
          },
          container: {},
          content: {
            none: "none"
          },
          contrast: {
            0: "0",
            50: ".5",
            75: ".75",
            100: "1",
            125: "1.25",
            150: "1.5",
            200: "2"
          },
          cursor: {
            auto: "auto",
            default: "default",
            pointer: "pointer",
            wait: "wait",
            text: "text",
            move: "move",
            help: "help",
            "not-allowed": "not-allowed",
            none: "none",
            "context-menu": "context-menu",
            progress: "progress",
            cell: "cell",
            crosshair: "crosshair",
            "vertical-text": "vertical-text",
            alias: "alias",
            copy: "copy",
            "no-drop": "no-drop",
            grab: "grab",
            grabbing: "grabbing",
            "all-scroll": "all-scroll",
            "col-resize": "col-resize",
            "row-resize": "row-resize",
            "n-resize": "n-resize",
            "e-resize": "e-resize",
            "s-resize": "s-resize",
            "w-resize": "w-resize",
            "ne-resize": "ne-resize",
            "nw-resize": "nw-resize",
            "se-resize": "se-resize",
            "sw-resize": "sw-resize",
            "ew-resize": "ew-resize",
            "ns-resize": "ns-resize",
            "nesw-resize": "nesw-resize",
            "nwse-resize": "nwse-resize",
            "zoom-in": "zoom-in",
            "zoom-out": "zoom-out"
          },
          divideColor: ({ theme }) => theme("borderColor"),
          divideOpacity: ({ theme }) => theme("borderOpacity"),
          divideWidth: ({ theme }) => theme("borderWidth"),
          dropShadow: {
            sm: "0 1px 1px rgb(0 0 0 / 0.05)",
            DEFAULT: ["0 1px 2px rgb(0 0 0 / 0.1)", "0 1px 1px rgb(0 0 0 / 0.06)"],
            md: ["0 4px 3px rgb(0 0 0 / 0.07)", "0 2px 2px rgb(0 0 0 / 0.06)"],
            lg: ["0 10px 8px rgb(0 0 0 / 0.04)", "0 4px 3px rgb(0 0 0 / 0.1)"],
            xl: ["0 20px 13px rgb(0 0 0 / 0.03)", "0 8px 5px rgb(0 0 0 / 0.08)"],
            "2xl": "0 25px 25px rgb(0 0 0 / 0.15)",
            none: "0 0 #0000"
          },
          fill: ({ theme }) => ({
            none: "none",
            ...theme("colors")
          }),
          flex: {
            1: "1 1 0%",
            auto: "1 1 auto",
            initial: "0 1 auto",
            none: "none"
          },
          flexBasis: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            "1/5": "20%",
            "2/5": "40%",
            "3/5": "60%",
            "4/5": "80%",
            "1/6": "16.666667%",
            "2/6": "33.333333%",
            "3/6": "50%",
            "4/6": "66.666667%",
            "5/6": "83.333333%",
            "1/12": "8.333333%",
            "2/12": "16.666667%",
            "3/12": "25%",
            "4/12": "33.333333%",
            "5/12": "41.666667%",
            "6/12": "50%",
            "7/12": "58.333333%",
            "8/12": "66.666667%",
            "9/12": "75%",
            "10/12": "83.333333%",
            "11/12": "91.666667%",
            full: "100%"
          }),
          flexGrow: {
            0: "0",
            DEFAULT: "1"
          },
          flexShrink: {
            0: "0",
            DEFAULT: "1"
          },
          fontFamily: {
            sans: [
              "ui-sans-serif",
              "system-ui",
              "sans-serif",
              '"Apple Color Emoji"',
              '"Segoe UI Emoji"',
              '"Segoe UI Symbol"',
              '"Noto Color Emoji"'
            ],
            serif: ["ui-serif", "Georgia", "Cambria", '"Times New Roman"', "Times", "serif"],
            mono: [
              "ui-monospace",
              "SFMono-Regular",
              "Menlo",
              "Monaco",
              "Consolas",
              '"Liberation Mono"',
              '"Courier New"',
              "monospace"
            ]
          },
          fontSize: {
            xs: ["0.75rem", { lineHeight: "1rem" }],
            sm: ["0.875rem", { lineHeight: "1.25rem" }],
            base: ["1rem", { lineHeight: "1.5rem" }],
            lg: ["1.125rem", { lineHeight: "1.75rem" }],
            xl: ["1.25rem", { lineHeight: "1.75rem" }],
            "2xl": ["1.5rem", { lineHeight: "2rem" }],
            "3xl": ["1.875rem", { lineHeight: "2.25rem" }],
            "4xl": ["2.25rem", { lineHeight: "2.5rem" }],
            "5xl": ["3rem", { lineHeight: "1" }],
            "6xl": ["3.75rem", { lineHeight: "1" }],
            "7xl": ["4.5rem", { lineHeight: "1" }],
            "8xl": ["6rem", { lineHeight: "1" }],
            "9xl": ["8rem", { lineHeight: "1" }]
          },
          fontWeight: {
            thin: "100",
            extralight: "200",
            light: "300",
            normal: "400",
            medium: "500",
            semibold: "600",
            bold: "700",
            extrabold: "800",
            black: "900"
          },
          gap: ({ theme }) => theme("spacing"),
          gradientColorStops: ({ theme }) => theme("colors"),
          gradientColorStopPositions: {
            "0%": "0%",
            "5%": "5%",
            "10%": "10%",
            "15%": "15%",
            "20%": "20%",
            "25%": "25%",
            "30%": "30%",
            "35%": "35%",
            "40%": "40%",
            "45%": "45%",
            "50%": "50%",
            "55%": "55%",
            "60%": "60%",
            "65%": "65%",
            "70%": "70%",
            "75%": "75%",
            "80%": "80%",
            "85%": "85%",
            "90%": "90%",
            "95%": "95%",
            "100%": "100%"
          },
          grayscale: {
            0: "0",
            DEFAULT: "100%"
          },
          gridAutoColumns: {
            auto: "auto",
            min: "min-content",
            max: "max-content",
            fr: "minmax(0, 1fr)"
          },
          gridAutoRows: {
            auto: "auto",
            min: "min-content",
            max: "max-content",
            fr: "minmax(0, 1fr)"
          },
          gridColumn: {
            auto: "auto",
            "span-1": "span 1 / span 1",
            "span-2": "span 2 / span 2",
            "span-3": "span 3 / span 3",
            "span-4": "span 4 / span 4",
            "span-5": "span 5 / span 5",
            "span-6": "span 6 / span 6",
            "span-7": "span 7 / span 7",
            "span-8": "span 8 / span 8",
            "span-9": "span 9 / span 9",
            "span-10": "span 10 / span 10",
            "span-11": "span 11 / span 11",
            "span-12": "span 12 / span 12",
            "span-full": "1 / -1"
          },
          gridColumnEnd: {
            auto: "auto",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12",
            13: "13"
          },
          gridColumnStart: {
            auto: "auto",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12",
            13: "13"
          },
          gridRow: {
            auto: "auto",
            "span-1": "span 1 / span 1",
            "span-2": "span 2 / span 2",
            "span-3": "span 3 / span 3",
            "span-4": "span 4 / span 4",
            "span-5": "span 5 / span 5",
            "span-6": "span 6 / span 6",
            "span-7": "span 7 / span 7",
            "span-8": "span 8 / span 8",
            "span-9": "span 9 / span 9",
            "span-10": "span 10 / span 10",
            "span-11": "span 11 / span 11",
            "span-12": "span 12 / span 12",
            "span-full": "1 / -1"
          },
          gridRowEnd: {
            auto: "auto",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12",
            13: "13"
          },
          gridRowStart: {
            auto: "auto",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12",
            13: "13"
          },
          gridTemplateColumns: {
            none: "none",
            subgrid: "subgrid",
            1: "repeat(1, minmax(0, 1fr))",
            2: "repeat(2, minmax(0, 1fr))",
            3: "repeat(3, minmax(0, 1fr))",
            4: "repeat(4, minmax(0, 1fr))",
            5: "repeat(5, minmax(0, 1fr))",
            6: "repeat(6, minmax(0, 1fr))",
            7: "repeat(7, minmax(0, 1fr))",
            8: "repeat(8, minmax(0, 1fr))",
            9: "repeat(9, minmax(0, 1fr))",
            10: "repeat(10, minmax(0, 1fr))",
            11: "repeat(11, minmax(0, 1fr))",
            12: "repeat(12, minmax(0, 1fr))"
          },
          gridTemplateRows: {
            none: "none",
            subgrid: "subgrid",
            1: "repeat(1, minmax(0, 1fr))",
            2: "repeat(2, minmax(0, 1fr))",
            3: "repeat(3, minmax(0, 1fr))",
            4: "repeat(4, minmax(0, 1fr))",
            5: "repeat(5, minmax(0, 1fr))",
            6: "repeat(6, minmax(0, 1fr))",
            7: "repeat(7, minmax(0, 1fr))",
            8: "repeat(8, minmax(0, 1fr))",
            9: "repeat(9, minmax(0, 1fr))",
            10: "repeat(10, minmax(0, 1fr))",
            11: "repeat(11, minmax(0, 1fr))",
            12: "repeat(12, minmax(0, 1fr))"
          },
          height: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            "1/5": "20%",
            "2/5": "40%",
            "3/5": "60%",
            "4/5": "80%",
            "1/6": "16.666667%",
            "2/6": "33.333333%",
            "3/6": "50%",
            "4/6": "66.666667%",
            "5/6": "83.333333%",
            full: "100%",
            screen: "100vh",
            svh: "100svh",
            lvh: "100lvh",
            dvh: "100dvh",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          hueRotate: {
            0: "0deg",
            15: "15deg",
            30: "30deg",
            60: "60deg",
            90: "90deg",
            180: "180deg"
          },
          inset: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            full: "100%"
          }),
          invert: {
            0: "0",
            DEFAULT: "100%"
          },
          keyframes: {
            spin: {
              to: {
                transform: "rotate(360deg)"
              }
            },
            ping: {
              "75%, 100%": {
                transform: "scale(2)",
                opacity: "0"
              }
            },
            pulse: {
              "50%": {
                opacity: ".5"
              }
            },
            bounce: {
              "0%, 100%": {
                transform: "translateY(-25%)",
                animationTimingFunction: "cubic-bezier(0.8,0,1,1)"
              },
              "50%": {
                transform: "none",
                animationTimingFunction: "cubic-bezier(0,0,0.2,1)"
              }
            }
          },
          letterSpacing: {
            tighter: "-0.05em",
            tight: "-0.025em",
            normal: "0em",
            wide: "0.025em",
            wider: "0.05em",
            widest: "0.1em"
          },
          lineHeight: {
            none: "1",
            tight: "1.25",
            snug: "1.375",
            normal: "1.5",
            relaxed: "1.625",
            loose: "2",
            3: ".75rem",
            4: "1rem",
            5: "1.25rem",
            6: "1.5rem",
            7: "1.75rem",
            8: "2rem",
            9: "2.25rem",
            10: "2.5rem"
          },
          listStyleType: {
            none: "none",
            disc: "disc",
            decimal: "decimal"
          },
          listStyleImage: {
            none: "none"
          },
          margin: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing")
          }),
          lineClamp: {
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6"
          },
          maxHeight: ({ theme }) => ({
            ...theme("spacing"),
            none: "none",
            full: "100%",
            screen: "100vh",
            svh: "100svh",
            lvh: "100lvh",
            dvh: "100dvh",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          maxWidth: ({ theme, breakpoints }) => ({
            ...theme("spacing"),
            none: "none",
            xs: "20rem",
            sm: "24rem",
            md: "28rem",
            lg: "32rem",
            xl: "36rem",
            "2xl": "42rem",
            "3xl": "48rem",
            "4xl": "56rem",
            "5xl": "64rem",
            "6xl": "72rem",
            "7xl": "80rem",
            full: "100%",
            min: "min-content",
            max: "max-content",
            fit: "fit-content",
            prose: "65ch",
            ...breakpoints(theme("screens"))
          }),
          minHeight: ({ theme }) => ({
            ...theme("spacing"),
            full: "100%",
            screen: "100vh",
            svh: "100svh",
            lvh: "100lvh",
            dvh: "100dvh",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          minWidth: ({ theme }) => ({
            ...theme("spacing"),
            full: "100%",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          objectPosition: {
            bottom: "bottom",
            center: "center",
            left: "left",
            "left-bottom": "left bottom",
            "left-top": "left top",
            right: "right",
            "right-bottom": "right bottom",
            "right-top": "right top",
            top: "top"
          },
          opacity: {
            0: "0",
            5: "0.05",
            10: "0.1",
            15: "0.15",
            20: "0.2",
            25: "0.25",
            30: "0.3",
            35: "0.35",
            40: "0.4",
            45: "0.45",
            50: "0.5",
            55: "0.55",
            60: "0.6",
            65: "0.65",
            70: "0.7",
            75: "0.75",
            80: "0.8",
            85: "0.85",
            90: "0.9",
            95: "0.95",
            100: "1"
          },
          order: {
            first: "-9999",
            last: "9999",
            none: "0",
            1: "1",
            2: "2",
            3: "3",
            4: "4",
            5: "5",
            6: "6",
            7: "7",
            8: "8",
            9: "9",
            10: "10",
            11: "11",
            12: "12"
          },
          outlineColor: ({ theme }) => theme("colors"),
          outlineOffset: {
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          outlineWidth: {
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          padding: ({ theme }) => theme("spacing"),
          placeholderColor: ({ theme }) => theme("colors"),
          placeholderOpacity: ({ theme }) => theme("opacity"),
          ringColor: ({ theme }) => ({
            DEFAULT: theme("colors.blue.500", "#3b82f6"),
            ...theme("colors")
          }),
          ringOffsetColor: ({ theme }) => theme("colors"),
          ringOffsetWidth: {
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          ringOpacity: ({ theme }) => ({
            DEFAULT: "0.5",
            ...theme("opacity")
          }),
          ringWidth: {
            DEFAULT: "3px",
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          rotate: {
            0: "0deg",
            1: "1deg",
            2: "2deg",
            3: "3deg",
            6: "6deg",
            12: "12deg",
            45: "45deg",
            90: "90deg",
            180: "180deg"
          },
          saturate: {
            0: "0",
            50: ".5",
            100: "1",
            150: "1.5",
            200: "2"
          },
          scale: {
            0: "0",
            50: ".5",
            75: ".75",
            90: ".9",
            95: ".95",
            100: "1",
            105: "1.05",
            110: "1.1",
            125: "1.25",
            150: "1.5"
          },
          screens: {
            sm: "640px",
            md: "768px",
            lg: "1024px",
            xl: "1280px",
            "2xl": "1536px"
          },
          scrollMargin: ({ theme }) => ({
            ...theme("spacing")
          }),
          scrollPadding: ({ theme }) => theme("spacing"),
          sepia: {
            0: "0",
            DEFAULT: "100%"
          },
          skew: {
            0: "0deg",
            1: "1deg",
            2: "2deg",
            3: "3deg",
            6: "6deg",
            12: "12deg"
          },
          space: ({ theme }) => ({
            ...theme("spacing")
          }),
          spacing: {
            px: "1px",
            0: "0px",
            0.5: "0.125rem",
            1: "0.25rem",
            1.5: "0.375rem",
            2: "0.5rem",
            2.5: "0.625rem",
            3: "0.75rem",
            3.5: "0.875rem",
            4: "1rem",
            5: "1.25rem",
            6: "1.5rem",
            7: "1.75rem",
            8: "2rem",
            9: "2.25rem",
            10: "2.5rem",
            11: "2.75rem",
            12: "3rem",
            14: "3.5rem",
            16: "4rem",
            20: "5rem",
            24: "6rem",
            28: "7rem",
            32: "8rem",
            36: "9rem",
            40: "10rem",
            44: "11rem",
            48: "12rem",
            52: "13rem",
            56: "14rem",
            60: "15rem",
            64: "16rem",
            72: "18rem",
            80: "20rem",
            96: "24rem"
          },
          stroke: ({ theme }) => ({
            none: "none",
            ...theme("colors")
          }),
          strokeWidth: {
            0: "0",
            1: "1",
            2: "2"
          },
          supports: {},
          data: {},
          textColor: ({ theme }) => theme("colors"),
          textDecorationColor: ({ theme }) => theme("colors"),
          textDecorationThickness: {
            auto: "auto",
            "from-font": "from-font",
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          textIndent: ({ theme }) => ({
            ...theme("spacing")
          }),
          textOpacity: ({ theme }) => theme("opacity"),
          textUnderlineOffset: {
            auto: "auto",
            0: "0px",
            1: "1px",
            2: "2px",
            4: "4px",
            8: "8px"
          },
          transformOrigin: {
            center: "center",
            top: "top",
            "top-right": "top right",
            right: "right",
            "bottom-right": "bottom right",
            bottom: "bottom",
            "bottom-left": "bottom left",
            left: "left",
            "top-left": "top left"
          },
          transitionDelay: {
            0: "0s",
            75: "75ms",
            100: "100ms",
            150: "150ms",
            200: "200ms",
            300: "300ms",
            500: "500ms",
            700: "700ms",
            1e3: "1000ms"
          },
          transitionDuration: {
            DEFAULT: "150ms",
            0: "0s",
            75: "75ms",
            100: "100ms",
            150: "150ms",
            200: "200ms",
            300: "300ms",
            500: "500ms",
            700: "700ms",
            1e3: "1000ms"
          },
          transitionProperty: {
            none: "none",
            all: "all",
            DEFAULT: "color, background-color, border-color, text-decoration-color, fill, stroke, opacity, box-shadow, transform, filter, backdrop-filter",
            colors: "color, background-color, border-color, text-decoration-color, fill, stroke",
            opacity: "opacity",
            shadow: "box-shadow",
            transform: "transform"
          },
          transitionTimingFunction: {
            DEFAULT: "cubic-bezier(0.4, 0, 0.2, 1)",
            linear: "linear",
            in: "cubic-bezier(0.4, 0, 1, 1)",
            out: "cubic-bezier(0, 0, 0.2, 1)",
            "in-out": "cubic-bezier(0.4, 0, 0.2, 1)"
          },
          translate: ({ theme }) => ({
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            full: "100%"
          }),
          size: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            "1/5": "20%",
            "2/5": "40%",
            "3/5": "60%",
            "4/5": "80%",
            "1/6": "16.666667%",
            "2/6": "33.333333%",
            "3/6": "50%",
            "4/6": "66.666667%",
            "5/6": "83.333333%",
            "1/12": "8.333333%",
            "2/12": "16.666667%",
            "3/12": "25%",
            "4/12": "33.333333%",
            "5/12": "41.666667%",
            "6/12": "50%",
            "7/12": "58.333333%",
            "8/12": "66.666667%",
            "9/12": "75%",
            "10/12": "83.333333%",
            "11/12": "91.666667%",
            full: "100%",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          width: ({ theme }) => ({
            auto: "auto",
            ...theme("spacing"),
            "1/2": "50%",
            "1/3": "33.333333%",
            "2/3": "66.666667%",
            "1/4": "25%",
            "2/4": "50%",
            "3/4": "75%",
            "1/5": "20%",
            "2/5": "40%",
            "3/5": "60%",
            "4/5": "80%",
            "1/6": "16.666667%",
            "2/6": "33.333333%",
            "3/6": "50%",
            "4/6": "66.666667%",
            "5/6": "83.333333%",
            "1/12": "8.333333%",
            "2/12": "16.666667%",
            "3/12": "25%",
            "4/12": "33.333333%",
            "5/12": "41.666667%",
            "6/12": "50%",
            "7/12": "58.333333%",
            "8/12": "66.666667%",
            "9/12": "75%",
            "10/12": "83.333333%",
            "11/12": "91.666667%",
            full: "100%",
            screen: "100vw",
            svw: "100svw",
            lvw: "100lvw",
            dvw: "100dvw",
            min: "min-content",
            max: "max-content",
            fit: "fit-content"
          }),
          willChange: {
            auto: "auto",
            scroll: "scroll-position",
            contents: "contents",
            transform: "transform"
          },
          zIndex: {
            auto: "auto",
            0: "0",
            10: "10",
            20: "20",
            30: "30",
            40: "40",
            50: "50"
          }
        },
        plugins: []
      };
    }
  });

  // tailwindcss/lib/util/getAllConfigs.js
  var require_getAllConfigs = __commonJS({
    "tailwindcss/lib/util/getAllConfigs.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return getAllConfigs;
        }
      });
      var _configfull = /* @__PURE__ */ _interop_require_default(require_config_full());
      var _featureFlags = require_featureFlags();
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function getAllConfigs(config) {
        var _config_presets;
        const configs = ((_config_presets = config === null || config === void 0 ? void 0 : config.presets) !== null && _config_presets !== void 0 ? _config_presets : [
          _configfull.default
        ]).slice().reverse().flatMap((preset) => getAllConfigs(preset instanceof Function ? preset() : preset));
        const features = {
          // Add experimental configs here...
          respectDefaultRingColorOpacity: {
            theme: {
              ringColor: ({ theme }) => ({
                DEFAULT: "#3b82f67f",
                ...theme("colors")
              })
            }
          },
          disableColorOpacityUtilitiesByDefault: {
            corePlugins: {
              backgroundOpacity: false,
              borderOpacity: false,
              divideOpacity: false,
              placeholderOpacity: false,
              ringOpacity: false,
              textOpacity: false
            }
          }
        };
        const experimentals = Object.keys(features).filter((feature) => (0, _featureFlags.flagEnabled)(config, feature)).map((feature) => features[feature]);
        return [
          config,
          ...experimentals,
          ...configs
        ];
      }
    }
  });

  // tailwindcss/lib/public/resolve-config.js
  var require_resolve_config = __commonJS({
    "tailwindcss/lib/public/resolve-config.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return resolveConfig2;
        }
      });
      var _resolveConfig = /* @__PURE__ */ _interop_require_default(require_resolveConfig());
      var _getAllConfigs = /* @__PURE__ */ _interop_require_default(require_getAllConfigs());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      function resolveConfig2(...configs) {
        let [, ...defaultConfigs] = (0, _getAllConfigs.default)(configs[0]);
        return (0, _resolveConfig.default)([
          ...configs,
          ...defaultConfigs
        ]);
      }
    }
  });

  // tailwindcss/resolveConfig.js
  var require_resolveConfig2 = __commonJS({
    "tailwindcss/resolveConfig.js"(exports, module) {
      var resolveConfig2 = require_resolve_config();
      module.exports = (resolveConfig2.__esModule ? resolveConfig2 : { default: resolveConfig2 }).default;
    }
  });

  // mini-svg-data-uri/shorter-css-color-names.js
  var require_shorter_css_color_names = __commonJS({
    "mini-svg-data-uri/shorter-css-color-names.js"(exports, module) {
      module.exports = {
        aqua: /#00ffff(ff)?(?!\w)|#0ff(f)?(?!\w)/gi,
        azure: /#f0ffff(ff)?(?!\w)/gi,
        beige: /#f5f5dc(ff)?(?!\w)/gi,
        bisque: /#ffe4c4(ff)?(?!\w)/gi,
        black: /#000000(ff)?(?!\w)|#000(f)?(?!\w)/gi,
        blue: /#0000ff(ff)?(?!\w)|#00f(f)?(?!\w)/gi,
        brown: /#a52a2a(ff)?(?!\w)/gi,
        coral: /#ff7f50(ff)?(?!\w)/gi,
        cornsilk: /#fff8dc(ff)?(?!\w)/gi,
        crimson: /#dc143c(ff)?(?!\w)/gi,
        cyan: /#00ffff(ff)?(?!\w)|#0ff(f)?(?!\w)/gi,
        darkblue: /#00008b(ff)?(?!\w)/gi,
        darkcyan: /#008b8b(ff)?(?!\w)/gi,
        darkgrey: /#a9a9a9(ff)?(?!\w)/gi,
        darkred: /#8b0000(ff)?(?!\w)/gi,
        deeppink: /#ff1493(ff)?(?!\w)/gi,
        dimgrey: /#696969(ff)?(?!\w)/gi,
        gold: /#ffd700(ff)?(?!\w)/gi,
        green: /#008000(ff)?(?!\w)/gi,
        grey: /#808080(ff)?(?!\w)/gi,
        honeydew: /#f0fff0(ff)?(?!\w)/gi,
        hotpink: /#ff69b4(ff)?(?!\w)/gi,
        indigo: /#4b0082(ff)?(?!\w)/gi,
        ivory: /#fffff0(ff)?(?!\w)/gi,
        khaki: /#f0e68c(ff)?(?!\w)/gi,
        lavender: /#e6e6fa(ff)?(?!\w)/gi,
        lime: /#00ff00(ff)?(?!\w)|#0f0(f)?(?!\w)/gi,
        linen: /#faf0e6(ff)?(?!\w)/gi,
        maroon: /#800000(ff)?(?!\w)/gi,
        moccasin: /#ffe4b5(ff)?(?!\w)/gi,
        navy: /#000080(ff)?(?!\w)/gi,
        oldlace: /#fdf5e6(ff)?(?!\w)/gi,
        olive: /#808000(ff)?(?!\w)/gi,
        orange: /#ffa500(ff)?(?!\w)/gi,
        orchid: /#da70d6(ff)?(?!\w)/gi,
        peru: /#cd853f(ff)?(?!\w)/gi,
        pink: /#ffc0cb(ff)?(?!\w)/gi,
        plum: /#dda0dd(ff)?(?!\w)/gi,
        purple: /#800080(ff)?(?!\w)/gi,
        red: /#ff0000(ff)?(?!\w)|#f00(f)?(?!\w)/gi,
        salmon: /#fa8072(ff)?(?!\w)/gi,
        seagreen: /#2e8b57(ff)?(?!\w)/gi,
        seashell: /#fff5ee(ff)?(?!\w)/gi,
        sienna: /#a0522d(ff)?(?!\w)/gi,
        silver: /#c0c0c0(ff)?(?!\w)/gi,
        skyblue: /#87ceeb(ff)?(?!\w)/gi,
        snow: /#fffafa(ff)?(?!\w)/gi,
        tan: /#d2b48c(ff)?(?!\w)/gi,
        teal: /#008080(ff)?(?!\w)/gi,
        thistle: /#d8bfd8(ff)?(?!\w)/gi,
        tomato: /#ff6347(ff)?(?!\w)/gi,
        violet: /#ee82ee(ff)?(?!\w)/gi,
        wheat: /#f5deb3(ff)?(?!\w)/gi,
        white: /#ffffff(ff)?(?!\w)|#fff(f)?(?!\w)/gi
      };
    }
  });

  // mini-svg-data-uri/index.js
  var require_mini_svg_data_uri = __commonJS({
    "mini-svg-data-uri/index.js"(exports, module) {
      var shorterNames = require_shorter_css_color_names();
      var REGEX = {
        whitespace: /\s+/g,
        urlHexPairs: /%[\dA-F]{2}/g,
        quotes: /"/g
      };
      function collapseWhitespace(str) {
        return str.trim().replace(REGEX.whitespace, " ");
      }
      function dataURIPayload(string) {
        return encodeURIComponent(string).replace(REGEX.urlHexPairs, specialHexEncode);
      }
      function colorCodeToShorterNames(string) {
        Object.keys(shorterNames).forEach(function(key) {
          if (shorterNames[key].test(string)) {
            string = string.replace(shorterNames[key], key);
          }
        });
        return string;
      }
      function specialHexEncode(match) {
        switch (match) {
          // Browsers tolerate these characters, and they're frequent
          case "%20":
            return " ";
          case "%3D":
            return "=";
          case "%3A":
            return ":";
          case "%2F":
            return "/";
          default:
            return match.toLowerCase();
        }
      }
      function svgToTinyDataUri(svgString) {
        if (typeof svgString !== "string") {
          throw new TypeError("Expected a string, but received " + typeof svgString);
        }
        if (svgString.charCodeAt(0) === 65279) {
          svgString = svgString.slice(1);
        }
        var body = colorCodeToShorterNames(collapseWhitespace(svgString)).replace(REGEX.quotes, "'");
        return "data:image/svg+xml," + dataURIPayload(body);
      }
      svgToTinyDataUri.toSrcset = function toSrcset(svgString) {
        return svgToTinyDataUri(svgString).replace(/ /g, "%20");
      };
      module.exports = svgToTinyDataUri;
    }
  });

  // tailwindcss/lib/util/createPlugin.js
  var require_createPlugin = __commonJS({
    "tailwindcss/lib/util/createPlugin.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      function createPlugin(plugin2, config) {
        return {
          handler: plugin2,
          config
        };
      }
      createPlugin.withOptions = function(pluginFunction, configFunction = () => ({})) {
        const optionsFunction = function(options) {
          return {
            __options: options,
            handler: pluginFunction(options),
            config: configFunction(options)
          };
        };
        optionsFunction.__isOptionsFunction = true;
        optionsFunction.__pluginFunction = pluginFunction;
        optionsFunction.__configFunction = configFunction;
        return optionsFunction;
      };
      var _default = createPlugin;
    }
  });

  // tailwindcss/lib/public/create-plugin.js
  var require_create_plugin = __commonJS({
    "tailwindcss/lib/public/create-plugin.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _createPlugin = /* @__PURE__ */ _interop_require_default(require_createPlugin());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var _default = _createPlugin.default;
    }
  });

  // tailwindcss/plugin.js
  var require_plugin = __commonJS({
    "tailwindcss/plugin.js"(exports, module) {
      var createPlugin = require_create_plugin();
      module.exports = (createPlugin.__esModule ? createPlugin : { default: createPlugin }).default;
    }
  });

  // tailwindcss/lib/public/default-theme.js
  var require_default_theme = __commonJS({
    "tailwindcss/lib/public/default-theme.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _cloneDeep = require_cloneDeep();
      var _configfull = /* @__PURE__ */ _interop_require_default(require_config_full());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var _default = (0, _cloneDeep.cloneDeep)(_configfull.default.theme);
    }
  });

  // tailwindcss/defaultTheme.js
  var require_defaultTheme = __commonJS({
    "tailwindcss/defaultTheme.js"(exports, module) {
      var defaultTheme2 = require_default_theme();
      module.exports = (defaultTheme2.__esModule ? defaultTheme2 : { default: defaultTheme2 }).default;
    }
  });

  // tailwindcss/colors.js
  var require_colors2 = __commonJS({
    "tailwindcss/colors.js"(exports, module) {
      var colors2 = require_colors();
      module.exports = (colors2.__esModule ? colors2 : { default: colors2 }).default;
    }
  });

  // @tailwindcss/forms/src/index.js
  var require_src = __commonJS({
    "@tailwindcss/forms/src/index.js"(exports, module) {
      var svgToDataUri = require_mini_svg_data_uri();
      var plugin2 = require_plugin();
      var defaultTheme2 = require_defaultTheme();
      var colors2 = require_colors2();
      var [baseFontSize, { lineHeight: baseLineHeight }] = defaultTheme2.fontSize.base;
      var { spacing, borderWidth, borderRadius } = defaultTheme2;
      function resolveColor(color, opacityVariableName) {
        return color.replace("<alpha-value>", `var(${opacityVariableName}, 1)`);
      }
      var forms2 = plugin2.withOptions(function(options = { strategy: void 0 }) {
        return function({ addBase, addComponents, theme }) {
          function resolveChevronColor(color, fallback) {
            let resolved = theme(color);
            if (!resolved || resolved.includes("var(")) {
              return fallback;
            }
            return resolved.replace("<alpha-value>", "1");
          }
          const strategy = options.strategy === void 0 ? ["base", "class"] : [options.strategy];
          const rules = [
            {
              base: [
                "[type='text']",
                "input:where(:not([type]))",
                "[type='email']",
                "[type='url']",
                "[type='password']",
                "[type='number']",
                "[type='date']",
                "[type='datetime-local']",
                "[type='month']",
                "[type='search']",
                "[type='tel']",
                "[type='time']",
                "[type='week']",
                "[multiple]",
                "textarea",
                "select"
              ],
              class: [".form-input", ".form-textarea", ".form-select", ".form-multiselect"],
              styles: {
                appearance: "none",
                "background-color": "#fff",
                "border-color": resolveColor(
                  theme("colors.gray.500", colors2.gray[500]),
                  "--tw-border-opacity"
                ),
                "border-width": borderWidth["DEFAULT"],
                "border-radius": borderRadius.none,
                "padding-top": spacing[2],
                "padding-right": spacing[3],
                "padding-bottom": spacing[2],
                "padding-left": spacing[3],
                "font-size": baseFontSize,
                "line-height": baseLineHeight,
                "--tw-shadow": "0 0 #0000",
                "&:focus": {
                  outline: "2px solid transparent",
                  "outline-offset": "2px",
                  "--tw-ring-inset": "var(--tw-empty,/*!*/ /*!*/)",
                  "--tw-ring-offset-width": "0px",
                  "--tw-ring-offset-color": "#fff",
                  "--tw-ring-color": resolveColor(
                    theme("colors.blue.600", colors2.blue[600]),
                    "--tw-ring-opacity"
                  ),
                  "--tw-ring-offset-shadow": `var(--tw-ring-inset) 0 0 0 var(--tw-ring-offset-width) var(--tw-ring-offset-color)`,
                  "--tw-ring-shadow": `var(--tw-ring-inset) 0 0 0 calc(1px + var(--tw-ring-offset-width)) var(--tw-ring-color)`,
                  "box-shadow": `var(--tw-ring-offset-shadow), var(--tw-ring-shadow), var(--tw-shadow)`,
                  "border-color": resolveColor(
                    theme("colors.blue.600", colors2.blue[600]),
                    "--tw-border-opacity"
                  )
                }
              }
            },
            {
              base: ["input::placeholder", "textarea::placeholder"],
              class: [".form-input::placeholder", ".form-textarea::placeholder"],
              styles: {
                color: resolveColor(theme("colors.gray.500", colors2.gray[500]), "--tw-text-opacity"),
                opacity: "1"
              }
            },
            {
              base: ["::-webkit-datetime-edit-fields-wrapper"],
              class: [".form-input::-webkit-datetime-edit-fields-wrapper"],
              styles: {
                padding: "0"
              }
            },
            {
              // Unfortunate hack until https://bugs.webkit.org/show_bug.cgi?id=198959 is fixed.
              // This sucks because users can't change line-height with a utility on date inputs now.
              // Reference: https://github.com/twbs/bootstrap/pull/31993
              base: ["::-webkit-date-and-time-value"],
              class: [".form-input::-webkit-date-and-time-value"],
              styles: {
                "min-height": "1.5em"
              }
            },
            {
              // In Safari on iOS date and time inputs are centered instead of left-aligned and can't be
              // changed with `text-align` utilities on the input by default. Resetting this to `inherit`
              // makes them left-aligned by default and makes it possible to override the alignment with
              // utility classes without using an arbitrary variant to target the pseudo-elements.
              base: ["::-webkit-date-and-time-value"],
              class: [".form-input::-webkit-date-and-time-value"],
              styles: {
                "text-align": "inherit"
              }
            },
            {
              // In Safari on macOS date time inputs that are set to `display: block` have unexpected
              // extra bottom spacing. This can be corrected by setting the `::-webkit-datetime-edit`
              // pseudo-element to `display: inline-flex`, instead of the browser default of
              // `display: inline-block`.
              base: ["::-webkit-datetime-edit"],
              class: [".form-input::-webkit-datetime-edit"],
              styles: {
                display: "inline-flex"
              }
            },
            {
              // In Safari on macOS date time inputs are 4px taller than normal inputs
              // This is because there is extra padding on the datetime-edit and datetime-edit-{part}-field pseudo elements
              // See https://github.com/tailwindlabs/tailwindcss-forms/issues/95
              base: [
                "::-webkit-datetime-edit",
                "::-webkit-datetime-edit-year-field",
                "::-webkit-datetime-edit-month-field",
                "::-webkit-datetime-edit-day-field",
                "::-webkit-datetime-edit-hour-field",
                "::-webkit-datetime-edit-minute-field",
                "::-webkit-datetime-edit-second-field",
                "::-webkit-datetime-edit-millisecond-field",
                "::-webkit-datetime-edit-meridiem-field"
              ],
              class: [
                ".form-input::-webkit-datetime-edit",
                ".form-input::-webkit-datetime-edit-year-field",
                ".form-input::-webkit-datetime-edit-month-field",
                ".form-input::-webkit-datetime-edit-day-field",
                ".form-input::-webkit-datetime-edit-hour-field",
                ".form-input::-webkit-datetime-edit-minute-field",
                ".form-input::-webkit-datetime-edit-second-field",
                ".form-input::-webkit-datetime-edit-millisecond-field",
                ".form-input::-webkit-datetime-edit-meridiem-field"
              ],
              styles: {
                "padding-top": 0,
                "padding-bottom": 0
              }
            },
            {
              base: ["select"],
              class: [".form-select"],
              styles: {
                "background-image": `url("${svgToDataUri(
                  `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 20 20"><path stroke="${resolveChevronColor(
                    "colors.gray.500",
                    colors2.gray[500]
                  )}" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6 8l4 4 4-4"/></svg>`
                )}")`,
                "background-position": `right ${spacing[2]} center`,
                "background-repeat": `no-repeat`,
                "background-size": `1.5em 1.5em`,
                "padding-right": spacing[10],
                "print-color-adjust": `exact`
              }
            },
            {
              base: ["[multiple]", '[size]:where(select:not([size="1"]))'],
              class: ['.form-select:where([size]:not([size="1"]))'],
              styles: {
                "background-image": "initial",
                "background-position": "initial",
                "background-repeat": "unset",
                "background-size": "initial",
                "padding-right": spacing[3],
                "print-color-adjust": "unset"
              }
            },
            {
              base: [`[type='checkbox']`, `[type='radio']`],
              class: [".form-checkbox", ".form-radio"],
              styles: {
                appearance: "none",
                padding: "0",
                "print-color-adjust": "exact",
                display: "inline-block",
                "vertical-align": "middle",
                "background-origin": "border-box",
                "user-select": "none",
                "flex-shrink": "0",
                height: spacing[4],
                width: spacing[4],
                color: resolveColor(theme("colors.blue.600", colors2.blue[600]), "--tw-text-opacity"),
                "background-color": "#fff",
                "border-color": resolveColor(
                  theme("colors.gray.500", colors2.gray[500]),
                  "--tw-border-opacity"
                ),
                "border-width": borderWidth["DEFAULT"],
                "--tw-shadow": "0 0 #0000"
              }
            },
            {
              base: [`[type='checkbox']`],
              class: [".form-checkbox"],
              styles: {
                "border-radius": borderRadius["none"]
              }
            },
            {
              base: [`[type='radio']`],
              class: [".form-radio"],
              styles: {
                "border-radius": "100%"
              }
            },
            {
              base: [`[type='checkbox']:focus`, `[type='radio']:focus`],
              class: [".form-checkbox:focus", ".form-radio:focus"],
              styles: {
                outline: "2px solid transparent",
                "outline-offset": "2px",
                "--tw-ring-inset": "var(--tw-empty,/*!*/ /*!*/)",
                "--tw-ring-offset-width": "2px",
                "--tw-ring-offset-color": "#fff",
                "--tw-ring-color": resolveColor(
                  theme("colors.blue.600", colors2.blue[600]),
                  "--tw-ring-opacity"
                ),
                "--tw-ring-offset-shadow": `var(--tw-ring-inset) 0 0 0 var(--tw-ring-offset-width) var(--tw-ring-offset-color)`,
                "--tw-ring-shadow": `var(--tw-ring-inset) 0 0 0 calc(2px + var(--tw-ring-offset-width)) var(--tw-ring-color)`,
                "box-shadow": `var(--tw-ring-offset-shadow), var(--tw-ring-shadow), var(--tw-shadow)`
              }
            },
            {
              base: [`[type='checkbox']:checked`, `[type='radio']:checked`],
              class: [".form-checkbox:checked", ".form-radio:checked"],
              styles: {
                "border-color": `transparent`,
                "background-color": `currentColor`,
                "background-size": `100% 100%`,
                "background-position": `center`,
                "background-repeat": `no-repeat`
              }
            },
            {
              base: [`[type='checkbox']:checked`],
              class: [".form-checkbox:checked"],
              styles: {
                "background-image": `url("${svgToDataUri(
                  `<svg viewBox="0 0 16 16" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12.207 4.793a1 1 0 010 1.414l-5 5a1 1 0 01-1.414 0l-2-2a1 1 0 011.414-1.414L6.5 9.086l4.293-4.293a1 1 0 011.414 0z"/></svg>`
                )}")`,
                "@media (forced-colors: active) ": {
                  appearance: "auto"
                }
              }
            },
            {
              base: [`[type='radio']:checked`],
              class: [".form-radio:checked"],
              styles: {
                "background-image": `url("${svgToDataUri(
                  `<svg viewBox="0 0 16 16" fill="white" xmlns="http://www.w3.org/2000/svg"><circle cx="8" cy="8" r="3"/></svg>`
                )}")`,
                "@media (forced-colors: active) ": {
                  appearance: "auto"
                }
              }
            },
            {
              base: [
                `[type='checkbox']:checked:hover`,
                `[type='checkbox']:checked:focus`,
                `[type='radio']:checked:hover`,
                `[type='radio']:checked:focus`
              ],
              class: [
                ".form-checkbox:checked:hover",
                ".form-checkbox:checked:focus",
                ".form-radio:checked:hover",
                ".form-radio:checked:focus"
              ],
              styles: {
                "border-color": "transparent",
                "background-color": "currentColor"
              }
            },
            {
              base: [`[type='checkbox']:indeterminate`],
              class: [".form-checkbox:indeterminate"],
              styles: {
                "background-image": `url("${svgToDataUri(
                  `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 16 16"><path stroke="white" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8h8"/></svg>`
                )}")`,
                "border-color": `transparent`,
                "background-color": `currentColor`,
                "background-size": `100% 100%`,
                "background-position": `center`,
                "background-repeat": `no-repeat`,
                "@media (forced-colors: active) ": {
                  appearance: "auto"
                }
              }
            },
            {
              base: [`[type='checkbox']:indeterminate:hover`, `[type='checkbox']:indeterminate:focus`],
              class: [".form-checkbox:indeterminate:hover", ".form-checkbox:indeterminate:focus"],
              styles: {
                "border-color": "transparent",
                "background-color": "currentColor"
              }
            },
            {
              base: [`[type='file']`],
              class: null,
              styles: {
                background: "unset",
                "border-color": "inherit",
                "border-width": "0",
                "border-radius": "0",
                padding: "0",
                "font-size": "unset",
                "line-height": "inherit"
              }
            },
            {
              base: [`[type='file']:focus`],
              class: null,
              styles: {
                outline: [`1px solid ButtonText`, `1px auto -webkit-focus-ring-color`]
              }
            }
          ];
          const getStrategyRules = (strategy2) => rules.map((rule) => {
            if (rule[strategy2] === null) return null;
            return { [rule[strategy2]]: rule.styles };
          }).filter(Boolean);
          if (strategy.includes("base")) {
            addBase(getStrategyRules("base"));
          }
          if (strategy.includes("class")) {
            addComponents(getStrategyRules("class"));
          }
        };
      });
      module.exports = forms2;
    }
  });

  // @tailwindcss/container-queries/dist/index.js
  var require_dist2 = __commonJS({
    "@tailwindcss/container-queries/dist/index.js"(exports, module) {
      "use strict";
      var _plugin = /* @__PURE__ */ _interopRequireDefault(require_plugin());
      function _interopRequireDefault(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      module.exports = (0, _plugin.default)(function containerQueries2(param) {
        var matchUtilities = param.matchUtilities, matchVariant = param.matchVariant, theme = param.theme;
        var parseValue = function parseValue2(value) {
          var _value_match;
          var _value_match_;
          var numericValue = (_value_match_ = (_value_match = value.match(/^(\d+\.\d+|\d+|\.\d+)\D+/)) === null || _value_match === void 0 ? void 0 : _value_match[1]) !== null && _value_match_ !== void 0 ? _value_match_ : null;
          if (numericValue === null) return null;
          return parseFloat(value);
        };
        var _theme;
        var values = (_theme = theme("containers")) !== null && _theme !== void 0 ? _theme : {};
        matchUtilities({
          "@container": function(value, param2) {
            var modifier = param2.modifier;
            return {
              "container-type": value,
              "container-name": modifier
            };
          }
        }, {
          values: {
            DEFAULT: "inline-size",
            normal: "normal"
          },
          modifiers: "any"
        });
        matchVariant("@", function() {
          var value = arguments.length > 0 && arguments[0] !== void 0 ? arguments[0] : "", modifier = (arguments.length > 1 ? arguments[1] : void 0).modifier;
          var parsed = parseValue(value);
          return parsed !== null ? "@container ".concat(modifier !== null && modifier !== void 0 ? modifier : "", " (min-width: ").concat(value, ")") : [];
        }, {
          values,
          sort: function sort(aVariant, zVariant) {
            var a = parseFloat(aVariant.value);
            var z = parseFloat(zVariant.value);
            if (a === null || z === null) return 0;
            if (a - z !== 0) return a - z;
            var _aVariant_modifier;
            var aLabel = (_aVariant_modifier = aVariant.modifier) !== null && _aVariant_modifier !== void 0 ? _aVariant_modifier : "";
            var _zVariant_modifier;
            var zLabel = (_zVariant_modifier = zVariant.modifier) !== null && _zVariant_modifier !== void 0 ? _zVariant_modifier : "";
            if (aLabel === "" && zLabel !== "") {
              return 1;
            } else if (aLabel !== "" && zLabel === "") {
              return -1;
            }
            return aLabel.localeCompare(zLabel, "en", {
              numeric: true
            });
          }
        });
      }, {
        theme: {
          containers: {
            xs: "20rem",
            sm: "24rem",
            md: "28rem",
            lg: "32rem",
            xl: "36rem",
            "2xl": "42rem",
            "3xl": "48rem",
            "4xl": "56rem",
            "5xl": "64rem",
            "6xl": "72rem",
            "7xl": "80rem"
          }
        }
      });
    }
  });

  // tailwindcss/lib/public/default-config.js
  var require_default_config = __commonJS({
    "tailwindcss/lib/public/default-config.js"(exports) {
      "use strict";
      Object.defineProperty(exports, "__esModule", {
        value: true
      });
      Object.defineProperty(exports, "default", {
        enumerable: true,
        get: function() {
          return _default;
        }
      });
      var _cloneDeep = require_cloneDeep();
      var _configfull = /* @__PURE__ */ _interop_require_default(require_config_full());
      function _interop_require_default(obj) {
        return obj && obj.__esModule ? obj : {
          default: obj
        };
      }
      var _default = (0, _cloneDeep.cloneDeep)(_configfull.default);
    }
  });

  // tailwindcss/defaultConfig.js
  var require_defaultConfig = __commonJS({
    "tailwindcss/defaultConfig.js"(exports, module) {
      var defaultConfig2 = require_default_config();
      module.exports = (defaultConfig2.__esModule ? defaultConfig2 : { default: defaultConfig2 }).default;
    }
  });

  // ../../../stage-b-spike-r01/src/index.js
  var postcss = require_postcss();
  var processFeatures = require_processTailwindFeatures().default;
  var { createContext } = require_setupContextUtils();
  var resolveConfig = require_resolveConfig2();
  var forms = require_src();
  var containerQueries = require_dist2();
  var colors = require_colors2();
  var defaultConfig = require_defaultConfig();
  var defaultTheme = require_defaultTheme();
  var plugin = require_plugin();
  var context = null;
  var revision = 0;
  var compiledRevision = -1;
  var lastCss = null;
  var pending = false;
  var running = false;
  var proxyCache = /* @__PURE__ */ new WeakMap();
  function invalidate() {
    revision++;
    schedule();
  }
  function reactive(value) {
    if (!value || typeof value !== "object") return value;
    if (proxyCache.has(value)) return proxyCache.get(value);
    const proxy = new Proxy(value, {
      get(target, key, receiver) {
        return reactive(Reflect.get(target, key, receiver));
      },
      set(target, key, next, receiver) {
        const old = target[key];
        const ok = Reflect.set(target, key, next, receiver);
        if (ok && old !== next) invalidate();
        return ok;
      },
      deleteProperty(target, key) {
        const exists = Object.hasOwn(target, key);
        const ok = Reflect.deleteProperty(target, key);
        if (ok && exists) invalidate();
        return ok;
      }
    });
    proxyCache.set(value, proxy);
    return proxy;
  }
  var surface = { config: {}, defaultTheme, defaultConfig, colors, plugin, resolveConfig };
  window.tailwind = new Proxy(surface, {
    get(target, key, receiver) {
      const value = Reflect.get(target, key, receiver);
      return key === "config" ? reactive(value) : value;
    },
    set(target, key, value, receiver) {
      const ok = Reflect.set(target, key, value, receiver);
      if (ok && key === "config") invalidate();
      return ok;
    }
  });
  var output = document.createElement("style");
  output.dataset.gwTailwind = "3.4.17";
  function schedule() {
    pending = true;
    if (!running) queueMicrotask(render);
  }
  async function render() {
    if (running || !pending) return;
    running = true;
    try {
      while (pending) {
        pending = false;
        const currentRevision = revision;
        const css = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n" + Array.from(document.querySelectorAll('style[type="text/tailwindcss"]'), (node) => node.textContent).join("\n");
        const content = Array.from(document.querySelectorAll("[class]"), (node) => node.getAttribute("class")).join("\n");
        const changedContent = [{ content, extension: "html" }];
        const reset = !context || compiledRevision !== currentRevision || lastCss !== css;
        const config = surface.config || {};
        const resolved = reset ? resolveConfig({ ...config, content: [{ raw: content, extension: "html" }], plugins: [forms, containerQueries, ...config.plugins || []] }) : null;
        const processor = { postcssPlugin: "gw-tailwind-browser", Once(root, { result: result2 }) {
          return processFeatures(() => (processedRoot) => {
            if (reset) context = createContext(resolved, changedContent, processedRoot);
            else context.changedContent.push(...changedContent);
            return context;
          })(root, result2);
        } };
        const result = await postcss([processor]).process(css, { map: false });
        compiledRevision = currentRevision;
        lastCss = css;
        if (currentRevision === revision) {
          if (!output.isConnected) document.head.appendChild(output);
          if (output.textContent !== result.css) output.textContent = result.css;
        }
      }
    } catch (error) {
      throw error;
    } finally {
      running = false;
      if (pending) schedule();
    }
  }
  new MutationObserver((records) => {
    const relevant = records.some((record) => {
      if (record.target === output || output.contains(record.target)) return false;
      if (record.type === "attributes") return true;
      if (record.type === "characterData") return record.target.parentElement?.matches('style[type="text/tailwindcss"]');
      return [...record.addedNodes].some((node) => node !== output);
    });
    if (relevant) schedule();
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["class"] });
  schedule();
})();
