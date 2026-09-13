"""
The behaviour that turns the notes field into a Markdown editor.

A note is Markdown, and until now the field it was typed into knew nothing
about that: Enter in the middle of a list ended the list, Tab jumped to the
next control, and the source was one undifferentiated wall of grey. This adds
the three things that make writing Markdown bearable - lists that carry on by
themselves, Tab that indents, and colour that tells the markers apart from
the text.

WHY THIS IS JAVASCRIPT, AND WHY IT LIVES IN A STRING
----------------------------------------------------
Streamlit's text_area is a plain <textarea> in the browser. Nothing Python
can do at redraw time changes what a keystroke does inside it, so the
behaviour has to run in the page.

Streamlit strips <script> out of st.markdown, so it is delivered through
st.components.v1.html(), which renders an iframe. That iframe is sandboxed
with allow-same-origin, which is what lets the script reach back into the
page that hosts it and find the real textarea. Verified rather than assumed;
if a future Streamlit drops that permission the editor quietly stops
enhancing anything and the field goes back to being an ordinary text box,
which is the right way for this to fail.

The script is a string here rather than a .js file beside it because of the
Windows build. A frozen build contains the modules PyInstaller can see by
following imports; a data file has to be listed separately in
TimeControl.spec and copied by hand, and issue #556 was exactly that kind of
omission - silent, and only visible on the one platform that is hardest to
try. A string in a module cannot be left behind.

HOW AN EDIT IS MADE
-------------------
Never by assigning to textarea.value. Streamlit's field is a React component
that holds its own copy of the text; an assignment changes what is on screen
and nothing else, and the note would save as though the typing had never
happened. document.execCommand('insertText') performs a real edit instead -
the browser moves the caret, keeps the undo history, and raises the input
event React is listening for. It is deprecated and universally implemented,
and there is no replacement that does the same job.

WHY THE HIGHLIGHTING ONLY CHANGES COLOUR
----------------------------------------
The colour comes from a second element sitting exactly behind the textarea,
holding the same text marked up, with the textarea's own glyphs made
transparent. That only works while both agree on where every character is.
Bold or italic in the overlay would move the characters after it on that
line, and the caret - drawn by the textarea, which does not know - would
drift further from the text the further along the line you typed. So
headings, emphasis and code are told apart by colour, and the markers
themselves are dimmed rather than restyled.
"""

import json

# Two spaces. Markdown's own nesting unit for list items, and the one thing
# that indents identically wherever the note is later read - a tab character
# is eight columns in some viewers and four in others.
INDENT = '  '


# ---------------------------------------------------------------------------
# The part with no browser in it.
#
# Everything here is text in, text out: what a key should do to a string and
# a caret position, and how to mark that string up. Kept apart from the DOM
# work below so that tests/test_markdown_editor.py can run it under node and
# check the answers - the wiring underneath needs a browser and a running
# Streamlit, this does not.
# ---------------------------------------------------------------------------

