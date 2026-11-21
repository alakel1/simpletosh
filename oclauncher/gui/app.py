"""Graphical installer (GTK4 + libadwaita), modelled on a classic OS installer.

Pages: Welcome -> Hardware -> macOS version -> Configuration -> Destination
       -> Confirm -> Installing -> Done

Works with GTK 4.6 / libadwaita 1.1 (Ubuntu 22.04) and newer (Debian 12+).
All long work runs in a thread; UI updates go through GLib.idle_add.
"""

import os
import shutil
import subprocess
import sys
import threading
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, __version__, builder, disks, fetch, hw, kextdb, planner, recovery  # noqa: E402
from ..data.macos import vkey  # noqa: E402
from ..i18n import _  # noqa: E402
from . import network  # noqa: E402

LIVE = os.path.exists("/etc/oclauncher-live")      # created inside the ISO
WORKDIR = os.environ.get("OCL_WORKDIR") or os.path.join(fetch.CACHE, "work")

STATUS_ICON = {"native": "emblem-ok-symbolic", "kext": "emblem-ok-symbolic",
               "experimental": "dialog-warning-symbolic", "unsupported": "dialog-error-symbolic"}
STATUS_CLASS = {"native": "success", "kext": "success", "experimental": "warning", "unsupported": "error"}
CATEGORY_ORDER = ["CPU", "Graphics", "Audio", "Ethernet", "Wi-Fi", "Bluetooth", "Webcam", "USB", "Storage",
                  "Input", "Card reader", "Thunderbolt", "Fingerprint"]

CSS = b"""
.success { color: @success_color; }
.warning { color: @warning_color; }
.error { color: @error_color; }
.badge { background: alpha(@accent_bg_color, 0.15); color: @accent_color; border-radius: 99px;
         padding: 2px 10px; font-weight: bold; font-size: 0.85em; }
.step-label { font-size: 0.9em; }
.mono { font-family: monospace; }
.error-box { background: alpha(@error_color, 0.12); color: @error_color; border-radius: 8px; padding: 12px;
             font-weight: bold; }
.bottom-bar { padding: 12px 18px; border-top: 1px solid alpha(currentColor, 0.12); }
"""


def esc(text):
    return GLib.markup_escape_text(str(text if text is not None else ""))


def row(title, subtitle=None, icon=None, icon_class=None):
    r = Adw.ActionRow()
    r.set_title(esc(title))
    if subtitle:
        r.set_subtitle(esc(subtitle))
    if icon:
        img = Gtk.Image.new_from_icon_name(icon)
        if icon_class:
            img.add_css_class(icon_class)
        r.add_prefix(img)
    return r


def page_box(title, subtitle=None):
    """Scrollable, centered page with a big title. Returns (outer, inner box)."""
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                    margin_start=12, margin_end=12)
    t = Gtk.Label(label=title, xalign=0, wrap=True)
    t.add_css_class("title-1")
    inner.append(t)
    if subtitle:
        s = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        s.add_css_class("dim-label")
        inner.append(s)
    clamp = Adw.Clamp(maximum_size=760, child=inner)
    viewport = Gtk.Viewport(scroll_to_focus=False, child=clamp)
    scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=viewport)
    return scroll, inner


