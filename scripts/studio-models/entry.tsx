import React from "react";
import { createRoot } from "react-dom/client";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import messages from "@/i18n/locales/zh-CN/common.json";
import { SettingsView } from "@/components/settings/SettingsView";
import { TooltipProvider } from "@/components/ui/tooltip";

async function main() {
  await i18n.use(initReactI18next).init({
    lng: "zh-CN", fallbackLng: false, defaultNS: "common",
    resources: { "zh-CN": { common: messages } }, interpolation: { escapeValue: false },
  });
  const noop = () => {};
  createRoot(document.getElementById("root")!).render(
    <TooltipProvider>
      <SettingsView initialSection="models" showSidebar={false} themePreference="dark"
        onChangeTheme={noop} onModelNameChange={noop}
        onBackToChat={() => { window.parent.location.hash = "settings/overview"; }} />
    </TooltipProvider>
  );
}

void main();
