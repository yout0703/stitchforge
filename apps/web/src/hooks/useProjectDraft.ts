import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DraftStore, draftFromEditor, restoreDraft } from '../lib/drafts'
import { createEditor, isDirty, type EditorState } from '../lib/editor'
import type { PatternDocument } from '../lib/types'

export function useProjectDraft(userId: string, session: { projectId: string; headRevisionId: string } | null, editor: EditorState) {
  const store = useMemo(() => new DraftStore(), [])
  const [status, setStatus] = useState('')
  const readFailed = useRef(false)
  useEffect(() => {
    if (!session || editor.active || readFailed.current) return
    let current = true
    const dirty = isDirty(editor)
    setStatus(dirty ? '正在保存本地草稿…' : '')
    const operation = dirty
      ? store.put(draftFromEditor(userId, session, editor))
      : store.remove(userId, session.projectId)
    operation.then(() => { if (current) setStatus(dirty ? '本地草稿已保存' : '') })
      .catch(() => { if (current) setStatus('本地草稿保存失败，请及时保存版本') })
    return () => { current = false }
  }, [userId, session, editor, store])

  const restore = useCallback(async (projectId: string, head: string, document: PatternDocument) => {
    try {
      const draft = await store.get(userId, projectId)
      if (draft) return restoreDraft(draft, head)
    } catch {
      readFailed.current = true
      setStatus('本地草稿读取失败，原记录已保留；当前显示服务器版本')
    }
    return { editor: createEditor(document), baseRevisionId: head, conflict: false }
  }, [userId, store])

  return { status, restore, discard: (projectId: string) => store.remove(userId, projectId) }
}
