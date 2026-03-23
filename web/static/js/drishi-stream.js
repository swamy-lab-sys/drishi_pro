/**
 * drishi-stream.js — Shared SSE stream manager
 *
 * Uses Web Locks API + BroadcastChannel so only ONE tab holds the real
 * /api/stream SSE connection. All other tabs on the same device receive
 * events via BroadcastChannel (zero extra server connections).
 *
 * When on a phone / different device: falls back to a direct SSE connection.
 * Works on Chrome, Firefox, Safari 15.4+, Edge.
 */
(function () {
  'use strict';

  const BROADCAST_CHANNEL = 'drishi-sse-v1';
  const LOCK_NAME         = 'drishi-sse-leader';
  const SSE_URL           = '/api/stream';

  // Per-event-type registered handlers: { 'chunk': fn, 'answer': fn, ... }
  const _handlers = {};
  let _isLeader   = false;
  let _es         = null;           // EventSource (leader only)
  let _bc         = null;           // BroadcastChannel

  // ── Dispatch an event to this page's registered handler ────────────────────
  function _dispatch(type, rawData) {
    const fn = _handlers[type];
    if (fn) fn(rawData);
  }

  // ── Open (or reopen) the real SSE connection ────────────────────────────────
  function _openSSE() {
    if (_es) { try { _es.close(); } catch (_) {} }
    _es = new EventSource(SSE_URL);

    const EVENT_TYPES = [
      'init', 'question', 'chunk', 'answer',
      'transcribing', 'ping', 'stt_event', 'status', 'error'
    ];

    EVENT_TYPES.forEach(function (type) {
      _es.addEventListener(type, function (e) {
        // 1. Handle in this (leader) tab
        _dispatch(type, e.data);
        // 2. Forward to all follower tabs on this device
        if (_bc) {
          try { _bc.postMessage({ t: type, d: e.data }); } catch (_) {}
        }
      });
    });

    _es.onerror = function () {
      _dispatch('_reconnecting', null);
      // Browser will auto-retry at the SSE retry interval (1000ms set by server)
    };
  }

  // ── Leader mode: hold the lock + SSE connection ─────────────────────────────
  function _becomeLeader() {
    _isLeader = true;
    _openSSE();
    // Keep lock held — returning this promise holds it until the tab closes
    return new Promise(function () {});
  }

  // ── Follower mode: listen on BroadcastChannel ───────────────────────────────
  function _becomeFollower() {
    _isLeader = false;
    _bc.onmessage = function (e) {
      if (e.data && e.data.t) _dispatch(e.data.t, e.data.d);
    };
    // Also queue up to become leader if the current leader tab closes
    navigator.locks.request(LOCK_NAME, function () {
      // We got the lock — previous leader tab closed, now we're the leader
      if (_bc) _bc.onmessage = null;
      return _becomeLeader();
    });
  }

  // ── Boot ────────────────────────────────────────────────────────────────────
  function _init() {
    // Always open a BroadcastChannel (safe even if we end up as leader)
    try {
      _bc = new BroadcastChannel(BROADCAST_CHANNEL);
    } catch (_) {
      _bc = null; // very old browsers — no BroadcastChannel
    }

    if (typeof navigator !== 'undefined' && navigator.locks) {
      // Try to grab the lock immediately (non-blocking)
      navigator.locks.request(LOCK_NAME, { ifAvailable: true }, function (lock) {
        if (lock) {
          return _becomeLeader();          // got it — we are the SSE leader
        }
        // Lock taken by another tab — become a follower
        _becomeFollower();
        return undefined;                  // release the ifAvailable attempt
      });
    } else {
      // Fallback: no Locks API (old browsers / some mobile) — connect directly
      _isLeader = true;
      _openSSE();
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────
  window.DrishiStream = {
    /**
     * Register a handler for an SSE event type.
     * Call before DrishiStream.start().
     *   DrishiStream.on('chunk', function(rawData) { ... });
     */
    on: function (type, fn) { _handlers[type] = fn; },

    /** True if this tab holds the actual SSE connection. */
    isLeader: function () { return _isLeader; },

    /** Start the shared stream (call once after registering all handlers). */
    start: _init,
  };
})();
