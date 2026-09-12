"use client";

import { useEffect, useMemo, useState } from "react";

import { getGenerationSettings, updateGenerationSettings, type GenerationOverride, type GenerationScope, type GenerationScopeSetting, type GenerationSettings } from "@/lib/api/client";
import { apiErrorMessage } from "@/lib/api/error";

type Props = {
  scope: GenerationScope;
  canOverride: boolean;
  canManageDefaults?: boolean;
  value: GenerationOverride;
  onChange: (next: GenerationOverride) => void;
};

/** Per-run selection only. Workspace defaults remain owner-managed server state. */
export function GenerationModelSelector({ scope, canOverride, canManageDefaults = false, value, onChange }: Props) {
  const [settings, setSettings] = useState<GenerationSettings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getGenerationSettings(controller.signal).then(setSettings).catch(requestError => {
      if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "モデル設定を取得できませんでした"));
    });
    return () => controller.abort();
  }, []);

  const current = settings?.scopes[scope];
  const selected = value.generation_provider && value.generation_model
    ? `${value.generation_provider}:${value.generation_model}`
    : "default";
  const options = useMemo(() => settings?.options ?? [], [settings]);
  const label = scope === "ask"
    ? "質問への回答"
    : scope === "discovery"
    ? "論文検索語の生成"
    : scope === "mind_map"
    ? "マインドマップ生成"
    : "比較・ギャップ分析";
  const saveDefault = async () => {
    if (!settings || !value.generation_provider || !value.generation_model || saving) return;
    const selectedOption=options.find(option => option.provider === value.generation_provider && option.model === value.generation_model);
    if (!selectedOption) { setError("選択したモデルは利用可能一覧にありません。"); return; }
    setSaving(true); setError(""); setNotice("");
    try {
      const fallback: GenerationScopeSetting={ provider:selectedOption.provider, model:selectedOption.model };
      const scopes: Record<GenerationScope, GenerationScopeSetting>={
        ask:settings.scopes.ask ?? fallback,
        discovery:settings.scopes.discovery ?? fallback,
        analysis:settings.scopes.analysis ?? fallback,
        mind_map:settings.scopes.mind_map ?? fallback,
      };
      scopes[scope]={ provider:selectedOption.provider, model:selectedOption.model };
      const next = await updateGenerationSettings(scopes);
      setSettings(next); setNotice("ワークスペースの既定に設定しました。"); onChange({});
    } catch (requestError) { setError(apiErrorMessage(requestError, "既定モデルを保存できませんでした")); }
    finally { setSaving(false); }
  };

  if (error) return <p role="status" className="text-[11px] text-amber-800">{error} ワークスペース既定値を使用します。</p>;
  if (!settings) return <p role="status" className="text-[11px] text-[#68736f]">モデル設定を確認しています…</p>;
  return <div className="block text-[11px] text-[#52605b]"><label>
    <span className="mb-1 block font-semibold">{label}のモデル</span>
    <select
      value={selected}
      disabled={!canOverride}
      onChange={event => {
        if (event.target.value === "default") { onChange({}); return; }
        const option = options.find(item => `${item.provider}:${item.model}` === event.target.value);
        onChange(option ? { generation_provider: option.provider, generation_model: option.model } : {});
      }}
      className="w-full rounded-lg border border-[#b8c7be] bg-white px-2.5 py-2 text-xs outline-none focus:border-[#164f3b] disabled:cursor-not-allowed disabled:opacity-60"
    >
      <option value="default">既定: {current?.provider} / {current?.model}</option>
      {options.map(option => <option key={`${option.provider}:${option.model}`} value={`${option.provider}:${option.model}`} disabled={!option.available}>{option.provider} / {option.model}{option.available ? "" : `（${option.reason || "利用不可"}）`}</option>)}
    </select>
    {!canOverride && <span className="mt-1 block text-[10px] text-amber-800">閲覧者はモデルを切り替えられません。</span>}
  </label>{canManageDefaults && value.generation_provider && value.generation_model && <button type="button" disabled={saving} onClick={() => void saveDefault()} className="mt-1.5 rounded-full border border-[#9ab6a6] bg-white px-2.5 py-1 text-[10px] font-semibold text-[#164f3b] disabled:opacity-50">{saving ? "保存中…" : "この選択を既定に設定"}</button>}{notice && <p role="status" className="mt-1 text-[10px] text-[#35634f]">{notice}</p>}</div>;
}