_PURE_JS = r"""
var TC_INDENT = "__INDENT__";

function tcLineStart(text, pos) {
  var i = text.lastIndexOf("\n", pos - 1);
  return i < 0 ? 0 : i + 1;
}

function tcLineEnd(text, pos) {
  var i = text.indexOf("\n", pos);
  return i < 0 ? text.length : i;
}

/* What kind of list item a line is, if any.

   `prefix` is everything the reader of the note will not see as content -
   the indentation, the marker and the space after it. `next` is what the
   line below has to start with to belong to the same list. */
function tcListMarker(line) {
  var m = line.match(/^([ \t]*)([-*+])([ \t]+)(\[[ xX]\][ \t]+)?([\s\S]*)$/);
  if (m) {
    return {
      kind: "bullet",
      indent: m[1],
      prefix: m[1] + m[2] + m[3] + (m[4] || ""),
      body: m[5],
      /* A ticked box carries on as an empty one. Repeating "[x] " would
         announce the next thing as done before it has been written. */
      next: m[1] + m[2] + m[3] + (m[4] ? "[ ] " : "")
    };
  }
  m = line.match(/^([ \t]*)(\d+)([.)])([ \t]+)([\s\S]*)$/);
  if (m) {
    return {
      kind: "ordered",
      indent: m[1],
      prefix: m[1] + m[2] + m[3] + m[4],
      body: m[5],
      next: m[1] + (parseInt(m[2], 10) + 1) + m[3] + m[4]
    };
  }
  m = line.match(/^([ \t]*)((?:>[ \t]?)+)([\s\S]*)$/);
  if (m) {
    return {
      kind: "quote",
      indent: m[1],
      prefix: m[1] + m[2],
      body: m[3],
      next: m[1] + m[2]
    };
  }
  return null;
}

/* What pressing Enter should do.

   Returns null to mean "nothing special - let the browser insert its own
   newline", or {select: [from, to], insert: "..."} for an edit to make in
   its place. */
function tcEnterAction(text, selStart, selEnd) {
  if (selStart !== selEnd) {
    return null;      /* replacing a selection is the browser's own business */
  }
  var ls = tcLineStart(text, selStart);
  var le = tcLineEnd(text, selStart);
  var marker = tcListMarker(text.slice(ls, le));
  if (!marker) {
    return null;
  }
  if (selStart < ls + marker.prefix.length) {
    /* The caret is in the marker itself, or in front of it. Enter there
       pushes the item down a line; carrying the marker along as well would
       write a second one in front of the first. */
    return null;
  }
  if (marker.body.trim() === "") {
    /* An item with nothing in it. Enter there means "I am done with the
       list" - or, one level in, "I am done with this level". */
    if (marker.indent.length) {
      var less = marker.indent.startsWith(TC_INDENT)
        ? marker.indent.slice(TC_INDENT.length)
        : marker.indent.replace(/^[ \t]/, "");
      var shortened = less + text.slice(ls + marker.indent.length, le);
      return {select: [ls, le], insert: shortened,
              caret: ls + shortened.length};
    }
    return {select: [ls, le], insert: "", caret: ls};
  }
  return {select: [selStart, selStart], insert: "\n" + marker.next};
}

/* What pressing Tab, or Shift+Tab, should do. */
function tcIndentAction(text, selStart, selEnd, outdent) {
  var ls = tcLineStart(text, selStart);
  var le = tcLineEnd(text, selEnd);
  var oneLine = ls === tcLineStart(text, selEnd);

  if (!outdent && selStart === selEnd && oneLine) {
    var marker = tcListMarker(text.slice(ls, le));
    var inTheMargin = marker
      ? selStart <= ls + marker.prefix.length
      : selStart <= ls + text.slice(ls, le).match(/^[ \t]*/)[0].length;
    if (!inTheMargin) {
      /* Mid-word: an indent here is just a wider space, the way Tab behaves
         in any editor that does not know what the line means. */
      return {select: [selStart, selStart], insert: TC_INDENT,
              caret: selStart + TC_INDENT.length};
    }
  }

  var block = text.slice(ls, le);
  var moved = 0;
  var lines = block.split("\n").map(function (line) {
    if (outdent) {
      var m = line.match(/^(?: {1,__WIDTH__}|\t)/);
      if (!m) {
        return line;
      }
      moved -= m[0].length;
      return line.slice(m[0].length);
    }
    if (line === "") {
      /* Indenting nothing would leave trailing spaces behind on a blank
         line, which some Markdown readers turn into a line break. */
      return line;
    }
    moved += TC_INDENT.length;
    return TC_INDENT + line;
  });
  var replacement = lines.join("\n");
  if (replacement === block) {
    return null;
  }
  var firstLineMoved = outdent
    ? -Math.min(TC_INDENT.length, block.match(/^(?: *|\t)/)[0].length)
    : (block.split("\n")[0] === "" ? 0 : TC_INDENT.length);
  return {
    select: [ls, le],
    insert: replacement,
    /* Keep hold of whatever was selected, so Tab can be pressed twice. */
    selectAfter: selStart === selEnd
      ? [Math.max(ls, selStart + firstLineMoved),
         Math.max(ls, selStart + firstLineMoved)]
      : [Math.max(ls, selStart + firstLineMoved), le + moved]
  };
}

/* ---- the colours ------------------------------------------------------ */

function tcEscape(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
             .replace(/>/g, "&gt;");
}

function tcRun(text, cls) {
  return cls ? '<span class="' + cls + '">' + tcEscape(text) + "</span>"
             : tcEscape(text);
}

var TC_INLINE = /(`+)([\s\S]*?)\1|(\*\*|__)([\s\S]+?)\3|(~~)([\s\S]+?)\5|([*_])([^*_\n]+?)\7|(\[)([^\]\n]*)(\]\()([^)\n]*)(\))/g;

/* Inline markup, on one line, as a string of <span>s.

   Markers get their own class so they can be dimmed: what is wanted at a
   glance is the text, with the asterisks and brackets fading into the
   background rather than competing with it. */
function tcInline(line) {
  var out = "";
  var at = 0;
  var m;
  TC_INLINE.lastIndex = 0;
  while ((m = TC_INLINE.exec(line)) !== null) {
    if (m.index > at) {
      out += tcRun(line.slice(at, m.index), "");
    }
    if (m[1] !== undefined) {
      out += tcRun(m[1], "tcm") + tcRun(m[2], "tcc") + tcRun(m[1], "tcm");
    } else if (m[3] !== undefined) {
      out += tcRun(m[3], "tcm") + tcRun(m[4], "tcb") + tcRun(m[3], "tcm");
    } else if (m[5] !== undefined) {
      out += tcRun(m[5], "tcm") + tcRun(m[6], "tcs") + tcRun(m[5], "tcm");
    } else if (m[7] !== undefined) {
      out += tcRun(m[7], "tcm") + tcRun(m[8], "tci") + tcRun(m[7], "tcm");
    } else {
      out += tcRun(m[9], "tcm") + tcRun(m[10], "tcl") + tcRun(m[11], "tcm")
           + tcRun(m[12], "tcu") + tcRun(m[13], "tcm");
    }
    at = m.index + m[0].length;
  }
  return out + tcRun(line.slice(at), "");
}

/* The whole note, marked up line by line.

   Line by line because that is the only reading that cannot lose track: a
   note is edited while it is half-written, and a construction that spans
   lines is as often unfinished as it is wrong. The one exception is the
   fenced code block, which is too useful to leave out and can be followed
   with a single flag. */
function tcHighlight(text) {
  var fenced = false;
  var lines = text.split("\n").map(function (line) {
    var fence = line.match(/^([ \t]*)(```+|~~~+)([\s\S]*)$/);
    if (fence) {
      fenced = !fenced;
      return tcRun(fence[1], "") + tcRun(fence[2], "tcm")
           + tcRun(fence[3], "tcc");
    }
    if (fenced) {
      return tcRun(line, "tcc");
    }
    var heading = line.match(/^([ \t]*)(#{1,6})([ \t]+)([\s\S]*)$/);
    if (heading) {
      return tcRun(heading[1], "") + tcRun(heading[2] + heading[3], "tcm")
           + tcRun(heading[4], "tch");
    }
    if (/^[ \t]*(?:[-*_][ \t]*){3,}$/.test(line) && line.trim() !== "") {
      return tcRun(line, "tcm");
    }
    var marker = tcListMarker(line);
    if (marker) {
      var rest = marker.kind === "quote"
        ? '<span class="tcq">' + tcInline(marker.body) + "</span>"
        : tcInline(marker.body);
      return tcRun(marker.prefix, "tcm") + rest;
    }
    return tcInline(line);
  });
  /* A trailing empty line has no height of its own; without this the
     overlay ends a line short of the textarea and the last line of a long
     note scrolls out of step. */
  return lines.join("\n") + "\n";
}
"""


