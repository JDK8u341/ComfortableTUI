"""Bounded integration exercise using only this example's UI and local pages."""
from comfortable_tui import Key, WebView


def install_checks(monitor, report):
    app, model = monitor.app, monitor.model

    def start():
        assert monitor.count >= 2 and model.sample.cpu is not None
        report["checks"].append("live_non_warmup_metrics")
        monitor.filter("no-process-can-match-this-ctui-fixture")
        assert monitor.table.records == []
        monitor.filter("")
        model.selected = monitor.table.records[0].key
        monitor.pin()
        assert len(model.pins) == 1
        report["checks"].append("filter_and_pin_by_identity")
        monitor.pause_changed(True)
        report["paused_count"] = monitor.count
        monitor.tabs.select(2)

    def compare():
        assert monitor.count == report.pop("paused_count")
        monitor.pause_changed(False)
        report["checks"].append("pause_retains_snapshot")
        if monitor.web:
            views = [w for w in monitor.compare.walk() if isinstance(w, WebView)]
            assert len(views) == 1
            page = views[0].page
            assert page is not monitor.web.pages["overview"] and page.loaded
            app.focus(monitor.compare)
            app.dispatch(Key("enter"))
            assert app.focused is views[0] and views[0].interacting
            app.dispatch(Key("escape"))
            assert app.focused is monitor.compare and not views[0].interacting
            report["checks"].append("independent_browser_page_and_embedded_enter_escape")
        else:
            assert monitor.watch._row_widgets and monitor.compare._row_widgets
            report["checks"].append("embedded_list_and_table_charts")
        monitor.tabs.select(1)

    def settings():
        if monitor.web:
            monitor.web.publish(model)
            report["checks"].append("telemetry_message_published")
        else:
            report["checks"].append("protocol_negotiated_resource_chart")
        monitor.tabs.select(4)
        monitor.cadence.set_value(.5)
        assert monitor.interval == .5
        monitor.help()
        app.close_modal()
        monitor.help()
        app.close_modal()
        report["checks"].append("settings_and_repeatable_help_modal")
        monitor.tabs.select(0)

    def message_send():
        if not monitor.web:
            return
        key = next(p.key for p in model.rows()[:40] if p.key != model.selected)
        report["expected_selection"] = key
        monitor.web.publish(model)
        monitor.web.probe("comfortable-top-self-test", key)

    def message_received():
        if monitor.web:
            assert len(monitor.web.probes) == len(monitor.web.pages)
            assert all(p["token"] == "comfortable-top-self-test" and p["received"] for p in monitor.web.probes.values())
            assert model.selected == tuple(report.pop("expected_selection"))
            report["checks"].append("browser_telemetry_received_and_selection_roundtrip")

    app.call_later(4, start)
    app.call_later(7, compare)
    app.call_later(10, settings)
    app.call_later(12, message_send)
    app.call_later(13, message_received)
