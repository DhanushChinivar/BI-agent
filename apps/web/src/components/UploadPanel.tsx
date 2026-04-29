"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UploadedFile {
  file_id: string;
  title: string;
}

export function UploadPanel() {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const fetchFiles = useCallback(async () => {
    try {
      const res = await fetch("/api/agent/v1/uploads");
      if (res.ok) setFiles(await res.json());
    } catch {
      // silently ignore — Redis may not be running in dev
    }
  }, []);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const upload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/agent/v1/uploads", { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? "Upload failed");
      } else {
        await fetchFiles();
      }
    } catch {
      setError("Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const remove = async (file_id: string) => {
    await fetch(`/api/agent/v1/uploads/${file_id}`, { method: "DELETE" });
    setFiles((prev) => prev.filter((f) => f.file_id !== file_id));
  };

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) upload(file);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return (
    <div className="px-3 py-3 border-t border-zinc-800">
      <p className="text-xs font-medium text-zinc-500 mb-2 px-1">Uploaded files</p>

      {/* Drop zone */}
      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`
          flex items-center justify-center rounded-lg border border-dashed cursor-pointer
          text-xs py-3 transition-colors select-none
          ${dragging ? "border-indigo-500 bg-indigo-950/30 text-indigo-300" : "border-zinc-700 text-zinc-500 hover:border-zinc-500 hover:text-zinc-300"}
          ${uploading ? "opacity-50 pointer-events-none" : ""}
        `}
      >
        {uploading ? "Uploading…" : "Drop CSV / Excel / PDF"}
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.xlsm,.pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
      </div>

      {error && <p className="text-xs text-red-400 mt-1 px-1">{error}</p>}

      {/* File list */}
      {files.length > 0 && (
        <ul className="mt-2 space-y-1">
          {files.map((f) => (
            <li
              key={f.file_id}
              className="flex items-center justify-between gap-1 px-2 py-1 rounded-md text-xs text-zinc-400 hover:bg-zinc-800 group"
            >
              <span className="truncate">{f.title}</span>
              <button
                onClick={() => remove(f.file_id)}
                className="opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 transition-opacity shrink-0"
                title="Remove"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
