/**
 * Knowledge — the renderer half of the Hermes Workspace plugin. A right-side
 * pane over the backend at /api/plugins/hermes-workspace: search, browse, read.
 *
 * Single file by contract: imports resolve only for '@hermes/plugin-sdk',
 * 'react', and 'react/jsx-runtime'. Tasks 14-15 extend register(ctx) with the
 * composer toggle/middleware and the extraction approval pane.
 */

import {
  atom,
  Button,
  COMPOSER_AREAS,
  ConfirmDialog,
  CopyButton,
  EmptyState,
  host,
  PALETTE_AREA,
  SegmentedControl,
  StatusDot,
  Streamdown,
  Textarea,
  PANES_AREA,
  STATUSBAR_AREAS,
  useValue,
} from '@hermes/plugin-sdk'
import { Component, useCallback, useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'hermes-workspace'

/** Set in register(); helpers reach the scoped context through it. */
let CTX = null
const api = (path, opts) => CTX.rest(path, opts)

// ctx.rest has no `query` option (PluginRestOptions = method/body/upload/
// timeoutMs), so GET params are baked into the path.
const qs = (obj) =>
  Object.entries(obj)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&')

// Pane-local view state — one pane instance, module scope is fine. Kept out of
// component state so it survives the view swap (Reader replaces SearchView).
const view$ = atom('search') // 'search' | 'browse' | 'reader' | 'injection'
const openNote$ = atom(null) // vault-relative path
const backView$ = atom('search') // where Reader's back control returns
const query$ = atom('') // search box text

const VIEW_OPTIONS = [
  { id: 'search', label: 'Search' },
  { id: 'browse', label: 'Browse' },
]

function openReader(path, from) {
  backView$.set(from)
  openNote$.set(path)
  view$.set('reader')
}

// ── Vault context: composer toggle + send-time middleware + preview strip ────
// The middleware handler is handed the live draft by core. There is no SDK
// draft atom, so the pre-send strip reads the composer's contentEditable text
// from the DOM. ponytail: the DOM read is the only hook the SDK leaves open —
// it degrades to rendering nothing if the composer markup ever changes.

const vaultOn$ = atom(false) // mirrors ctx.storage 'vaultContext.on'
const lastInjection$ = atom(null) // mirrors ctx.storage 'lastInjection'
const sessionExcludes = new Set() // note paths the user ✕'d, this window only

function setVaultOn(v) {
  vaultOn$.set(!!v)
  try {
    CTX.storage.set('vaultContext.on', !!v)
  } catch {}
}

const withTimeout = (p, ms) =>
  Promise.race([p, new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms))])

// Shared by the middleware and the preview strip. 1.5s ceiling; excludes applied.
async function contextFor(query) {
  const res = await withTimeout(
    api('/context', {
      method: 'POST',
      // excludes are applied server-side: the block must match the notes list
      body: { query, budget_tokens: 1500, k_max: 6, exclude: [...sessionExcludes] },
    }),
    1500,
  )
  const notes = ((res && res.notes) || []).filter((n) => !sessionExcludes.has(n.path))
  return { block: (res && res.block) || '', total_tokens: (res && res.total_tokens) || 0, notes }
}

const composerMiddleware = {
  // Fail-open by contract: never throw, never return null/undefined. A hanging
  // or throwing injector must not block or cancel the send.
  handler: async (draft) => {
    try {
      if (!vaultOn$.get()) return draft
      const q = (draft.text || '').trim()
      if (q.length < 12) return draft

      let res
      try {
        res = await contextFor(q.slice(0, 500))
      } catch {
        host.notify({ kind: 'info', message: 'Vault context skipped (backend timeout)' })
        return draft
      }
      if (!res.notes.length || !res.block) return draft

      const injection = {
        ts: Date.now(),
        query: q,
        notes: res.notes.map((n) => n.path),
        block: res.block,
      }
      lastInjection$.set(injection)
      try {
        CTX.storage.set('lastInjection', injection)
      } catch {}

      return { ...draft, text: res.block + '\n\n' + draft.text }
    } catch {
      return draft
    }
  },
}

function TogglePill() {
  const on = useValue(vaultOn$)
  return jsx(Button, {
    size: 'sm',
    variant: on ? 'default' : 'ghost',
    onClick: () => setVaultOn(!on),
    title: 'Prepend relevant vault notes to your next message',
    children: on ? 'Vault context: on' : 'Vault context: off',
  })
}

const stripLinkStyle = {
  background: 'none',
  border: 'none',
  padding: 0,
  marginLeft: 8,
  font: 'inherit',
  color: 'inherit',
  opacity: 0.75,
  cursor: 'pointer',
  textDecoration: 'underline',
}

function PreviewStrip() {
  const on = useValue(vaultOn$)
  const anchorRef = useRef(null)
  const [info, setInfo] = useState(null)

  useEffect(() => {
    if (!on) {
      setInfo(null)
      return
    }
    let live = true
    let lastText = null
    let lastQuery = null
    let settle = null

    const readDraft = () => {
      const root = anchorRef.current && anchorRef.current.closest('[data-slot="composer-root"]')
      const box = root && root.querySelector('[role="textbox"]')
      return box ? (box.textContent || '').trim() : ''
    }

    const tick = () => {
      const text = readDraft()
      if (text === lastText) return
      lastText = text
      if (settle) clearTimeout(settle)
      settle = setTimeout(() => {
        if (!text || text.length < 12) {
          lastQuery = null
          setInfo(null)
          return
        }
        const q = text.slice(0, 500)
        if (q === lastQuery) return
        lastQuery = q
        contextFor(q)
          .then((r) => live && setInfo(r))
          .catch(() => live && setInfo(null))
      }, 400)
    }

    tick()
    const id = setInterval(tick, 350)
    return () => {
      live = false
      clearInterval(id)
      if (settle) clearTimeout(settle)
    }
  }, [on])

  if (!on) return null

  return jsx('div', {
    ref: anchorRef,
    style: { fontSize: 11, opacity: 0.8, padding: info && info.notes.length ? '2px 10px 4px' : 0 },
    children:
      info && info.notes.length
        ? jsxs('div', {
            children: [
              `vault context: ${info.notes.length} note${info.notes.length === 1 ? '' : 's'}`,
              info.notes.map((n) =>
                jsx(
                  'button',
                  {
                    type: 'button',
                    onClick: () => {
                      sessionExcludes.add(n.path)
                      setInfo((cur) =>
                        cur ? { ...cur, notes: cur.notes.filter((x) => x.path !== n.path) } : cur,
                      )
                    },
                    style: stripLinkStyle,
                    title: `Exclude ${n.path}`,
                    children: `✕ ${n.path.split('/').pop()}`,
                  },
                  n.path,
                ),
              ),
              jsx(
                'button',
                { type: 'button', onClick: () => setVaultOn(false), style: stripLinkStyle, children: 'off' },
                '__off',
              ),
            ],
          })
        : null,
  })
}

function InjectionView() {
  const last = useValue(lastInjection$)
  if (!last) {
    return jsx('div', {
      style: { padding: 12, fontSize: 12, opacity: 0.6 },
      children: 'No vault context has been injected yet.',
    })
  }
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 },
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 8, alignItems: 'center', padding: 6, flexWrap: 'wrap' },
        children: [
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => view$.set('search'), children: '‹ Back' }),
          jsx(Button, {
            size: 'xs',
            variant: 'ghost',
            onClick: () => CTX.os.writeClipboard(last.block),
            children: 'Copy block',
          }),
        ],
      }),
      jsxs('div', {
        style: { padding: '0 10px', fontSize: 11, opacity: 0.6 },
        children: [
          new Date(last.ts).toLocaleString(),
          ' · ',
          `${last.notes.length} note${last.notes.length === 1 ? '' : 's'}`,
        ],
      }),
      jsx('div', {
        title: last.query,
        style: {
          padding: '2px 10px 6px',
          fontSize: 12,
          fontWeight: 600,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        },
        children: last.query,
      }),
      jsx('div', {
        style: { overflow: 'auto', flex: 1, minHeight: 0, padding: '0 10px 16px' },
        children: jsx('pre', {
          style: {
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 12,
            margin: 0,
            fontFamily: 'inherit',
          },
          children: last.block,
        }),
      }),
    ],
  })
}

// The FTS snippet wraps matches in <b>…</b>. Render it as text nodes (any other
// markup in the note body stays literal — never injected as HTML).
function excerptNodes(ex) {
  const out = []
  let bold = false
  String(ex || '')
    .split(/(<\/?b>)/)
    .forEach((part, i) => {
      if (part === '<b>') return void (bold = true)
      if (part === '</b>') return void (bold = false)
      if (!part) return
      out.push(bold ? jsx('b', { children: part }, i) : part)
    })
  return out
}

function useStatus() {
  const [st, setSt] = useState(null)
  const load = useCallback(() => api('/status').then(setSt).catch(() => {}), [])
  useEffect(() => {
    let live = true
    const tick = () => api('/status').then((r) => live && setSt(r)).catch(() => {})
    tick()
    const id = setInterval(tick, 5000)
    return () => {
      live = false
      clearInterval(id)
    }
  }, [])
  return [st, load]
}

function Header({ st, refreshStatus }) {
  const [busy, setBusy] = useState(false)
  const tone = !st ? 'muted' : !st.vault_exists ? 'bad' : st.indexing ? 'warn' : 'good'
  const name = st && st.vault_path ? st.vault_path.split(/[\\/]/).filter(Boolean).pop() : 'No vault'
  const count =
    st && st.vault_exists && st.note_count != null
      ? `${st.note_count} note${st.note_count === 1 ? '' : 's'} · ${st.indexing ? 'indexing…' : 'indexed'}`
      : ''

  const reindex = () => {
    setBusy(true)
    api('/reindex', { method: 'POST', body: { full: false } })
      .catch(() => {})
      .finally(() => {
        setBusy(false)
        refreshStatus()
      })
  }

  return jsxs('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '8px 10px',
      borderBottom: '1px solid var(--ui-stroke-secondary, rgba(128,128,128,0.2))',
    },
    children: [
      jsx(StatusDot, { tone }),
      jsxs('div', {
        style: { flex: 1, minWidth: 0 },
        children: [
          jsx('div', {
            title: st && st.vault_path ? st.vault_path : undefined,
            style: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
            children: name,
          }),
          count &&
            jsx('div', {
              style: { fontSize: 11, opacity: 0.6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
              children: count,
            }),
        ],
      }),
      st &&
        st.vault_exists &&
        jsx(Button, {
          size: 'xs',
          variant: 'ghost',
          disabled: busy || st.indexing,
          onClick: reindex,
          children: busy ? '…' : 'Reindex',
        }),
    ],
  })
}

