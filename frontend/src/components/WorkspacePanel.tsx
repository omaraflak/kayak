import React, { useCallback, useEffect, useRef, useState } from 'react';
import { WorkspaceFileEntry } from '../types';
import { api } from '../api/client';
import { ContainerTerminal } from './ContainerTerminal';
import {
  breadcrumbs,
  fileKind,
  formatFileSize,
  joinPath,
  parentPath,
} from './workspaceFiles';
import {
  ArrowLeft,
  ChevronRight,
  Download,
  File,
  FileText,
  Folder,
  FolderUp,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Terminal as TerminalIcon,
  Upload,
  X,
} from 'lucide-react';

/**
 * Side panel giving direct access to a conversation's container: the workspace
 * filesystem (browse, preview, upload) and a real shell executing inside the
 * container itself. The workspace is bind-mounted at /workspace, so everything
 * seen or uploaded here is exactly what the agent sees.
 */

export type WorkspaceTab = 'files' | 'terminal';

/** Largest file the text preview will fetch; anything bigger is download-only. */
const MAX_TEXT_PREVIEW_BYTES = 512 * 1024;

interface WorkspacePanelProps {
  conversationId: string;
  initialTab: WorkspaceTab;
  /**
   * File to open in the preview, e.g. from a chip in the chat. Wrapped in an
   * object so repeating the same path still re-opens the preview.
   */
  previewRequest?: { path: string } | null;
  onClose: () => void;
}

function entryIcon(entry: WorkspaceFileEntry): React.ReactNode {
  if (entry.is_dir) return <Folder className="w-4 h-4 text-md-primary shrink-0" />;
  switch (fileKind(entry.name)) {
    case 'image':
      return <ImageIcon className="w-4 h-4 text-md-on-surface-variant shrink-0" />;
    case 'pdf':
    case 'text':
      return <FileText className="w-4 h-4 text-md-on-surface-variant shrink-0" />;
    default:
      return <File className="w-4 h-4 text-md-on-surface-variant shrink-0" />;
  }
}

