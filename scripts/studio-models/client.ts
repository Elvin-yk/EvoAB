// Model settings use the same-origin Studio API in place of nanobot's gateway.
const client = {
  async requestMutation(action: string, payload = {}, timeoutMs = 20_000) {
    const response = await fetch("/model-ui/actions/" + encodeURIComponent(action), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(timeoutMs),
    });
    const result = await response.json();
    if (!response.ok) throw Object.assign(new Error(result.detail || result.error || response.statusText), { status: response.status });
    return result;
  },
};
const context = { client, token: "studio", getToken: () => "studio", webuiCapabilities: [], ingressLimits: null, modelName: null };
export const useClient = () => context;
