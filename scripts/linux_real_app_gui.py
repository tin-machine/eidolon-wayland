"""Synthetic Mousepad GUI experiments driven exclusively by the Wayland seat.

The GTK observer only reads a fixed set of widget properties. Its coordinates
and layout never enter receipts. GSettings/keyfiles are never written here;
the on-disk boolean is a separate, no-follow read oracle after actual clicks.
"""
from __future__ import annotations

import configparser
import math
import os
import time

import linux_real_app_wayland as app
from linux_real_app_gui_observer import ObserverError

driver = app.driver
LINE_SECTION = "org/xfce/mousepad/preferences/view"
LINE_KEY = "show-line-numbers"


def read_line_numbers(profile, lock_fd):
    """Read one exact boolean under the same live profile lock; never seed it."""
    profile.validate(lock_fd)
    directory = profile.root / "config/glib-2.0/settings"
    try:
        fd = app.inspect_api._open_absolute_directory(directory)
    except FileNotFoundError:
        return {"saved_present": False, "saved_value": None}
    try:
        app.inspect_api._directory_info(fd, os.getuid(), True, "APP_GUI_SETTINGS_PATH")
        try:
            data = app.inspect_api._read_file_at(fd, "keyfile", uid=os.getuid(), limit=1024 * 1024,
                    private=True, allow_empty=True, reason="APP_GUI_SETTINGS_FILE")
        except FileNotFoundError:
            return {"saved_present": False, "saved_value": None}
    finally:
        os.close(fd)
    profile.validate(lock_fd)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeError, configparser.Error) as error:
        raise app.WaylandError("APP_GUI_SETTINGS_SYNTAX") from error
    app.require(not parser.defaults(), "APP_GUI_SETTINGS_SYNTAX")
    if not parser.has_option(LINE_SECTION, LINE_KEY):
        return {"saved_present": False, "saved_value": None}
    value = parser.get(LINE_SECTION, LINE_KEY)
    app.require(value in ("true", "false"), "APP_GUI_SETTINGS_TYPE")
    return {"saved_present": True, "saved_value": value == "true"}


