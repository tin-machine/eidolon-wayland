/* A0-only read-only GTK3 observation module; never installed with Mousepad.
 *
 * GTK3_MODULES loads this fixed fixture only in a marked synthetic run. The
 * unmodified application still receives every operation through Wayland seat
 * events. This module neither invokes actions nor writes GTK/GSettings state.
 * Its bounded socket protocol has one operation and a fixed widget allowlist;
 * it never reads document text, titles, labels, tooltips, or pixel buffers.
 */
#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <glib-unix.h>
#include <gmodule.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#define MAX_WIDGETS 4096
#define MAX_DEPTH 64
#define MAX_TOPLEVELS 32
#define MAX_REPLY 16384
#define MARKER "gui-observer-v1"
#define SOCKET_NAME "gui-observer.sock"

static gchar run_id[33], profile_id[33];
static guint64 last_request;
static gboolean client_active;

static G_NORETURN void
fail_closed (void)
{
  static const char reason[] = "APP_GUI_OBSERVER_UNVERIFIED\n";
  ssize_t ignored = write (STDERR_FILENO, reason, sizeof reason - 1);
  (void) ignored;
  _exit (78);
}

static gboolean
hex_id (const gchar *value)
{
  guint n;
  if (value == NULL || strlen (value) != 32)
    return FALSE;
  for (n = 0; n < 32; n++)
    if (!(value[n] >= '0' && value[n] <= '9') && !(value[n] >= 'a' && value[n] <= 'f'))
      return FALSE;
  return TRUE;
}

static void
private_directory (int fd)
{
  struct stat info;
  if (fstat (fd, &info) != 0 || !S_ISDIR (info.st_mode) || info.st_uid != getuid ()
      || (info.st_mode & 07777) != 0700)
    fail_closed ();
}