function SearchView() {
  const q = useValue(query$)
  const [rows, setRows] = useState([])
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!q.trim()) {
      setRows([])
      setNote('')
      return
    }
    const t = setTimeout(() => {
      api('/search', { method: 'POST', body: { query: q, limit: 20 } })
        .then((r) => {
          setRows(r.results || [])
          setNote(r.error ? 'Vault not indexed yet.' : (r.results || []).length ? '' : 'No matches.')
        })
        .catch(() => {
          setRows([])
          setNote('Search failed.')
        })
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 },
    children: [
      jsx('div', {
        style: { padding: 8 },
        children: jsx('input', {
          value: q,
          autoFocus: true,
          placeholder: 'Search the vault…',
          onChange: (e) => query$.set(e.target.value),
          style: {
            width: '100%',
            boxSizing: 'border-box',
            padding: '6px 8px',
            fontSize: 13,
            borderRadius: 4,
            border: '1px solid var(--ui-stroke-secondary, rgba(128,128,128,0.3))',
            background: 'var(--ui-bg-tertiary, transparent)',
            color: 'inherit',
            outline: 'none',
          },
        }),
      }),
      note &&
        jsx('div', { style: { padding: '0 10px 8px', fontSize: 12, opacity: 0.6 }, children: note }),
      jsx('div', {
        style: { overflow: 'auto', flex: 1, minHeight: 0 },
        children: rows.map((r) =>
          jsxs('div', {
            onClick: () => openReader(r.path, 'search'),
            style: { padding: '8px 10px', cursor: 'pointer', borderBottom: '1px solid rgba(128,128,128,0.12)' },
            children: [
              jsx('div', { style: { fontWeight: 600 }, children: r.title || r.path }),
              jsx('div', {
                style: { fontSize: 11, opacity: 0.55, margin: '2px 0' },
                children: r.path.split('/').slice(0, -1).join(' / '),
              }),
              jsx('div', { style: { fontSize: 12, opacity: 0.85 }, children: excerptNodes(r.excerpt) }),
            ],
          }, r.path)
        ),
      }),
    ],
  })
}

function BrowseView() {
  const [path, setPath] = useState(() => {
    try {
      return CTX.storage.get('browse.path', '')
    } catch {
      return ''
    }
  })
  const [tree, setTree] = useState({ dirs: [], files: [] })
  const [err, setErr] = useState(false)

  useEffect(() => {
    try {
      CTX.storage.set('browse.path', path)
    } catch {}
    setErr(false)
    api('/tree' + (path ? '?' + qs({ path }) : ''))
      .then((r) => setTree({ dirs: r.dirs || [], files: r.files || [] }))
      .catch(() => {
        setErr(true)
        setTree({ dirs: [], files: [] })
      })
  }, [path])

  const row = (label, onClick, key) =>
    jsx('div', {
      onClick,
      style: { padding: '6px 10px', cursor: 'pointer', fontSize: 13, borderBottom: '1px solid rgba(128,128,128,0.1)' },
      children: label,
    }, key)

  return jsxs('div', {
    style: { overflow: 'auto', flex: 1, minHeight: 0 },
    children: [
      path && row('⬅  ..', () => setPath(path.split('/').slice(0, -1).join('/')), '..'),
      err && jsx('div', { style: { padding: 10, fontSize: 12, opacity: 0.6 }, children: 'Could not read folder.' }),
      tree.dirs.map((d) => row('📁  ' + d.split('/').pop(), () => setPath(d), 'd:' + d)),
      tree.files.map((f) => row('📄  ' + (f.title || f.path), () => openReader(f.path, 'browse'), 'f:' + f.path)),
      !err && !path && !tree.dirs.length && !tree.files.length
        ? jsx('div', { style: { padding: 10, fontSize: 12, opacity: 0.6 }, children: 'Vault is empty.' })
        : null,
    ],
  })
}

function ReaderView() {
  const path = useValue(openNote$)
  const back = useValue(backView$)
  const [note, setNote] = useState(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    if (!path) return
    setNote(null)
    setErr(false)
    api('/note?' + qs({ path }))
      .then(setNote)
      .catch(() => setErr(true))
  }, [path])

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 },
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 4, padding: 6, flexWrap: 'wrap', alignItems: 'center' },
        children: [
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => view$.set(back), children: '‹ Back' }),
          note &&
            jsx(Button, {
              size: 'xs',
              variant: 'ghost',
              onClick: () => CTX.os.openExternal('obsidian://open?path=' + encodeURIComponent(note.abspath)),
              children: 'Open in Obsidian',
            }),
          note &&
            jsx(Button, {
              size: 'xs',
              variant: 'ghost',
              onClick: () => CTX.os.revealPath(note.abspath),
              children: 'Reveal',
            }),
        ],
      }),
      jsx('div', {
        style: { overflow: 'auto', flex: 1, minHeight: 0, padding: '0 12px 16px', fontSize: 13 },
        children: err
          ? jsx(EmptyState, { title: 'Could not open note' })
          : !note
            ? jsx('div', { style: { padding: 8, fontSize: 12, opacity: 0.6 }, children: 'Loading…' })
            : jsx(Streamdown, { children: note.markdown }),
      }),
    ],
  })
}

function KnowledgePane() {
  const view = useValue(view$)
  const [st, refreshStatus] = useStatus()
  const noVault = st && !st.vault_exists

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontSize: 13 },
    children: [
      jsx(Header, { st, refreshStatus }),
      noVault
        ? jsx('div', {
            style: { padding: 16 },
            children: jsx(EmptyState, {
              title: 'No vault configured',
              description: 'Set your Obsidian vault path in Settings ▸ Plugins ▸ Knowledge, then Reindex.',
            }),
          })
        : jsxs('div', {
            style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 },
            children: [
              view !== 'reader' &&
                jsxs('div', {
                  style: { padding: '6px 8px', display: 'flex', gap: 6, alignItems: 'center' },
                  children: [
                    jsx('div', {
                      style: { flex: 1, minWidth: 0 },
                      children: jsx(SegmentedControl, {
                        options: VIEW_OPTIONS,
                        value: view === 'browse' ? 'browse' : 'search',
                        onChange: (v) => view$.set(v),
                      }),
                    }),
                    jsx(Button, {
                      size: 'xs',
                      variant: view === 'injection' ? 'default' : 'ghost',
                      onClick: () => view$.set(view === 'injection' ? 'search' : 'injection'),
                      title: 'Last vault-context injection',
                      children: 'Injection',
                    }),
                  ],
                }),
              view === 'reader'
                ? jsx(ReaderView, {})
                : view === 'injection'
                  ? jsx(InjectionView, {})
                  : view === 'browse'
                    ? jsx(BrowseView, {})
                    : jsx(SearchView, {}),
            ],
          }),
    ],
  })
}

function KnowledgeStatusItem() {
  const [st] = useStatus()
  if (!st || !st.vault_exists || st.note_count == null) return null
  return jsxs('span', {
    style: { display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontSize: 11 },
    title: 'Knowledge vault' + (st.vault_path ? ': ' + st.vault_path : ''),
    children: [
      jsx(StatusDot, { tone: st.indexing ? 'warn' : 'good' }),
      jsx('span', { children: String(st.note_count) }),
    ],
  })
}

// ── Memory extraction: palette command + approval pane ─────────────────────
// `session.history` / `llm.oneshot` are newer gateway RPCs. Probe once on load;
// if absent, the extract/undo commands never register — the read path (toggle,
// middleware, pane) is wired before this and is unaffected. isMissingRpcMethod
// (apps/desktop/src/lib/gateway-rpc.ts) is not re-exported by the SDK, so its
// regex is inlined here — one file by contract.
const MISSING_RPC = /method not found|-32601|unknown method|no such method/i

let RPC_OK = null
async function rpcAvailable() {
  if (RPC_OK !== null) return RPC_OK
  try {
    const sid = host.state.focusedSessionId.get() || host.state.activeSessionId.get()
    await host.request('session.history', { session_id: sid })
    RPC_OK = true
  } catch (e) {
    // A "no session" / "gateway unavailable" error still proves the method
    // exists — only a missing-method error gates the feature off.
    RPC_OK = !MISSING_RPC.test(String((e && e.message) || e))
  }
  return RPC_OK
}

function sessionId() {
  return (
    host.state.focusedStoredSessionId.get() ||
    host.state.focusedSessionId.get() ||
    host.state.activeSessionId.get() ||
    ''
  )
}

// Transient pane. `Contribution.when` is NOT reactive (re-checked only on a
// registry mutation in its area), so instead of gating a permanent pane we
// register the contribution on open and dispose it on close.
const approval$ = atom({
  open: false,
  phase: 'idle', // idle | loading | review | preview | result | error
  cards: [],
  rejected: [],
  preview: null,
  result: null,
  error: '',
})
let approvalDispose = null

function openApproval(next) {
  approval$.set({
    open: true,
    phase: 'idle',
    cards: [],
    rejected: [],
    preview: null,
    result: null,
    error: '',
    ...next,
  })
  if (!approvalDispose && CTX) {
    approvalDispose = CTX.register({
      id: 'memory-approval',
      area: PANES_AREA,
      title: 'Memory approval',
      // dock: a bare placement:'right' pane stacks into the collapsed `review`
      // zone (Task 13) — anchor it beside the workspace like the Knowledge pane.
      data: { placement: 'right', width: '420px', dock: { pane: 'workspace', pos: 'right' } },
      render: () => jsx(ApprovalPane, {}),
    })
  }
}

function closeApproval() {
  approval$.set({
    open: false,
    phase: 'idle',
    cards: [],
    rejected: [],
    preview: null,
    result: null,
    error: '',
  })
  if (approvalDispose) {
    approvalDispose()
    approvalDispose = null
  }
}

async function callModelAndParse(instructions, input, sid) {
  // Param names match apps/desktop/src/lib/oneshot.ts's snake_case gateway call
  // (maxTokens→max_tokens, sessionId→session_id); returns { text }.
  const r = await host.request('llm.oneshot', {
    instructions,
    input,
    session_id: sid || undefined,
    temperature: 0,
    max_tokens: 2048,
  })
  return api('/extract/parse', { method: 'POST', body: { raw: (r && r.text) || '' } })
}

async function runExtraction() {
  if (approval$.get().phase === 'loading') return // in-flight: no double pipeline / double llm.oneshot
  const sid = sessionId() // stored-first — stable key for resolve/dedup/journal
  // Runtime id: session.history and llm.oneshot resolve against the gateway's
  // live _sessions; a stored key there makes llm.oneshot fall back to the aux
  // model instead of the model the user is chatting with.
  const runtimeSid = host.state.focusedSessionId.get() || host.state.activeSessionId.get() || ''
  openApproval({ phase: 'loading' })
  let messages
  try {
    const r = await host.request('session.history', { session_id: runtimeSid })
    messages = (r && r.messages) || []
  } catch (e) {
    if (MISSING_RPC.test(String((e && e.message) || e))) {
      RPC_OK = false
      closeApproval()
      host.notify({ kind: 'warning', message: 'Memory extraction is unavailable on this Hermes build.' })
      return
    }
    openApproval({ phase: 'error', error: 'Could not read this chat: ' + ((e && e.message) || e) })
    return
  }
  try {
    const prep = await api('/extract/prepare', { method: 'POST', body: { messages } })
    let parsed = await callModelAndParse(prep.prompt, prep.transcript_text, runtimeSid)
    if (parsed.error === 'model_output_unparseable') {
      parsed = await callModelAndParse(
        prep.prompt + '\n\nYour previous reply was not valid JSON. Return only the JSON object.',
        prep.transcript_text,
        runtimeSid,
      )
    }
    if (parsed.error) {
      openApproval({ phase: 'error', error: 'The model did not return usable JSON.' })
      return
    }
    const resolved = await api('/extract/resolve', {
      method: 'POST',
      body: { candidates: parsed.candidates, source_session_id: sid },
    })
    const cards = (resolved.candidates || []).map((c) => ({
      ...c,
      checked: !c.duplicate,
      history_line: c.history_line,
      target_path: c.target_path,
      quoteOpen: false,
    }))
    openApproval({ phase: 'review', cards, rejected: parsed.rejected || [] })
  } catch (e) {
    if (MISSING_RPC.test(String((e && e.message) || e))) {
      RPC_OK = false
      closeApproval()
      host.notify({ kind: 'warning', message: 'Memory extraction is unavailable on this Hermes build.' })
      return
    }
    openApproval({ phase: 'error', error: String((e && e.message) || e) })
  }
}

