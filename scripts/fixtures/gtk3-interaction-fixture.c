/* Fixed GTK3 counters only. No text, key strings, screenshots or arbitrary commands are reported. */
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#ifdef EIDOLON_GTK_FIXTURE_SELFTEST
#define HOST_SELFTEST "true"
#else
#define HOST_SELFTEST "false"
#endif

typedef enum { CASE_INPUT, CASE_DIALOG, CASE_RESIZE } Case;
typedef struct {
    unsigned a_down, a_up, delete_down, delete_up, other_keys, changes, clicks;
    unsigned opened, closed, response, parent_set, dialog_visible;
    unsigned main_configures, dialog_configures, main_draws, dialog_draws;
    unsigned token_seen, delete_seen, large_seen, returned_seen;
    int width, height, entry_x, entry_y, entry_w, entry_h, button_x, button_y, button_w, button_h;
} Counters;
typedef struct {
    Case test_case;
    const char *case_name;
    char run_id[33], app_id[128], dialog_id[144];
    GtkWidget *window, *entry, *button, *dialog;
    Counters value, previous;
    unsigned sequence;
    gboolean ready, finished;
    int exit_code;
} Fixture;

static gboolean complete(Fixture *f) {
    Counters *v = &f->value;
    if (f->test_case == CASE_INPUT)
        return v->token_seen && v->delete_seen && v->changes == 2 && v->clicks == 1 &&
               v->a_down == 1 && v->a_up == 1 && v->delete_down == 1 && v->delete_up == 1 && !v->other_keys;
    if (f->test_case == CASE_DIALOG)
        return v->opened == 1 && v->closed == 1 && v->response == 1 && v->parent_set && !v->dialog_visible;
    return v->large_seen && v->returned_seen;
}

static void geometry(Fixture *f) {
    if (!f->window) return;
    GtkAllocation entry, button;
    gtk_widget_get_allocation(f->window, &entry);
    f->value.width = entry.width; f->value.height = entry.height;
    gtk_widget_get_allocation(f->entry, &entry);
    gtk_widget_get_allocation(f->button, &button);
    gtk_widget_translate_coordinates(f->entry, f->window, 0, 0, &f->value.entry_x, &f->value.entry_y);
    gtk_widget_translate_coordinates(f->button, f->window, 0, 0, &f->value.button_x, &f->value.button_y);
    f->value.entry_w = entry.width; f->value.entry_h = entry.height;
    f->value.button_w = button.width; f->value.button_h = button.height;
}

static void report(Fixture *f, const char *phase, const char *reason) {
    if (f->sequence >= 256) return;
    geometry(f);
    Counters *v = &f->value;
    printf("{\"schema\":1,\"probe\":\"eidolon-gtk3-interaction-v1\",\"run_id\":\"%s\",\"case\":\"%s\","
           "\"app_id\":\"%s\",\"dialog_app_id\":\"%s\",\"pid\":%ld,\"uid\":%ld,\"sequence\":%u,"
           "\"phase\":\"%s\",\"reason\":\"%s\",\"complete\":%s,\"host_selftest\":%s,\"physical_input_verified\":false,"
           "\"a_down\":%u,\"a_up\":%u,\"delete_down\":%u,\"delete_up\":%u,\"other_keys\":%u,"
           "\"entry_changes\":%u,\"button_clicks\":%u,\"token_seen\":%s,\"delete_seen\":%s,"
           "\"dialog_opened\":%u,\"dialog_closed\":%u,\"dialog_responses\":%u,\"dialog_parent_set\":%s,\"dialog_visible\":%s,"
           "\"main_configures\":%u,\"dialog_configures\":%u,\"main_draws\":%u,\"dialog_draws\":%u,"
           "\"resize_large_seen\":%s,\"resize_return_seen\":%s,\"logical_size\":[%d,%d],"
           "\"entry_rect\":[%d,%d,%d,%d],\"button_rect\":[%d,%d,%d,%d]}\n",
           f->run_id, f->case_name, f->app_id, f->dialog_id, (long)getpid(), (long)getuid(), ++f->sequence,
           phase, reason, complete(f) ? "true" : "false", HOST_SELFTEST, v->a_down, v->a_up, v->delete_down, v->delete_up, v->other_keys,
           v->changes, v->clicks, v->token_seen ? "true" : "false", v->delete_seen ? "true" : "false",
           v->opened, v->closed, v->response, v->parent_set ? "true" : "false", v->dialog_visible ? "true" : "false",
           v->main_configures, v->dialog_configures, v->main_draws, v->dialog_draws,
           v->large_seen ? "true" : "false", v->returned_seen ? "true" : "false", v->width, v->height,
           v->entry_x, v->entry_y, v->entry_w, v->entry_h, v->button_x, v->button_y, v->button_w, v->button_h);
    fflush(stdout);
    f->previous = f->value;
}