static int
open_directory (const gchar *path)
{
  gchar **parts;
  int fd;
  guint n;
  if (path == NULL || path[0] != '/' || path[1] == '\0'
      || path[strlen (path) - 1] == '/' || strstr (path, "//") != NULL)
    fail_closed ();
  parts = g_strsplit (path + 1, "/", -1);
  fd = open ("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0)
    fail_closed ();
  for (n = 0; parts[n] != NULL; n++)
    {
      struct stat info;
      int next;
      if (g_str_equal (parts[n], ".") || g_str_equal (parts[n], ".."))
        fail_closed ();
      next = openat (fd, parts[n], O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      if (next < 0 || fstat (next, &info) != 0
          || (info.st_uid != 0 && info.st_uid != getuid ())
          || ((info.st_mode & 0022) != 0
              && !(info.st_uid == 0 && (info.st_mode & S_ISVTX) != 0)))
        fail_closed ();
      close (fd);
      fd = next;
    }
  g_strfreev (parts);
  private_directory (fd);
  return fd;
}

static void
validate_environment (void)
{
  const gchar *runtime = g_getenv ("XDG_RUNTIME_DIR");
  const gchar *home = g_getenv ("HOME");
  const gchar *run = g_getenv ("EIDOLON_A0_GUI_RUN_ID");
  const gchar *profile = g_getenv ("EIDOLON_A0_GUI_PROFILE_ID");
  const gchar *variables[] = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"};
  const gchar *children[] = {"home", "config", "data", "state"};
  gchar *root, *expected;
  int runtime_fd, root_fd, marker;
  struct stat identity, info;
  char content[256];
  ssize_t count;
  guint n;

  if (getuid () == 0 || !hex_id (run) || !hex_id (profile)
      || home == NULL || !g_str_has_suffix (home, "/home")
      || g_strcmp0 (g_getenv ("NO_AT_BRIDGE"), "1") != 0
      || g_strcmp0 (g_getenv ("GDK_BACKEND"), "wayland") != 0
      || g_strcmp0 (g_getenv ("GSETTINGS_BACKEND"), "eidolon-keyfile-v1") != 0)
    fail_closed ();
  root = g_strndup (home, strlen (home) - strlen ("/home"));
  root_fd = open_directory (root);
  if (fstat (root_fd, &identity) != 0)
    fail_closed ();
  for (n = 0; n < G_N_ELEMENTS (variables); n++)
    {
      gchar *path = g_build_filename (root, children[n], NULL);
      int fd;
      if (g_strcmp0 (g_getenv (variables[n]), path) != 0)
        fail_closed ();
      fd = open_directory (path);
      close (fd);
      g_free (path);
    }
  runtime_fd = open_directory (runtime);
  marker = openat (runtime_fd, MARKER, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (marker < 0 || fstat (marker, &info) != 0 || !S_ISREG (info.st_mode)
      || info.st_uid != getuid () || (info.st_mode & 07777) != 0600 || info.st_nlink != 1)
    fail_closed ();
  expected = g_strdup_printf ("eidolon-a0-gui-observer-v1\n%s\n%s\n%ju:%ju\n",
                              run, profile, (uintmax_t) identity.st_dev, (uintmax_t) identity.st_ino);
  count = read (marker, content, sizeof content);
  if (count < 0 || (gsize) count != strlen (expected)
      || (guint64) info.st_size != strlen (expected)
      || memcmp (content, expected, (gsize) count) != 0)
    fail_closed ();
  close (marker);
  close (runtime_fd);
  close (root_fd);
  g_free (root);
  g_free (expected);
  g_strlcpy (run_id, run, sizeof run_id);
  g_strlcpy (profile_id, profile, sizeof profile_id);
}

enum { LINE_NUMBERS, OPENING_MODE, SESSION_RESTORE, ALWAYS_SHOW_TABS, MAKE_BACKUP,
       TAB_VIEW, TAB_WINDOW, TAB_FILE, N_TARGETS };
static const gchar *target_names[N_TARGETS] = {
  "line_numbers", "opening_mode", "session_restore", "always_show_tabs", "make_backup",
  "tab_view", "tab_window", "tab_file"
};

typedef struct {
  GtkWidget *main, *prefs, *view, *document_notebook, *prefs_notebook;
  GtkWidget *targets[N_TARGETS];
  guint main_count, prefs_count, views, mapped_views, visited;
  gboolean valid;
} Observation;

static gboolean
named_type (GtkWidget *widget, const gchar *name)
{
  return g_strcmp0 (G_OBJECT_TYPE_NAME (widget), name) == 0;
}

static void
one_widget (GtkWidget **slot, GtkWidget *widget, Observation *observation)
{
  if (*slot != NULL && *slot != widget)
    observation->valid = FALSE;
  *slot = widget;
}

static void
scan (GtkWidget *widget, GtkWidget *root, Observation *observation, guint depth)
{
  const gchar *buildable, *action = NULL;
  if (++observation->visited > MAX_WIDGETS || depth > MAX_DEPTH)
    {
      observation->valid = FALSE;
      return;
    }
  if (root == observation->main && named_type (widget, "MousepadView"))
    {
      observation->views++;
      if (gtk_widget_get_mapped (widget))
        {
          observation->mapped_views++;
          one_widget (&observation->view, widget, observation);
          one_widget (&observation->document_notebook,
                      gtk_widget_get_ancestor (widget, GTK_TYPE_NOTEBOOK), observation);
        }
    }
  if (root == observation->prefs)
    {
      buildable = gtk_buildable_get_name (GTK_BUILDABLE (widget));
      if (GTK_IS_ACTIONABLE (widget))
        action = gtk_actionable_get_action_name (GTK_ACTIONABLE (widget));
      if (g_strcmp0 (buildable, "/prefs/main-notebook") == 0 && GTK_IS_NOTEBOOK (widget))
        one_widget (&observation->prefs_notebook, widget, observation);
      if (g_strcmp0 (buildable, "/prefs/window/notebook/opening-mode-combo") == 0 && GTK_IS_COMBO_BOX (widget))
        one_widget (&observation->targets[OPENING_MODE], widget, observation);
      if (g_strcmp0 (buildable, "/prefs/file/history/session-combo") == 0 && GTK_IS_COMBO_BOX (widget))
        one_widget (&observation->targets[SESSION_RESTORE], widget, observation);
      if (GTK_IS_TOGGLE_BUTTON (widget))
        {
          if (g_strcmp0 (action, "app.preferences.view.show-line-numbers") == 0)
            one_widget (&observation->targets[LINE_NUMBERS], widget, observation);
          if (g_strcmp0 (action, "app.preferences.window.always-show-tabs") == 0)
            one_widget (&observation->targets[ALWAYS_SHOW_TABS], widget, observation);
          if (g_strcmp0 (action, "app.preferences.file.make-backup") == 0)
            one_widget (&observation->targets[MAKE_BACKUP], widget, observation);
        }
    }
  if (GTK_IS_CONTAINER (widget))
    {
      GList *children = gtk_container_get_children (GTK_CONTAINER (widget)), *item;
      for (item = children; item != NULL && observation->valid; item = item->next)
        scan (GTK_WIDGET (item->data), root, observation, depth + 1);
      g_list_free (children);
    }
}

static const gchar *
boolean (gboolean value)
{
  return value ? "true" : "false";
}

static void
append_target (GString *json, GtkWidget *widget, GtkWidget *toplevel, guint target)
{
  gint x = 0, y = 0, width = 0, height = 0;
  gboolean located = FALSE;
  const gchar *active = "null", *expected = "null";
  gchar *digest = NULL;
  if (widget != NULL)
    {
      width = gtk_widget_get_allocated_width (widget);
      height = gtk_widget_get_allocated_height (widget);
      located = width > 0 && height > 0
        && gtk_widget_translate_coordinates (widget, toplevel, width / 2, height / 2, &x, &y);
      if (GTK_IS_TOGGLE_BUTTON (widget))
        active = boolean (gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (widget)));
      if (target == OPENING_MODE || target == SESSION_RESTORE)
        {
          const gchar *value = gtk_combo_box_get_active_id (GTK_COMBO_BOX (widget));
          const gchar *required = target == OPENING_MODE ? "tab" : "never";
          expected = boolean (g_strcmp0 (value, required) == 0);
          /* Only the fixed enum domain may enter the digest oracle. */
          gboolean known = target == OPENING_MODE
            ? (g_strcmp0 (value, "tab") == 0 || g_strcmp0 (value, "window") == 0
               || g_strcmp0 (value, "mixed") == 0)
            : (g_strcmp0 (value, "never") == 0 || g_strcmp0 (value, "after-a-crash") == 0
               || g_strcmp0 (value, "unsaved-documents") == 0 || g_strcmp0 (value, "always") == 0);
          if (known)
            digest = g_compute_checksum_for_string (G_CHECKSUM_SHA256, value, -1);
        }
    }
  g_string_append_printf (json,
      "{\"present\":%s,\"mapped\":%s,\"sensitive\":%s,\"center\":",
      boolean (widget != NULL), boolean (widget != NULL && gtk_widget_get_mapped (widget)),
      boolean (widget != NULL && gtk_widget_is_sensitive (widget)));
  if (located)
    g_string_append_printf (json, "[%d,%d],\"size\":[%d,%d]", x, y, width, height);
  else
    g_string_append (json, "null,\"size\":null");
  g_string_append_printf (json, ",\"active\":%s,\"expected_value\":%s,\"value_digest\":", active, expected);
  if (digest != NULL)
    g_string_append_printf (json, "\"%s\"}", digest);
  else
    g_string_append (json, "null}");
  g_free (digest);
}

static GString *
observe (guint64 request)
{
  Observation observation = { .valid = TRUE };
  GList *windows = gtk_window_list_toplevels (), *item;
  GString *json;
  gboolean line_numbers = FALSE;
  GParamSpec *property;
  guint n, count = 0;
  if (!GDK_IS_WAYLAND_DISPLAY (gdk_display_get_default ()))
    observation.valid = FALSE;
  for (item = windows; item != NULL; item = item->next)
    {
      GtkWidget *widget = GTK_WIDGET (item->data);
      if (++count > MAX_TOPLEVELS)
        observation.valid = FALSE;
      if (named_type (widget, "MousepadWindow"))
        {
          observation.main_count++;
          one_widget (&observation.main, widget, &observation);
        }
      if (named_type (widget, "MousepadPrefsDialog"))
        {
          observation.prefs_count++;
          one_widget (&observation.prefs, widget, &observation);
        }
    }
  if (observation.main != NULL)
    scan (observation.main, observation.main, &observation, 0);
  if (observation.prefs != NULL)
    scan (observation.prefs, observation.prefs, &observation, 0);
  if (observation.prefs_notebook != NULL)
    {
      const gint pages[] = {0, 2, 3};
      for (n = 0; n < G_N_ELEMENTS (pages); n++)
        {
          GtkWidget *page = gtk_notebook_get_nth_page (GTK_NOTEBOOK (observation.prefs_notebook), pages[n]);
          if (page != NULL)
            observation.targets[TAB_VIEW + n] = gtk_notebook_get_tab_label (GTK_NOTEBOOK (observation.prefs_notebook), page);
        }
    }
  json = g_string_new (NULL);
  g_string_append_printf (json,
      "{\"schema\":1,\"id\":%" G_GUINT64_FORMAT ",\"run_id\":\"%s\",\"profile_id\":\"%s\",\"ok\":%s",
      request, run_id, profile_id, boolean (observation.valid));
  if (!observation.valid)
    {
      g_string_append (json, ",\"error\":\"APP_GUI_OBSERVER_TOPOLOGY\"}\n");
      g_list_free (windows);
      return json;
    }
  g_string_append_printf (json, ",\"main_count\":%u,\"preferences_count\":%u,\"main\":",
                          observation.main_count, observation.prefs_count);
  if (observation.main == NULL)
    g_string_append (json, "null");
  else
    {
      g_string_append_printf (json,
          "{\"mapped\":%s,\"view_count\":%u,\"mapped_view_count\":%u,\"line_numbers\":",
          boolean (gtk_widget_get_mapped (observation.main)), observation.views, observation.mapped_views);
      property = observation.view == NULL ? NULL
        : g_object_class_find_property (G_OBJECT_GET_CLASS (observation.view), "show-line-numbers");
      if (property != NULL && G_PARAM_SPEC_VALUE_TYPE (property) == G_TYPE_BOOLEAN)
        {
          g_object_get (observation.view, "show-line-numbers", &line_numbers, NULL);
          g_string_append (json, boolean (line_numbers));
        }
      else
        g_string_append (json, "null");
      g_string_append_printf (json, ",\"document_tabs\":%d,\"toplevel_size\":[%d,%d]}",
          observation.document_notebook == NULL ? 0
            : gtk_notebook_get_n_pages (GTK_NOTEBOOK (observation.document_notebook)),
          gtk_widget_get_allocated_width (observation.main), gtk_widget_get_allocated_height (observation.main));
    }
  g_string_append (json, ",\"preferences\":");
  if (observation.prefs == NULL)
    g_string_append (json, "null");
  else
    {
      g_string_append_printf (json,
          "{\"mapped\":%s,\"transient_for_main\":%s,\"page\":%d,\"pages\":%d,\"toplevel_size\":[%d,%d],\"targets\":{",
          boolean (gtk_widget_get_mapped (observation.prefs)),
          boolean (observation.main != NULL && gtk_window_get_transient_for (GTK_WINDOW (observation.prefs))
                   == GTK_WINDOW (observation.main)),
          observation.prefs_notebook == NULL ? -1
            : gtk_notebook_get_current_page (GTK_NOTEBOOK (observation.prefs_notebook)),
          observation.prefs_notebook == NULL ? 0
            : gtk_notebook_get_n_pages (GTK_NOTEBOOK (observation.prefs_notebook)),
          gtk_widget_get_allocated_width (observation.prefs), gtk_widget_get_allocated_height (observation.prefs));
      for (n = 0; n < N_TARGETS; n++)
        {
          g_string_append_printf (json, "%s\"%s\":", n == 0 ? "" : ",", target_names[n]);
          append_target (json, observation.targets[n], observation.prefs, n);
        }
      g_string_append (json, "}}");
    }
  g_string_append (json, "}\n");
  g_list_free (windows);
  return json;
}

typedef struct {
  int fd;
  gchar input[64];
  gsize length, sent;
  gint64 deadline;
  GString *output;
} Client;

static void
client_free (gpointer data)
{
  Client *client = data;
  close (client->fd);
  if (client->output != NULL)
    g_string_free (client->output, TRUE);
  g_free (client);
  client_active = FALSE;
}

static gboolean
serve_client (gpointer data)
{
  Client *client = data;
  if (g_get_monotonic_time () >= client->deadline)
    return G_SOURCE_REMOVE;
  if (client->output == NULL)
    {
      ssize_t got = recv (client->fd, client->input + client->length,
                          sizeof client->input - client->length - 1, MSG_DONTWAIT);
      if (got == 0 || (got < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR))
        return G_SOURCE_REMOVE;
      if (got < 0)
        return G_SOURCE_CONTINUE;
      client->length += (gsize) got;
      client->input[client->length] = '\0';
      if (memchr (client->input, '\0', client->length) != NULL || client->length >= sizeof client->input - 1)
        return G_SOURCE_REMOVE;
      if (memchr (client->input, '\n', client->length) != NULL)
        {
          gchar *end;
          guint64 request;
          if (!g_str_has_prefix (client->input, "OBSERVE ")
              || client->input[8] < '1' || client->input[8] > '9')
            return G_SOURCE_REMOVE;
          errno = 0;
          request = g_ascii_strtoull (client->input + 8, &end, 10);
          if (errno != 0 || request > G_MAXUINT32 || request <= last_request
              || end[0] != '\n' || end[1] != '\0')
            return G_SOURCE_REMOVE;
          last_request = request;
          client->output = observe (request);
          if (client->output->len > MAX_REPLY)
            return G_SOURCE_REMOVE;
        }
      else
        return G_SOURCE_CONTINUE;
    }
  while (client->sent < client->output->len)
    {
      ssize_t sent = send (client->fd, client->output->str + client->sent,
                           client->output->len - client->sent, MSG_DONTWAIT | MSG_NOSIGNAL);
      if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR))
        return G_SOURCE_CONTINUE;
      if (sent <= 0)
        return G_SOURCE_REMOVE;
      client->sent += (gsize) sent;
    }
  return G_SOURCE_REMOVE;
}

