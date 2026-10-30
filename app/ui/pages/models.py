"""Model and third-party API profile management."""

from __future__ import annotations

import threading
import tkinter as tk
import uuid
from tkinter import ttk

from ...model_client import ModelClient, make_client
from ...secretstore import masked, protect, unprotect
from ...ui.theme import C, display_font, font, px, truncate
from ...ui.widgets import (
    BaseDialog,
    Card,
    LabeledEntry,
    RoundedButton,
    ScrollFrame,
    confirm,
)
from .base import BasePage

PROVIDERS = [
    ("Ollama（本地）", "ollama"),
    ("OpenAI 兼容 API", "openai"),
    ("离线演示（无需模型）", "mock"),
]
PRESETS = {
    "本地 Ollama": {"provider": "ollama", "base_url": "http://127.0.0.1:11434",
                  "model": "qwen3:1.7b"},
    "DeepSeek": {"provider": "openai", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat", "supports_vision": True},
    "硅基流动 SiliconFlow": {"provider": "openai", "base_url": "https://api.siliconflow.cn/v1",
                           "model": "Qwen/Qwen2.5-7B-Instruct"},
    "Moonshot": {"provider": "openai", "base_url": "https://api.moonshot.cn/v1",
                 "model": "moonshot-v1-8k"},
    "OpenAI": {"provider": "openai", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4o-mini"},
    "自定义": {"provider": "openai", "base_url": "", "model": ""},
}


def provider_label(provider: str) -> str:
    if provider == "ollama":
        return "OLLAMA · 本地"
    if provider == "mock":
        return "DEMO · 离线"
    return "API · OpenAI 兼容"


class ModelDialog(BaseDialog):
    def __init__(self, parent, page: "ModelsPage", profile: dict | None = None) -> None:
        self.page = page
        self.profile = profile
        self.is_new = profile is None
        super().__init__(parent, "配置模型" if profile else "添加模型",
                         "本地 Ollama 或第三方 OpenAI 兼容接口", width=720, height=730,
                         icon=page.icon)
        self.models: list[str] = []
        scroll = ScrollFrame(self.body, bg=C["card"])
        scroll.pack(fill="both", expand=True, padx=px(22), pady=px(14))
        form = tk.Frame(scroll.inner, bg=C["card"])
        form.pack(fill="both", expand=True, padx=px(4), pady=px(4))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)

        self.name = LabeledEntry(form, "配置名称", (profile or {}).get("name", ""),
                                 hint="给自己看的名字", width=28)
        self.name.grid(row=0, column=0, sticky="ew", padx=(0, px(8)), pady=(0, px(8)))
        preset_frame = tk.Frame(form, bg=C["card"])
        preset_frame.grid(row=0, column=1, sticky="ew", padx=(px(8), 0), pady=(0, px(8)))
        tk.Label(preset_frame, text="快速填充", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(5)))
        self.preset = ttk.Combobox(preset_frame, values=list(PRESETS), state="readonly",
                                   style="Arona.TCombobox", font=font(9))
        self.preset.pack(fill="x")
        self.preset.set("自定义")
        self.preset.bind("<<ComboboxSelected>>", lambda _e: self._apply_preset())

        row = tk.Frame(form, bg=C["card"])
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)
        provider_frame = tk.Frame(row, bg=C["card"])
        provider_frame.grid(row=0, column=0, sticky="ew", padx=(0, px(8)))
        tk.Label(provider_frame, text="提供方", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(5)))
        self.provider = ttk.Combobox(provider_frame, values=[label for label, _ in PROVIDERS],
                                     state="readonly", style="Arona.TCombobox", font=font(9))
        self.provider.pack(fill="x")
        current_provider = (profile or {}).get("provider", "ollama")
        self.provider.set(next((label for label, value in PROVIDERS if value == current_provider),
                               PROVIDERS[0][0]))
        self.provider.bind("<<ComboboxSelected>>", lambda _e: self._provider_changed())
        self.base_url = LabeledEntry(row, "API 地址 / Base URL",
                                     (profile or {}).get("base_url", "http://127.0.0.1:11434"),
                                     hint="不含 /chat/completions", width=32)
        self.base_url.grid(row=0, column=1, sticky="ew", padx=(px(8), 0))
        self.base_url.grid_configure(pady=(px(19), 0))

        model_row = tk.Frame(form, bg=C["card"])
        model_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        model_row.grid_columnconfigure(0, weight=1)
        model_label = tk.Frame(model_row, bg=C["card"])
        model_label.grid(row=0, column=0, sticky="ew", padx=(0, px(8)))
        tk.Label(model_label, text="模型名称", bg=C["card"], fg=C["text_soft"],
                 font=font(9, "bold")).pack(anchor="w", pady=(0, px(5)))
        model_inner = tk.Frame(model_label, bg=C["card_soft"], highlightthickness=1,
                               highlightbackground=C["line"])
        model_inner.pack(fill="x")
        self.model = ttk.Combobox(model_inner, values=[], style="Arona.TCombobox",
                                  font=font(9))
        self.model.pack(side="left", fill="x", expand=True, padx=px(2), pady=px(2))
        self.model.set((profile or {}).get("model", "qwen3:1.7b"))
        self.fetch_btn = RoundedButton(model_row, text="拉取模型列表", command=self._fetch_models,
                                       style="secondary", width=100, height=34,
                                       bg=C["card"], font_size=8)
        self.fetch_btn.grid(row=0, column=1, sticky="se", pady=(px(19), 0))
        self.key = LabeledEntry(form, "API Key", "", hint="Ollama 可留空", width=40,
                                show="•")
        self.key.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, px(2)))
        if profile and profile.get("api_key_enc"):
            try:
                existing_key = unprotect(profile["api_key_enc"])
                self.key.entry.configure(disabledbackground=C["card_soft"])
                self.key.entry.insert(0, existing_key)
                self.key.entry.configure(show="•")
            except Exception:
                pass
        key_tools = tk.Frame(form, bg=C["card"])
        key_tools.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        self.show_key = tk.BooleanVar(value=False)
        tk.Checkbutton(key_tools, text="显示 Key", variable=self.show_key,
                       command=self._toggle_key, bg=C["card"], fg=C["muted"],
                       activebackground=C["card"], selectcolor=C["card"],
                       font=font(8), highlightthickness=0, bd=0).pack(side="left")
        self.clear_key = tk.BooleanVar(value=False)
        if profile and profile.get("api_key_enc"):
            tk.Checkbutton(key_tools, text="清除已保存的 Key", variable=self.clear_key,
                           bg=C["card"], fg=C["muted"], activebackground=C["card"],
                           selectcolor=C["card"], font=font(8),
                           highlightthickness=0, bd=0).pack(side="left", padx=(px(12), 0))
            tk.Label(key_tools, text="Key 已用 Windows DPAPI 加密保存", bg=C["card"],
                     fg=C["green"], font=font(7)).pack(side="right")
        else:
            tk.Label(key_tools, text="Key 只会加密保存在本机 data/config.json", bg=C["card"],
                     fg=C["muted"], font=font(7)).pack(side="right")

        params = tk.Frame(form, bg=C["card_soft"], highlightthickness=1,
                          highlightbackground=C["line"])
        params.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, px(8)))
        for col in range(4):
            params.grid_columnconfigure(col, weight=1)
        self.temp = self._param(params, 0, "温度", str((profile or {}).get("temperature", 0.8)))
        self.max_tokens = self._param(params, 1, "最大回复 tokens",
                                      str((profile or {}).get("max_tokens", 1200)))
        self.num_ctx = self._param(params, 2, "上下文 num_ctx",
                                   str((profile or {}).get("num_ctx", 4096)))
        self.timeout = self._param(params, 3, "超时（秒）", str((profile or {}).get("timeout", 300)))
        think_row = tk.Frame(form, bg=C["card"])
        think_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        self.think_var = tk.BooleanVar(value=(profile or {}).get("think", None) is False)
        tk.Checkbutton(think_row, text="尝试关闭思考输出", variable=self.think_var,
                       bg=C["card"], fg=C["text_soft"], activebackground=C["card"],
                       selectcolor=C["card"], font=font(8), highlightthickness=0,
                       bd=0).pack(side="left")
        self.compact_var = tk.BooleanVar(value=bool((profile or {}).get("compact_prompt", False)))
        tk.Checkbutton(think_row, text="精简提示词（本地小模型推荐）", variable=self.compact_var,
                       bg=C["card"], fg=C["text_soft"], activebackground=C["card"],
                       selectcolor=C["card"], font=font(8), highlightthickness=0,
                       bd=0).pack(side="left", padx=(px(12), 0))
        self.vision_var = tk.BooleanVar(
            value=bool((profile or {}).get("supports_vision", False))
        )
        tk.Checkbutton(think_row, text="支持图片理解", variable=self.vision_var,
                       bg=C["card"], fg=C["text_soft"], activebackground=C["card"],
                       selectcolor=C["card"], font=font(8), highlightthickness=0,
                       bd=0).pack(side="left", padx=(px(12), 0))
        tk.Label(think_row, text="Qwen3 会消耗较多 tokens；内存不足时请降低 num_ctx",
                 bg=C["card"], fg=C["muted"], font=font(7)).pack(side="right")
        self.test_label = tk.Label(form, text="", bg=C["card"], fg=C["muted"],
                                   font=font(8), anchor="w", justify="left",
                                   wraplength=px(610))
        self.test_label.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(px(2), px(4)))
        self.error = tk.Label(form, text="", bg=C["card"], fg=C["red"], font=font(8),
                              anchor="w")
        self.error.grid(row=8, column=0, columnspan=2, sticky="ew")
        self.use_after = tk.BooleanVar(value=self.is_new)
        if self.is_new:
            tk.Checkbutton(form, text="保存后立即设为当前模型", variable=self.use_after,
                           bg=C["card"], fg=C["text_soft"], activebackground=C["card"],
                           selectcolor=C["card"], font=font(8),
                           highlightthickness=0, bd=0).grid(row=9, column=0, columnspan=2,
                                                            sticky="w", pady=(px(4), 0))
        test_btn = RoundedButton(self._footer_inner, text="测试连接", command=self._test,
                                 style="secondary", width=92, height=36,
                                 bg=C["card_soft"], font_size=8)
        test_btn.pack(side="left")
        self.add_footer("取消", self.close, "secondary", 90)
        self.add_footer("保存", self._save, "primary", 100)
        self._provider_changed(silent=True)

    def _param(self, parent, column: int, label: str, value: str) -> LabeledEntry:
        holder = tk.Frame(parent, bg=C["card_soft"])
        holder.grid(row=0, column=column, sticky="ew", padx=px(9), pady=px(9))
        entry = LabeledEntry(holder, label, value, width=9, bg=C["card_soft"])
        entry.pack(fill="x")
        return entry

    def _provider_value(self) -> str:
        return next((value for label, value in PROVIDERS if label == self.provider.get()), "ollama")

    def _provider_changed(self, silent: bool = False) -> None:
        provider = self._provider_value()
        if provider == "mock":
            self.base_url.set("")
            self.model.set("arona-demo")
            self.key.set("")
            if not silent:
                self.test_label.configure(text="离线演示模型不会调用网络，适合检查界面与上下文流程。",
                                          fg=C["muted"])
        elif provider == "ollama":
            if not self.base_url.get() or "api.deepseek" in self.base_url.get() or "api.openai" in self.base_url.get():
                self.base_url.set("http://127.0.0.1:11434")
            if silent and not (self.profile or {}).get("model"):
                self.model.set("qwen3:1.7b")

    def _apply_preset(self) -> None:
        preset = PRESETS.get(self.preset.get())
        if not preset:
            return
        provider_value = preset["provider"]
        self.provider.set(next((label for label, value in PROVIDERS
                               if value == provider_value), PROVIDERS[0][0]))
        self.base_url.set(preset["base_url"])
        self.model.set(preset["model"])
        if hasattr(self, "compact_var"):
            self.compact_var.set(provider_value == "ollama")
        if hasattr(self, "vision_var"):
            self.vision_var.set(bool(preset.get("supports_vision", False)))
        if self.name.get() in ("", "新模型"):
            self.name.set(self.preset.get())
        self._provider_changed()

    def _toggle_key(self) -> None:
        self.key.entry.configure(show="" if self.show_key.get() else "•")

    def _temp_profile(self) -> dict:
        provider = self._provider_value()
        key_text = self.key.get()
        if self.clear_key.get():
            key_enc = ""
        elif key_text:
            key_enc = protect(key_text)
        else:
            key_enc = (self.profile or {}).get("api_key_enc", "")
        try:
            temperature = max(0.0, min(2.0, float(self.temp.get() or 0.8)))
            max_tokens = max(32, min(8192, int(self.max_tokens.get() or 1200)))
            num_ctx = max(1024, min(131072, int(self.num_ctx.get() or 4096)))
            timeout = max(10, min(300, int(self.timeout.get() or 300)))
        except ValueError as exc:
            raise ValueError("温度、tokens、上下文、超时必须填写数字") from exc
        return {
            "id": (self.profile or {}).get("id") or "tmp",
            "name": self.name.get() or "未命名模型",
            "provider": provider,
            "base_url": self.base_url.get(),
            "model": self.model.get().strip(),
            "api_key_enc": key_enc,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "num_ctx": num_ctx,
            "think": False if self.think_var.get() else None,
            "compact_prompt": bool(self.compact_var.get()),
            "supports_vision": bool(self.vision_var.get()),
            "timeout": timeout,
        }

    def _test(self) -> None:
        try:
            profile = self._temp_profile()
        except Exception as exc:
            self.test_label.configure(text=str(exc), fg=C["red"])
            return
        if profile["provider"] != "mock" and not profile["model"]:
            self.test_label.configure(text="先填写模型名称。", fg=C["red"])
            return
        self.test_label.configure(text="正在连接…", fg=C["cyan_deep"])

        def worker():
            try:
                health = make_client(profile).health()
                text = health.message + (f"\n{health.detail}" if health.detail else "")
                self.after(0, lambda: self._test_done(health.ok, text, health.models))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._test_done(False, message, []))

        threading.Thread(target=worker, daemon=True).start()

    def _test_done(self, ok: bool, text: str, models: list[str]) -> None:
        if not self.winfo_exists():
            return
        self.test_label.configure(text=text, fg=C["green"] if ok else C["red"])
        if models:
            self.models = models
            self.model.configure(values=models)
            if not self.model.get() and models:
                self.model.set(models[0])

    def _fetch_models(self) -> None:
        try:
            profile = self._temp_profile()
        except Exception as exc:
            self.test_label.configure(text=str(exc), fg=C["red"])
            return
        self.fetch_btn.set_busy(True, "拉取中")
        self.test_label.configure(text="正在拉取模型列表…", fg=C["cyan_deep"])

        def worker():
            try:
                models = make_client(profile).list_models()
                self.after(0, lambda: self._models_done(models))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self._models_error(message))

        threading.Thread(target=worker, daemon=True).start()

    def _models_done(self, models: list[str]) -> None:
        if not self.winfo_exists():
            return
        self.fetch_btn.set_busy(False, "拉取模型列表")
        self.models = models
        self.model.configure(values=models)
        if models:
            current = self.model.get().strip()
            if not current:
                self.model.set(models[0])
                self.test_label.configure(
                    text=f"已获取 {len(models)} 个模型，可在下拉框中选择。",
                    fg=C["green"],
                )
            elif current not in models:
                # /models is advisory: DeepSeek keeps deepseek-chat working
                # while listing only newer aliases.  Never silently replace
                # the user's model; test the actual chat endpoint instead.
                self.test_label.configure(
                    text=(f"已获取 {len(models)} 个模型；当前 “{current}” 不在列表中，"
                          "但可以直接点击「测试连接」验证并保存。"),
                    fg=C["yellow_deep"],
                )
            else:
                self.test_label.configure(
                    text=f"已获取 {len(models)} 个模型，当前模型在列表中。",
                    fg=C["green"],
                )
        else:
            self.test_label.configure(text="连接成功，但服务没有返回模型列表。", fg=C["yellow_deep"])

    def _models_error(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self.fetch_btn.set_busy(False, "拉取模型列表")
        self.test_label.configure(text=message, fg=C["red"])

    def _save(self) -> None:
        self.error.configure(text="")
        if not self.name.get():
            self.error.configure(text="请填写配置名称。")
            return
        try:
            profile = self._temp_profile()
        except Exception as exc:
            self.error.configure(text=str(exc))
            return
        if profile["provider"] != "mock" and (not profile["model"] or not profile["base_url"]):
            self.error.configure(text="请填写 API 地址和模型名称。")
            return
        profile["id"] = (self.profile or {}).get("id") or "model_" + uuid.uuid4().hex[:8]
        try:
            self.page.config.upsert_profile(profile)
            if self.use_after.get():
                self.page.config.set_active_profile(profile["id"])
            self.page.service.reload_config()
            self.close()
            self.page.refresh()
            self.page.app.toast("模型配置已保存。", "success")
        except Exception as exc:
            self.error.configure(text=str(exc))


class ModelCard(Card):
    def __init__(self, parent, page: "ModelsPage", profile: dict, active: bool) -> None:
        super().__init__(parent, padding=15, accent=C["cyan"] if active else None)
        self.profile = profile
        self.page = page
        top = tk.Frame(self.body, bg=C["card"])
        top.pack(fill="x")
        badge = tk.Label(top, text=provider_label(profile.get("provider", "")),
                         bg=C["card_blue"], fg=C["cyan_deep"], font=display_font(6, "bold"),
                         padx=px(6), pady=px(2))
        badge.pack(side="left")
        if active:
            tk.Label(top, text="当前使用", bg=C["green_pale"], fg=C["green"],
                     font=font(7, "bold"), padx=px(6), pady=px(2)).pack(side="right")
        tk.Label(self.body, text=truncate(profile.get("name", "未命名"), 28),
                 bg=C["card"], fg=C["text"], font=font(11, "bold"), anchor="w").pack(
            fill="x", pady=(px(8), 0))
        tk.Label(self.body, text="MODEL · " + truncate(profile.get("model", ""), 36),
                 bg=C["card"], fg=C["cyan_deep"], font=display_font(7, "bold"),
                 anchor="w").pack(fill="x", pady=(px(3), px(2)))
        endpoint = profile.get("base_url") or "内置离线演示"
        tk.Label(self.body, text=truncate(endpoint, 46), bg=C["card"], fg=C["muted"],
                 font=font(7), anchor="w").pack(fill="x")
        key_state = "已加密保存 Key" if profile.get("api_key_enc") else "未设置 Key"
        if profile.get("provider") == "ollama":
            key_state = "本地服务 · 无 Key"
        elif profile.get("provider") == "mock":
            key_state = "无网络 · 仅界面演示"
        think_state = " · 思考关闭" if profile.get("think") is False else ""
        if profile.get("compact_prompt"):
            think_state += " · 精简提示"
        tk.Label(self.body,
                 text=f"{key_state}  ·  温度 {profile.get('temperature', 0.8)}"
                      f"  ·  {profile.get('max_tokens', 1200)} tokens"
                      f"  ·  ctx {profile.get('num_ctx', 4096)}{think_state}",
                 bg=C["card"], fg=C["text_soft"], font=font(7), anchor="w",
                 wraplength=px(420)).pack(fill="x", pady=(px(5), px(7)))
        actions = tk.Frame(self.body, bg=C["card"])
        actions.pack(fill="x")
        if not active:
            RoundedButton(actions, text="使用", command=lambda: page.activate(profile),
                          style="primary", width=58, height=28, bg=C["card"],
                          font_size=7).pack(side="left")
        RoundedButton(actions, text="测试", command=lambda: page.test(profile),
                      style="secondary", width=58, height=28, bg=C["card"],
                      font_size=7).pack(side="left", padx=(px(4), 0))
        RoundedButton(actions, text="编辑", command=lambda: page.edit(profile),
                      style="ghost", width=52, height=28, bg=C["card"],
                      font_size=7).pack(side="left")
        if not active:
            RoundedButton(actions, text="删除", command=lambda: page.delete(profile),
                          style="ghost", width=52, height=28, bg=C["card"],
                          font_size=7).pack(side="right")


class ModelsPage(BasePage):
    key = "models"
    title = "模型与 API"
    subtitle = "选择本地 Ollama，或接入老师信任的第三方模型。"

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app)
        self._build()
        self.refresh()

    def _build(self) -> None:
        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        wrap = tk.Frame(scroll.inner, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=(0, px(4)), pady=(0, px(18)))

        toolbar = tk.Frame(wrap, bg=C["bg"])
        toolbar.pack(fill="x", pady=(0, px(12)))
        tk.Label(toolbar, text="MODEL GATEWAY", bg=C["bg"], fg=C["cyan_deep"],
                 font=display_font(7, "bold")).pack(side="left", pady=(px(8), 0))
        RoundedButton(toolbar, text="＋ 添加模型", command=self.add, style="primary",
                      width=112, height=34, bg=C["bg"], font_size=9).pack(side="right")

        self.active_card = Card(wrap, padding=18, bg=C["sidebar"], border=False)
        self.active_card.pack(fill="x", pady=(0, px(12)))
        self.active_inner = tk.Frame(self.active_card.body, bg=C["sidebar"])
        self.active_inner.pack(fill="x")

        note = tk.Frame(wrap, bg=C["card_blue"], height=px(56))
        note.pack(fill="x", pady=(0, px(12)))
        note.pack_propagate(False)
        tk.Label(note, text="◇", bg=C["card_blue"], fg=C["cyan_deep"],
                 font=font(13)).pack(side="left", padx=(px(13), px(5)))
        tk.Label(note, text="默认已绑定本机 Ollama 的 qwen3:1.7b，不会自动下载或替换模型。\n"
                            "API Key 使用 Windows DPAPI 绑定当前用户加密，界面和日志不会显示完整 Key。",
                 bg=C["card_blue"], fg=C["text_soft"], font=font(8), justify="left").pack(side="left")

        self.grid_frame = tk.Frame(wrap, bg=C["bg"])
        self.grid_frame.pack(fill="both", expand=True)
        self.empty = tk.Frame(wrap, bg=C["bg"])

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for child in self.active_inner.winfo_children():
            child.destroy()
        profile = self.config.active_profile()
        left = tk.Frame(self.active_inner, bg=C["sidebar"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="CURRENT MODEL / 当前使用", bg=C["sidebar"], fg=C["cyan"],
                 font=display_font(7, "bold"), anchor="w").pack(fill="x")
        tk.Label(left, text=profile.get("name", "未命名模型"), bg=C["sidebar"], fg="white",
                 font=font(15, "bold"), anchor="w").pack(fill="x", pady=(px(3), 0))
        tk.Label(left, text=f"{profile.get('model', '')}  ·  {profile.get('base_url') or '内置离线演示'}",
                 bg=C["sidebar"], fg=C["nav_text"], font=font(8), anchor="w").pack(fill="x")
        tk.Label(left, text="温度 " + str(profile.get("temperature", 0.8)) +
                            "   ·   tokens " + str(profile.get("max_tokens", 1200)) +
                            "   ·   ctx " + str(profile.get("num_ctx", 4096)) +
                            "   ·   超时 " + str(profile.get("timeout", 300)) + "s",
                 bg=C["sidebar"], fg=C["nav_text_dim"], font=display_font(7, "normal"),
                 anchor="w").pack(fill="x", pady=(px(6), 0))
        right = tk.Frame(self.active_inner, bg=C["sidebar"])
        right.pack(side="right")
        RoundedButton(right, text="测试连接", command=lambda: self.test(profile),
                      style="secondary", width=92, height=32, bg=C["sidebar"],
                      font_size=8).pack(side="right", padx=(px(6), 0))
        RoundedButton(right, text="编辑", command=lambda: self.edit(profile),
                      style="on_dark", width=58, height=32, bg=C["sidebar"],
                      font_size=8).pack(side="right")
        for child in self.grid_frame.winfo_children():
            child.destroy()
        profiles = self.config.data.get("model_profiles", [])
        active_id = self.config.data.get("active_profile_id")
        for index, item in enumerate(profiles):
            card = ModelCard(self.grid_frame, self, item, item.get("id") == active_id)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=px(5), pady=px(5))
        self.grid_frame.grid_columnconfigure(0, weight=1, uniform="models")
        self.grid_frame.grid_columnconfigure(1, weight=1, uniform="models")

    def add(self) -> None:
        ModelDialog(self.winfo_toplevel(), self).show()
        self.refresh()

    def edit(self, profile: dict) -> None:
        ModelDialog(self.winfo_toplevel(), self, profile).show()
        self.refresh()

    def activate(self, profile: dict) -> None:
        self.config.set_active_profile(profile["id"])
        self.service.reload_config()
        self.service.emit("state", {"patch": {"model_ok": None, "model_status": "尚未检测"}})
        self.service.refresh_balance(force=True)
        self.refresh()
        self.app.refresh_header()
        self.app.toast(f"当前模型已切换为「{profile.get('name')}」。", "success")

    def test(self, profile: dict) -> None:
        self.app.toast("正在测试模型连接…", "info")

        def worker():
            try:
                result = self.service.test_model(profile)
                self.after(0, lambda: self.app.toast(
                    ("连接成功：" if result["ok"] else "连接失败：") + result["message"],
                    "success" if result["ok"] else "error"))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda message=message: self.app.toast("测试失败：" + message, "error"))

        threading.Thread(target=worker, daemon=True).start()

    def delete(self, profile: dict) -> None:
        if len(self.config.data.get("model_profiles", [])) <= 1:
            self.app.toast("至少需要保留一个模型配置。", "warn")
            return
        if confirm(self.winfo_toplevel(), "删除模型配置",
                   f"确定删除「{profile.get('name', '')}」吗？加密保存的 Key 也会一并移除。",
                   ok_text="删除", danger=True, icon=self.icon):
            self.config.delete_profile(profile["id"])
            self.service.reload_config()
            self.refresh()
            self.app.refresh_header()
            self.app.toast("模型配置已删除。", "info")