export const WorkspacePanel: React.FC<WorkspacePanelProps> = ({
  conversationId,
  initialTab,
  previewRequest,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(initialTab);
  const [browserPath, setBrowserPath] = useState('.');
  const [entries, setEntries] = useState<WorkspaceFileEntry[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const [preview, setPreview] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const refreshListing = useCallback(async () => {
    try {
      const listing = await api.listWorkspaceFiles(conversationId, browserPath);
      setEntries(listing.entries);
      setListError(null);
    } catch (err) {
      setEntries([]);
      setListError(String(err));
    }
  }, [conversationId, browserPath]);

  useEffect(() => {
    refreshListing();
  }, [refreshListing]);

  // A chip in the chat (or a re-click on the same chip) opens its file here.
  useEffect(() => {
    if (previewRequest) {
      setActiveTab('files');
      setPreview(previewRequest.path);
    }
  }, [previewRequest]);

  // Text files are fetched for inline display; other kinds render from the URL.
  useEffect(() => {
    if (!preview || fileKind(preview) !== 'text') {
      setPreviewText(null);
      return;
    }
    let cancelled = false;
    setIsPreviewLoading(true);
    setPreviewError(null);
    api
      .readWorkspaceFileText(conversationId, preview)
      .then((text) => {
        if (cancelled) return;
        setPreviewText(
          text.length > MAX_TEXT_PREVIEW_BYTES
            ? text.slice(0, MAX_TEXT_PREVIEW_BYTES) + '\n… [truncated preview]'
            : text
        );
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(String(err));
      })
      .finally(() => {
        if (!cancelled) setIsPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, preview]);

  const handleUpload = async (fileList: FileList | null, preserveFolders: boolean) => {
    if (!fileList?.length) return;
    const uploads = Array.from(fileList).map((file) => {
      const relative = preserveFolders
        ? (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
        : file.name;
      return { file, path: joinPath(browserPath, relative) };
    });

    setIsUploading(true);
    try {
      await api.uploadWorkspaceFiles(conversationId, uploads);
      await refreshListing();
      setListError(null);
    } catch (err) {
      setListError(`Upload failed: ${err}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (folderInputRef.current) folderInputRef.current.value = '';
    }
  };

  const previewKind = preview ? fileKind(preview) : null;
  const previewUrl = preview ? api.workspaceFileUrl(conversationId, preview) : '';

  return (
    <aside className="w-[400px] shrink-0 border-l border-md-outline-variant bg-md-surface flex flex-col h-full min-h-0 transition-colors">
      {/* Tab bar */}
      <div className="h-12 px-3 border-b border-md-outline-variant flex items-center justify-between shrink-0 bg-md-surface-container-low">
        <div className="flex items-center gap-1">
          {(
            [
              { id: 'files', label: 'Files', icon: <Folder className="w-3.5 h-3.5" /> },
              { id: 'terminal', label: 'Terminal', icon: <TerminalIcon className="w-3.5 h-3.5" /> },
            ] as const
          ).map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                activeTab === tab.id
                  ? 'bg-md-primary text-md-on-primary shadow-xs'
                  : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer"
          title="Close panel"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {activeTab === 'files' && !preview && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Breadcrumbs + actions */}
          <div className="px-3 py-2 border-b border-md-outline-variant flex items-center justify-between gap-2 shrink-0">
            <div className="flex items-center gap-0.5 text-[11px] font-mono text-md-on-surface-variant overflow-x-auto min-w-0">
              {breadcrumbs(browserPath).map((crumb, index) => (
                <React.Fragment key={crumb.path}>
                  {index > 0 && <ChevronRight className="w-3 h-3 shrink-0" />}
                  <button
                    type="button"
                    onClick={() => setBrowserPath(crumb.path)}
                    className="hover:text-md-on-surface transition-colors cursor-pointer whitespace-nowrap"
                  >
                    {crumb.label}
                  </button>
                </React.Fragment>
              ))}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
                className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer disabled:opacity-40"
                title="Upload files here"
              >
                {isUploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
              </button>
              <button
                type="button"
                onClick={() => folderInputRef.current?.click()}
                disabled={isUploading}
                className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer disabled:opacity-40"
                title="Upload a folder here"
              >
                <FolderUp className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={refreshListing}
                className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer"
                title="Refresh"
              >
                <RefreshCw className="w-4 h-4" />
              </button>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => handleUpload(event.target.files, false)}
            />
            <input
              ref={folderInputRef}
              type="file"
              multiple
              className="hidden"
              {...({ webkitdirectory: '' } as Record<string, string>)}
              onChange={(event) => handleUpload(event.target.files, true)}
            />
          </div>

          {listError && (
            <p className="px-3 py-2 text-[11px] text-md-error leading-relaxed">{listError}</p>
          )}

          {/* Entries */}
          <div className="flex-1 overflow-y-auto py-1">
            {browserPath !== '.' && (
              <button
                type="button"
                onClick={() => setBrowserPath(parentPath(browserPath))}
                className="w-full px-3 py-1.5 flex items-center gap-2 text-xs text-md-on-surface-variant hover:bg-md-surface-container transition-colors cursor-pointer"
              >
                <ArrowLeft className="w-4 h-4 shrink-0" />
                <span className="font-mono">..</span>
              </button>
            )}
            {entries.map((entry) => (
              <button
                key={entry.name}
                type="button"
                onClick={() =>
                  entry.is_dir
                    ? setBrowserPath(joinPath(browserPath, entry.name))
                    : setPreview(joinPath(browserPath, entry.name))
                }
                className="w-full px-3 py-1.5 flex items-center gap-2 text-xs text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer text-left"
              >
                {entryIcon(entry)}
                <span className="truncate flex-1 min-w-0">{entry.name}</span>
                {!entry.is_dir && (
                  <span className="text-[10px] text-md-on-surface-variant font-mono shrink-0">
                    {formatFileSize(entry.size)}
                  </span>
                )}
              </button>
            ))}
            {entries.length === 0 && !listError && (
              <p className="px-3 py-6 text-center text-[11px] text-md-on-surface-variant leading-relaxed">
                This folder is empty. The agent's files appear here, and anything
                you upload is visible to the agent immediately.
              </p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'files' && preview && (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Preview header */}
          <div className="px-3 py-2 border-b border-md-outline-variant flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer"
              title="Back to files"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <span className="text-xs font-mono text-md-on-surface truncate flex-1 min-w-0">
              {preview}
            </span>
            <a
              href={previewUrl}
              download
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors"
              title="Download"
            >
              <Download className="w-4 h-4" />
            </a>
          </div>

          <div className="flex-1 overflow-auto min-h-0 bg-md-surface-container-lowest">
            {previewKind === 'image' && (
              <div className="p-3">
                <img
                  src={previewUrl}
                  alt={preview}
                  className="max-w-full rounded-xl border border-md-outline-variant"
                />
              </div>
            )}
            {previewKind === 'pdf' && (
              <iframe src={previewUrl} title={preview} className="w-full h-full border-0" />
            )}
            {previewKind === 'text' &&
              (isPreviewLoading ? (
                <div className="p-4 flex items-center gap-2 text-xs text-md-on-surface-variant">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              ) : previewError ? (
                <p className="p-4 text-[11px] text-md-error leading-relaxed">{previewError}</p>
              ) : (
                <pre className="p-3 text-[11px] font-mono text-md-on-surface whitespace-pre-wrap break-words leading-relaxed">
                  {previewText}
                </pre>
              ))}
            {previewKind === 'binary' && (
              <div className="p-6 text-center space-y-2">
                <File className="w-8 h-8 mx-auto text-md-on-surface-variant" />
                <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
                  No inline preview for this file type.
                </p>
                <a
                  href={previewUrl}
                  download
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity"
                >
                  <Download className="w-3.5 h-3.5" /> Download
                </a>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'terminal' && (
        <div className="flex-1 min-h-0">
          {/* Keyed so switching conversations opens a fresh shell. */}
          <ContainerTerminal key={conversationId} conversationId={conversationId} />
        </div>
      )}
    </aside>
  );
};
