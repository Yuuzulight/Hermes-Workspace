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
  EmptyState,
  host,
  PALETTE_AREA,
  SegmentedControl,
  StatusDot,
  Streamdown,
  PANES_AREA,
  STATUSBAR_AREAS,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
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
    api('/context', { method: 'POST', body: { query, budget_tokens: 1500, k_max: 6 } }),
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
  },
}