static gboolean
accept_client (gint listener, GIOCondition condition, gpointer unused)
{
  struct ucred credentials;
  socklen_t length = sizeof credentials;
  Client *client;
  int fd;
  (void) unused;
  if (condition & (G_IO_ERR | G_IO_HUP | G_IO_NVAL))
    fail_closed ();
  fd = accept4 (listener, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
  if (fd < 0)
    {
      if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
        fail_closed ();
      return G_SOURCE_CONTINUE;
    }
  if (client_active || getsockopt (fd, SOL_SOCKET, SO_PEERCRED, &credentials, &length) != 0
      || length != sizeof credentials || credentials.uid != getuid ())
    {
      close (fd);
      return G_SOURCE_CONTINUE;
    }
  client_active = TRUE;
  client = g_new0 (Client, 1);
  client->fd = fd;
  client->deadline = g_get_monotonic_time () + 4 * G_USEC_PER_SEC;
  g_timeout_add_full (G_PRIORITY_DEFAULT, 5, serve_client, client, client_free);
  return G_SOURCE_CONTINUE;
}

G_MODULE_EXPORT void
gtk_module_init (gint *argc, gchar ***argv)
{
  struct sockaddr_un address = { .sun_family = AF_UNIX };
  gchar *path;
  int listener;
  (void) argc;
  (void) argv;
  validate_environment ();
  path = g_build_filename (g_getenv ("XDG_RUNTIME_DIR"), SOCKET_NAME, NULL);
  if (strlen (path) >= sizeof address.sun_path)
    fail_closed ();
  g_strlcpy (address.sun_path, path, sizeof address.sun_path);
  listener = socket (AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (listener < 0 || bind (listener, (struct sockaddr *) &address, sizeof address) != 0
      || chmod (path, 0600) != 0 || listen (listener, 1) != 0)
    fail_closed ();
  g_free (path);
  g_unix_fd_add (listener, G_IO_IN | G_IO_ERR | G_IO_HUP, accept_client, NULL);
}