class Installer(Adw.ApplicationWindow):
    STEPS = ["welcome", "hardware", "versions", "expect", "config", "target", "confirm", "install", "done"]
    STEP_NAMES = {"welcome": "Welcome", "hardware": "Hardware", "versions": "macOS", "expect": "What to expect",
                  "config": "Setup", "target": "Destination", "confirm": "Confirm", "install": "Install",
                  "done": "Done"}

    def __init__(self, app, report_path=None):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(980, 720)
        self.report_path = report_path
        self.report = None
        self.analysis = None
        self.version = None
        self.plan = None
        self.layouts = []
        self.layout = None
        self.target_mode = "usb"
        self.target_disk = None
        self.folder = os.environ.get("OCL_FOLDER") or os.path.expanduser("~/OpenCore-EFI")
        self.extra_args = ""
        self.tahoe_audio = False        # opt-in SimpleHDA on macOS Tahoe
        self.cancelled = False
        self.busy = False

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        self.step_label = Gtk.Label()
        self.step_label.add_css_class("step-label")
        header.set_title_widget(self.step_label)
        about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text=_("About"))
        about.connect("clicked", self.on_about)
        header.pack_end(about)
        root.append(header)

        self.toasts = Adw.ToastOverlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.toasts.set_child(self.stack)
        root.append(self.toasts)

        bar = Gtk.Box(spacing=12)
        bar.add_css_class("bottom-bar")
        self.back_btn = Gtk.Button(label=_("Back"))
        self.back_btn.connect("clicked", lambda *_a: self.go(-1))
        self.next_btn = Gtk.Button(label=_("Next"))
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", lambda *_a: self.go(+1))
        self.dots = Gtk.Label(hexpand=True)
        self.dots.add_css_class("dim-label")
        bar.append(self.back_btn)
        bar.append(self.dots)
        bar.append(self.next_btn)
        root.append(bar)
        self.set_content(root)

        self.pages = {}
        self.show_step("welcome")
        debug_step = os.environ.get("OCL_STEP")     # developer aid: jump straight to a page
        if debug_step in self.STEPS:
            self.accept.set_active(True)
            self.report = hw.load(report_path) if report_path else hw.detect()
            self.analysis = planner.analyze(self.report)
            if self.analysis.recommended:
                self.version = os.environ.get("OCL_MACOS") or self.analysis.recommended.macos.version
            if debug_step in ("target", "confirm", "install", "done"):
                self.plan = planner.make_plan(self.analysis, self.version, 1, tahoe_audio=self.tahoe_audio)
                if os.geteuid() != 0:
                    self.target_mode = "folder"
            self.show_step(debug_step)

    # ------------------------------------------------------------ navigation

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=esc(text)))

    def show_step(self, step):
        self.step = step
        builder_fn = getattr(self, f"page_{step}")
        old = self.stack.get_child_by_name(step)
        if old:
            self.stack.remove(old)
        widget = builder_fn()
        self.stack.add_named(widget, step)
        self.stack.set_visible_child_name(step)
        if isinstance(widget, Gtk.ScrolledWindow):
            adj = widget.get_vadjustment()
            GLib.timeout_add(60, lambda: adj.set_value(0) and False)
        i = self.STEPS.index(step)
        self.step_label.set_label(f"{APP_NAME} — {_(self.STEP_NAMES[step])}")
        self.dots.set_label("  ".join("●" if j == i else "○" for j in range(len(self.STEPS))))
        self.back_btn.set_visible(step not in ("welcome", "install", "done"))
        self.next_btn.set_visible(step not in ("install",))
        self.next_btn.set_label({"confirm": _("Install"), "done": _("Finish")}.get(step, _("Next")))
        self.next_btn.grab_focus()      # keeps scrolled pages at the top
        if step == "confirm":
            self.next_btn.remove_css_class("suggested-action")
            self.next_btn.add_css_class("destructive-action" if self.target_mode != "folder" else "suggested-action")
        else:
            self.next_btn.remove_css_class("destructive-action")
            self.next_btn.add_css_class("suggested-action")
        self.update_next()

    def update_next(self, *_a):
        ok = True
        s = self.step
        if s == "welcome":
            ok = self.accept.get_active()
        elif s == "hardware":
            ok = self.analysis is not None
        elif s == "versions":
            ok = self.version is not None
        elif s == "config":
            ok = self.plan is not None
        elif s == "target":
            ok = self.target_mode == "folder" or self.target_disk is not None
        elif s == "confirm":
            ok = self.target_mode == "folder" or self.confirm_check.get_active()
        self.next_btn.set_sensitive(ok and not self.busy)

    def go(self, delta):
        if self.step == "done" and delta > 0:
            self.close()
            return
        i = self.STEPS.index(self.step) + delta
        if 0 <= i < len(self.STEPS):
            self.show_step(self.STEPS[i])

    def on_about(self, *_a):
        about = Adw.AboutWindow(transient_for=self, application_name=APP_NAME, version=__version__,
                                comments=_("Educational project: builds an OpenCore configuration for your PC "
                                           "and prepares the macOS installer."),
                                license_type=Gtk.License.MIT_X11) if hasattr(Adw, "AboutWindow") else None
        if about:
            about.present()
        else:
            dlg = Gtk.AboutDialog(transient_for=self, program_name=APP_NAME, version=__version__,
                                  comments=_("Educational project: builds an OpenCore configuration for your PC "
                                             "and prepares the macOS installer."))
            dlg.present()

    # ------------------------------------------------------------ 1. welcome

    def page_welcome(self):
        status = Adw.StatusPage(icon_name="computer-symbolic", title=esc(_("Install macOS on this PC")),
                                description=esc(_("This assistant analyses your hardware, tells you which macOS "
                                                  "versions can run on it, builds a matching OpenCore bootloader "
                                                  "and prepares the macOS installer.")))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        group = Adw.PreferencesGroup(title=esc(_("Before you start")))
        for icon, title, sub in [
            ("network-wired-symbolic", _("Internet connection"),
             _("OpenCore, drivers and macOS are downloaded during the process. Ethernet is best.")),
            ("drive-removable-media-symbolic", _("A USB drive or a spare disk"),
             _("At least 16 GB. Everything on it will be erased.")),
            ("dialog-information-symbolic", _("Educational and personal use"),
             _("Apple's licence allows macOS only on Apple hardware. Results are not guaranteed: "
               "keep backups of your data.")),
        ]:
            group.add(row(title, sub, icon))
        box.append(group)

        net = Adw.PreferencesGroup()
        self.net_row = row(_("Internet"), _("Checking…"), "network-transmit-receive-symbolic")
        self.wifi_btn = Gtk.Button(label=_("Wi-Fi…"), valign=Gtk.Align.CENTER, visible=False)
        self.wifi_btn.connect("clicked", lambda *_a: network.WifiDialog(self, self.check_network).present())
        self.net_row.add_suffix(self.wifi_btn)
        net.add(self.net_row)
        box.append(net)
        self.check_network()

        self.accept = Gtk.CheckButton(label=_("I understand, let's start"))
        self.accept.connect("toggled", self.update_next)
        box.append(self.accept)
        if not LIVE and os.geteuid() != 0:
            note = Gtk.Label(label=_("Running without root: you can explore and save to a folder, but "
                                     "writing to disks is disabled."), wrap=True, xalign=0)
            note.add_css_class("dim-label")
            box.append(note)
        status.set_child(Adw.Clamp(maximum_size=600, child=box))
        return status

    def check_network(self):
        self.net_row.set_subtitle(esc(_("Checking…")))

        def work():
            ok = network.online()
            GLib.idle_add(self._network_checked, ok, network.has_wifi_tools())
        threading.Thread(target=work, daemon=True).start()

    def _network_checked(self, ok, wifi):
        self.online = ok
        self.net_row.set_subtitle(esc(_("Connected") if ok else
                                      _("Not connected: plug in an Ethernet cable or use Wi-Fi")))
        self.wifi_btn.set_visible(wifi)
        self.wifi_btn.set_label(_("Wi-Fi…") if not ok else _("Change Wi-Fi…"))
        return False

    # ------------------------------------------------------------ 2. hardware

    def page_hardware(self):
        if self.analysis is None:
            spinner = Gtk.Spinner(spinning=True, width_request=48, height_request=48)
            status = Adw.StatusPage(title=esc(_("Analysing your hardware…")),
                                    description=esc(_("Reading the CPU, graphics, audio, network and "
                                                      "firmware details.")))
            status.set_child(spinner)
            self.busy = True
            threading.Thread(target=self._detect, daemon=True).start()
            return status

        outer, box = page_box(_("Your hardware"),
                              _("Here is what was found and how well each part works with macOS."))
        a = self.analysis
        m = a.report.get("machine", {})
        summary = Adw.PreferencesGroup()
        kind = _("Laptop") if a.laptop else _("Desktop")
        if a.virtual:
            kind = _("Virtual machine") + f" ({a.virtual})"
        summary.add(row(f"{m.get('vendor', '')} {m.get('product', '')}".strip() or _("Unknown computer"),
                        f"{kind} · {m.get('board_name', '')} · {m.get('memory_gb', '?')} GB RAM",
                        "computer-symbolic"))
        box.append(summary)

        for cat in CATEGORY_ORDER:
            comps = a.by_category(cat)
            if not comps:
                continue
            g = Adw.PreferencesGroup(title=esc(_(cat)))
            for c in comps:
                s = c.support
                rng = ""
                if s.min_macos and s.usable:
                    rng = f" · macOS {s.min_macos} – {s.max_macos}"
                sub = (c.name if c.name != s.summary else "") + rng
                if s.notes or s.source:
                    er = Adw.ExpanderRow()
                    er.set_title(esc(s.summary))
                    er.set_subtitle(esc(sub.strip(" ·")))
                    img = Gtk.Image.new_from_icon_name(STATUS_ICON[s.status] if s.status in STATUS_ICON else "dialog-question-symbolic")
                    img.add_css_class(STATUS_CLASS.get(s.status, ""))
                    er.add_prefix(img)
                    for n in s.notes:
                        er.add_row(row(n))
                    if s.source:
                        er.add_row(row(_("Source") + ": " + s.source, icon="text-x-generic-symbolic"))
                    g.add(er)
                else:
                    g.add(row(s.summary, sub.strip(" ·") or None, STATUS_ICON.get(s.status),
                              STATUS_CLASS.get(s.status)))
            box.append(g)

        save = Gtk.Button(label=_("Save hardware report…"), halign=Gtk.Align.START)
        save.connect("clicked", self.on_save_report)
        box.append(save)
        return outer

    def _detect(self):
        try:
            report = hw.load(self.report_path) if self.report_path else hw.detect()
            kextdb.refresh()        # newest driver ID lists if online; shipped snapshot otherwise
            analysis = planner.analyze(report)
            err = None
        except Exception as e:     # noqa: BLE001 - show anything to the user
            report = analysis = None
            err = f"{e}\n{traceback.format_exc()}"
        GLib.idle_add(self._detect_done, report, analysis, err)

    def _detect_done(self, report, analysis, err):
        self.busy = False
        if err:
            self.toast(_("Hardware detection failed"))
            print(err, file=sys.stderr)
        self.report, self.analysis = report, analysis
        if self.step == "hardware":
            self.show_step("hardware")
        return False

    def on_save_report(self, *_a):
        path = os.path.join(os.path.expanduser("~"), "oclauncher-hardware.json")
        hw.save(self.report, path)
        self.toast(_("Saved to") + f" {path}")

    # ------------------------------------------------------------ 3. versions

    def page_versions(self):
        a = self.analysis
        if not a.installable:
            status = Adw.StatusPage(icon_name="dialog-error-symbolic",
                                    title=esc(_("No macOS version can run on this computer")),
                                    description=esc(_("These parts prevent it:")))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            g = Adw.PreferencesGroup()
            for b in sorted({b for v in a.verdicts for b in v.blockers}):
                g.add(row(b, icon="dialog-error-symbolic", icon_class="error"))
            box.append(g)
            hint = self._upgrade_hint()
            if hint:
                lab = Gtk.Label(label=hint, wrap=True, xalign=0)
                lab.add_css_class("dim-label")
                box.append(lab)
            status.set_child(Adw.Clamp(maximum_size=640, child=box))
            return status

        outer, box = page_box(_("Choose a macOS version"),
                              _("Only versions that can work with your hardware can be selected."))
        hint = self._upgrade_hint()
        if hint:
            hg = Adw.PreferencesGroup()
            hg.add(row(hint, icon="dialog-information-symbolic"))
            box.append(hg)
        g = Adw.PreferencesGroup()
        group_btn = None
        rec = a.recommended
        if self.version is None and rec:
            self.version = rec.macos.version
        for v in reversed(a.verdicts):
            er = Adw.ExpanderRow()
            er.set_title(esc(v.macos.label))
            if v.ok:
                sub = _("Recommended") if v.recommended else (_("Works") if not v.caveats else
                                                               _("Works, with limitations"))
                radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                if group_btn:
                    radio.set_group(group_btn)
                else:
                    group_btn = radio
                radio.set_active(v.macos.version == self.version)
                radio.connect("toggled", self.on_version_toggled, v.macos.version)
                er.add_prefix(radio)
                if v.recommended:
                    badge = Gtk.Label(label=_("Recommended"), valign=Gtk.Align.CENTER)
                    badge.add_css_class("badge")
                    er.add_action(badge)
                for c in v.caveats:
                    er.add_row(row(c, icon="dialog-warning-symbolic", icon_class="warning"))
                if not v.caveats:
                    er.add_row(row(_("Everything detected should work"), icon="emblem-ok-symbolic",
                                   icon_class="success"))
            else:
                sub = v.blockers[0] if v.blockers else _("Not supported")
                img = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
                img.add_css_class("error")
                er.add_prefix(img)
                er.set_sensitive(True)
                for b in v.blockers:
                    er.add_row(row(b, icon="dialog-error-symbolic", icon_class="error"))
            er.set_subtitle(esc(sub))
            g.add(er)
        box.append(g)
        return outer

    def _upgrade_hint(self):
        """If graphics is the only limit, say how far the CPU could go."""
        a = self.analysis
        cpu = a.by_category("CPU")[0].support
        if not cpu.usable or not cpu.max_macos:
            return None
        best = max((v for v in a.installable), default=None, key=lambda v: v.macos.key)
        if best and vkey(best.macos.version) >= vkey(cpu.max_macos):
            return None
        if not any("graphics" in b.lower() for v in a.verdicts for b in v.blockers):
            return None
        from ..data import macos as M
        name = M.get(cpu.max_macos).label if cpu.max_macos in M.BY_VERSION else cpu.max_macos
        if a.laptop:
            return _("Your processor could run up to {v}, but the built-in graphics limits which versions "
                     "work.").format(v=name)
        return _("Your processor could run up to {v}. The graphics card is the limit: a supported AMD Radeon "
                 "(e.g. RX 580 or RX 6600) would unlock newer versions.").format(v=name)

    def on_version_toggled(self, btn, version):
        if btn.get_active():
            self.version = version
            self.plan = None
            self.update_next()

    # ------------------------------------------------------------ 3b. what to expect

    def page_expect(self):
        from ..data import macos as M
        mac = M.get(self.version)
        self.plan = self._make_plan()
        exp = planner.expectations(self.analysis, self.version, self.plan)
        outer, box = page_box(_("What to expect on {v}").format(v=mac.label),
                              _("Before anything is downloaded or written: what will work right after "
                                "installing, what may need some tweaking, and what will not work."))
        for bucket, title, icon, css in (
                (exp.works, _("Will work"), "emblem-ok-symbolic", "success"),
                (exp.uncertain, _("Uncertain — may need tweaks"), "dialog-warning-symbolic", "warning"),
                (exp.broken, _("Will not work"), "dialog-error-symbolic", "error")):
            g = Adw.PreferencesGroup(title=esc(f"{title} ({len(bucket)})"))
            if not bucket:
                g.add(row(_("Nothing here"), icon=icon, icon_class=css))
            for t, d in bucket:
                if d:
                    er = Adw.ExpanderRow()
                    er.set_title(esc(t))
                    img = Gtk.Image.new_from_icon_name(icon)
                    img.add_css_class(css)
                    er.add_prefix(img)
                    er.add_row(row(d))
                    g.add(er)
                else:
                    g.add(row(t, icon=icon, icon_class=css))
            box.append(g)
        note = Gtk.Label(label=_("Only drivers that work out of the box are installed: nothing is patched inside "
                                 "macOS after installation."), wrap=True, xalign=0)
        note.add_css_class("dim-label")
        box.append(note)
        return outer

    # ------------------------------------------------------------ 4. config

    def _make_plan(self):
        return planner.make_plan(self.analysis, self.version, self.layout, tahoe_audio=self.tahoe_audio)

    def _tahoe_audio_row(self):
        """Opt-in switch for SimpleHDA, shown only for macOS Tahoe on machines with a supported analog codec."""
        if self.version != "26" or not any(c.support.usable and c.support.info.get("analog")
                                           for c in self.analysis.by_category("Audio")):
            return None
        r = Adw.ActionRow(title=esc(_("Experimental onboard audio (SimpleHDA)")),
                          subtitle=esc(_("macOS Tahoe has no driver for onboard audio. SimpleHDA, a young "
                                         "third-party kext, brings it back without patching macOS. "
                                         "Leave it off if you prefer the safest install.")))
        sw = Gtk.Switch(active=self.tahoe_audio, valign=Gtk.Align.CENTER)
        sw.connect("notify::active", self.on_tahoe_audio)
        r.add_suffix(sw)
        r.set_activatable_widget(sw)
        return r

    def on_tahoe_audio(self, sw, _pspec):
        if sw.get_active() == self.tahoe_audio:
            return
        self.tahoe_audio = sw.get_active()
        self.plan = None
        GLib.idle_add(lambda: (self.show_step("config"), False)[1])

    def page_config(self):
        if self.plan is None or self.plan.macos.version != self.version:
            self.plan = self._make_plan()
            if self.plan.audio_codec and not self.layouts:
                threading.Thread(target=self._load_layouts, args=(self.plan.audio_codec,), daemon=True).start()
        p = self.plan
        outer, box = page_box(_("What will be set up"),
                              _("Automatically chosen for your hardware. Expand an item to see why."))

        g = Adw.PreferencesGroup(title=esc(_("Identity")))
        g.add(row(_("Mac model (SMBIOS)") + f": {p.smbios}", p.smbios_reason, "computer-symbolic"))
        box.append(g)

        ta = self._tahoe_audio_row()
        if ta or p.audio_codec:
            ga = Adw.PreferencesGroup(title=esc(_("Audio")))
            if ta:
                ga.add(ta)
            box.append(ga)
        if p.audio_codec:
            self.layout_row = Adw.ComboRow(title=esc(_("Audio layout-id")),
                                           subtitle=esc(_("If there is no sound after installing, try "
                                                          "another value.") + f" ({p.audio_codec})"))
            self._fill_layouts()
            self.layout_row.connect("notify::selected", self.on_layout_changed)
            ga.add(self.layout_row)

        def listing(title, items, icon):
            gg = Adw.PreferencesGroup(title=esc(title))
            for it in items:
                gg.add(row(it.name, it.reason, icon))
            box.append(gg)

        listing(_("Drivers (kexts)"), p.kexts, "application-x-addon-symbolic")
        if p.ssdts:
            listing(_("ACPI tables (SSDT)"), p.ssdts, "text-x-generic-symbolic")
        listing(_("UEFI drivers"), p.drivers, "application-x-firmware-symbolic")

        if p.explanations:
            ge = Adw.PreferencesGroup(title=esc(_("Other settings")))
            for topic, text in p.explanations:
                ge.add(row(text, topic, "dialog-information-symbolic"))
            box.append(ge)
        if p.warnings:
            gw = Adw.PreferencesGroup(title=esc(_("Good to know")))
            for w in dict.fromkeys(p.warnings):
                gw.add(row(w, icon="dialog-warning-symbolic", icon_class="warning"))
            box.append(gw)

        adv = Adw.PreferencesGroup(title=esc(_("Advanced")))
        er = Adw.ExpanderRow()
        er.set_title(esc(_("Boot arguments")))
        er.set_subtitle(esc(" ".join(p.boot_args)))
        entry = Gtk.Entry(text=self.extra_args, placeholder_text=_("Extra boot-args, e.g. npci=0x2000"),
                          hexpand=True, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        entry.connect("changed", lambda e: setattr(self, "extra_args", e.get_text().strip()))
        holder = Gtk.ListBoxRow(child=entry, activatable=False)
        er.add_row(holder)
        adv.add(er)
        box.append(adv)
        return outer

    def _load_layouts(self, codec):
        layouts = fetch.applealc_layouts(codec)
        GLib.idle_add(self._layouts_loaded, codec, layouts)

    def _layouts_loaded(self, codec, layouts):
        self.layouts = layouts or [1]
        if self.layout is None:
            self.layout = fetch.default_layout(codec, layouts)
            self.plan = self._make_plan()
        if self.step == "config":
            self.show_step("config")
        return False

    def _fill_layouts(self):
        values = self.layouts or ([self.layout] if self.layout is not None else [1])
        model = Gtk.StringList()
        for v in values:
            model.append(str(v))
        self.layout_row.set_model(model)
        cur = self.layout if self.layout in values else values[0]
        self.layout_row.set_selected(values.index(cur))

    def on_layout_changed(self, combo, _pspec):
        item = combo.get_selected_item()
        if item is None:
            return
        val = int(item.get_string())
        if val != self.layout:
            self.layout = val
            self.plan = self._make_plan()

    # ------------------------------------------------------------ 5. target

    def page_target(self):
        outer, box = page_box(_("Where should it be installed?"))
        root = os.geteuid() == 0
        modes = Adw.PreferencesGroup()
        first = None
        options = [
            ("disk", "drive-harddisk-symbolic", _("Erase a disk and install macOS"),
             _("Like a normal installer: the disk gets OpenCore and the macOS installer, then the PC "
               "restarts into it. Everything on that disk is erased.")),
            ("usb", "drive-removable-media-symbolic", _("Create a bootable USB drive"),
             _("The classic way: a USB drive with OpenCore and the macOS installer. Your disks are not touched.")),
            ("folder", "folder-symbolic", _("Only save the files to a folder"),
             _("Advanced / testing: get the EFI folder and the recovery files without writing any disk.")),
        ]
        for mode, icon, title, sub in options:
            r = row(title, sub, icon)
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first:
                radio.set_group(first)
            else:
                first = radio
            radio.set_active(self.target_mode == mode)
            radio.connect("toggled", self.on_mode, mode)
            r.add_suffix(radio)
            r.set_activatable_widget(radio)
            if mode != "folder" and not root:
                r.set_sensitive(False)
                r.set_subtitle(esc(sub + " " + _("(needs root)")))
            modes.add(r)
        if not root and self.target_mode != "folder":
            self.target_mode = "folder"
            GLib.idle_add(lambda: self.show_step("target") and False)
        box.append(modes)

        if self.target_mode == "folder":
            g = Adw.PreferencesGroup(title=esc(_("Folder")))
            entry = Gtk.Entry(text=self.folder, hexpand=True, margin_top=8, margin_bottom=8,
                              margin_start=8, margin_end=8)
            entry.connect("changed", lambda e: setattr(self, "folder", e.get_text().strip()))
            g.add(Gtk.ListBoxRow(child=entry, activatable=False))
            box.append(g)
        else:
            g = Adw.PreferencesGroup(title=esc(_("Choose the disk")))
            refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                 tooltip_text=_("Refresh"))
            refresh.add_css_class("flat")
            refresh.connect("clicked", lambda *_a: self.show_step("target"))
            g.set_header_suffix(refresh)
            try:
                all_disks = disks.list_disks()
            except Exception as e:      # noqa: BLE001
                all_disks = []
                self.toast(str(e))
            shown = [d for d in all_disks if not d["live"] and (self.target_mode == "disk" or d["removable"])]
            if not shown:
                g.add(row(_("No suitable disk found"),
                          _("Plug in a USB drive and press refresh.") if self.target_mode == "usb" else None,
                          "dialog-warning-symbolic", "warning"))
                self.target_disk = None
            first = None
            if self.target_disk not in [d["path"] for d in shown]:
                self.target_disk = None
            for d in shown:
                icon = "drive-removable-media-symbolic" if d["removable"] else "drive-harddisk-symbolic"
                r = row(f"{d['model']} — {d['size_h']}", f"{d['path']} · {d['transport'] or 'internal'}", icon)
                radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
                if first:
                    radio.set_group(first)
                else:
                    first = radio
                radio.set_active(self.target_disk == d["path"])
                radio.connect("toggled", self.on_disk, d["path"])
                r.add_suffix(radio)
                r.set_activatable_widget(radio)
                g.add(r)
            box.append(g)
            if self.target_mode == "disk":
                note = Gtk.Label(label=_("Dual boot is not supported yet: the whole disk will be used for macOS."),
                                 wrap=True, xalign=0)
                note.add_css_class("dim-label")
                box.append(note)
        return outer

    def on_mode(self, btn, mode):
        if btn.get_active() and mode != self.target_mode:
            self.target_mode = mode
            self.target_disk = None
            GLib.idle_add(lambda: self.show_step("target") and False)

    def on_disk(self, btn, path):
        if btn.get_active():
            self.target_disk = path
            self.update_next()

    # ------------------------------------------------------------ 6. confirm

    def page_confirm(self):
        outer, box = page_box(_("Ready to install"))
        p = self.plan
        g = Adw.PreferencesGroup()
        g.add(row(p.macos.label, _("Mac model") + f": {p.smbios}", "computer-symbolic"))
        dest = {"disk": _("Erase and install on") + f" {self.target_disk}",
                "usb": _("Create a USB installer on") + f" {self.target_disk}",
                "folder": _("Save files to") + f" {self.folder}"}[self.target_mode]
        g.add(row(dest, None, "drive-harddisk-symbolic"))
        g.add(row(_("Downloads"), _("OpenCore, {n} kexts and the macOS recovery (about 1 GB)").format(n=len(p.kexts)),
                  "folder-download-symbolic"))
        box.append(g)
        self.confirm_check = Gtk.CheckButton(
            label=_("I understand that ALL data on {d} will be permanently erased").format(d=self.target_disk))
        self.confirm_check.connect("toggled", self.update_next)
        if self.target_mode != "folder":
            box.append(self.confirm_check)
        return outer

    # ------------------------------------------------------------ 7. install

    def page_install(self):
        outer, box = page_box(_("Installing"), _("This can take a while, mostly depending on your connection."))
        self.task_label = Gtk.Label(label=_("Starting…"), xalign=0, wrap=True)
        self.task_label.add_css_class("title-4")
        self.progress = Gtk.ProgressBar(show_text=False)
        self.overall = Gtk.Label(xalign=0)
        self.overall.add_css_class("dim-label")
        box.append(self.task_label)
        box.append(self.progress)
        box.append(self.overall)
        # error banner: hidden until something fails
        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
        self.error_label.add_css_class("error-box")
        box.append(self.error_label)
        self.details = Gtk.Expander(label=_("Details"))
        self.log_view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_scroll = Gtk.ScrolledWindow(min_content_height=220, child=self.log_view)
        self.details.set_child(self.log_scroll)
        box.append(self.details)
        self.cancel_btn = Gtk.Button(label=_("Cancel"), halign=Gtk.Align.START)
        self.cancel_btn.connect("clicked", self.on_cancel)
        box.append(self.cancel_btn)
        self.cancelled = False
        self.result = None
        self.error = None
        self.error_detail = None
        self.busy = True
        self.log_path = os.path.join(WORKDIR, "install.log")
        os.makedirs(WORKDIR, exist_ok=True)
        threading.Thread(target=self._install, daemon=True).start()
        return outer

    def on_cancel(self, *_a):
        self.cancelled = True
        self.cancel_btn.set_sensitive(False)
        self.log(_("Cancelling…"))

    def log(self, text):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text + "\n")
        adj = self.log_scroll.get_vadjustment()        # always show the newest line (after layout)
        GLib.timeout_add(50, lambda: adj.set_value(adj.get_upper() - adj.get_page_size()) and False)
        try:
            with open(self.log_path, "a") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def _ui(self, fn, *args):
        GLib.idle_add(lambda: fn(*args) and False)

    def _set_progress(self, stage, n_stages, msg, frac):
        self.task_label.set_label(msg)
        if frac is None:
            self.progress.pulse()
        else:
            self.progress.set_fraction(max(0.0, min(1.0, frac)))
        self.overall.set_label(_("Step {i} of {n}").format(i=stage, n=n_stages))

    def _install(self):
        try:
            plan = self.plan
            if self.extra_args:
                for a in self.extra_args.split():
                    plan.add_arg(a)
            stages = 3
            last = {"msg": None}

            def prog(stage):
                def f(msg, frac=None):
                    if self.cancelled:
                        raise InterruptedError("Cancelled")
                    if msg != last["msg"]:
                        last["msg"] = msg
                        self._ui(self.log, msg)
                    self._ui(self._set_progress, stage, stages, msg, frac)
                return f

            # 1. OpenCore EFI (small: built in the work dir, or straight into the chosen folder)
            work = os.path.join(WORKDIR, "build") if self.target_mode != "folder" else self.folder
            os.makedirs(work, exist_ok=True)
            res = builder.build_efi(plan, self.analysis, work, prog(1))
            ok, out = res["validation"]
            self._ui(self.log, f"OpenCore {res['opencore']} — ocvalidate: {'OK' if ok else out}")
            for name, err in res["failed_kexts"]:
                self._ui(self.log, f"! {name}: {err}")

            if self.target_mode == "folder":
                prog(2)(_("Files saved"), 1.0)
                recovery.download(plan.macos, work, res["oc_dir"], prog(3), cancel=lambda: self.cancelled)
            else:
                # 2. erase + format the target, copy OpenCore
                prepare = disks.prepare_usb if self.target_mode == "usb" else disks.prepare_disk
                with prepare(self.target_disk, prog(2)) as target:
                    target.install_efi(res["efi"], prog(2))
                    # 3. macOS recovery downloaded straight onto the target (no RAM needed)
                    recovery.download(plan.macos, target.rec_mnt, res["oc_dir"], prog(3),
                                      cancel=lambda: self.cancelled)
                    target.finish(prog(3))
            self.result = res
        except InterruptedError:
            self.error = _("Installation cancelled.")
        except Exception as e:      # noqa: BLE001 - everything is reported to the user
            self.error = f"{type(e).__name__}: {e}"
            self.error_detail = traceback.format_exc()
            print(self.error_detail, file=sys.stderr)
        GLib.idle_add(self._install_done)

    def _install_done(self):
        self.busy = False
        if self.error:
            self.task_label.set_label(_("Installation stopped"))
            self.error_label.set_label(self.error)
            self.error_label.set_visible(True)
            if self.error_detail:
                self.log(self.error_detail)
            self.log(_("Full log saved to") + f" {self.log_path}")
            self.details.set_expanded(True)
            self.cancel_btn.set_label(_("Back"))
            self.cancel_btn.set_sensitive(True)
            self.cancel_btn.disconnect_by_func(self.on_cancel)
            self.cancel_btn.connect("clicked", lambda *_a: self.show_step("confirm"))
        else:
            self.show_step("done")
        return False

    # ------------------------------------------------------------ 8. done

    def page_done(self):
        outer, box = page_box(_("All done!"), _("Follow these steps to finish installing macOS."))
        steps = []
        bios = [_("Disable Secure Boot, CSM/Legacy boot and Fast Boot"),
                _("Enable AHCI mode for SATA, XHCI Hand-off and 'Above 4G decoding'"),
                _("Disable VT-d if the option exists (or leave it, DisableIoMapper is set)"),
                _("If the BIOS has 'CFG Lock', disable it")]
        if self.target_mode == "usb":
            steps = [_("Restart and open the boot menu (often F8, F11, F12 or Esc)."),
                     _("Choose the USB drive (UEFI). The OpenCore menu appears."),
                     _("Select the macOS installer entry (e.g. 'macOS Base System') and press Enter.")]
        elif self.target_mode == "disk":
            steps = [_("Remove the installer USB and restart: the PC boots into OpenCore."),
                     _("If it does not, choose the disk in the firmware boot menu (UEFI).")]
        else:
            steps = [_("The files are in {f}: copy EFI and com.apple.recovery.boot to the root of a "
                       "FAT32 USB drive.").format(f=self.folder)]
        steps += [_("In the installer open Disk Utility, choose View → Show All Devices."),
                  (_("Erase the 'MACINTOSHHD' partition as APFS, named 'Macintosh HD'.") if self.target_mode == "disk"
                   else _("Erase the target disk as APFS with GUID Partition Map.")),
                  _("Close Disk Utility, choose 'Reinstall macOS' and follow the steps. The PC restarts "
                    "several times: always pick the macOS entry in OpenCore.")]
        if self.target_mode == "usb":
            steps.append(_("After installing, copy the EFI folder from the USB to the EFI partition of the "
                           "macOS disk so you can boot without the USB."))
        g1 = Adw.PreferencesGroup(title=esc(_("1. Firmware (BIOS/UEFI) settings")))
        for b in bios:
            g1.add(row(b, icon="emblem-system-symbolic"))
        box.append(g1)
        g2 = Adw.PreferencesGroup(title=esc(_("2. Install macOS")))
        for i, s in enumerate(steps, 1):
            g2.add(row(f"{i}. {s}"))
        box.append(g2)
        if self.plan and self.plan.warnings:
            g3 = Adw.PreferencesGroup(title=esc(_("3. Good to know")))
            for w in dict.fromkeys(self.plan.warnings):
                g3.add(row(w, icon="dialog-warning-symbolic", icon_class="warning"))
            box.append(g3)
        if LIVE:
            btn = Gtk.Button(label=_("Restart now"), halign=Gtk.Align.START)
            btn.add_css_class("pill")
            btn.connect("clicked", lambda *_a: subprocess.run(["systemctl", "reboot"]))
            box.append(btn)
        return outer


class App(Adw.Application):
    def __init__(self, report_path=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.report_path = report_path

    def do_activate(self):
        win = Installer(self, self.report_path)
        if LIVE:
            win.fullscreen()
        win.present()


def run(argv=None):
    report = os.environ.get("OCL_REPORT")
    if shutil.which("lsblk") is None:
        print("warning: lsblk not found, disk listing disabled", file=sys.stderr)
    return App(report).run([sys.argv[0]])
