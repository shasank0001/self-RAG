import { useQueries } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "@/lib/api/client";
import type { IngestionStatusResponse } from "@/types/chat";
import { useBins, useCreateBin, useUploadBinFile } from "@/hooks/useChat";

type LocalUploadEntry = {
  jobId: string;
  itemId: string;
  binId: string;
  fileName: string;
};

export function BinListPage() {
  const binsQuery = useBins();
  const createBin = useCreateBin();
  const uploadBinFile = useUploadBinFile();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [selectedBinId, setSelectedBinId] = useState<string | null>(null);
  const [binTitle, setBinTitle] = useState("");
  const [binDescription, setBinDescription] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [recentUploads, setRecentUploads] = useState<LocalUploadEntry[]>([]);

  useEffect(() => {
    const bins = binsQuery.data ?? [];
    if (!bins.length) {
      setSelectedBinId(null);
      return;
    }

    if (!selectedBinId || !bins.some((bin) => bin.id === selectedBinId)) {
      setSelectedBinId(bins[0].id);
    }
  }, [binsQuery.data, selectedBinId]);

  const selectedBin = useMemo(
    () => (binsQuery.data ?? []).find((bin) => bin.id === selectedBinId) ?? null,
    [binsQuery.data, selectedBinId],
  );

  const uploadQueries = useQueries({
    queries: recentUploads.map((entry) => ({
      queryKey: ["ingestion-job", entry.jobId],
      queryFn: () => apiFetch<IngestionStatusResponse>(`/api/v1/ingestion/jobs/${entry.jobId}`),
      refetchInterval: (query: { state: { data?: IngestionStatusResponse } }) => {
        const status = query.state.data?.status;
        return status === "succeeded" || status === "failed" ? false : 2000;
      },
      staleTime: 0,
      retry: 1,
    })),
  });

  const uploadStatusMap = useMemo(
    () =>
      Object.fromEntries(
        recentUploads.map((entry, index) => [entry.jobId, uploadQueries[index]?.data ?? null]),
      ) as Record<string, IngestionStatusResponse | null>,
    [recentUploads, uploadQueries],
  );

  const onCreateBin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = binTitle.trim();
    if (!title) {
      return;
    }

    const created = await createBin.mutateAsync({
      title,
      description: binDescription.trim() || null,
    });
    setBinTitle("");
    setBinDescription("");
    setSelectedBinId(created.id);
  };

  const onPickFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setPendingFiles(Array.from(event.target.files ?? []));
  };

  const onUploadFiles = async () => {
    if (!selectedBin || pendingFiles.length === 0) {
      return;
    }

    const createdEntries: LocalUploadEntry[] = [];
    for (const file of pendingFiles) {
      const result = await uploadBinFile.mutateAsync({ binId: selectedBin.id, file });
      createdEntries.push({
        jobId: result.job_id,
        itemId: result.item_id,
        binId: result.bin_id,
        fileName: file.name,
      });
    }

    setRecentUploads((current) => [...createdEntries.reverse(), ...current].slice(0, 12));
    setPendingFiles([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <section className="bins-page page-card">
      <header className="bins-header">
        <div>
          <h2>Bins</h2>
          <p className="bins-subtitle">Create a knowledge bin, load documents into it, then use it from chat.</p>
        </div>
        <Link to="/chat" className="bins-cta">
          Start chat
        </Link>
      </header>

      <section className="bins-flow">
        <article className="bin-workbench-card bin-create-card">
          <div className="bin-step-kicker">Step 1</div>
          <h3>Create a bin</h3>
          <p className="bin-panel-copy">Each bin is a private document collection. Keep titles clear so it is easy to choose inside chat.</p>
          <form className="bin-form" onSubmit={(event) => void onCreateBin(event)}>
            <label>
              <span>Bin title</span>
              <input
                value={binTitle}
                onChange={(event) => setBinTitle(event.target.value)}
                placeholder="Example: HR policies"
                maxLength={80}
              />
            </label>
            <label>
              <span>Description</span>
              <textarea
                value={binDescription}
                onChange={(event) => setBinDescription(event.target.value)}
                placeholder="What belongs in this bin?"
                rows={3}
                maxLength={240}
              />
            </label>
            <button type="submit" className="bins-primary-button" disabled={!binTitle.trim() || createBin.isPending}>
              {createBin.isPending ? "Creating..." : "Create bin"}
            </button>
          </form>
          {createBin.error ? <p className="bins-inline-error">{createBin.error.message}</p> : null}
        </article>

        <article className="bin-workbench-card bin-upload-card">
          <div className="bin-step-kicker">Step 2</div>
          <h3>Upload files</h3>
          {selectedBin ? (
            <>
              <p className="bin-panel-copy">
                Adding files to <strong>{selectedBin.title}</strong> makes grounded responses available in chat.
              </p>
              <label className="bin-file-picker">
                <span>Choose files</span>
                <input ref={fileInputRef} type="file" multiple onChange={onPickFiles} />
              </label>
              <p className="bin-upload-hint">Supported types depend on the backend parser. Try `.pdf`, `.docx`, `.txt`, or `.md`.</p>
              {pendingFiles.length ? (
                <div className="bin-file-list">
                  {pendingFiles.map((file) => (
                    <span key={`${file.name}-${file.size}`} className="bin-file-pill">
                      {file.name}
                    </span>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                className="bins-primary-button"
                onClick={() => void onUploadFiles()}
                disabled={pendingFiles.length === 0 || uploadBinFile.isPending}
              >
                {uploadBinFile.isPending ? "Queueing upload..." : "Upload to selected bin"}
              </button>
            </>
          ) : (
            <p className="bin-panel-copy">Create a bin first, then select it here to upload files.</p>
          )}
          {uploadBinFile.error ? <p className="bins-inline-error">{uploadBinFile.error.message}</p> : null}
        </article>
      </section>

      <section className="bins-section">
        <div className="bins-section-heading">
          <h3>Your bins</h3>
          <span>{binsQuery.data?.length ?? 0} total</span>
        </div>
        <div className="bins-grid">
          {(binsQuery.data ?? []).map((bin) => (
            <button
              key={bin.id}
              type="button"
              className={`bin-card ${selectedBinId === bin.id ? "is-selected" : ""}`}
              onClick={() => setSelectedBinId(bin.id)}
            >
              <div className="bin-card-header">
                <h4>{bin.title}</h4>
                {selectedBinId === bin.id ? <span className="bin-card-badge">Selected</span> : null}
              </div>
              <p>{bin.description || "No description yet."}</p>
              <small>{bin.vector_namespace}</small>
            </button>
          ))}
        </div>
        {!binsQuery.isLoading && (binsQuery.data?.length ?? 0) === 0 ? (
          <div className="bins-empty-state">
            <strong>No bins yet</strong>
            <p>Create your first bin above, then upload documents into it.</p>
          </div>
        ) : null}
      </section>

      <section className="bins-section">
        <div className="bins-section-heading">
          <h3>Recent uploads</h3>
          <span>Live ingestion status</span>
        </div>
        {recentUploads.length ? (
          <div className="upload-status-list">
            {recentUploads.map((entry) => {
              const status = uploadStatusMap[entry.jobId];
              return (
                <article key={entry.jobId} className="upload-status-card">
                  <div className="upload-status-header">
                    <strong>{entry.fileName}</strong>
                    <span className={`upload-status-badge is-${status?.status ?? "queued"}`}>
                      {status?.status ?? "queued"}
                    </span>
                  </div>
                  <p>Bin: {(binsQuery.data ?? []).find((bin) => bin.id === entry.binId)?.title ?? "Selected bin"}</p>
                  <small>Job: {entry.jobId}</small>
                  {status?.last_error ? <p className="bins-inline-error">{status.last_error}</p> : null}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="bins-empty-state">
            <strong>No uploads queued yet</strong>
            <p>When you upload a file, its ingestion job status will appear here.</p>
          </div>
        )}
      </section>

      <div className="bins-next-step">
        <span>Step 3</span>
        <p>After at least one file finishes ingesting, open chat and select that bin for grounded answers.</p>
      </div>
    </section>
  );
}
