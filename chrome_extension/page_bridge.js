(function () {
  'use strict';

  const SOURCE = 'programiz-code-typer-bridge';
  const TARGET = 'programiz-code-typer-content';
  const LOG = '[Bridge]';

  function getCM6View() {
    const el = document.querySelector('.cm-editor');
    if (!el) return null;
    if (el.cmView && el.cmView.view) return el.cmView.view;
    for (const k of Object.keys(el)) {
      if (k.startsWith('__') || k.startsWith('$')) {
        try {
          if (el[k] && el[k].view && el[k].view.state) return el[k].view;
        } catch (e) { }
      }
    }
    return null;
  }

  function getCM5Instance() {
    const el = document.querySelector('.CodeMirror');
    if (el && el.CodeMirror) return el.CodeMirror;
    return null;
  }

  function getAceEditor() {
    const el = document.querySelector('.ace_editor');
    if (el && el.env && el.env.editor) return el.env.editor;
    if (typeof ace !== 'undefined') {
      try { return ace.edit(document.querySelector('.ace_editor')); } catch (e) { }
    }
    return null;
  }

  function getMonacoEditor() {
    // Path 1: Global monaco API
    if (typeof monaco !== 'undefined' && monaco.editor) {
      const editors = monaco.editor.getEditors ? monaco.editor.getEditors() : [];
      if (editors.length > 0) {
        // Return the focused editor if possible
        for (const ed of editors) {
          if (ed.hasTextFocus && ed.hasTextFocus()) return ed;
        }
        // Fallback to first editor
        return editors[0];
      }
    }

    // Path 2: Look for instance on DOM nodes
    // Try to find the focused one first
    const activeElement = document.activeElement;
    if (activeElement) {
      const closestEditor = activeElement.closest('.monaco-editor');
      if (closestEditor) {
        // Try to find the instance on this element
        for (const key of Object.keys(closestEditor)) {
          if (closestEditor[key] && closestEditor[key].editor) return closestEditor[key].editor;
        }
        if (closestEditor.parentElement && closestEditor.parentElement.editor) return closestEditor.parentElement.editor;
      }
    }

    const models = document.querySelectorAll('.monaco-editor');
    for (const el of models) {
      if (el && el.parentElement && el.parentElement.editor) return el.parentElement.editor;
      for (const key of Object.keys(el)) {
        if (el[key] && el[key].editor) return el[key].editor;
      }
    }
    return null;
  }

  function getTextarea() {
    const selectors = [
      'textarea[data-cy="code-editor"]',
      'textarea.cm-content',
      '#editor textarea',
      '.editor-container textarea',
      'textarea'
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function detectEditor() {
    // Check Monaco FIRST (HackerRank priority)
    const mon = getMonacoEditor();
    if (mon) return { type: 'monaco', instance: mon };

    const cm6 = getCM6View();
    if (cm6) return { type: 'cm6', instance: cm6 };
    const cm5 = getCM5Instance();
    if (cm5) return { type: 'cm5', instance: cm5 };
    const ace = getAceEditor();
    if (ace) return { type: 'ace', instance: ace };
    const ta = getTextarea();
    if (ta) return { type: 'textarea', instance: ta };
    return null;
  }

  function getEditorContent() {
    const ed = detectEditor();
    if (!ed) return null;
    switch (ed.type) {
      case 'cm6': return ed.instance.state.doc.toString();
      case 'cm5': return ed.instance.getValue();
      case 'ace': return ed.instance.getValue();
      case 'monaco': return ed.instance.getValue();
      case 'textarea': return ed.instance.value;
    }
    return null;
  }

  function setEditorContent(text) {
    const ed = detectEditor();
    if (!ed) return false;

    // FORCE FOCUS
    try { if (ed.instance.focus) ed.instance.focus(); } catch (e) { }

    switch (ed.type) {
      case 'cm6':
        ed.instance.dispatch({ changes: { from: 0, to: ed.instance.state.doc.length, insert: text } });
        return true;
      case 'cm5':
        ed.instance.setValue(text);
        return true;
      case 'ace':
        ed.instance.setValue(text);
        ed.instance.setBehavioursEnabled(false);
        return true;
      case 'monaco':
        try {
          const editor = ed.instance;
          const val = text || '';

          // If we are clearing (val is empty), use SIMPLER TRIGGERS
          // (getAction causes 'command not found' errors on HackerRank)
          if (val === '') {
            editor.focus();

            // Strategy 1: setValue (Best if working)
            let setWorked = false;
            try {
              const model = editor.getModel ? editor.getModel() : null;
              if (model) {
                model.setValue('');
                if (model.getValue() === '') setWorked = true;
              }
            } catch (e) { }

            if (!setWorked && editor.setValue) {
              try {
                editor.setValue('');
                setWorked = true;
              } catch (e) { }
            }

            // Strategy 2: Triggers (Backup)
            if (!setWorked) {
              // Try multiple variants of selectAll common in different Monaco versions
              editor.trigger('keyboard', 'selectAll');
              editor.trigger('any', 'editor.action.selectAll');

              // Then delete
              editor.trigger('keyboard', 'deleteLeft');
              editor.trigger('any', 'deleteLeft');
            }

          } else {
            // Setting content
            const model = editor.getModel ? editor.getModel() : null;
            if (model) {
              model.setValue(val);
            } else if (editor.setValue) {
              editor.setValue(val);
            }
          }

          if (editor.layout) editor.layout();
        } catch (e) {
          console.error(LOG, 'Monaco clear failed:', e);
          const ta = document.querySelector('.monaco-editor textarea');
          if (ta) {
            ta.value = text || '';
            ta.dispatchEvent(new Event('input', { bubbles: true }));
          }
        }
        return true;
      case 'textarea':
        setTextareaValue(ed.instance, text);
        return true;
    }
    return false;
  }

  function setTextareaValue(ta, text) {
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    nativeSetter.call(ta, text);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    ta.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function insertChar(char) {
    const ed = detectEditor();
    if (!ed) return false;

    // FORCE FOCUS
    try { if (ed.instance.focus) ed.instance.focus(); } catch (e) { }

    switch (ed.type) {
      case 'cm6': {
        const pos = ed.instance.state.doc.length;
        ed.instance.dispatch({ changes: { from: pos, insert: char } });
        return true;
      }
      case 'cm5': {
        const doc = ed.instance.getDoc();
        const cursor = doc.getCursor();
        doc.replaceRange(char, cursor);
        return true;
      }
      case 'ace': {
        ed.instance.setBehavioursEnabled(false);
        const session = ed.instance.getSession();
        const doc = session.getDocument();
        const lastRow = doc.getLength() - 1;
        const lastCol = doc.getLine(lastRow).length;
        doc.insert({ row: lastRow, column: lastCol }, char);
        return true;
      }
      case 'monaco': {
        const editor = ed.instance;
        if (!editor || !editor.getModel) return false;
        const model = editor.getModel();
        if (!model) return false;

        try {
          const pos = model.getFullModelRange().getEndPosition();
          editor.setPosition(pos);
          editor.trigger('keyboard', 'type', { text: char });
          return true;
        } catch (e) {
          return false;
        }
      }
      case 'textarea': {
        const ta = ed.instance;
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        ta.value = ta.value.substring(0, start) + char + ta.value.substring(end);
        ta.selectionStart = ta.selectionEnd = start + char.length;
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
      }
    }
    return false;
  }

  window.addEventListener('message', function (event) {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== TARGET) return;

    const { command, payload, id } = event.data;
    let result = null;
    let success = false;

    // Safety: If this frame doesn't have an editor (and it's not a ping), ignore!
    const ed = detectEditor();
    if (!ed && command !== 'ping') return;

    switch (command) {
      case 'getContent':
        result = getEditorContent();
        success = result !== null;
        break;
      case 'setContent':
        success = setEditorContent(payload);
        break;
      case 'clear':
        success = setEditorContent('');
        break;
      case 'insertChar':
        success = insertChar(payload);
        break;
      case 'stealthBackspace': {
        // Backspace N times
        const count = payload || 1;
        const ed = detectEditor();
        if (ed) {
          for (let i = 0; i < count; i++) {
            if (ed.type === 'monaco') {
              ed.instance.trigger('keyboard', 'deleteLeft');
            } else if (ed.type === 'cm6') {
              ed.instance.dispatch({ changes: { from: ed.instance.state.selection.main.head - 1, to: ed.instance.state.selection.main.head, insert: '' } });
            } else {
              // Generic fallback for others
              success = insertChar(''); // Try to write empty? No.
              // Actually let's just focus and hope execCommand works for others?
              // But Monaco is priority.
            }
          }
          success = true;
        }
        break;
      }
      case 'ping': {
        success = true;
        result = ed ? ed.type : 'no-editor';
        break;
      }
    }

    window.postMessage({ source: SOURCE, id: id, success: success, result: result }, '*');
  });

  function initBridge() {
    const ed = detectEditor();
    if (ed) {
      console.log(LOG, 'Ready. Found editor:', ed.type);
      window.postMessage({ source: SOURCE, ready: true, editorType: ed.type }, '*');
    } else {
      // Keep trying if not found yet (important for HackerRank/slow loads)
      setTimeout(initBridge, 1000);
    }
  }

  initBridge();
})();