# ---------------------------------------------------------------------------
# The part that needs a browser: finding Streamlit's textareas, keeping the
# overlay on top of them, and putting the answers above into effect.
# ---------------------------------------------------------------------------

_WIRING_JS = r"""
(function () {
  var KEYS = __KEYS__;
  var doc, win;
  try {
    doc = window.parent.document;
    win = window.parent;
  } catch (err) {
    /* No reach into the page. The field stays an ordinary text box, which
       is a working notes editor - just not an improved one. */
    return;
  }
  if (!doc || !doc.body) {
    return;
  }

  function styleSheet() {
    var existing = doc.getElementById("tc-md-editor-style");
    if (existing) {
      return;
    }
    var dark = isDark();
    var css = doc.createElement("style");
    css.id = "tc-md-editor-style";
    css.textContent = [
      ".tc-md-overlay {",
      "  position: absolute; inset: 0; overflow: hidden;",
      "  pointer-events: none; z-index: 0;",
      "  -webkit-user-select: none; user-select: none;",
      "}",
      ".tc-md-live { position: relative !important; z-index: 1 !important;",
      "  background-color: transparent !important; }",
      /* Translucent, so the coloured text behind stays readable while it is
         selected. An opaque selection would paint over the only copy of the
         text there is. */
      ".tc-md-live::selection { background: " +
        (dark ? "rgba(120,160,255,0.35)" : "rgba(60,110,220,0.25)") + "; }",
      ".tcm { color: " + (dark ? "#7b8494" : "#9aa0aa") + "; }",
      ".tch { color: " + (dark ? "#7fb2ff" : "#1f5fd0") + "; }",
      ".tcb { color: " + (dark ? "#ffc46b" : "#9a5b00") + "; }",
      ".tci { color: " + (dark ? "#c7a2ff" : "#6b3fb8") + "; }",
      ".tcs { color: " + (dark ? "#7b8494" : "#9aa0aa") + ";",
      "  text-decoration: line-through; }",
      ".tcc { color: " + (dark ? "#7ddba1" : "#0f7b46") + "; }",
      ".tcq { color: " + (dark ? "#9fb0c9" : "#5a6b84") + "; }",
      ".tcl { color: " + (dark ? "#7fb2ff" : "#1f5fd0") + "; }",
      ".tcu { color: " + (dark ? "#7b8494" : "#9aa0aa") + ";",
      "  text-decoration: underline; }"
    ].join("\n");
    doc.head.appendChild(css);
  }

  /* Which of the two palettes to use. Read from what is actually painted
     rather than from a setting, because the theme can be the operating
     system's and never appear in config.json at all. */
  function isDark() {
    var body = win.getComputedStyle(doc.body).backgroundColor;
    var rgb = (body || "").match(/\d+/g);
    if (!rgb || rgb.length < 3) {
      return false;
    }
    var luminance = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255;
    return luminance < 0.5;
  }

  var METRICS = [
    "fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight",
    "letterSpacing", "wordSpacing", "textIndent", "textTransform",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth",
    "borderLeftWidth", "boxSizing", "tabSize"
  ];

  function copyMetrics(area, overlay) {
    var from = win.getComputedStyle(area);
    METRICS.forEach(function (name) {
      overlay.style[name] = from[name];
    });
    overlay.style.borderStyle = "solid";
    overlay.style.borderColor = "transparent";
    overlay.style.whiteSpace = "pre-wrap";
    overlay.style.overflowWrap = "break-word";
    overlay.style.wordBreak = from.wordBreak;
    /* Not from `from`: getComputedStyle hands back a live view of the
       element, and by the time this runs again the textarea's own colour is
       the transparent one set below. Copying that would make every
       unmarked word - which is most of the note - invisible. */
    overlay.style.color = area.tcInk || from.color;
  }

  function paint(area) {
    var overlay = area.tcOverlay;
    if (!overlay) {
      return;
    }
    overlay.innerHTML = tcHighlight(area.value);
    overlay.scrollTop = area.scrollTop;
    overlay.scrollLeft = area.scrollLeft;
  }

  function overlayFor(area) {
    var host = area.parentElement;
    if (!host) {
      return;
    }
    if (win.getComputedStyle(host).position === "static") {
      host.style.position = "relative";
    }
    var overlay = doc.createElement("div");
    overlay.className = "tc-md-overlay";
    host.insertBefore(overlay, area);
    area.tcOverlay = overlay;
    /* The colour the note is written in, remembered before the textarea's
       own glyphs are made transparent - it is the one thing here that
       cannot be read back off the element afterwards. */
    area.tcInk = win.getComputedStyle(area).color;
    area.classList.add("tc-md-live");
    copyMetrics(area, overlay);
    /* The glyphs go, the caret stays. */
    area.style.setProperty("caret-color", area.tcInk, "important");
    area.style.setProperty("color", "transparent", "important");
    area.addEventListener("scroll", function () { paint(area); });
    area.addEventListener("input", function () { paint(area); });
    if (win.ResizeObserver) {
      new win.ResizeObserver(function () {
        copyMetrics(area, overlay);
        paint(area);
      }).observe(area);
    }
    paint(area);
  }

  /* Carries out one of the answers from the pure half. */
  function apply(area, action) {
    if (!action) {
      return false;
    }
    area.setSelectionRange(action.select[0], action.select[1]);
    doc.execCommand("insertText", false, action.insert);
    if (action.selectAfter) {
      area.setSelectionRange(action.selectAfter[0], action.selectAfter[1]);
    } else if (typeof action.caret === "number") {
      area.setSelectionRange(action.caret, action.caret);
    }
    paint(area);
    return true;
  }

  function onKeyDown(event) {
    var area = event.currentTarget;
    if (event.isComposing || event.metaKey || event.ctrlKey || event.altKey) {
      return;     /* Ctrl+Enter still submits, and shortcuts still work */
    }
    if (event.key === "Enter" && !event.shiftKey) {
      if (apply(area, tcEnterAction(area.value, area.selectionStart,
                                    area.selectionEnd))) {
        event.preventDefault();
      }
      return;
    }
    if (event.key === "Tab") {
      var action = tcIndentAction(area.value, area.selectionStart,
                                  area.selectionEnd, event.shiftKey);
      /* Even when there is nothing to outdent, Tab must not move the focus
         away - the note is being written, not filled in. */
      event.preventDefault();
      apply(area, action);
      return;
    }
    if (event.key === "Escape") {
      /* The way out, for a keyboard. Tab belongs to the editor now, so
         something else has to hand the focus on. */
      area.blur();
    }
  }

  function attach(area) {
    if (area.dataset.tcMdEditor) {
      return;
    }
    area.dataset.tcMdEditor = "1";
    styleSheet();
    area.addEventListener("keydown", onKeyDown);
    overlayFor(area);
  }

  function scan() {
    KEYS.forEach(function (key) {
      var selector = ".st-key-" + (win.CSS && win.CSS.escape
        ? win.CSS.escape(key) : key) + " textarea";
      doc.querySelectorAll(selector).forEach(attach);
    });
  }

  scan();
  /* Streamlit rebuilds the page on every redraw, and a redraw happens
     whenever anything at all is clicked. Whatever it puts back has to be
     found again. */
  if (!win.tcMdEditorWatching) {
    win.tcMdEditorWatching = true;
    new win.MutationObserver(function () {
      win.clearTimeout(win.tcMdEditorTimer);
      win.tcMdEditorTimer = win.setTimeout(function () {
        (win.tcMdEditorScans || []).forEach(function (again) { again(); });
      }, 60);
    }).observe(doc.body, {childList: true, subtree: true});
  }
  win.tcMdEditorScans = (win.tcMdEditorScans || []).filter(function (fn) {
    return fn.tcKeys !== JSON.stringify(KEYS);
  });
  scan.tcKeys = JSON.stringify(KEYS);
  win.tcMdEditorScans.push(scan);
})();
"""