static void finish(Fixture *f, const char *reason, int code) {
    if (f->finished) return;
    f->finished = TRUE; f->exit_code = code;
    report(f, "final", reason);
    if (f->dialog) gtk_widget_destroy(f->dialog);
    if (f->window) gtk_widget_destroy(f->window);
    gtk_main_quit();
}

static gboolean timeout_cb(gpointer data) { finish(data, "timeout", 124); return G_SOURCE_REMOVE; }
static gboolean close_cb(GtkWidget *widget, GdkEvent *event, gpointer data) {
    (void)widget; (void)event;
    Fixture *f = data; finish(f, complete(f) ? "completed" : "window_closed", complete(f) ? 0 : 1);
    return TRUE;
}

static gboolean key_cb(GtkWidget *widget, GdkEventKey *event, gpointer data) {
    (void)widget; Fixture *f = data;
    gboolean down = event->type == GDK_KEY_PRESS;
    if (event->keyval == GDK_KEY_a) { if (down) f->value.a_down++; else f->value.a_up++; }
    else if (event->keyval == GDK_KEY_BackSpace) { if (down) f->value.delete_down++; else f->value.delete_up++; }
    else f->value.other_keys++;
    return FALSE;  /* GtkEntryの通常入力処理を通す。 */
}

static void changed_cb(GtkEditable *editable, gpointer data) {
    Fixture *f = data;
    const char *text = gtk_entry_get_text(GTK_ENTRY(editable));
    f->value.changes++;
    if (strcmp(text, "a") == 0) f->value.token_seen = TRUE;
    if (f->value.token_seen && text[0] == '\0') f->value.delete_seen = TRUE;
}

static gboolean configure_cb(GtkWidget *widget, GdkEventConfigure *event, gpointer data) {
    Fixture *f = data;
    if (widget == f->window) {
        f->value.main_configures++;
        if (event->width == 800 && event->height == 500) f->value.large_seen = TRUE;
        if (f->value.large_seen && event->width == 640 && event->height == 360) f->value.returned_seen = TRUE;
    } else f->value.dialog_configures++;
    return FALSE;
}

static gboolean draw_cb(GtkWidget *widget, cairo_t *context, gpointer data) {
    (void)context; Fixture *f = data;
    if (widget == f->window) f->value.main_draws++; else f->value.dialog_draws++;
    return FALSE;
}

static gboolean set_app_id(GtkWidget *widget, const char *app_id) {
    gtk_widget_realize(widget);
    GdkWindow *window = gtk_widget_get_window(widget);
    if (GDK_IS_WAYLAND_WINDOW(window)) {
        gdk_wayland_window_set_application_id(window, app_id);
        return TRUE;
    }
#ifdef EIDOLON_GTK_FIXTURE_SELFTEST
    return TRUE;  /* localhost Xvfbのcallback検査専用。製品buildへは含めない。 */
#else
    return FALSE;
#endif
}

