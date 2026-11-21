"""Internet check and a small Wi-Fi picker built on NetworkManager's nmcli."""

import shutil
import socket
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..i18n import _  # noqa: E402


def online(timeout=3):
    for host in ("api.github.com", "deb.debian.org"):
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def has_wifi_tools():
    """nmcli is available and NetworkManager sees at least one Wi-Fi adapter."""
    if shutil.which("nmcli") is None:
        return False
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "TYPE", "device"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "wifi" in out.split()


def scan():
    out = subprocess.run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                          "--rescan", "yes"], capture_output=True, text=True, timeout=30).stdout
    nets = {}
    for line in out.splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4:
            continue
        inuse, ssid, signal, sec = [p.replace("\x00", ":") for p in parts[:4]]
        if not ssid:
            continue
        sig = int(signal or 0)
        if ssid not in nets or nets[ssid]["signal"] < sig:
            nets[ssid] = {"ssid": ssid, "signal": sig, "secure": bool(sec and sec != "--"), "active": inuse == "*"}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def connect(ssid, password=None):
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


class WifiDialog(Adw.Window):
    def __init__(self, parent, on_done):
        super().__init__(transient_for=parent, modal=True, title=_("Wi-Fi"), default_width=480, default_height=520)
        self.on_done = on_done
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Scan again"))
        refresh.connect("clicked", lambda *_a: self.refresh())
        header.pack_start(refresh)
        box.append(header)
        self.status = Gtk.Label(label=_("Scanning…"), margin_top=8, margin_bottom=8)
        box.append(self.status)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=12, margin_end=12,
                                margin_bottom=12)
        self.list.add_css_class("boxed-list")
        box.append(Gtk.ScrolledWindow(vexpand=True, child=self.list))
        self.set_content(box)
        self.refresh()

    def refresh(self):
        self.status.set_label(_("Scanning…"))
        threading.Thread(target=lambda: GLib.idle_add(self._fill, scan()), daemon=True).start()

    def _fill(self, nets):
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        self.status.set_label(_("Choose a network") if nets else _("No Wi-Fi networks found"))
        for n in nets:
            row = Adw.ActionRow(title=GLib.markup_escape_text(n["ssid"]),
                                subtitle=f"{n['signal']}%" + (" · " + _("connected") if n["active"] else ""))
            row.add_prefix(Gtk.Image.new_from_icon_name(
                "network-wireless-encrypted-symbolic" if n["secure"] else "network-wireless-symbolic"))
            row.set_activatable(True)
            row.connect("activated", self._pick, n)
            self.list.append(row)
        return False

    def _pick(self, _row, net):
        if not net["secure"]:
            self._connect(net["ssid"], None)
            return
        dlg = Adw.Window(transient_for=self, modal=True, title=net["ssid"], default_width=380)
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                    margin_start=18, margin_end=18)
        b.append(Gtk.Label(label=_("Password for {n}").format(n=net["ssid"]), xalign=0))
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        b.append(entry)
        btn = Gtk.Button(label=_("Connect"))
        btn.add_css_class("suggested-action")
        b.append(btn)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(Adw.HeaderBar())
        outer.append(b)
        dlg.set_content(outer)

        def go(*_a):
            dlg.close()
            self._connect(net["ssid"], entry.get_text())
        btn.connect("clicked", go)
        entry.connect("activate", go)
        dlg.present()

    def _connect(self, ssid, password):
        self.status.set_label(_("Connecting to {n}…").format(n=ssid))

        def work():
            ok, msg = connect(ssid, password)
            GLib.idle_add(self._connected, ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def _connected(self, ok, msg):
        if ok:
            self.on_done()
            self.close()
        else:
            self.status.set_label(_("Could not connect") + f": {msg[:120]}")
        return False
