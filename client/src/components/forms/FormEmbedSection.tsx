/**
 * Form Embed Section
 *
 * Collapsible inline section for managing embed secrets and showing
 * integration guide. Mounted inside FormInfoDialog when editing.
 */

import { useState, useEffect, useCallback } from "react";
import {
  Plus,
  Trash2,
  Copy,
  Check,
  AlertTriangle,
  Code,
  Link,
  ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";

// ============================================================================
// Types
// ============================================================================

interface EmbedSecret {
  id: string;
  name: string;
  is_active: boolean;
  created_at: string;
}

interface EmbedSecretCreated extends EmbedSecret {
  raw_secret: string;
}

interface FormEmbedSectionProps {
  formId: string;
}

// ============================================================================
// Component
// ============================================================================

export function FormEmbedSection({ formId }: FormEmbedSectionProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [secrets, setSecrets] = useState<EmbedSecret[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Create form state
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSecret, setCreateSecret] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  // Reveal state (shown once after creation)
  const [revealedSecret, setRevealedSecret] = useState<EmbedSecretCreated | null>(null);
  const [copied, setCopied] = useState(false);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<EmbedSecret | null>(null);

  // ========================================================================
  // Data fetching
  // ========================================================================

  const fetchSecrets = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await authFetch(`/api/forms/${formId}/embed-secrets`);
      if (res.ok) {
        setSecrets(await res.json());
      }
    } catch {
      toast.error("Failed to load embed secrets");
    } finally {
      setIsLoading(false);
    }
  }, [formId]);

  useEffect(() => {
    if (isOpen) {
      fetchSecrets();
    }
  }, [isOpen, fetchSecrets]);

  // ========================================================================
  // Actions
  // ========================================================================

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createName.trim()) return;

    setIsCreating(true);
    try {
      const res = await authFetch(`/api/forms/${formId}/embed-secrets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: createName.trim(),
          ...(createSecret.trim() && { secret: createSecret.trim() }),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const created: EmbedSecretCreated = await res.json();
      setRevealedSecret(created);
      setIsCreateOpen(false);
      setCreateName("");
      setCreateSecret("");
      fetchSecrets();
      toast.success("Embed secret created");
    } catch {
      toast.error("Failed to create embed secret");
    } finally {
      setIsCreating(false);
    }
  };

  const handleToggleActive = async (secret: EmbedSecret) => {
    try {
      const res = await authFetch(
        `/api/forms/${formId}/embed-secrets/${secret.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: !secret.is_active }),
        },
      );
      if (!res.ok) throw new Error(await res.text());
      fetchSecrets();
      toast.success(
        secret.is_active ? "Secret deactivated" : "Secret activated",
      );
    } catch {
      toast.error("Failed to update secret");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      const res = await authFetch(
        `/api/forms/${formId}/embed-secrets/${deleteTarget.id}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(await res.text());
      setDeleteTarget(null);
      fetchSecrets();
      toast.success("Secret deleted");
    } catch {
      toast.error("Failed to delete secret");
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ========================================================================
  // Code snippets
  // ========================================================================

  const embedUrl = `${window.location.origin}/embed/forms/${formId}`;

  const iframeSnippet = `<iframe
  src="${embedUrl}?param1=value1&hmac=COMPUTED_HMAC"
  style="width: 100%; height: 600px; border: none;"
  allow="clipboard-write"
></iframe>`;

  const pythonSnippet = `import hashlib
import hmac
from urllib.parse import urlencode

def embed_url(params: dict, secret: str) -> str:
    """Build a signed embed URL."""
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return f"${embedUrl}?{urlencode(params)}&hmac={signature}"

# Example:
url = embed_url({"agent_id": "42", "ticket_id": "1001"}, "YOUR_SECRET")`;

  const jsSnippet = `async function embedUrl(params, secret) {
  const message = Object.keys(params)
    .sort()
    .map(k => \`\${k}=\${params[k]}\`)
    .join("&");

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  const hmac = Array.from(new Uint8Array(sig))
    .map(b => b.toString(16).padStart(2, "0")).join("");

  const qs = new URLSearchParams({ ...params, hmac }).toString();
  return \`${embedUrl}?\${qs}\`;
}

// Example:
const url = await embedUrl({ agent_id: "42", ticket_id: "1001" }, "YOUR_SECRET");`;

  // ========================================================================
  // Render
  // ========================================================================

  return (
    <>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex items-center gap-2 text-sm font-medium hover:underline"
          >
            <ChevronRight
              className={`h-4 w-4 transition-transform ${isOpen ? "rotate-90" : ""}`}
            />
            <Link className="h-4 w-4" />
            Embed Settings
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent className="mt-3 space-y-4">
          {/* ============ Secrets ============ */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Shared secrets for HMAC-authenticated iframe embedding.
              </p>
              <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Create Secret
              </Button>
            </div>

            {isLoading ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                Loading...
              </p>
            ) : secrets.length === 0 ? (
              <div className="text-center py-4 text-muted-foreground">
                <Link className="h-6 w-6 mx-auto mb-2 opacity-50" />
                <p className="text-sm">No embed secrets configured.</p>
                <p className="text-xs mt-1">
                  Create a secret to enable iframe embedding.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {secrets.map((secret) => (
                  <div
                    key={secret.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${
                      secret.is_active
                        ? "border-l-4 border-l-green-500"
                        : "border-l-4 border-l-gray-300 opacity-60"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <div>
                        <p className="text-sm font-medium">{secret.name}</p>
                        <p className="text-xs text-muted-foreground">
                          Created{" "}
                          {new Date(secret.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      <Badge
                        variant={secret.is_active ? "default" : "secondary"}
                      >
                        {secret.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleToggleActive(secret)}
                      >
                        {secret.is_active ? "Deactivate" : "Activate"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setDeleteTarget(secret)}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Inline create form */}
            {isCreateOpen && (
              <form
                onSubmit={handleCreate}
                className="space-y-3 rounded-lg border p-4 bg-muted/50"
              >
                <div className="space-y-2">
                  <Label htmlFor="embed-secret-name">Name</Label>
                  <Input
                    id="embed-secret-name"
                    placeholder="e.g., Halo Production"
                    value={createName}
                    onChange={(e) => setCreateName(e.target.value)}
                    autoFocus
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="embed-secret-value">Secret (optional)</Label>
                  <Input
                    id="embed-secret-value"
                    placeholder="Leave blank to auto-generate"
                    value={createSecret}
                    onChange={(e) => setCreateSecret(e.target.value)}
                    className="font-mono text-sm"
                  />
                </div>
                <div className="flex gap-2 justify-end">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setIsCreateOpen(false);
                      setCreateName("");
                      setCreateSecret("");
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    size="sm"
                    disabled={isCreating || !createName.trim()}
                  >
                    {isCreating ? "Creating..." : "Add"}
                  </Button>
                </div>
              </form>
            )}

            {/* One-time secret reveal */}
            {revealedSecret && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription className="space-y-2">
                  <p>Copy this secret now. It will not be shown again.</p>
                  <div className="flex gap-2">
                    <Input
                      value={revealedSecret.raw_secret}
                      readOnly
                      className="font-mono text-sm"
                    />
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => handleCopy(revealedSecret.raw_secret)}
                    >
                      {copied ? (
                        <Check className="h-4 w-4" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRevealedSecret(null)}
                  >
                    Dismiss
                  </Button>
                </AlertDescription>
              </Alert>
            )}
          </div>

          {/* ============ Integration Guide ============ */}
          <div className="space-y-4 pt-2">
            <h4 className="text-sm font-semibold flex items-center gap-2">
              <Code className="h-4 w-4" />
              Integration Guide
            </h4>

            <div>
              <p className="text-xs text-muted-foreground mb-1">iframe HTML</p>
              <div className="relative">
                <pre className="bg-muted p-3 rounded-md text-xs overflow-x-auto">
                  {iframeSnippet}
                </pre>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-2 right-2 h-6 w-6"
                  onClick={() => handleCopy(iframeSnippet)}
                >
                  <Copy className="h-3 w-3" />
                </Button>
              </div>
            </div>

            <div>
              <p className="text-xs text-muted-foreground mb-1">
                HMAC Signing — Python
              </p>
              <div className="relative">
                <pre className="bg-muted p-3 rounded-md text-xs overflow-x-auto">
                  {pythonSnippet}
                </pre>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-2 right-2 h-6 w-6"
                  onClick={() => handleCopy(pythonSnippet)}
                >
                  <Copy className="h-3 w-3" />
                </Button>
              </div>
            </div>

            <div>
              <p className="text-xs text-muted-foreground mb-1">
                HMAC Signing — JavaScript
              </p>
              <div className="relative">
                <pre className="bg-muted p-3 rounded-md text-xs overflow-x-auto">
                  {jsSnippet}
                </pre>
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-2 right-2 h-6 w-6"
                  onClick={() => handleCopy(jsSnippet)}
                >
                  <Copy className="h-3 w-3" />
                </Button>
              </div>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>

      {/* ============ Delete Confirmation ============ */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete embed secret?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &quot;{deleteTarget?.name}&quot;.
              Any integrations using this secret will stop working.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