function selectedItems(s) {
  return s.cards
    .filter((c) => c.checked)
    .map((c) => ({
      target_path: c.target_path,
      history_line: c.history_line,
      supersedes: c.supersedes || null,
      candidate_index: c.candidate_index,
    }))
}

async function doPreview(s) {
  if (approval$.get().busy) return
  approval$.set({ ...approval$.get(), busy: true })
  try {
    const preview = await api('/memories/preview', {
      method: 'POST',
      body: { items: selectedItems(s), source_session_id: sessionId() },
    })
    approval$.set({ ...approval$.get(), busy: false, phase: 'preview', preview })
  } catch (e) {
    approval$.set({
      ...approval$.get(),
      busy: false,
      phase: 'error',
      error: String((e && e.message) || e),
    })
  }
}

async function doCommit(s) {
  if (approval$.get().busy) return // a second click must not write twice
  approval$.set({ ...approval$.get(), busy: true })
  const items = selectedItems(s)
  try {
    const preview =
      s.preview ||
      (await api('/memories/preview', {
        method: 'POST',
        body: { items, source_session_id: sessionId() },
      }))
    preview.forEach((p, i) => {
      if (items[i]) items[i].pre_sha = p.pre_sha
    })
    const res = await api('/memories/commit', {
      method: 'POST',
      body: { items, source_session_id: sessionId() },
    })
    const wrote = res.filter((r) => r.status === 'written').length
    approval$.set({
      ...approval$.get(),
      busy: false,
      phase: 'result',
      result: { items: res, wrote, batch: res[0] && res[0].batch_id },
    })
    host.notify({ kind: 'success', message: `Wrote ${wrote} note(s) to the vault` })
  } catch (e) {
    approval$.set({
      ...approval$.get(),
      busy: false,
      phase: 'error',
      error: String((e && e.message) || e),
    })
  }
}

function ApprovalPane() {
  const s = useValue(approval$)
  if (!s.open) return null
  const pad = { padding: 12, fontSize: 13 }

  if (s.phase === 'loading') {
    return jsx('div', { style: pad, children: 'Reading this chat and extracting memories…' })
  }
  if (s.phase === 'error') {
    return jsxs('div', {
      style: pad,
      children: [
        jsx('div', { style: { marginBottom: 8 }, children: s.error || 'Something went wrong.' }),
        jsx(Button, { size: 'sm', variant: 'ghost', onClick: closeApproval, children: 'Close' }),
      ],
    })
  }

  if (s.phase === 'result') {
    return jsxs('div', {
      style: { ...pad, display: 'flex', flexDirection: 'column', gap: 8, height: '100%', overflow: 'auto' },
      children: [
        jsx('div', {
          style: { fontWeight: 700 },
          children: `Wrote ${s.result.wrote} of ${s.result.items.length} note(s)`,
        }),
        s.result.items.map((r, i) =>
          jsx('div', {
            style: { fontSize: 12 },
            children:
              (r.status === 'written' ? '✓ ' : '• ') +
              r.target_path +
              ' — ' +
              r.status +
              (r.detail ? ` (${r.detail})` : ''),
          }, i),
        ),
        jsxs('div', {
          style: { display: 'flex', gap: 6, marginTop: 8 },
          children: [
            s.result.batch
              ? jsx(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  onClick: () => {
                    api('/memories/undo', { method: 'POST', body: { batch_id: s.result.batch } })
                      .then((r) => host.notify({ kind: 'info', message: `Undid ${r.length} write(s)` }))
                      .catch((e) => host.notifyError(e, 'Undo failed'))
                    closeApproval()
                  },
                  children: 'Undo this batch',
                })
              : null,
            jsx(Button, { size: 'sm', onClick: closeApproval, children: 'Done' }),
          ],
        }),
      ],
    })
  }

  if (s.phase === 'preview') {
    return jsxs('div', {
      style: { ...pad, display: 'flex', flexDirection: 'column', gap: 8, height: '100%', overflow: 'auto' },
      children: [
        jsx('div', { style: { fontWeight: 700 }, children: `Preview — ${s.preview.length} note(s)` }),
        s.preview.map((p, i) =>
          jsxs('div', {
            children: [
              jsx('div', {
                style: { fontWeight: 600, fontSize: 12 },
                children: p.target_path + (p.action === 'create' ? '  (will be created)' : ''),
              }),
              jsx('pre', {
                style: {
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontSize: 11,
                  background: 'var(--ui-bg-tertiary, rgba(128,128,128,0.08))',
                  padding: 6,
                  margin: '2px 0',
                  borderRadius: 4,
                  fontFamily: 'inherit',
                },
                children: p.diff || '(no textual change)',
              }),
            ],
          }, i),
        ),
        jsxs('div', {
          style: { display: 'flex', gap: 6 },
          children: [
            jsx(Button, {
              size: 'sm',
              disabled: !!s.busy,
              onClick: () => doCommit(s),
              children: s.busy ? 'Writing…' : `Write ${s.preview.length} note(s)`,
            }),
            jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              onClick: () => approval$.set({ ...s, phase: 'review', preview: null }),
              children: 'Back',
            }),
          ],
        }),
      ],
    })
  }

  // phase === 'review'
  const patch = (i, p) =>
    approval$.set({ ...s, cards: s.cards.map((c, j) => (j === i ? { ...c, ...p } : c)) })
  const selected = s.cards.filter((c) => c.checked)

  const order = []
  const byPath = {}
  s.cards.forEach((c, i) => {
    if (!byPath[c.target_path]) {
      byPath[c.target_path] = []
      order.push(c.target_path)
    }
    byPath[c.target_path].push([c, i])
  })

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontSize: 13 },
    children: [
      jsx('div', {
        style: { padding: '8px 10px', fontWeight: 700, borderBottom: '1px solid rgba(128,128,128,0.2)' },
        children: 'Review memories',
      }),
      jsx('div', {
        style: { overflow: 'auto', flex: 1, minHeight: 0, padding: 8 },
        children: jsxs('div', {
          children: [
            s.cards.length === 0
              ? jsx('div', {
                  style: { fontSize: 12, opacity: 0.6, padding: 8 },
                  children: 'No memories found in this chat.',
                })
              : null,
            order.map((tp) =>
              jsxs('div', {
                style: { marginBottom: 10 },
                children: [
                  jsx('div', {
                    style: { fontWeight: 700, fontSize: 12, margin: '6px 0 2px' },
                    children:
                      '→ ' +
                      tp +
                      (byPath[tp][0][0].action === 'create' ? '  (will be created)' : '  (existing)'),
                  }),
                  byPath[tp].map(([c, i]) =>
                    jsxs('div', {
                      style: {
                        borderLeft: '2px solid rgba(128,128,128,0.35)',
                        padding: '4px 8px',
                        margin: '4px 0',
                      },
                      children: [
                        jsxs('label', {
                          style: { display: 'flex', gap: 6, alignItems: 'center', fontSize: 11 },
                          children: [
                            jsx('input', {
                              type: 'checkbox',
                              checked: !!c.checked,
                              onChange: (e) => patch(i, { checked: e.target.checked }),
                            }),
                            c.duplicate
                              ? jsx('span', {
                                  style: { color: '#e5a11d' },
                                  children:
                                    c.reason === 'already_written' ? 'already saved' : 'possible duplicate',
                                })
                              : null,
                            c.warning
                              ? jsx('span', { style: { color: '#e5a11d' }, children: c.warning })
                              : null,
                          ],
                        }),
                        jsx('textarea', {
                          value: c.history_line,
                          rows: 2,
                          onChange: (e) => patch(i, { history_line: e.target.value }),
                          style: {
                            width: '100%',
                            boxSizing: 'border-box',
                            fontSize: 12,
                            marginTop: 4,
                            fontFamily: 'inherit',
                          },
                        }),
                        jsxs('div', {
                          style: { display: 'flex', gap: 4, alignItems: 'center', marginTop: 2, fontSize: 11 },
                          children: [
                            jsx('span', { style: { opacity: 0.6 }, children: 'note:' }),
                            jsx('input', {
                              value: c.target_path,
                              onChange: (e) => patch(i, { target_path: e.target.value }),
                              style: { flex: 1, fontSize: 11, fontFamily: 'inherit' },
                            }),
                          ],
                        }),
                        c.colliding_line
                          ? jsx('div', {
                              style: { fontSize: 11, opacity: 0.6, marginTop: 2 },
                              children: 'collides with: ' + c.colliding_line,
                            })
                          : null,
                        c.quote
                          ? jsxs('div', {
                              style: { fontSize: 11, marginTop: 2 },
                              children: [
                                jsx('button', {
                                  type: 'button',
                                  onClick: () => patch(i, { quoteOpen: !c.quoteOpen }),
                                  style: { ...stripLinkStyle, marginLeft: 0 },
                                  children: c.quoteOpen ? 'hide quote' : 'show quote',
                                }),
                                c.quoteOpen
                                  ? jsx('div', {
                                      style: { opacity: 0.6, fontStyle: 'italic', marginTop: 2 },
                                      children: c.quote,
                                    })
                                  : null,
                              ],
                            })
                          : null,
                      ],
                    }, i),
                  ),
                ],
              }, tp),
            ),
            s.rejected && s.rejected.length
              ? jsx('div', {
                  style: { opacity: 0.5, fontSize: 11, marginTop: 8 },
                  children: `${s.rejected.length} candidate(s) discarded`,
                })
              : null,
          ],
        }),
      }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, padding: 8, borderTop: '1px solid rgba(128,128,128,0.2)' },
        children: [
          jsx(Button, {
            size: 'sm',
            disabled: !selected.length || !!s.busy,
            onClick: () => doPreview(s),
            children: `Preview & write (${selected.length} selected)`,
          }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: closeApproval, children: 'Cancel' }),
        ],
      }),
    ],
  })
}

