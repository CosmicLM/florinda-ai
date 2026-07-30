/* extension.js — Florinda Status: a top-bar indicator for GNOME, the
 * equivalent of this project's Waybar custom/flora module for desktops
 * that don't run Waybar. Shows the same states waybar_flora_status.py
 * shows (Idle/Listening/Thinking/Talking/Watching/Offline) and provides a
 * menu to stop/start flora-daemon.service and open the activity log.
 *
 * WHY read status.json directly instead of shelling out to something like
 * `systemctl is-active`: this is the exact same file flora_service.py's
 * StatusBroadcaster already writes for Waybar, including the "missing or
 * stale file means offline" convention (see waybar_flora_status.py) — one
 * source of truth for "is Florinda running" shared by both status widgets,
 * rather than a second, potentially-inconsistent way of asking.
 *
 * WHY call systemctl directly instead of scripts/flora_kill_switch.py: that
 * script exists so Waybar's on-click-right (which only runs a fixed shell
 * command string) doesn't need Waybar itself to know which state to toggle
 * to. This extension already computes the current state to render the
 * label, so it can make the same start/stop decision inline — and doing so
 * avoids this extension needing to know where the flora-ai project is
 * checked out (systemctl only needs the unit name, which is fixed).
 *
 * NOTE: written and reviewed against GNOME Shell 45+'s documented ESM
 * extension API (gjs.guide) and PopupMenu conventions used across the
 * wider extension ecosystem, but not run inside an actual GNOME Shell
 * session (this project's own dev machine runs Hyprland). If a menu item
 * or signal name has drifted on the GNOME Shell version you're running,
 * `journalctl --user -f -o cat /usr/bin/gnome-shell` while reloading the
 * extension will show the exact JS exception.
 */
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const SERVICE_NAME = 'flora-daemon.service';
const STALE_AFTER_S = 60;
const REFRESH_FALLBACK_INTERVAL_S = 15;

const STATE_LABELS = {
    idle: 'Idle',
    listening: 'Listening',
    thinking: 'Thinking',
    talking: 'Talking',
    watching: 'Watching',
    offline: 'Offline',
};

export default class FloraStatusExtension extends Extension {
    enable() {
        this._statusFile = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_home_dir(), '.local', 'share', 'flora-ai', 'status.json']));
        this._activityLogPath =
            GLib.build_filenamev([GLib.get_home_dir(), '.local', 'share', 'flora-ai', 'activity.log']);
        this._isOffline = true;

        this._indicator = new PanelMenu.Button(0.0, 'Florinda Status', false);

        this._label = new St.Label({
            text: '✱ Offline',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._indicator.add_child(this._label);

        this._statusItem = new PopupMenu.PopupMenuItem('Florinda: offline', {reactive: false});
        this._indicator.menu.addMenuItem(this._statusItem);
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._toggleItem = new PopupMenu.PopupMenuItem('Start Florinda');
        this._toggleItem.connect('activate', () => this._toggleService());
        this._indicator.menu.addMenuItem(this._toggleItem);

        this._activityItem = new PopupMenu.PopupMenuItem('View Activity Log');
        this._activityItem.connect('activate', () => this._openActivityLog());
        this._indicator.menu.addMenuItem(this._activityItem);

        Main.panel.addToStatusArea(this.uuid, this._indicator);

        try {
            this._monitor = this._statusFile.monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._monitorId = this._monitor.connect('changed', () => this._refresh());
        } catch (e) {
            logError(e, 'Florinda Status: failed to watch status.json, falling back to polling only');
            this._monitor = null;
        }

        // WHY a periodic fallback tick in addition to the file monitor: two
        // things a change-event alone can't catch — the self-heal-to-idle
        // check for a state that's gone stale without the file changing
        // again (see waybar_flora_status.py's own STALE_AFTER_S logic), and
        // any missed inotify event (rare, but the monitor is best-effort).
        this._timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, REFRESH_FALLBACK_INTERVAL_S, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });

        this._refresh();
    }

    disable() {
        if (this._monitor) {
            this._monitor.disconnect(this._monitorId);
            this._monitor.cancel();
            this._monitor = null;
        }
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        this._indicator?.destroy();
        this._indicator = null;
    }

    _readStatus() {
        if (!this._statusFile.query_exists(null))
            return null;
        try {
            const [ok, contents] = this._statusFile.load_contents(null);
            if (!ok)
                return null;
            return JSON.parse(new TextDecoder('utf-8').decode(contents));
        } catch (e) {
            return null;
        }
    }

    _refresh() {
        const data = this._readStatus();
        let state = 'offline';
        if (data) {
            state = data.state || 'idle';
            const updatedAt = data.updated_at || 0;
            const nowS = GLib.DateTime.new_now_utc().to_unix();
            if (state !== 'idle' && (nowS - updatedAt) > STALE_AFTER_S)
                state = 'idle';
        }
        this._isOffline = state === 'offline';

        const label = STATE_LABELS[state] || 'Offline';
        this._label.set_text(`✱ ${label}`);
        this._statusItem.label.text = `Florinda: ${label.toLowerCase()}`;
        this._toggleItem.label.text = this._isOffline ? 'Start Florinda' : 'Stop Florinda';
    }

    _toggleService() {
        const action = this._isOffline ? 'start' : 'stop';
        try {
            GLib.spawn_command_line_async(`systemctl --user ${action} ${SERVICE_NAME}`);
        } catch (e) {
            logError(e, `Florinda Status: failed to ${action} ${SERVICE_NAME}`);
        }
        // Refresh shortly after — systemctl returning doesn't mean
        // status.json has updated yet (stop's status.json removal happens
        // inside flora_service.py's own shutdown handling, start's happens
        // once the process is far enough into startup to call
        // set_idle()). The file monitor will also catch it when it lands;
        // this just avoids sitting on a stale label for the 15s fallback
        // tick in the meantime.
        GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._refresh();
            return GLib.SOURCE_REMOVE;
        });
    }

    _openActivityLog() {
        const command = 'kitty --class flora-activity ' +
            '--title "Florinda Activity (read-only)" ' +
            `-e sh -c "tail -n 200 -f '${this._activityLogPath}'"`;
        try {
            GLib.spawn_command_line_async(command);
        } catch (e) {
            logError(e, 'Florinda Status: failed to open activity log (is kitty installed?)');
        }
    }
}
