import { useState, type FormEvent } from "react";
import { Loader2, Upload, CheckCircle2, AlertCircle, FileUp } from "lucide-react";
import { uploadItem, type Collection, type Credentials } from "@/lib/webdav";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  creds: Credentials;
  collection: Collection;
}

interface FileResult {
  name: string;
  status: "pending" | "ok" | "error";
  message?: string;
}

function contentTypeFor(filename: string): string {
  if (filename.toLowerCase().endsWith(".vcf")) return "text/vcard; charset=utf-8";
  return "text/calendar; charset=utf-8";
}

export function UploadDialog({ open, onClose, onDone, creds, collection }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<FileResult[]>([]);
  const [busy, setBusy] = useState(false);

  function reset() {
    setFiles([]);
    setResults([]);
    setBusy(false);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (files.length === 0) return;
    setBusy(true);
    setResults(files.map((f) => ({ name: f.name, status: "pending" })));

    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      try {
        const text = await f.text();
        await uploadItem(creds, collection.href, f.name, text, contentTypeFor(f.name));
        setResults((prev) => {
          const next = [...prev];
          next[i] = { name: f.name, status: "ok" };
          return next;
        });
      } catch (err) {
        setResults((prev) => {
          const next = [...prev];
          next[i] = {
            name: f.name,
            status: "error",
            message: err instanceof Error ? err.message : String(err),
          };
          return next;
        });
      }
    }
    setBusy(false);
  }

  function handleClose() {
    if (busy) return;
    const hadResults = results.length > 0;
    onClose();
    reset();
    if (hadResults) onDone();
  }

  const allDone = results.length > 0 && results.every((r) => r.status !== "pending");
  const anyOk = results.some((r) => r.status === "ok");

  return (
    <Dialog open={open} onClose={handleClose} dismissible={!busy}>
      <DialogHeader>
        <DialogTitle>Upload to {collection.displayname || "collection"}</DialogTitle>
        <DialogDescription>
          Drop one or more <code>.ics</code> or <code>.vcf</code> files. Each file becomes a
          calendar item in this collection.
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={handleSubmit}>
        <DialogBody className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="up-files">Files</Label>
            <label
              htmlFor="up-files"
              className="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-[var(--color-border)] p-6 cursor-pointer hover:border-[var(--color-primary)] hover:bg-[var(--color-muted)]/40 transition-colors"
            >
              <FileUp className="size-6 text-[var(--color-muted-foreground)]" />
              <div className="text-sm text-center">
                <span className="font-medium text-[var(--color-primary)]">Click to select</span>{" "}
                or drop here
              </div>
              <div className="text-xs text-[var(--color-muted-foreground)]">
                {files.length === 0
                  ? "ICS or VCF files"
                  : `${files.length} file${files.length === 1 ? "" : "s"} selected`}
              </div>
            </label>
            <input
              id="up-files"
              type="file"
              accept=".ics,.vcf,text/calendar,text/vcard"
              multiple
              className="sr-only"
              onChange={(e) => {
                setFiles(Array.from(e.target.files ?? []));
                setResults([]);
              }}
              disabled={busy}
            />
          </div>

          {results.length > 0 && (
            <ul className="space-y-1.5 rounded-md border border-[var(--color-border)] p-2 max-h-48 overflow-y-auto">
              {results.map((r) => (
                <li key={r.name} className="flex items-start gap-2 text-sm">
                  {r.status === "pending" && (
                    <Loader2 className="size-4 mt-0.5 shrink-0 animate-spin text-[var(--color-muted-foreground)]" />
                  )}
                  {r.status === "ok" && (
                    <CheckCircle2 className="size-4 mt-0.5 shrink-0 text-green-600" />
                  )}
                  {r.status === "error" && (
                    <AlertCircle className="size-4 mt-0.5 shrink-0 text-[var(--color-destructive)]" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate" title={r.name}>{r.name}</div>
                    {r.message && (
                      <div className="text-xs text-[var(--color-destructive)] whitespace-pre-wrap">
                        {r.message}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {allDone && (
            <Alert variant={anyOk ? "default" : "destructive"}>
              <AlertDescription>
                {anyOk
                  ? `Upload complete. Close to refresh the collection.`
                  : `All uploads failed.`}
              </AlertDescription>
            </Alert>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={handleClose} disabled={busy}>
            {allDone ? "Close" : "Cancel"}
          </Button>
          <Button type="submit" disabled={busy || files.length === 0 || allDone}>
            {busy ? <Loader2 className="animate-spin" /> : <Upload />}
            {busy ? "Uploading..." : `Upload ${files.length || ""}`}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