static void dialog_destroyed(GtkWidget *widget, gpointer data) {
    (void)widget; Fixture *f = data;
    f->dialog = NULL; f->value.closed++; f->value.dialog_visible = FALSE;
}
static void dialog_response(GtkDialog *dialog, gint response, gpointer data) {
    Fixture *f = data;
    if (response == GTK_RESPONSE_CLOSE || response == GTK_RESPONSE_DELETE_EVENT) f->value.response++;
    gtk_widget_destroy(GTK_WIDGET(dialog));
}
static void click_cb(GtkButton *button, gpointer data) {
    (void)button; Fixture *f = data; f->value.clicks++;
    if (f->test_case != CASE_DIALOG || f->dialog || f->value.opened) return;
    f->dialog = gtk_dialog_new_with_buttons("Eidolon fixture dialog", GTK_WINDOW(f->window),
                                           GTK_DIALOG_MODAL | GTK_DIALOG_DESTROY_WITH_PARENT,
                                           "Close", GTK_RESPONSE_CLOSE, NULL);
    gtk_window_set_decorated(GTK_WINDOW(f->dialog), FALSE);
    gtk_window_set_default_size(GTK_WINDOW(f->dialog), 320, 180);
    GtkWidget *label = gtk_label_new("Fixed GTK3 dialog");
    gtk_box_pack_start(GTK_BOX(gtk_dialog_get_content_area(GTK_DIALOG(f->dialog))), label, TRUE, TRUE, 16);
    g_signal_connect(f->dialog, "response", G_CALLBACK(dialog_response), f);
    g_signal_connect(f->dialog, "destroy", G_CALLBACK(dialog_destroyed), f);
    g_signal_connect(f->dialog, "configure-event", G_CALLBACK(configure_cb), f);
    g_signal_connect(f->dialog, "draw", G_CALLBACK(draw_cb), f);
    if (!set_app_id(f->dialog, f->dialog_id)) { finish(f, "wayland_required", 2); return; }
    f->value.parent_set = gtk_window_get_transient_for(GTK_WINDOW(f->dialog)) == GTK_WINDOW(f->window);
    f->value.opened++; f->value.dialog_visible = TRUE;
    gtk_widget_show_all(f->dialog);
}

static gboolean publish_cb(gpointer data) {
    Fixture *f = data;
    if (f->finished) return G_SOURCE_REMOVE;
    geometry(f);
    if (!f->ready && gtk_widget_get_mapped(f->window) && f->value.main_draws) {
        f->ready = TRUE; report(f, "ready", "running");
    } else if (f->ready && memcmp(&f->value, &f->previous, sizeof(Counters)) != 0) report(f, "state", "running");
    return G_SOURCE_CONTINUE;
}

#ifdef EIDOLON_GTK_FIXTURE_SELFTEST
static gboolean selftest_cb(gpointer data) {
    Fixture *f = data;
    if (!f->ready) return G_SOURCE_CONTINUE;
    if (f->test_case == CASE_INPUT && !f->value.token_seen) {
        GdkEventKey event = {0}; event.type = GDK_KEY_PRESS; event.keyval = GDK_KEY_a; key_cb(NULL, &event, f);
        gtk_entry_set_text(GTK_ENTRY(f->entry), "a");
        event.type = GDK_KEY_RELEASE; key_cb(NULL, &event, f);
        event.type = GDK_KEY_PRESS; event.keyval = GDK_KEY_BackSpace; key_cb(NULL, &event, f);
        gtk_entry_set_text(GTK_ENTRY(f->entry), "");
        event.type = GDK_KEY_RELEASE; key_cb(NULL, &event, f);
        gtk_button_clicked(GTK_BUTTON(f->button));
    } else if (f->test_case == CASE_DIALOG) {
        if (!f->value.opened) gtk_button_clicked(GTK_BUTTON(f->button));
        else if (f->dialog && f->value.dialog_draws) gtk_dialog_response(GTK_DIALOG(f->dialog), GTK_RESPONSE_CLOSE);
    } else if (f->test_case == CASE_RESIZE) {
        if (!f->value.large_seen) gtk_window_resize(GTK_WINDOW(f->window), 800, 500);
        else if (!f->value.returned_seen) gtk_window_resize(GTK_WINDOW(f->window), 640, 360);
    }
    if (complete(f)) { finish(f, "completed", 0); return G_SOURCE_REMOVE; }
    return G_SOURCE_CONTINUE;
}
#endif