def editor_script(keys, indent=INDENT):
    """
    The JavaScript that turns the named fields into Markdown editors.

    :param keys: The Streamlit widget keys of the text areas to enhance.
                 Streamlit writes each one into the page as a class,
                 ``st-key-<key>``, which is what the script looks for.
    :param indent: One level of indentation, for Tab.
    :return: The script, without any surrounding <script> tag.
    :rtype: str
    """
    keys = [str(key) for key in keys]
    # json.dumps already makes each key a single JavaScript string, escapes
    # included. What it leaves alone is the slash, and this ends up between
    # <script> and </script>: a key containing "</script>" would close the
    # tag early and print the rest of the editor onto the page. Escaping the
    # slash means nothing to JSON and everything to the parser reading the
    # tag. The keys are built from task ids today, so this is insurance
    # rather than a fix.
    embedded = json.dumps(keys).replace('</', '<\\/')
    pure = (_PURE_JS
            .replace('__INDENT__', indent.replace('\\', '\\\\').replace('"', '\\"'))
            .replace('__WIDTH__', str(len(indent))))
    return pure + _WIRING_JS.replace('__KEYS__', embedded)


def editor_html(keys, indent=INDENT):
    """
    The same, wrapped for st.components.v1.html().

    :return: A complete fragment to hand to the components API, which renders
             it in an iframe of its own. The iframe shows nothing; everything
             it does, it does to the page around it.
    :rtype: str
    """
    return '<script>\n%s\n</script>' % editor_script(keys, indent)