// ===== CREATOR =====
// The renderer half of the Creator module. Shares nothing with Knowledge: its
// own `crCtx` binding, its own `cr`-prefixed atoms, all state module-scoped.
// Spec §5.11 (pane content + per-type preview) and §3.6 (inner error boundary,
// iframe theme prelude). Backend lives under
// /api/plugins/hermes-workspace/creator/ (spec §5.10).

let crCtx = null
const crApi = (p, o) => crCtx.rest(p, o)
const crQs = (obj) =>
  Object.entries(obj)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&')

// Phase 2 spike (spec §6.1-6.2): fetch+decode+verify a creator-libs asset,
// and bootstrap esbuild-wasm from it. crAssetCache/crEsbuildPromise are
// module-scoped so both survive across pane remounts for the plugin's life.
const crAssetCache = new Map() // name -> Uint8Array | string
const crHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')

async function crAsset(name) {
  if (crAssetCache.has(name)) return crAssetCache.get(name)
  const env = await crApi('/creator/asset/' + name, { timeoutMs: 120000 })
  const bytes =
    env.encoding === 'base64'
      ? Uint8Array.from(atob(env.data), (c) => c.charCodeAt(0))
      : new TextEncoder().encode(env.data)
  const digest = crHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
  if (digest !== env.sha256) throw new Error(`crAsset(${name}): sha256 mismatch`)
  const value = env.encoding === 'base64' ? bytes : env.data
  crAssetCache.set(name, value)
  return value
}

let crEsbuildPromise = null

// esbuild-wasm's vendored browser driver (creator-libs/esbuild.js is the raw
// esbuild-wasm/lib/browser.min.js UMD build, not an ES module) has no export
// statements; blob-importing it as a module runs its top-level code, which
// falls through its `module` check and assigns `self.esbuild` instead.
function crEsbuild() {
  if (crEsbuildPromise) return crEsbuildPromise
  crEsbuildPromise = (async () => {
    const src = await crAsset('esbuild.js')
    const blobUrl = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }))
    try {
      await import(blobUrl)
    } finally {
      URL.revokeObjectURL(blobUrl)
    }
    const esbuild = globalThis.esbuild
    if (!esbuild) throw new Error('crEsbuild: esbuild.js did not expose a global esbuild')
    const wasmModule = await WebAssembly.compile(await crAsset('esbuild.wasm'))
    try {
      await esbuild.initialize({ wasmModule, worker: true })
    } catch {
      await esbuild.initialize({ wasmModule, worker: false })
    }
    return esbuild
  })().catch((e) => {
    // Don't let a transient failure (e.g. an asset fetch blip) wedge every
    // future call behind this same rejected promise — clear the memo so the
    // next call retries fresh (README: "pane retries gracefully").
    crEsbuildPromise = null
    throw e
  })
  return crEsbuildPromise
}

// MANIFEST.json ({specifier: {file, subdeps}}), fetched once and memoized —
// crAsset already caches the raw text by name, this just parses it once.
let crManifestPromise = null
function crManifest() {
  if (!crManifestPromise) {
    crManifestPromise = crAsset('MANIFEST.json')
      .then((text) => JSON.parse(text))
      .catch((e) => {
        crManifestPromise = null
        throw e
      })
  }
  return crManifestPromise
}

// specifier -> Promise<string> (vendored lib source text). Module-scoped so it
// survives across crBundle calls. This IS the "cache by name" from the brief,
// and it doubles as the diamond-dependency de-dup: if two libs both need
// 'react' concurrently, both onLoad calls for 'react' await this SAME
// in-flight crAsset promise (stored before either await resolves) rather than
// firing two fetches.
const crLibCache = new Map()

// esbuild vfs plugin: bare specifiers found in MANIFEST resolve into the
// 'creator-vfs' namespace and load from the vendored bundle text. Anything
// else (a relative import, an unknown package) is left alone so esbuild's own
// resolver fails it with a normal "could not resolve" diagnostic.
//
// react/react-dom/react-dom-client are the one exception: they're left
// `external` instead of inlined. React's hooks read a dispatcher that
// react-dom's *own* evaluated 'react' module instance sets — if this bundle
// inlined its own private copy of react.js, that copy's dispatcher would
// never get set by whatever ReactDOM later mounts it (crReactSrcdoc's
// preview bootstrap runs react-dom-client.js as a separate script, outside
// this bundle's closure), and every hook call would throw "Invalid hook
// call". Leaving them external makes esbuild emit `__require("react")` etc.
// in the iife output; crReactSrcdoc supplies a matching global `require()`
// resolving to the SAME window.React/window.ReactDOM instance it boots, so
// there is exactly one React module instance shared by the artifact and its
// renderer. react/jsx-runtime stays inlined — it's stateless (Symbol.for-
// keyed element objects only), so a per-bundle copy is harmless.
const CR_REACT_EXTERNAL = new Set(['react', 'react-dom', 'react-dom/client'])

function crVfsPlugin(manifest) {
  return {
    name: 'creator-vfs',
    setup(build) {
      build.onResolve({ filter: /.*/ }, (args) => {
        if (!Object.prototype.hasOwnProperty.call(manifest, args.path)) return null
        if (CR_REACT_EXTERNAL.has(args.path)) return { path: args.path, external: true }
        return { path: args.path, namespace: 'creator-vfs' }
      })
      build.onLoad({ filter: /.*/, namespace: 'creator-vfs' }, async (args) => {
        if (!crLibCache.has(args.path)) {
          crLibCache.set(
            args.path,
            crAsset(manifest[args.path].file).catch((e) => {
              crLibCache.delete(args.path)
              throw e
            }),
          )
        }
        return { contents: await crLibCache.get(args.path), loader: 'js' }
      })
    },
  }
}

function crFormatBuildErrors(errors) {
  return (errors || [])
    .map((e) => {
      const loc = e.location
      return (loc ? `${loc.file}:${loc.line}:${loc.column}: ` : '') + e.text
    })
    .join('\n')
}

async function crBundleHash(source) {
  const bytes = new TextEncoder().encode(source)
  return crHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
}

// Preview build (spec §6.2 step 3): bundle `source` (a react-artifact module)
// against the vendored library vfs. No cache of its own — its one caller,
// crReactBuild, already memoizes by this exact same content hash
// (crReactBuildCache) before ever calling in, so a second Map here would
// just be a strict-subset duplicate that never gets a hit of its own.
async function crBundle(source) {
  const [esbuild, manifest] = await Promise.all([crEsbuild(), crManifest()])
  let result
  try {
    const built = await esbuild.build({
      stdin: { contents: source, loader: 'tsx', resolveDir: '/', sourcefile: 'artifact.tsx' },
      bundle: true,
      format: 'iife',
      globalName: '__CreatorArtifact',
      jsx: 'automatic',
      jsxImportSource: 'react',
      write: false,
      plugins: [crVfsPlugin(manifest)],
    })
    result = { ok: true, code: built.outputFiles[0].text }
  } catch (e) {
    // esbuild-wasm's build() rejects (rather than resolving with .errors) on a
    // failed build outside a rebuild/watch context — the thrown error carries
    // the diagnostics array.
    const errors = e && e.errors
    result = { ok: false, errors: errors && errors.length ? crFormatBuildErrors(errors) : String((e && e.message) || e) }
  }
  return result
}

// Renderer-side Tailwind compile (task-20, see creator-libs/tailwind-entry.js
// and task-20-report.md for why the vendored `tailwind.js` wraps tailwindcss
// v4's core `compile()` rather than `@tailwindcss/browser`). tailwind.js is a
// real ESM (unlike esbuild.js's UMD build) so blob-importing it yields its
// actual `compile` export directly, no globalThis fallback needed.
let crTailwindLibPromise = null
function crTailwindLib() {
  if (crTailwindLibPromise) return crTailwindLibPromise
  crTailwindLibPromise = (async () => {
    const src = await crAsset('tailwind.js')
    const blobUrl = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }))
    try {
      return await import(blobUrl)
    } finally {
      URL.revokeObjectURL(blobUrl)
    }
  })().catch((e) => {
    crTailwindLibPromise = null
    throw e
  })
  return crTailwindLibPromise
}

