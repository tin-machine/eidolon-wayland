/* Fixed normal Linux GUI demo. No synthetic input, shell, probe-success rule or visible process data. */
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static GtkWidget *main_window;
static GtkWidget *dialog_window;
static char application_id[128];
static const char *run_id;

static gboolean valid_run_id(const char *value) {
    if (strlen(value) != 32) return FALSE;
    for (unsigned i = 0; i < 32; ++i)
        if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) return FALSE;
    return TRUE;
}
static void report(const char *phase) {
    printf("{\"schema\":1,\"revision\":\"eidolon-linux-gtk-desktop-v1\",\"run_id\":\"%s\",\"pid\":%ld,\"uid\":%ld,\"app_id\":\"%s\",\"phase\":\"%s\"}\n",
           run_id, (long)getpid(), (long)getuid(), application_id, phase);
    fflush(stdout);
}
static void assign_id(GtkWidget *widget, const char *id) {
    GdkWindow *window = gtk_widget_get_window(widget);
    if (gtk_widget_get_mapped(widget) && window != NULL) gdk_wayland_window_set_application_id(window, id);
}
static void dialog_destroyed(GtkWidget *widget, gpointer data) {
    (void)widget; (void)data; dialog_window = NULL;
}
static void dialog_response(GtkDialog *dialog, gint response, gpointer data) {
    (void)response; (void)data; gtk_widget_destroy(GTK_WIDGET(dialog));
}
static void show_dialog(GtkButton *button, gpointer data) {
    (void)button; (void)data;
    if (dialog_window != NULL) { gtk_window_present(GTK_WINDOW(dialog_window)); return; }
    dialog_window = gtk_dialog_new_with_buttons("ダイアログ", GTK_WINDOW(main_window), GTK_DIALOG_DESTROY_WITH_PARENT,
                                                "閉じる", GTK_RESPONSE_CLOSE, NULL);
    gtk_container_set_border_width(GTK_CONTAINER(dialog_window), 18);
    gtk_box_pack_start(GTK_BOX(gtk_dialog_get_content_area(GTK_DIALOG(dialog_window))),
                       gtk_label_new("これは Linux アプリの別ウィンドーです。"), TRUE, TRUE, 8);
    g_signal_connect(dialog_window, "response", G_CALLBACK(dialog_response), NULL);
    g_signal_connect(dialog_window, "destroy", G_CALLBACK(dialog_destroyed), NULL);
    gtk_widget_show_all(dialog_window);
    char id[144]; snprintf(id, sizeof id, "%s.dialog", application_id); assign_id(dialog_window, id);
}
static void close_clicked(GtkButton *button, gpointer data) {
    (void)button; (void)data; gtk_widget_destroy(main_window);
}
static void main_destroyed(GtkWidget *widget, gpointer data) {
    (void)widget; (void)data;
    if (dialog_window != NULL) gtk_widget_destroy(dialog_window);
    main_window = NULL; gtk_main_quit();
}
int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        printf("{\"schema\":1,\"revision\":\"eidolon-linux-gtk-desktop-v1\",\"gtk_version\":\"%d.%d.%d\"}\n", GTK_MAJOR_VERSION, GTK_MINOR_VERSION, GTK_MICRO_VERSION);
        return 0;
    }
    if (argc != 3 || strcmp(argv[1], "--run-id") != 0 || !valid_run_id(argv[2])) return 64;
    run_id = argv[2];
    snprintf(application_id, sizeof application_id, "io.github.tinmachine.eidolon.linuxdesktop.r%s", run_id);
    g_set_prgname(application_id);
    if (!gtk_init_check(NULL, NULL) || !GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default())) return 69;
    main_window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(main_window), "Linux アプリ");
    gtk_window_set_default_size(GTK_WINDOW(main_window), 640, 360);
    gtk_container_set_border_width(GTK_CONTAINER(main_window), 24);
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    gtk_container_add(GTK_CONTAINER(main_window), box);
    gtk_box_pack_start(GTK_BOX(box), gtk_label_new("Linux デスクトップの小さなアプリ"), FALSE, FALSE, 0);
    GtkWidget *entry = gtk_entry_new();
    gtk_entry_set_placeholder_text(GTK_ENTRY(entry), "文字を入力してください");
    gtk_entry_set_max_length(GTK_ENTRY(entry), 4096);
    gtk_box_pack_start(GTK_BOX(box), entry, FALSE, FALSE, 0);
    GtkWidget *dialog = gtk_button_new_with_label("ダイアログを開く");
    g_signal_connect(dialog, "clicked", G_CALLBACK(show_dialog), NULL);
    gtk_box_pack_start(GTK_BOX(box), dialog, FALSE, FALSE, 0);
    GtkWidget *close = gtk_button_new_with_label("アプリを閉じる");
    g_signal_connect(close, "clicked", G_CALLBACK(close_clicked), NULL);
    gtk_box_pack_end(GTK_BOX(box), close, FALSE, FALSE, 0);
    g_signal_connect(main_window, "destroy", G_CALLBACK(main_destroyed), NULL);
    gtk_widget_show_all(main_window); assign_id(main_window, application_id); report("ready");
    gtk_main(); report("closed"); return 0;
}