class Experiment:
    def __init__(self, run, control, receipt):
        self.run, self.control, self.receipt = run, control, receipt
        self.main, self.prefs = control.main, None
        self.records = receipt.setdefault("gui_observations", {})

    def private_snapshot(self):
        try:
            value = self.run.gui_observer.snapshot()
        except ObserverError as error:
            raise app.WaylandError(str(error)) from error
        app.require(value["main_count"] == 1 and value["main"]["mapped"] is True
                    and value["main"]["mapped_view_count"] >= 1
                    and type(value["main"]["line_numbers"]) is bool,
                    "APP_GUI_MAIN_OBSERVATION")
        return value

    def capture(self):
        """Pair a fresh private GTK reply with the same real transient window."""
        private = self.private_snapshot()
        state = self.control.snapshot()
        self.control.assert_topology(state)
        windows = driver.mapped(state)
        prefs = next((w for w in windows if w["window"] == self.prefs), None)
        app.require(private["preferences_count"] == 1
                    and private["preferences"]["mapped"] is True
                    and private["preferences"]["transient_for_main"] is True
                    and len(windows) == 2 and prefs is not None
                    and prefs["parent"] == self.main and driver.has_frame(prefs),
                    "APP_GUI_PREFERENCES_PARENT")
        inputs = state["input"]
        app.require(type(inputs.get("pointer_buttons")) is int
                    and inputs["pressed"] == 0 and inputs.get("pointer_pressed") == 0,
                    "APP_GUI_INPUT_RELEASE")
        base = {"run_id": self.receipt["run_id"], "main": self.main,
                "prefs_window": self.prefs, "parent": prefs["parent"], "frame": prefs["frame"],
                "observer_sequence": private["id"], "input_delivered": inputs["delivered"],
                "pointer_buttons": inputs["pointer_buttons"]}
        return private, prefs, base

    def view(self):
        private, _, base = self.capture()
        checkbox = private["preferences"]["targets"]["line_numbers"]
        app.require(checkbox["present"] is True and checkbox["mapped"] is True
                    and checkbox["sensitive"] is True and type(checkbox["active"]) is bool,
                    "APP_GUI_LINE_WIDGET")
        app.require(private["main"]["line_numbers"] == checkbox["active"],
                    "APP_GUI_LINE_VIEW_DISAGREES")
        return {**base, "line_numbers": private["main"]["line_numbers"],
                "gui_checked": checkbox["active"], **read_line_numbers(self.run.profile, self.run.profile_lock)}

    def widget(self, target, *, combo=False):
        private, _, base = self.capture()
        widget = private["preferences"]["targets"][target]
        app.require(widget["present"] is True and widget["mapped"] is True,
                    "APP_GUI_WIDGET_NOT_VISIBLE")
        if combo:
            app.require(widget["sensitive"] is False and widget["expected_value"] is True,
                        "APP_GUI_PROTECTED_WIDGET_WRITABLE")
            return {**base, "present": True, "mapped": True, "sensitive": False,
                    "expected_value": True, "value_sha256": widget["value_digest"]}
        app.require(widget["sensitive"] is True and type(widget["active"]) is bool,
                    "APP_GUI_POSITIVE_WIDGET_INSENSITIVE")
        return {**base, "mapped": True, "sensitive": True, "active": widget["active"]}

    @staticmethod
    def point(widget, window):
        app.require(widget["present"] is True and widget["mapped"] is True,
                    "APP_GUI_CLICK_TARGET")
        center, size = widget["center"], widget["size"]
        geometry, logical = window.get("geometry_origin"), window.get("logical_size")
        app.require(type(center) is list and len(center) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) for v in center)
                    and type(size) is list and len(size) == 2
                    and all(type(v) is int and v > 0 for v in size)
                    and type(geometry) is dict and set(geometry) == {"x", "y"}
                    and all(type(v) is int for v in geometry.values())
                    and type(logical) is dict
                    and all(type(logical.get(k)) is int and logical[k] > 0 for k in ("width", "height")),
                    "APP_GUI_CLICK_GEOMETRY")
        uv = ((center[0] - geometry["x"]) / logical["width"],
              (center[1] - geometry["y"]) / logical["height"])
        app.require(all(0 < v < 1 for v in uv), "APP_GUI_CLICK_OUTSIDE")
        return uv

    def click(self, target, before):
        self.control.focus(self.prefs)
        private, window, current = self.capture()
        app.require(current["input_delivered"] == before["input_delivered"]
                    and current["pointer_buttons"] == before["pointer_buttons"],
                    "APP_GUI_UNEXPECTED_INPUT")
        u, v = self.point(private["preferences"]["targets"][target], window)
        after = self.control.click(self.prefs, u, v)
        inputs = after["input"]
        app.require(inputs["pointer_buttons"] >= before["pointer_buttons"] + 2
                    and inputs["pointer_pressed"] == 0 and inputs["pressed"] == 0,
                    "APP_GUI_CLICK_NOT_DELIVERED")
        self.control.settle(.12)
        return {"window": self.prefs, "key_before": before["input_delivered"],
                "key_after": inputs["delivered"], "pointer_before": before["pointer_buttons"],
                "pointer_after": inputs["pointer_buttons"], "keys_pressed": inputs["pressed"],
                "pointer_pressed": inputs["pointer_pressed"], "target_observed": True}

    def wait(self, reader, predicate, reason, seconds=4):
        deadline = min(self.run.deadline, time.monotonic() + seconds)
        while True:
            self.run.pump()
            value = reader()
            if predicate(value):
                return value
            app.require(time.monotonic() < deadline, reason)
            self.control.settle(.03)

    def open_preferences(self):
        self.control.phase = "gui_preferences"
        self.control.focus(self.main)
        private = self.private_snapshot()
        state = self.control.snapshot()
        self.control.assert_topology(state)
        windows = driver.mapped(state)
        app.require(len(windows) == 1 and windows[0]["window"] == self.main
                    and private["main"]["edit_menu_open"] is False,
                    "APP_GUI_MENU_INITIAL")
        window = windows[0]
        target = private["main"]["edit_menu"]
        app.require(target["sensitive"] is True, "APP_GUI_MENU_INSENSITIVE")
        u, v = self.point(target, window)
        initial_buttons = state["input"]["pointer_buttons"]
        # The native popup policy requires a live pointer press serial. GTK's
        # keyboard menu shortcuts cannot establish that grab. Hold a genuine
        # press while GTK maps the Edit submenu, then release before keynav.
        # The observer reads only the pinned model/action/selection structure.
        try:
            self.control.button(self.main, u, v, True)
            def menu_mapped():
                observed = self.private_snapshot()
                graph = self.control.snapshot()
                self.control.assert_topology(graph)
                mapped = driver.mapped(graph)
                app.require(len(mapped) == 1 and mapped[0]["window"] == self.main,
                            "APP_GUI_MENU_TOPOLOGY")
                return (observed["main"]["edit_menu_open"] is True
                        and mapped[0]["layers"] > window["layers"])
            self.wait(menu_mapped, bool, "APP_GUI_MENU_NOT_MAPPED")
        finally:
            released = self.control.button(self.main, u, v, False)
        app.require(released["input"]["pointer_buttons"] >= initial_buttons + 2
                    and released["input"]["pointer_pressed"] == 0
                    and released["input"]["pressed"] == 0,
                    "APP_GUI_MENU_INPUT_RELEASE")
        self.control.settle(.08)
        driver.chord(self.control, self.main, driver.END)
        self.wait(self.private_snapshot,
                  lambda value: value["main"]["edit_menu_open"] is True
                    and value["main"]["preferences_selected"] is True,
                  "APP_GUI_PREFERENCES_NOT_SELECTED")
        driver.chord(self.control, self.main, driver.ENTER)
        self.prefs = self.control.wait_dialog({self.main})
        self.control.focus(self.prefs)
        self.control.settle(.15)
        self.capture()

    def page(self, name, index):
        private, _, before = self.capture()
        if private["preferences"]["page"] != index:
            self.click("tab_" + name, before)
        self.wait(lambda: self.capture()[0]["preferences"], lambda p: p["page"] == index,
                  "APP_GUI_NOTEBOOK_PAGE")

    def close_preferences(self):
        self.control.close(self.prefs)
        self.control.wait_closed(self.prefs)
        self.control.focus(self.main)
        self.control.settle(.12)
        private = self.private_snapshot()
        app.require(private["preferences_count"] == 0, "APP_GUI_PREFERENCES_NOT_CLOSED")
        self.prefs = None

    def protected_trial(self, page, index, ordinary, target):
        self.control.phase = "gui_protected"
        self.page(page, index)
        initial = self.widget(ordinary)
        positive = self.click(ordinary, initial)
        changed = self.wait(lambda: self.widget(ordinary), lambda v: v["active"] != initial["active"],
                            "APP_GUI_POSITIVE_NO_CHANGE")
        restore = self.click(ordinary, changed)
        restored = self.wait(lambda: self.widget(ordinary), lambda v: v["active"] == initial["active"],
                             "APP_GUI_POSITIVE_NOT_RESTORED")
        before = self.widget(target, combo=True)
        attempted = self.click(target, before)
        after = self.widget(target, combo=True)
        app.require(after["value_sha256"] == before["value_sha256"], "APP_GUI_PROTECTED_VALUE_CHANGED")
        return {"before": before, "after": after, "input": attempted,
                "positive_before": initial, "positive_changed": changed, "positive_restored": restored,
                "positive_input": positive, "restore_input": restore}

    def open_new_tab(self):
        """Open an additional file after Save As so no empty tab is replaced."""
        self.control.phase = "gui_open_tab"
        before = driver.observe_ready(self.control)
        initial = self.private_snapshot()
        path = self.run.profile.documents / "gui-other.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"eidolon-a0-independent-tab\n")
            stream.flush()
            os.fsync(stream.fileno())
        driver.chord(self.control, self.main, driver.CTRL, 24)
        dialog = self.control.wait_dialog({self.main})
        graph = self.control.snapshot()
        window = next(w for w in driver.mapped(graph) if w["window"] == dialog)
        app.require(window["parent"] == self.main, "APP_GUI_OPEN_DIALOG_PARENT")
        self.control.focus(dialog)
        driver.chord(self.control, dialog, driver.CTRL, 38)
        self.control.settle()
        driver.chord(self.control, dialog, driver.CTRL, 30)
        driver.type_ascii(self.control, dialog, str(path))
        self.control.settle(.25)
        driver.chord(self.control, dialog, driver.ENTER)
        self.control.wait_closed(dialog)
        self.control.focus(self.main)
        self.control.settle(.15)
        final = self.wait(self.private_snapshot,
                          lambda v: v["main"]["document_tabs"] == initial["main"]["document_tabs"] + 1,
                          "APP_GUI_OPEN_NOT_NEW_TAB")
        after = driver.observe_ready(self.control)
        return {"run_id": self.receipt["run_id"], "main": self.main,
                "before_tabs": initial["main"]["document_tabs"], "after_tabs": final["main"]["document_tabs"],
                "before": before, "after": after, "observer_before": initial["id"],
                "observer_after": final["id"], "open_dialog": dialog, "parent": window["parent"]}