// Candidate extractor: tailwindcss v4's own scanner (@tailwindcss/oxide) is a
// native/wasm package meant for scanning files, not a fit for an offline
// browser bundle — so this is the regex sweep from the task-20 brief instead.
const CR_CLASS_ATTR_RE = /class(?:Name)?\s*[:=]\s*["'`]([^"'`]+)/g

// Broader sweep (review finding: cn()/clsx()/ternary/cva() className patterns
// produce zero matches for CR_CLASS_ATTR_RE above, so the artifact renders
// unstyled with no error). Every quoted string literal in the artifact's own
// SOURCE — small, not the multi-MB bundle — split on whitespace and tossed in
// as a candidate too. Tailwind's compile() silently drops anything that isn't
// a real utility, so over-collecting costs nothing.
const CR_STRING_LITERAL_RE = /["'`]([^"'`]{1,500})["'`]/g
function crExtractCandidates(bundleText, sourceText) {
  const candidates = new Set()
  for (const m of bundleText.matchAll(CR_CLASS_ATTR_RE)) {
    for (const token of m[1].split(/\s+/)) if (token) candidates.add(token)
  }
  if (sourceText) {
    for (const m of sourceText.matchAll(CR_STRING_LITERAL_RE)) {
      for (const token of m[1].split(/\s+/)) if (token) candidates.add(token)
    }
  }
  return [...candidates]
}

const crTailwindCache = new Map() // sha256(candidates+themeBlock) -> css string

// Preview Tailwind compile (spec task-20): extract class candidates out of a
// bundled artifact's source text, compile against an optional @theme block.
// Cached by content hash, same pattern as crBundle.
async function crTailwind(bundleText, themeBlock, sourceText) {
  const candidates = crExtractCandidates(bundleText, sourceText)
  const hash = await crBundleHash(candidates.join(' ') + (themeBlock || ''))
  if (crTailwindCache.has(hash)) return crTailwindCache.get(hash)
  const { compile } = await crTailwindLib()
  const css = await compile('@tailwind utilities;' + (themeBlock || ''), { candidates })
  crTailwindCache.set(hash, css)
  return css
}

const crSid$ = atom('') // focusedStoredSessionId at last poll ('' = no chat)
const crOpen$ = atom(null) // open artifact identifier | null
const crList$ = atom([]) // [{identifier,type,title,version,updated_at,origin,in_session}]
const crDetail$ = atom(null) // GET /artifacts/{id} body
const crContent$ = atom('') // content of the viewed version
const crViewVersion$ = atom(null) // viewed version n | null = latest
const crDirty$ = atom(false) // editor has unsaved edits
const crBusy$ = atom(false) // a write is in flight
const crPinned$ = atom(false) // user picked from the <select> — stop auto-following
const crVersionGone$ = atom(false) // last /v/{n} fetch 404/410'd — show a message, not stale content

// Dedup /v/{n} refetches across poll ticks: skip the fetch when neither the
// artifact's updated_at nor the viewed version number changed since last time.
let crLastFetch = { id: null, updatedAt: null, n: null }

const crPath = (id, rest) => `/creator/artifacts/${encodeURIComponent(id)}${rest || ''}`

// This-session artifacts first, then the rest, newest updated_at first.
function crSort(list) {
  return [...list].sort(
    (a, b) =>
      (b.in_session ? 1 : 0) - (a.in_session ? 1 : 0) ||
      String(b.updated_at || '').localeCompare(String(a.updated_at || '')),
  )
}

// One poll tick — CreatorPane runs it now and every 2s while mounted. Null
// session → EmptyState, no fetch. Any call that throws bubbles to the caller,
// which keeps last state and retries next tick (spec §5.10).
async function crPoll() {
  const sid = host.state.focusedStoredSessionId?.get?.() || ''
  crSid$.set(sid)
  if (!sid) {
    crList$.set([])
    crOpen$.set(null)
    crDetail$.set(null)
    crContent$.set('')
    return
  }

  const r = await crApi(`/creator/artifacts?${crQs({ session_id: sid })}`)
  const list = crSort((r && r.artifacts) || [])
  crList$.set(list)

  let open = crOpen$.get()
  const stillThere = open && list.some((a) => a.identifier === open)
  // Never auto-switch/reset out from under an unsaved draft (spec §5.10):
  // a dirty editor keeps showing what it has, even if the pane isn't pinned
  // or the open artifact dropped out of the list.
  if ((!stillThere || !crPinned$.get()) && !crDirty$.get()) {
    const next = list.length ? list[0].identifier : null
    if (next !== open) {
      open = next
      crOpen$.set(open)
      crViewVersion$.set(null)
      crDirty$.set(false)
    }
  }
  if (!open) {
    crDetail$.set(null)
    crContent$.set('')
    return
  }

  let detail
  try {
    detail = await crApi(crPath(open))
  } catch (e) {
    // 404 → deleted elsewhere; drop back to the picker (spec §5.10).
    if (/\b404\b/.test(String((e && e.message) || e))) {
      crOpen$.set(null)
      crPinned$.set(false)
      crDetail$.set(null)
      crContent$.set('')
      return
    }
    throw e
  }
  crDetail$.set(detail)

  if (crDirty$.get()) return // don't clobber active edits
  const count = detail.version_count || 1
  const n = crViewVersion$.get() == null ? count : crViewVersion$.get()

  // Nothing changed since the last successful (or 404/410'd) fetch of this
  // exact artifact+version — skip re-downloading up to 1MB of content.
  const unchanged =
    crLastFetch.id === open && crLastFetch.updatedAt === detail.updated_at && crLastFetch.n === n
  if (unchanged) return

  try {
    const v = await crApi(crPath(open, `/v/${n}`))
    crVersionGone$.set(false)
    crContent$.set((v && v.content) || '')
  } catch (e) {
    // 404/410 (StoreGone) → the version row/file is gone; stop showing stale
    // content and stop silently retrying every tick (spec §5.10/§3.4).
    if (/\b(404|410)\b/.test(String((e && e.message) || e))) {
      crVersionGone$.set(true)
      crContent$.set('')
    } else {
      throw e
    }
  }
  crLastFetch = { id: open, updatedAt: detail.updated_at, n }
}

async function crScan() {
  const sid = host.state.focusedStoredSessionId?.get?.() || ''
  return crApi('/creator/scan', { method: 'POST', body: { session_id: sid } })
}

// Copied (not imported) from inline-preview-directive.tsx per §3.6: resolve the
// five theme-bridge tokens + the app font against the live document, for the
// html preview iframe's opaque origin.
function crThemePrelude() {
  const map = {
    '--foreground': '--ui-text-primary',
    '--muted-foreground': '--ui-text-tertiary',
    '--accent': '--ui-accent',
    '--border': '--ui-stroke-tertiary',
    '--card': '--ui-bg-editor',
  }
  let tokens = ''
  try {
    const root = getComputedStyle(document.documentElement)
    for (const [alias, src] of Object.entries(map)) {
      const val = root.getPropertyValue(src).trim()
      if (val) tokens += `${alias}:${val};`
    }
  } catch {}
  let font = ''
  try {
    font = getComputedStyle(document.body).fontFamily
  } catch {}
  return (
    `<style>:root{${tokens}}` +
    `html,body{margin:0;padding:0;background:transparent;color:var(--foreground,inherit);` +
    (font ? `font-family:${font};` : '') +
    `}</style>`
  )
}

function crHtmlDoc(html) {
  const src = html || ''
  const prelude = crThemePrelude()
  if (/<html[\s>]/i.test(src)) {
    // Full document — the doctype must stay the first token or the browser
    // renders in quirks mode, so splice the prelude in rather than prepend it.
    // Prefer just inside <head…>; else right after <!doctype …>; else (no
    // doctype, no head — rare) fall back to a raw prepend.
    const headOpen = /<head[^>]*>/i.exec(src)
    if (headOpen) {
      const at = headOpen.index + headOpen[0].length
      return src.slice(0, at) + prelude + src.slice(at)
    }
    const doctype = /^\s*<!doctype\s[^>]*>/i.exec(src)
    if (doctype) {
      const at = doctype.index + doctype[0].length
      return src.slice(0, at) + prelude + src.slice(at)
    }
    return prelude + src
  }
  // Fragment → minimal doc + reset.
  return (
    '<!doctype html><meta charset="utf-8">' +
    prelude +
    '<style>*,*::before,*::after{box-sizing:border-box}body{margin:12px;color:var(--foreground,inherit)}</style>' +
    src
  )
}

// Vendored creator-libs files (spec §6.1) are zero-import ESM whose only
// top-level statement is a trailing `export default <expr>;` (confirmed
// against the committed build.mjs output) — turning that into a plain
// assignment is enough to run the file as a classic (non-module) <script>
// inside the sandboxed srcdoc iframe and expose it as a window global.
async function crReactGlobalScript(assetFile, globalName) {
  const src = await crAsset(assetFile)
  const marker = 'export default '
  const at = src.lastIndexOf(marker)
  if (at === -1) throw new Error(`crReactGlobalScript(${assetFile}): no "${marker}" found`)
  return src.slice(0, at) + `window.${globalName} = ` + src.slice(at + marker.length)
}

// react-dom.js/react-dom-client.js call a genuine runtime `require("react")`
// / `require("react-dom")` internally (Facebook's own CJS sources, buried
// inside factory closures esbuild can't statically hoist to an import) — this
// is the global `require` those calls need, resolving to the SAME instances
// crBundle's now-external react/react-dom/react-dom-client imports resolve
// to (see crVfsPlugin), so there is exactly one shared React module.
const CR_REACT_REQUIRE_SHIM = `
function require(name) {
  if (name === 'react') return window.React
  if (name === 'react-dom') return window.__crReactDomBase
  if (name === 'react-dom/client') return window.ReactDOM
  throw new Error('crReactSrcdoc: require("' + name + '") is not available in the preview iframe')
}
`

// Minimal inline ErrorBoundary (spec §6.3/§6.4) — wraps the artifact render so
// a render-time error shows a fallback instead of leaving a blank frame.
// componentDidCatch also reports through the bridge (window.__crReportError,
// installed first by crBridgeScript below) so the host pane's CrErrorStrip
// sees it too, not just the in-frame fallback.
const CR_ERROR_BOUNDARY_SRC = `
class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null } }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) {
    try { window.__crReportError && window.__crReportError(error) } catch (e) {}
  }
  render() {
    if (this.state.error) {
      return React.createElement('pre', {
        style: { color: '#e5484d', padding: 12, margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 },
      }, 'Render error: ' + ((this.state.error && this.state.error.message) || this.state.error))
    }
    return this.props.children
  }
}
`

// Task 22 (spec §6.4): the preview iframe's error + console postMessage
// bridge. Spliced as the VERY FIRST <script> in the srcdoc (crReactSrcdoc,
// below) — before the React-globals scripts and before the bundle script —
// so window.onerror/onunhandledrejection are installed before anything else
// in the frame can run, catching a synchronous top-level throw in the bundle
// itself and any async error in the gap that would otherwise exist. Kept as
// one string (not a template literal with real newlines mattering) since it
// runs as classic inline JS inside the sandboxed srcdoc, same trick as
// CR_ERROR_BOUNDARY_SRC above.
function crBridgeScript(nonce) {
  const token = JSON.stringify(String(nonce || ''))
  return `
(function () {
  var TOKEN = ${token}
  function post(msg) { try { parent.postMessage(msg, '*') } catch (e) {} }
  function safe(v) {
    try {
      if (v instanceof Error) return v.stack || v.message
      if (typeof v === 'object' && v !== null) return JSON.stringify(v)
      return String(v)
    } catch (e) { return String(v) }
  }
  window.onerror = function (message, source, lineno, colno, err) {
    post({ type: 'cr-error', token: TOKEN, message: String(message), stack: err && err.stack, line: lineno, col: colno })
  }
  window.onunhandledrejection = function (event) {
    var reason = event && event.reason
    post({ type: 'cr-error', token: TOKEN, message: safe(reason), stack: reason && reason.stack })
  }
  // window.__crReportError: called by ErrorBoundary.componentDidCatch (React
  // catches render errors itself; window.onerror never sees those).
  window.__crReportError = function (error) {
    post({ type: 'cr-error', token: TOKEN, message: safe(error), stack: error && error.stack })
  }
  ;['log', 'warn', 'error', 'info', 'debug'].forEach(function (level) {
    var orig = console[level]
    console[level] = function () {
      var args = Array.prototype.slice.call(arguments).map(safe)
      post({ type: 'cr-console', token: TOKEN, level: level, args: args })
      return orig.apply(console, arguments)
    }
  })
})();
`
}

// srcdoc script tags can't contain a literal "</script" — the HTML tokenizer
// matches it regardless of JS string/comment context, so bundle text built
// from AI-authored artifact source could accidentally close the tag early.
function crEscapeScriptClose(s) {
  return String(s || '').replace(/<\/script/gi, '<\\/script')
}

// The preview iframe's document (spec §6.3): theme prelude + the Tailwind CSS
// compiled in the renderer (crTailwind) + the iife bundle (crBundle), mounted
// via React. `bundle`/`css` are handed in already-built by the caller; this
// only inlines them, plus the React/ReactDOM globals the bootstrap needs (see
// crVfsPlugin and CR_REACT_REQUIRE_SHIM above for why those can't just come
// from the bundle itself). `nonce`/`injectRuntime` drive Task 22's error +
// console bridge: when injectRuntime is set, crBridgeScript(nonce) is
// spliced in as the VERY FIRST <script> — before the React-globals scripts,
// before the bundle script, before the bootstrap — per the Task 21 review
// finding that the bootstrap-tag splice point ran too late to catch a
// synchronous top-level throw in the bundle or an async error in the gap
// before window.onerror was patched.
async function crReactSrcdoc({ bundle, css, nonce, injectRuntime }) {
  const [reactSrc, reactDomBaseSrc, reactDomSrc] = await Promise.all([
    crReactGlobalScript('react.js', 'React'),
    crReactGlobalScript('react-dom.js', '__crReactDomBase'),
    crReactGlobalScript('react-dom-client.js', 'ReactDOM'),
  ])
  const prelude = crThemePrelude()
  const bridgeScript = injectRuntime ? `<script>${crBridgeScript(nonce)}</script>` : ''
  return (
    '<!doctype html><meta charset="utf-8">' +
    bridgeScript +
    prelude +
    `<style>${css || ''}</style>` +
    '<div id="root"></div>' +
    `<script>${CR_REACT_REQUIRE_SHIM}</script>` +
    `<script>${reactSrc}</script>` +
    `<script>${reactDomBaseSrc}</script>` +
    `<script>${reactDomSrc}</script>` +
    `<script>${crEscapeScriptClose(bundle)}</script>` +
    '<script>' +
    CR_ERROR_BOUNDARY_SRC +
    `