int main(int argc, char **argv) {
    Fixture f = {0};
    if (argc != 5 || strcmp(argv[1], "--case") || strcmp(argv[3], "--run-id") || strlen(argv[4]) != 32) return 64;
    for (unsigned i = 0; i < 32; i++) if (!g_ascii_isdigit(argv[4][i]) && !(argv[4][i] >= 'a' && argv[4][i] <= 'f')) return 64;
    if (!strcmp(argv[2], "input")) f.test_case = CASE_INPUT;
    else if (!strcmp(argv[2], "dialog")) f.test_case = CASE_DIALOG;
    else if (!strcmp(argv[2], "resize")) f.test_case = CASE_RESIZE;
    else return 64;
    f.case_name = argv[2]; memcpy(f.run_id, argv[4], 33);
    snprintf(f.app_id, sizeof f.app_id, "io.github.tinmachine.eidolon.gtkfixture.r%s", f.run_id);
    snprintf(f.dialog_id, sizeof f.dialog_id, "%s.dialog", f.app_id);
    if (!gtk_init_check(NULL, NULL)) { report(&f, "final", "display_unavailable"); return 2; }
    f.window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(f.window), "Eidolon GTK3 interaction fixture");
    gtk_window_set_decorated(GTK_WINDOW(f.window), FALSE);
    gtk_window_set_default_size(GTK_WINDOW(f.window), 640, 360);
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    gtk_container_set_border_width(GTK_CONTAINER(box), 24);
    gtk_container_add(GTK_CONTAINER(f.window), box);
    gtk_box_pack_start(GTK_BOX(box), gtk_label_new("Fixed GTK3 interaction fixture"), FALSE, FALSE, 0);
    f.entry = gtk_entry_new(); gtk_widget_set_size_request(f.entry, -1, 56);
    gtk_entry_set_input_purpose(GTK_ENTRY(f.entry), GTK_INPUT_PURPOSE_FREE_FORM);
    gtk_entry_set_input_hints(GTK_ENTRY(f.entry), GTK_INPUT_HINT_NO_EMOJI);
    gtk_box_pack_start(GTK_BOX(box), f.entry, FALSE, FALSE, 0);
    f.button = gtk_button_new_with_label(f.test_case == CASE_DIALOG ? "Open fixed dialog" : "Fixed click");
    gtk_widget_set_size_request(f.button, -1, 88);
    gtk_box_pack_start(GTK_BOX(box), f.button, TRUE, TRUE, 0);
    g_signal_connect(f.entry, "changed", G_CALLBACK(changed_cb), &f);
    g_signal_connect(f.entry, "key-press-event", G_CALLBACK(key_cb), &f);
    g_signal_connect(f.entry, "key-release-event", G_CALLBACK(key_cb), &f);
    g_signal_connect(f.button, "clicked", G_CALLBACK(click_cb), &f);
    g_signal_connect(f.window, "configure-event", G_CALLBACK(configure_cb), &f);
    g_signal_connect(f.window, "draw", G_CALLBACK(draw_cb), &f);
    g_signal_connect(f.window, "delete-event", G_CALLBACK(close_cb), &f);
    if (!set_app_id(f.window, f.app_id)) { report(&f, "final", "wayland_required"); gtk_widget_destroy(f.window); return 2; }
    gtk_widget_show_all(f.window); gtk_widget_grab_focus(f.entry);
    g_timeout_add(50, publish_cb, &f); g_timeout_add_seconds(60, timeout_cb, &f);
#ifdef EIDOLON_GTK_FIXTURE_SELFTEST
    g_timeout_add(100, selftest_cb, &f);
#endif
    gtk_main();
    return f.exit_code;
}