def first_run(run, control, receipt):
    experiment = Experiment(run, control, receipt)
    experiment.open_preferences()
    experiment.page("view", 0)
    control.phase = "gui_line_numbers"
    initial = experiment.view()
    app.require(initial["line_numbers"] is False and initial["saved_present"] is False,
                "APP_GUI_DEFAULT_NOT_OBSERVED")
    experiment.records["initial"] = initial
    change = experiment.click("line_numbers", initial)
    changed = experiment.wait(experiment.view,
                lambda v: v["line_numbers"] is True and v["saved_present"] is True and v["saved_value"] is True,
                "APP_GUI_LINE_CHANGE_NOT_PERSISTED")
    experiment.records.update(changed=changed, change_input=change)
    protected = {"run_id": receipt["run_id"], "main": control.main, "prefs_window": experiment.prefs,
                 "parent": control.main, "positive_before": initial, "positive_after": changed,
                 "positive_input": change, "widgets": {}}
    experiment.records["protected"] = protected
    protected["widgets"]["opening-mode"] = experiment.protected_trial("window", 2, "always_show_tabs", "opening_mode")
    protected["widgets"]["session-restore"] = experiment.protected_trial("file", 3, "make_backup", "session_restore")
    experiment.close_preferences()
    protected["open_tab"] = experiment.open_new_tab()