try {
  ReactDOM.createRoot(document.getElementById('root')).render(
    React.createElement(ErrorBoundary, null, React.createElement(window.__CreatorArtifact.default))
  )
} catch (e) {
  document.getElementById('root').textContent = 'Render error: ' + ((e && e.message) || e)
}
` +
    '</script>'
  )
}

function crB64(s) {
  const bytes = new TextEncoder().encode(String(s || ''))
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

const CR_CSS = `
.cr-body{container-type:inline-size;flex:1;min-height:0;display:flex}
.cr-split{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;gap:1px}
.cr-split>.cr-cell{flex:1;min-width:0;min-height:0;overflow:auto;background:var(--ui-bg-editor,transparent)}
@container (min-width:640px){.cr-split{flex-direction:row}}
.cr-frame{width:100%;height:100%;min-height:180px;border:0;background:#fff}
`

// Creator's own React class boundary (§3.6). Reused as the per-artifact inner
// boundary via a `key`, so one bad artifact can't blank the whole pane.
class CrErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { err: null }
  }
  static getDerivedStateFromError(err) {
    return { err }
  }
  componentDidCatch(err) {
    try {
      host.notifyError?.(err, 'Creator pane error')
    } catch {}
  }
  render() {
    if (!this.state.err) return this.props.children
    return jsx('div', {
      style: { padding: 12, fontSize: 12, opacity: 0.7 },
      children: `Creator hit an error: ${(this.state.err && this.state.err.message) || this.state.err}`,
    })
  }
}

function crPick(id) {
  crPinned$.set(true)
  crOpen$.set(id)
  crViewVersion$.set(null)
  crDirty$.set(false)
  crVersionGone$.set(false)
}

function CrHeader() {
  const list = useValue(crList$)
  const open = useValue(crOpen$)
  const detail = useValue(crDetail$)
  const vv = useValue(crViewVersion$)
  const busy = useValue(crBusy$)
  const [confirm, setConfirm] = useState(false)
  const count = (detail && detail.version_count) || 1
  const n = vv == null ? count : vv

  const step = (to) => {
    crDirty$.set(false)
    crVersionGone$.set(false)
    crViewVersion$.set(to >= count ? null : Math.max(1, to))
    crPoll().catch(() => {}) // pull the picked version's content now, not in ~2s
  }
  const restore = () => {
    if (!open) return
    crBusy$.set(true)
    crApi(crPath(open, '/versions'), { method: 'POST', body: { restore_from: n } })
      .then(() => {
        crViewVersion$.set(null)
        crDirty$.set(false)
        return crPoll()
      })
      .catch((e) => host.notifyError?.(e, 'Restore failed'))
      .finally(() => crBusy$.set(false))
  }

  return jsxs('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '6px 8px',
      borderBottom: '1px solid rgba(128,128,128,0.2)',
      flexWrap: 'wrap',
    },
    children: [
      jsx('select', {
        value: open || '',
        onChange: (e) => crPick(e.target.value),
        style: { flex: 1, minWidth: 120, maxWidth: 200, fontSize: 12, padding: '2px 4px' },
        children: list.map((a) =>
          jsx(
            'option',
            { value: a.identifier, children: `${a.in_session ? '● ' : ''}${a.title || a.identifier}` },
            a.identifier,
          ),
        ),
      }),
      jsx('button', {
        onClick: () => step(n - 1),
        disabled: n <= 1,
        title: 'Older version',
        style: { fontSize: 12, padding: '0 4px' },
        children: '◀',
      }),
      jsx('span', { style: { fontSize: 11, opacity: 0.7 }, children: `v${n}/${count}` }),
      jsx('button', {
        onClick: () => step(n + 1),
        disabled: n >= count,
        title: 'Newer version',
        style: { fontSize: 12, padding: '0 4px' },
        children: '▶',
      }),
      n >= count
        ? jsx('span', { style: { fontSize: 10, opacity: 0.5 }, children: 'latest' })
        : jsx(Button, { size: 'xs', variant: 'ghost', disabled: busy, onClick: restore, children: '↺ restore' }),
      jsx(CopyButton, { appearance: 'icon', text: () => crContent$.get(), title: 'Copy content' }),
      jsx(Button, {
        size: 'xs',
        variant: 'ghost',
        disabled: !open || busy,
        onClick: () => setConfirm(true),
        children: 'Delete',
      }),
      jsx(ConfirmDialog, {
        open: confirm,
        onClose: () => setConfirm(false),
        destructive: true,
        title: 'Delete this artifact?',
        description: 'Every version and its files are removed. This cannot be undone.',
        confirmLabel: 'Delete',
        onConfirm: async () => {
          if (!open) return
          await crApi(crPath(open), { method: 'DELETE' })
          crOpen$.set(null)
          crPinned$.set(false)
          await crPoll()
        },
      }),
    ],
  })
}

function CrEditor() {
  const content = useValue(crContent$)
  const detail = useValue(crDetail$)
  const vv = useValue(crViewVersion$)
  const busy = useValue(crBusy$)
  const dirty = useValue(crDirty$)
  const [draft, setDraft] = useState(content)
  const baseRef = useRef(content)
  const count = (detail && detail.version_count) || 1
  const readOnly = vv != null && vv < count

  useEffect(() => {
    setDraft(content)
    baseRef.current = content
    crDirty$.set(false)
  }, [content])

  const setBoth = (next) => {
    setDraft(next)
    crDirty$.set(next !== baseRef.current)
  }
  const save = () => {
    const open = crOpen$.get()
    if (!open || readOnly || !crDirty$.get()) return
    crBusy$.set(true)
    crApi(crPath(open, '/versions'), { method: 'POST', body: { content: draft } })
      .then(() => {
        baseRef.current = draft
        crDirty$.set(false)
        return crPoll()
      })
      .catch((e) => host.notifyError?.(e, 'Save failed'))
      .finally(() => crBusy$.set(false))
  }
  const onKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault()
      save()
      return
    }
    if (e.key === 'Tab' && !readOnly) {
      e.preventDefault()
      const el = e.target
      const s = el.selectionStart
      const en = el.selectionEnd
      const next = draft.slice(0, s) + '  ' + draft.slice(en)
      setBoth(next)
      requestAnimationFrame(() => {
        try {
          el.selectionStart = el.selectionEnd = s + 2
        } catch {}
      })
    }
  }

  return jsxs('div', {
    className: 'cr-cell',
    style: { display: 'flex', flexDirection: 'column' },
    children: [
      jsxs('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 8px',
          borderBottom: '1px solid rgba(128,128,128,0.15)',
        },
        children: [
          jsx('span', {
            style: { fontSize: 14, lineHeight: 1, color: 'var(--ui-accent, #6ab)', opacity: dirty ? 1 : 0 },
            title: dirty ? 'Unsaved edits' : '',
            children: '●',
          }),
          jsx('span', {
            style: { flex: 1, fontSize: 11, opacity: 0.5 },
            children: readOnly ? 'read-only · older version' : '',
          }),
          jsx(Button, { size: 'xs', disabled: readOnly || !dirty || busy, onClick: save, children: 'Save' }),
        ],
      }),
      jsx(Textarea, {
        className:
          'block w-full resize-none rounded-none border-0 bg-transparent p-2.5 font-mono text-xs leading-relaxed shadow-none focus-visible:ring-0',
        style: { flex: 1, minHeight: 140 },
        value: draft,
        readOnly,
        spellCheck: false,
        onKeyDown,
        onChange: (e) => setBoth(e.target.value),
      }),
    ],
  })
}

// CodeMirror 6 loader (task-27, creator-libs/codemirror.js from task-26).
// codemirror.js is a real ESM (unlike esbuild.js's UMD build) so blob-
// importing it yields its actual exports directly, same trick as
// crTailwindLib. Only a SUCCESSFUL load is memoized — a transient crAsset
// failure (network blip) clears the memo and resolves `null` for that one
// call instead of wedging every future call behind a cached rejection/null
// (the Phase 2 review finding this task was told to avoid repeating).
let crCodeMirrorPromise = null
function crCodeMirror() {
  if (crCodeMirrorPromise) return crCodeMirrorPromise
  crCodeMirrorPromise = (async () => {
    const src = await crAsset('codemirror.js')
    const blobUrl = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }))
    try {
      return await import(blobUrl)
    } finally {
      URL.revokeObjectURL(blobUrl)
    }
  })().catch((e) => {
    console.warn('crCodeMirror: codemirror.js failed to load, falling back to plain textarea', e)
    crCodeMirrorPromise = null
    return null
  })
  return crCodeMirrorPromise
}

// Rich editor wrapper (task-27). `basicExtensions`/`readOnly` are all
// codemirror-entry.js exports (task-26 report) — no `Compartment` and no bare
// `keymap` facet are part of that surface, so live reconfiguration below
// leans on two things that ARE static members of the exported `EditorView`
// class: `EditorView.updateListener` for the dirty/onChange wire and
// `EditorView.domEventHandlers` for the Mod-s save shortcut (CM6's normal
// alternative to a one-off `keymap.of(...)` entry). Toggling `readOnly` after
// mount goes through `view.setState(EditorState.create(...))` — a fresh state
// dropped onto the SAME EditorView/DOM instance, not a Compartment-scoped
// facet swap (which we have no Compartment to build), but it still avoids the
// full `destroy()` + reconstruct the brief warns against.
function crCmExtensions(cm, { language, dark, readOnly, onChange, onSave }) {
  return [
    cm.basicExtensions(language, dark),
    cm.readOnly(!!readOnly),
    cm.EditorView.updateListener.of((u) => {
      if (u.docChanged) onChange?.(u.state.doc.toString())
    }),
    cm.EditorView.domEventHandlers({
      keydown(e) {
        if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
          e.preventDefault()
          onSave?.()
          return true
        }
        return false
      },
    }),
  ]
}

function CrCmEditor({ value, language, readOnly, onChange, onSave }) {
  const hostRef = useRef(null)
  const viewRef = useRef(null)
  const propsRef = useRef({ value, language, readOnly, onChange, onSave })
  propsRef.current = { value, language, readOnly, onChange, onSave }
  const [cmOk, setCmOk] = useState(null) // null = loading, false = unavailable, true = mounted

  useEffect(() => {
    let cancelled = false
    crCodeMirror().then((cm) => {
      if (cancelled || !hostRef.current) return
      if (!cm) {
        setCmOk(false)
        return
      }
      const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
      viewRef.current = new cm.EditorView({
        doc: propsRef.current.value || '',
        extensions: crCmExtensions(cm, { ...propsRef.current, dark }),
        parent: hostRef.current,
      })
      setCmOk(true)
    })
    return () => {
      cancelled = true
      viewRef.current?.destroy()
      viewRef.current = null
    }
  }, [])

  useEffect(() => {
    const view = viewRef.current
    if (!view || value === view.state.doc.toString()) return
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value || '' } })
  }, [value])

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    crCodeMirror().then((cm) => {
      if (!cm || viewRef.current !== view) return
      const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
      view.setState(
        cm.EditorState.create({
          doc: view.state.doc,
          extensions: crCmExtensions(cm, { ...propsRef.current, dark, readOnly }),
        }),
      )
    })
  }, [readOnly])

  if (cmOk === false) {
    return jsxs('div', {
      style: { display: 'flex', flexDirection: 'column', height: '100%' },
      children: [
        jsx('div', { style: { fontSize: 11, opacity: 0.6, padding: '2px 4px' }, children: 'Rich editor unavailable — plain text editor' }),
        jsx(Textarea, {
          className: 'block w-full resize-none rounded-none border-0 bg-transparent p-2.5 font-mono text-xs leading-relaxed shadow-none focus-visible:ring-0',
          style: { flex: 1, minHeight: 140 },
          value,
          readOnly: !!readOnly,
          spellCheck: false,
          onChange: (e) => onChange?.(e.target.value),
          onKeyDown: (e) => {
            if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
              e.preventDefault()
              onSave?.()
            }
          },
        }),
      ],
    })
  }
  return jsx('div', { ref: hostRef, className: 'cr-cm-editor', style: { height: '100%' } })
}

// React artifact preview iframe (spec §6.3). `bundle`/`css`/`nonce`/
// `injectRuntime` are handed straight to crReactSrcdoc (async, hence the
// build-in-an-effect state below). Re-keyed on the bundle's content hash so a
// bundle change remounts a fresh iframe rather than React trying to diff a
// changed srcDoc in place (an iframe never re-evaluates a changed srcDoc).
// `frameRef` (Task 22) is forwarded onto the <iframe> so a caller can pass it
// straight to crUseFrameBridge(frameRef, nonce, …) to validate that incoming
// postMessages come from THIS iframe, not any other window.
function CrReactFrame({ bundle, css, nonce, injectRuntime, frameRef }) {
  const [built, setBuilt] = useState(null) // {hash, srcDoc} | null while building

  useEffect(() => {
    let live = true
    ;(async () => {
      const hash = await crBundleHash(bundle || '')
      const srcDoc = await crReactSrcdoc({ bundle, css, nonce, injectRuntime })
      if (live) setBuilt({ hash, srcDoc })
    })().catch((e) => {
      if (live)
        setBuilt({
          hash: 'error',
          srcDoc: `<!doctype html><pre style="color:#e5484d;padding:12px;white-space:pre-wrap">${
            (e && e.message) || e
          }</pre>`,
        })
    })
    return () => {
      live = false
    }
  }, [bundle, css, nonce, injectRuntime])

  if (!built) return null
  return jsx('iframe', {
    key: built.hash,
    ref: frameRef,
    className: 'cr-frame',
    sandbox: 'allow-scripts',
    srcDoc: built.srcDoc,
    title: 'React preview',
  })
}

// Task 22 (spec §6.4): listens for the crBridgeScript postMessages from one
// specific preview iframe and forwards validated cr-error/cr-console events
// to the caller. Three checks gate every message before anything in it is
// trusted: `event.source` pointing at exactly this iframe's contentWindow
// (checked first — cheapest and most decisive, so a message from any other
// frame/window is dropped before its shape is even inspected), known `type`,
// and matching `token` (the nonce is the trust boundary — a message can only
// carry the current build's nonce if it came from code crBridgeScript itself
// installed). Every field read out of msg is still re-typed/clamped below —
// a compromised artifact can call postMessage directly with a correct
// type+token+source and an oversized or malformed payload, and that
// shouldn't be able to bloat the console pane or hand a non-string into
// rendering. A `console.error` call also feeds `onError` (in addition to
// `onConsole`) so CrErrorStrip surfaces it exactly like a thrown error, per
// the brief.
function crUseFrameBridge(frameRef, nonce, { onError, onConsole } = {}) {
  useEffect(() => {
    const clampStr = (v, max) => String(v == null ? '' : v).slice(0, max)
    function handler(event) {
      if (event.source !== frameRef.current?.contentWindow) return
      const msg = event.data
      if (!msg || typeof msg !== 'object') return
      if (msg.type !== 'cr-error' && msg.type !== 'cr-console') return
      if (msg.token !== nonce) return

      if (msg.type === 'cr-error') {
        onError?.({
          message: clampStr(msg.message, 2000),
          stack: msg.stack ? clampStr(msg.stack, 4000) : null,
          line: Number.isFinite(msg.line) ? msg.line : null,
          col: Number.isFinite(msg.col) ? msg.col : null,
        })
      } else {
        const level = ['log', 'warn', 'error', 'info', 'debug'].includes(msg.level) ? msg.level : 'log'
        const args = Array.isArray(msg.args) ? msg.args.slice(0, 20).map((a) => clampStr(a, 500)) : []
        onConsole?.({ level, args })
        if (level === 'error') onError?.({ message: clampStr(args.join(' '), 2000), stack: null, line: null, col: null })
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [frameRef, nonce, onError, onConsole])
}

// Latest render/runtime error surfaced above the preview frame (spec §6.4).
// Purely controlled: the caller owns the error state (fed by crUseFrameBridge
// / CrReactFrame's own build-error catch) and clears it — e.g. back to null
// when a new build starts, or by keying this component on the bundle hash
// like CrReactFrame keys its <iframe> — so a fixed artifact's next successful
// mount drops the strip instead of it lingering.
function CrErrorStrip({ error }) {
  if (!error) return null
  return jsx('div', {
    style: {
      padding: '6px 8px',
      fontSize: 11,
      lineHeight: 1.4,
      color: '#e5484d',
      background: 'rgba(229,72,77,0.1)',
      borderBottom: '1px solid rgba(229,72,77,0.3)',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
    },
    children: `Render error: ${error.message || error}`,
  })
}

const CR_CONSOLE_COLORS = { error: '#e5484d', warn: '#f5a623', info: '#5b9dd9', log: 'inherit', debug: 'inherit' }

// Plain <pre> console scrollback (spec §6.4). `event` is the latest single
// cr-console message (a new object each time, from crUseFrameBridge's
// onConsole) — appended to an internal ~300-line ring buffer on change, so
// the caller doesn't have to lift the whole log into its own state. Clear
// empties the buffer; a `console.error` line is colored the same as
// CrErrorStrip so it reads as the same severity in both places.
function CrConsolePane({ event }) {
  const [entries, setEntries] = useState([])

  useEffect(() => {
    if (!event) return
    setEntries((prev) => {
      const next = prev.length >= 300 ? prev.slice(prev.length - 299) : prev.slice()
      next.push(event)
      return next
    })
  }, [event])

  return jsxs('div', {
    className: 'cr-cell',
    style: { display: 'flex', flexDirection: 'column', minHeight: 0 },
    children: [
      jsx('div', {
        style: {
          display: 'flex',
          justifyContent: 'flex-end',
          padding: '2px 4px',
          borderBottom: '1px solid rgba(128,128,128,0.15)',
        },
        children: jsx(Button, { size: 'xs', variant: 'ghost', disabled: !entries.length, onClick: () => setEntries([]), children: 'Clear' }),
      }),
      jsx('pre', {
        style: {
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          margin: 0,
          padding: '4px 8px',
          fontSize: 11,
          fontFamily: 'monospace',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        },
        children: entries.map((e, i) =>
          jsx(
            'div',
            { style: { color: CR_CONSOLE_COLORS[e.level] || 'inherit' }, children: `[${e.level}] ${e.args.join(' ')}` },
            i,
          ),
        ),
      }),
    ],
  })
}

// Build-error panel (Task 24, brief step 1): shown INSTEAD of the iframe
// when crBundle fails — there's no bundle to run, so no iframe is attempted
// at all.
function CrDiagnostics({ errors }) {
  return jsx('pre', {
    className: 'cr-cell',
    style: {
      margin: 0,
      padding: 12,
      fontSize: 12,
      fontFamily: 'monospace',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      color: '#e5484d',
    },
    children: errors || 'Build failed.',
  })
}

// crypto.randomUUID() is a platform global, no import needed. A fresh nonce
// per build, tied to the same content-hash lifecycle as CrReactFrame's
// remount key (crReactBuild below), so a stale iframe's postMessages can
// never be mistaken for the current build's — crUseFrameBridge drops
// anything whose token doesn't match the CURRENT nonce.
function crNonce() {
  return crypto.randomUUID()
}

// hash(source) -> {nonce, ok, code, css, errors}. crBundle and crTailwind
// already cache by their own content hash (Tasks 19/20) — this adds one more
// cache layer keyed the same way so that re-visiting an already-seen version
// (version-stepping back and forth) reuses the SAME nonce too, not just a
// skipped rebuild: CrReactFrame's key (the hash) and nonce both stay
// identical, so there's no pointless iframe remount either.
const crReactBuildCache = new Map()

async function crReactBuild(source) {
  const src = source || ''
  const hash = await crBundleHash(src)
  if (crReactBuildCache.has(hash)) return { hash, ...crReactBuildCache.get(hash) }
  const result = await crBundle(src)
  const built = result.ok
    ? { nonce: crNonce(), ok: true, code: result.code, css: await crTailwind(result.code, null, src) }
    : { nonce: crNonce(), ok: false, errors: result.errors }
  // Bound the cache (review finding: unbounded Map of full bundle+CSS strings
  // leaks memory across an editing session) — insertion-order eviction is
  // enough since Map preserves it, no LRU bookkeeping needed.
  if (crReactBuildCache.size >= 8) crReactBuildCache.delete(crReactBuildCache.keys().next().value)
  crReactBuildCache.set(hash, built)
  return { hash, ...built }
}

// React artifact preview (Task 24, spec §6.2-§6.4). Debounces `content`
// (~400ms) before feeding crReactBuild, then either renders CrDiagnostics
// (build failed — no iframe) or CrErrorStrip + CrReactFrame + CrConsolePane,
// with the Task 22 bridge (crUseFrameBridge) wired to a real caller here:
// its onError/onConsole feed this component's own error/console state, which
// in turn are the props CrErrorStrip/CrConsolePane render.
function CrReactPreview({ content }) {
  const [debounced, setDebounced] = useState(content)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(content), 400)
    return () => clearTimeout(id)
  }, [content])

  const [build, setBuild] = useState(null) // {hash, nonce, ok, code?, css?, errors?} | null
  // A rejected crReactBuild (e.g. a transient asset fetch failure) used to be
  // an unhandled rejection: `build` just stayed null forever and this
  // component silently rendered nothing. Surface it via CrDiagnostics instead.
  const [buildError, setBuildError] = useState(null)
  useEffect(() => {
    let live = true
    setBuildError(null)
    crReactBuild(debounced || '').then(
      (b) => {
        if (live) setBuild(b)
      },
      (e) => {
        if (live) setBuildError(e)
      },
    )
    return () => {
      live = false
    }
  }, [debounced])

  const frameRef = useRef(null)
  const [error, setError] = useState(null)
  const [consoleEvent, setConsoleEvent] = useState(null)
  const hash = build && build.hash

  // Loose end (brief item 1): CrErrorStrip is purely controlled, so this IS
  // its reset — clear back to null the moment a NEW content hash starts
  // loading (same lifecycle CrReactFrame keys its remount on), so a fixed
  // artifact's next successful build can't still be showing the old error.
  useEffect(() => {
    setError(null)
  }, [hash])

  // Loose end (brief item 2): the bridge's real caller. `nonce` is undefined
  // until the first build resolves; the effect inside just re-subscribes
  // once it's set, same as any other dependency change.
  crUseFrameBridge(frameRef, build && build.nonce, { onError: setError, onConsole: setConsoleEvent })

  if (buildError) return jsx(CrDiagnostics, { errors: String((buildError && buildError.message) || buildError) })
  if (!build) return null
  if (!build.ok) return jsx(CrDiagnostics, { errors: build.errors })

  return jsxs('div', {
    className: 'cr-cell',
    style: { display: 'flex', flexDirection: 'column', minHeight: 0, padding: 0 },
    children: [
      jsx(CrErrorStrip, { error }),
      jsx('div', {
        style: { flex: 1, minHeight: 0, display: 'flex' },
        children: jsx(CrReactFrame, {
          bundle: build.code,
          css: build.css,
          nonce: build.nonce,
          injectRuntime: true,
          frameRef,
        }),
      }),
      jsx(CrConsolePane, { event: consoleEvent }),
    ],
  })
}

// Per-type preview (spec §5.11 table). `code` has no preview — the editor is
// the view.
function CrPreview() {
  const detail = useValue(crDetail$)
  const content = useValue(crContent$)
  const type = (detail && detail.type) || 'code'
  if (type === 'code') return null
  if (type === 'react') return jsx(CrReactPreview, { content })

  let body
  if (type === 'markdown') {
    body = jsx(Streamdown, { children: content })
  } else if (type === 'mermaid') {
    body = jsx(Streamdown, { children: '```mermaid\n' + content + '\n```' })
  } else if (type === 'html') {
    body = jsx('iframe', {
      className: 'cr-frame',
      sandbox: 'allow-scripts',
      srcDoc: crHtmlDoc(content),
      title: 'HTML preview',
    })
  } else if (type === 'svg') {
    body = jsx('img', {
      src: `data:image/svg+xml;base64,${crB64(content)}`,
      style: { maxWidth: '100%' },
      alt: 'SVG preview',
    })
  } else {
    body = jsx('div', { style: { padding: 12, fontSize: 12, opacity: 0.6 }, children: `No preview for “${type}”.` })
  }

  return jsx('div', {
    className: 'cr-cell',
    style: { padding: type === 'html' ? 0 : 12, fontSize: 13 },
    children: body,
  })
}

function CrVersionGone() {
  return jsx('div', {
    className: 'cr-cell',
    style: { padding: 12, fontSize: 12, opacity: 0.7 },
    children: 'This version is no longer available.',
  })
}

function CreatorPane() {
  const sid = useValue(crSid$)
  const open = useValue(crOpen$)
  const detail = useValue(crDetail$)
  const gone = useValue(crVersionGone$)

  useEffect(() => {
    let live = true
    const tick = () => {
      if (live) crPoll().catch(() => {})
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => {
      live = false
      clearInterval(id)
    }
  }, [])

  let inner
  if (!sid) {
    inner = jsx('div', {
      style: { padding: 16 },
      children: jsx(EmptyState, {
        title: 'No chat in focus',
        description: 'Focus a saved chat to see the artifacts it created.',
      }),
    })
  } else if (!open) {
    inner = jsxs('div', {
      style: { padding: 16, display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' },
      children: [
        jsx(EmptyState, {
          title: 'No artifacts yet',
          description: 'Ask the assistant to create one, or rescan this chat.',
        }),
        jsx(Button, {
          size: 'sm',
          onClick: () =>
            crScan()
              .then(() => crPoll())
              .catch((e) => host.notifyError?.(e, 'Rescan failed')),
          children: 'Rescan this chat',
        }),
      ],
    })
  } else {
    const type = (detail && detail.type) || 'code'
    inner = jsxs('div', {
      style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 },
      children: [
        jsx(CrHeader, {}),
        jsxs('div', {
          className: 'cr-body',
          children: [
            jsx('style', { children: CR_CSS }),
            jsx(CrErrorBoundary, {
              children: gone
                ? jsx(CrVersionGone, {})
                : jsxs('div', {
                    className: 'cr-split',
                    children: [jsx(CrEditor, {}), type === 'code' ? null : jsx(CrPreview, {})],
                  }),
            }, open),
          ],
        }),
      ],
    })
  }

  return jsx('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontSize: 13 },
    children: inner,
  })
}

function CrStatusItem() {
  const list = useValue(crList$)
  const sid = useValue(crSid$)
  if (!sid) return null
  return jsxs('span', {
    style: { display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontSize: 11 },
    title: 'Creator artifacts in this chat',
    children: ['◆ Creator', list.length ? jsx('span', { style: { opacity: 0.6 }, children: String(list.length) }) : null],
  })
}

function crRegister(ctx) {
  crCtx = ctx
  ctx.registerMany([
    {
      id: 'cr-pane',
      area: PANES_AREA,
      title: 'Creator',
      data: {
        placement: 'right',
        width: '440px',
        hideOnly: true,
        collapsible: true,
        dock: { pane: 'workspace', pos: 'right' },
      },
      render: () => jsx(CrErrorBoundary, { children: jsx(CreatorPane, {}) }),
    },
    {
      id: 'cr-status',
      area: STATUSBAR_AREAS.right,
      order: 71,
      render: () => jsx(CrStatusItem, {}),
    },
    {
      id: 'cr-palette-open',
      area: PALETTE_AREA,
      data: {
        id: 'hermes-workspace.open-creator',
        label: 'Open Creator',
        keywords: ['creator', 'artifact', 'preview', 'code'],
        run: () => host.panes?.reveal?.('hermes-workspace.cr-pane'),
      },
    },
    {
      id: 'cr-palette-scan',
      area: PALETTE_AREA,
      data: {
        id: 'hermes-workspace.creator-rescan',
        label: 'Creator: rescan this chat',
        keywords: ['creator', 'scan', 'artifact', 'rescan'],
        run: () =>
          crScan()
            .then(() => host.notify({ kind: 'info', message: 'Rescanned' }))
            .catch((e) => host.notifyError(e, 'Rescan failed')),
      },
    },
  ])
}

export default {
  id: PLUGIN_ID,
  name: 'Knowledge',
  description: 'Search, browse, and read your Obsidian vault in a side pane.',
  register(ctx) {
    CTX = ctx

    try {
      vaultOn$.set(!!ctx.storage.get('vaultContext.on', false))
    } catch {}
    try {
      lastInjection$.set(ctx.storage.get('lastInjection', null) || null)
    } catch {}

    ctx.registerMany([
      {
        // Standing right-side chrome (~360px). hideOnly: no dismiss-without-
        // reopen; core auto-registers a "Toggle Knowledge" ⌘K row for it.
        // collapsible: leaves the grid on narrow viewports like the file tree.
        id: 'pane',
        area: PANES_AREA,
        title: 'Knowledge',
        // dock: a resolvable anchor (core `files`/`sessions` carry the same
        // shape) so adoption opens a fresh visible zone beside the workspace
        // instead of stacking into the collapsed `review` zone.
        data: {
          placement: 'right',
          width: '360px',
          hideOnly: true,
          collapsible: true,
          dock: { pane: 'workspace', pos: 'right' },
        },
        render: () => jsx(KnowledgePane, {}),
      },
      {
        id: 'status',
        area: STATUSBAR_AREAS.right,
        order: 70,
        render: () => jsx(KnowledgeStatusItem, {}),
      },
      {
        id: 'composer-toggle',
        area: COMPOSER_AREAS.leading,
        render: () => jsx(TogglePill, {}),
      },
      {
        id: 'composer-strip',
        area: COMPOSER_AREAS.top,
        render: () => jsx(PreviewStrip, {}),
      },
      {
        id: 'composer-middleware',
        area: COMPOSER_AREAS.middleware,
        data: composerMiddleware,
      },
      {
        id: 'palette-toggle',
        area: PALETTE_AREA,
        data: {
          id: 'hermes-workspace.toggle-vault-context',
          label: 'Toggle vault context',
          keywords: ['vault', 'context', 'knowledge', 'obsidian', 'composer'],
          detail: () => (vaultOn$.get() ? 'on' : 'off'),
          detailVariant: 'state',
          keepOpen: true,
          run: () => setVaultOn(!vaultOn$.get()),
        },
      },
    ])

    // "Reindex vault" always registers — plain REST, no newer RPC needed.
    ctx.register({
      id: 'palette-reindex',
      area: PALETTE_AREA,
      data: {
        id: 'hermes-workspace.reindex-vault',
        label: 'Reindex vault',
        keywords: ['reindex', 'vault', 'knowledge', 'rebuild', 'index', 'obsidian'],
        run: () =>
          api('/reindex', { method: 'POST', body: { full: true } })
            .then((r) => host.notify({ kind: 'info', message: `Indexed ${(r && r.indexed) || 0} note(s)` }))
            .catch((e) => host.notifyError(e, 'Reindex failed')),
      },
    })

    try { crRegister(ctx) } catch (e) { host.notifyError?.(e, 'Creator failed to load') }

    // The extraction commands touch session.history + llm.oneshot. Gate them
    // on a one-time probe; the read path above is already wired and untouched.
    rpcAvailable().then((ok) => {
      if (!ok) {
        host.notify({
          kind: 'info',
          message: 'Chat memory extraction is unavailable on this Hermes build.',
        })
        return
      }
      ctx.registerMany([
        {
          id: 'palette-extract',
          area: PALETTE_AREA,
          data: {
            id: 'hermes-workspace.extract-memories',
            label: 'Extract memories from this chat',
            keywords: ['memory', 'memories', 'extract', 'knowledge', 'vault', 'save', 'remember'],
            run: runExtraction,
          },
        },
        {
          id: 'palette-undo-extract',
          area: PALETTE_AREA,
          data: {
            id: 'hermes-workspace.undo-memory-extraction',
            label: 'Undo last memory extraction',
            keywords: ['memory', 'undo', 'revert', 'knowledge', 'vault'],
            run: () =>
              api('/memories/undo', { method: 'POST', body: {} })
                .then((r) => host.notify({ kind: 'info', message: `Undid ${(r && r.length) || 0} write(s)` }))
                .catch((e) => host.notifyError(e, 'Undo failed')),
          },
        },
      ])
    })
  },
}
