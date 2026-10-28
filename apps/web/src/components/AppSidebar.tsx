import {
  FileCodeIcon,
  FolderOpenIcon,
  GridFourIcon,
  ImageIcon,
  PlusIcon,
  UserCircleIcon,
} from '@phosphor-icons/react'
import type { User } from '../lib/account'
import { BrandMark } from './BrandMark'

interface Props {
  user: User
  inert?: boolean
  libraryOpen: boolean
  busy: boolean
  onShowLibrary: () => void
  onImportImage: () => void
  onImportOxs: () => void
  onCreateBlank: () => void
  onOpenAccount: () => void
}

export function AppSidebar({
  user,
  inert,
  libraryOpen,
  busy,
  onShowLibrary,
  onImportImage,
  onImportOxs,
  onCreateBlank,
  onOpenAccount,
}: Props) {
  const initial = user.email.trim().charAt(0).toUpperCase() || 'U'

  return (
    <aside className="app-sidebar" aria-label="主导航" inert={inert}>
      <div className="sidebar-brand" aria-label="StitchForge 十字绣工作台">
        <BrandMark className="sidebar-brand-mark" />
        <span>StitchForge</span>
      </div>

      {libraryOpen && (
        <div className="sidebar-start-actions" aria-label="开始制作图纸">
          <span className="sidebar-section-label">开始</span>
          <button
            className="sidebar-task-button primary"
            type="button"
            disabled={busy}
            onClick={onImportImage}
          >
            <ImageIcon size={20} weight="duotone" aria-hidden="true" />
            <span><strong>图片转图纸</strong><small>上传图片自动生成</small></span>
          </button>
          <button className="sidebar-task-button" type="button" disabled={busy} onClick={onImportOxs}>
            <FileCodeIcon size={20} weight="duotone" aria-hidden="true" />
            <span><strong>导入 OXS / XSD</strong><small>打开已有图纸文件</small></span>
          </button>
          <button className="sidebar-blank-action" type="button" disabled={busy} onClick={onCreateBlank}>
            <PlusIcon size={15} aria-hidden="true" />新建空白图纸
          </button>
        </div>
      )}

      <nav className="sidebar-nav" aria-label="工作台">
        {libraryOpen && <span className="sidebar-section-label">图纸</span>}
        <button
          type="button"
          className={libraryOpen ? 'active' : ''}
          aria-current={libraryOpen ? 'page' : undefined}
          onClick={onShowLibrary}
        >
          <FolderOpenIcon size={20} weight={libraryOpen ? 'fill' : 'regular'} aria-hidden="true" />
          <span>图纸库</span>
        </button>
        {!libraryOpen && (
          <div className="sidebar-current" aria-current="page">
            <GridFourIcon size={20} weight="fill" aria-hidden="true" />
            <span>当前图纸</span>
          </div>
        )}
      </nav>

      <button className="sidebar-account" type="button" onClick={onOpenAccount}>
        <span className="sidebar-avatar" aria-hidden="true">{initial}</span>
        <span className="sidebar-account-copy">
          <strong title={user.email}>{user.email}</strong>
          <small>剩余 {user.credits} 次</small>
        </span>
        <UserCircleIcon size={19} aria-hidden="true" />
      </button>
    </aside>
  )
}