def observe_run(run, control, receipt, *, expected):
    experiment = Experiment(run, control, receipt)
    experiment.open_preferences()
    experiment.page("view", 0)
    view = experiment.view()
    app.require(view["line_numbers"] is expected and view["gui_checked"] is expected
                and view["saved_present"] is expected and view["saved_value"] is (True if expected else None),
                "APP_GUI_RESTART_OR_ISOLATION")
    experiment.records["view"] = view
    experiment.close_preferences()


def exercise_suite(directory, settings, observer_bundle, call_run, compact):
    profile = app.SyntheticProfile.create(directory / "profile-gui")
    first = call_run(directory, settings, profile, "gui-first", exercise=True, on_ready=first_run,
                     gui_observer_bundle=observer_bundle)
    app.require(first.get("state") == "partial" and first.get("error") is None, "APP_GUI_FIRST_FAILED")
    restart = call_run(directory, settings, profile, "gui-restart",
                       on_ready=lambda r, c, v: observe_run(r, c, v, expected=True), gui_observer_bundle=observer_bundle)
    app.require(restart.get("state") == "partial" and restart.get("error") is None, "APP_GUI_RESTART_FAILED")
    other = app.SyntheticProfile.create(directory / "profile-gui-isolation")
    isolation = call_run(directory, settings, other, "gui-isolation",
                         on_ready=lambda r, c, v: observe_run(r, c, v, expected=False), gui_observer_bundle=observer_bundle)
    app.require(isolation.get("state") == "partial" and isolation.get("error") is None, "APP_GUI_ISOLATION_FAILED")
    app.require(first["gui_observer"] == restart["gui_observer"] == isolation["gui_observer"],
                "APP_GUI_OBSERVER_CHANGED")
    observed = first["gui_observations"]
    return {"schema": 1, "observer": first["gui_observer"],
            "persistence": {"first": compact(first), "restart": compact(restart), "isolation": compact(isolation),
                            "initial": observed["initial"], "changed": observed["changed"],
                            "restarted": restart["gui_observations"]["view"],
                            "isolated": isolation["gui_observations"]["view"], "change_input": observed["change_input"]},
            "protected": observed["protected"]}
