/* Fixed A0 probe. No caller-supplied schemas, keys, values, executable or paths.
 * --check is used only with a private synthetic profile by Inspect/Launch.
 * --exercise requires a marker in that synthetic profile, and leaves known
 * ordinary settings for --verify-persistence in a second process.
 */
#define _GNU_SOURCE
#define G_SETTINGS_ENABLE_BACKEND
#include <gio/gio.h>
#include <gio/gsettingsbackend.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#define BACKEND_NAME "eidolon-keyfile-v1"
#define BACKEND_TYPE "EidolonKeyfileV1Backend"
#define VIEW_SCHEMA "org.xfce.mousepad.preferences.view"
#define VIEW_PATH "/org/xfce/mousepad/preferences/view/"
#define PROBE_FONT "Monospace 13"

typedef struct {
  const gchar *schema;
  const gchar *path;
  const gchar *key;
  const GVariantType *type;
  const gchar *fixed_string;
} PolicyKey;

static const PolicyKey policy[] = {
  { "org.xfce.mousepad.preferences.file", "/org/xfce/mousepad/preferences/file/",
    "session-restore", G_VARIANT_TYPE_STRING, "never" },
  { "org.xfce.mousepad.preferences.file", "/org/xfce/mousepad/preferences/file/",
    "autosave-timer", G_VARIANT_TYPE_UINT32, NULL },
  { "org.xfce.mousepad.preferences.window", "/org/xfce/mousepad/preferences/window/",
    "opening-mode", G_VARIANT_TYPE_STRING, "tab" }
};

static GSettingsBackend *backend;
static GSettingsBackendClass *backend_class;
static guint line_notifications, font_notifications;

static void
require (gboolean success, const gchar *reason)
{
  if (!success)
    {
      g_print ("{\"schema\":1,\"status\":\"failed\",\"reason\":\"%s\"}\n", reason);
      exit (1);
    }
}

static GSettings *
settings (const gchar *id, const gchar *path, const gchar *key, const GVariantType *type)
{
  GSettingsSchemaSource *source = g_settings_schema_source_get_default ();
  GSettingsSchema *schema;
  GSettingsSchemaKey *schema_key;
  GSettings *result;
  require (source != NULL, "APP_SETTINGS_SCHEMA_UNVERIFIED");
  schema = g_settings_schema_source_lookup (source, id, TRUE);
  require (schema != NULL, "APP_SETTINGS_SCHEMA_UNVERIFIED");
  require (g_strcmp0 (g_settings_schema_get_path (schema), path) == 0 &&
           g_settings_schema_has_key (schema, key), "APP_SETTINGS_SCHEMA_UNVERIFIED");
  schema_key = g_settings_schema_get_key (schema, key);
  require (g_variant_type_equal (g_settings_schema_key_get_value_type (schema_key), type),
           "APP_SETTINGS_SCHEMA_UNVERIFIED");
  result = g_settings_new_full (schema, NULL, NULL);
  g_settings_schema_key_unref (schema_key);
  g_settings_schema_unref (schema);
  return result;
}

static gboolean
matches_policy (GVariant *value, const PolicyKey *item)
{
  gboolean matched = value != NULL && g_variant_is_of_type (value, item->type) &&
    (item->fixed_string != NULL
     ? g_str_equal (g_variant_get_string (value, NULL), item->fixed_string)
     : g_variant_get_uint32 (value) == 0);
  if (value != NULL)
    g_variant_unref (value);
  return matched;
}

static void
check_policy (gboolean try_mutations)
{
  guint i;
  for (i = 0; i < G_N_ELEMENTS (policy); i++)
    {
      const PolicyKey *item = &policy[i];
      GSettings *instance = settings (item->schema, item->path, item->key, item->type);
      gchar *full_key = g_strconcat (item->path, item->key, NULL);
      require (!g_settings_is_writable (instance, item->key) &&
               !backend_class->get_writable (backend, full_key), "APP_SETTINGS_POLICY_UNVERIFIED");
      require (matches_policy (g_settings_get_value (instance, item->key), item) &&
               matches_policy (g_settings_get_default_value (instance, item->key), item) &&
               matches_policy (g_settings_get_user_value (instance, item->key), item) &&
               matches_policy (backend_class->read (backend, full_key, item->type, TRUE), item) &&
               matches_policy (backend_class->read_user_value (backend, full_key, item->type), item),
               "APP_SETTINGS_POLICY_UNVERIFIED");
      if (try_mutations)
        {
          GVariant *conflicting = g_variant_ref_sink (item->fixed_string != NULL
            ? g_variant_new_string (i == 0 ? "after-a-crash" : "window") : g_variant_new_uint32 (30));
          require (!g_settings_set_value (instance, item->key, conflicting) &&
                   !backend_class->write (backend, full_key, conflicting, NULL),
                   "APP_SETTINGS_POLICY_UNVERIFIED");
          g_variant_unref (conflicting);
          g_settings_reset (instance, item->key);
          backend_class->reset (backend, full_key, NULL);
          require (matches_policy (g_settings_get_value (instance, item->key), item),
                   "APP_SETTINGS_POLICY_UNVERIFIED");
        }
      g_free (full_key);
      g_object_unref (instance);
    }
}

static void
require_synthetic_profile (void)
{
  const gchar *config = g_getenv ("XDG_CONFIG_HOME");
  gchar *root, *marker;
  struct stat info;
  static const gchar expected[] = "eidolon-settings-probe-v1\n";
  gchar data[sizeof expected];
  ssize_t length;
  int fd;
  require (config != NULL, "SETTINGS_PROBE_SYNTHETIC_REQUIRED");
  root = g_path_get_dirname (config);
  marker = g_build_filename (root, "settings-probe-v1", NULL);
  fd = open (marker, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  require (fd >= 0, "SETTINGS_PROBE_SYNTHETIC_REQUIRED");
  require (fstat (fd, &info) == 0 && S_ISREG (info.st_mode) &&
           info.st_uid == getuid () && (info.st_mode & 07777) == 0600 &&
           info.st_nlink == 1 && info.st_size == (off_t) sizeof expected - 1,
           "SETTINGS_PROBE_SYNTHETIC_REQUIRED");
  length = read (fd, data, sizeof data);
  require (length == (ssize_t) sizeof expected - 1 &&
           memcmp (data, expected, sizeof expected - 1) == 0,
           "SETTINGS_PROBE_SYNTHETIC_REQUIRED");
  close (fd);
  g_free (root);
  g_free (marker);
}

static void
changed (GSettings *instance, const gchar *key, gpointer data)
{
  (void) instance;
  (void) data;
  if (g_str_equal (key, "show-line-numbers"))
    line_notifications++;
  if (g_str_equal (key, "font-name"))
    font_notifications++;
}

static void
drain_notifications (void)
{
  gint64 deadline = g_get_monotonic_time () + G_TIME_SPAN_SECOND;
  while (g_get_monotonic_time () < deadline)
    {
      if (!g_main_context_iteration (NULL, FALSE))
        break;
    }
}

static void
expect_ordinary (GSettings *instance, gboolean lines, const gchar *font)
{
  gchar *actual = g_settings_get_string (instance, "font-name");
  require (g_settings_get_boolean (instance, "show-line-numbers") == lines &&
           g_str_equal (actual, font), "APP_SETTINGS_PERSISTENCE_UNVERIFIED");
  g_free (actual);
}

static gint
compare_keys (gconstpointer a, gconstpointer b, gpointer data)
{
  (void) data;
  return strcmp (a, b);
}

static void
free_value (gpointer value)
{
  if (value != NULL)
    g_variant_unref (value);
}

static GTree *
new_tree (void)
{
  return g_tree_new_full (compare_keys, NULL, g_free, free_value);
}

static void
insert_value (GTree *tree, const gchar *key, GVariant *value)
{
  g_tree_insert (tree, g_strdup (key), value == NULL ? NULL : g_variant_ref_sink (value));
}

static void
exercise (GSettings *writer)
{
  GSettings *observer = settings (VIEW_SCHEMA, VIEW_PATH, "font-name", G_VARIANT_TYPE_STRING);
  GTree *tree;
  GVariant *default_font;
  guint before_lines, before_font;
  gchar *initial_font;
  require_synthetic_profile ();
  require (g_settings_is_writable (writer, "font-name") &&
           g_settings_is_writable (writer, "show-line-numbers"), "APP_SETTINGS_WRITABILITY_UNVERIFIED");
  /* GSettings only promises change notification for keys read by the observer. */
  (void) g_settings_get_boolean (observer, "show-line-numbers");
  initial_font = g_settings_get_string (observer, "font-name");
  g_free (initial_font);
  g_signal_connect (observer, "changed", G_CALLBACK (changed), NULL);
  require (g_settings_set_boolean (writer, "show-line-numbers", FALSE) &&
           g_settings_set_string (writer, "font-name", "Monospace 12"),
           "APP_SETTINGS_PERSISTENCE_UNVERIFIED");
  drain_notifications ();
  before_lines = line_notifications;
  before_font = font_notifications;
  require (g_settings_set_boolean (writer, "show-line-numbers", TRUE) &&
           g_settings_set_string (writer, "font-name", PROBE_FONT),
           "APP_SETTINGS_PERSISTENCE_UNVERIFIED");
  drain_notifications ();
  require (line_notifications > before_lines && font_notifications > before_font,
           "APP_SETTINGS_NOTIFICATION_UNVERIFIED");
  expect_ordinary (observer, TRUE, PROBE_FONT);

  /* A rejected mixed tree must not even change the ordinary value in memory. */
  before_lines = line_notifications;
  before_font = font_notifications;
  tree = new_tree ();
  insert_value (tree, VIEW_PATH "show-line-numbers", g_variant_new_boolean (FALSE));
  insert_value (tree, policy[2].path, NULL);
  require (!backend_class->write_tree (backend, tree, NULL), "APP_SETTINGS_TRANSACTION_UNVERIFIED");
  g_tree_unref (tree);
  tree = new_tree ();
  insert_value (tree, VIEW_PATH "show-line-numbers", g_variant_new_boolean (FALSE));
  insert_value (tree, "/org/xfce/mousepad/preferences/window/opening-mode", g_variant_new_string ("window"));
  require (!backend_class->write_tree (backend, tree, NULL), "APP_SETTINGS_TRANSACTION_UNVERIFIED");
  g_tree_unref (tree);
  drain_notifications ();
  expect_ordinary (observer, TRUE, PROBE_FONT);
  require (line_notifications == before_lines && font_notifications == before_font,
           "APP_SETTINGS_TRANSACTION_UNVERIFIED");

  before_font = font_notifications;
  g_settings_reset (writer, "font-name");
  drain_notifications ();
  default_font = g_settings_get_default_value (writer, "font-name");
  require (default_font != NULL && font_notifications > before_font,
           "APP_SETTINGS_RESET_UNVERIFIED");
  expect_ordinary (observer, TRUE, g_variant_get_string (default_font, NULL));
  /* Keep the observed default across the delayed-write assertion. */

  /* Real delayed GSettings writes take the backend's write_tree path. */
  before_lines = line_notifications;
  before_font = font_notifications;
  g_settings_delay (writer);
  require (g_settings_set_boolean (writer, "show-line-numbers", FALSE) &&
           g_settings_set_string (writer, "font-name", "Monospace 14"),
           "APP_SETTINGS_TRANSACTION_UNVERIFIED");
  expect_ordinary (observer, TRUE, g_variant_get_string (default_font, NULL));
  g_variant_unref (default_font);
  g_settings_apply (writer);
  drain_notifications ();
  require (!g_settings_get_has_unapplied (writer) && line_notifications > before_lines &&
           font_notifications > before_font, "APP_SETTINGS_NOTIFICATION_UNVERIFIED");
  expect_ordinary (observer, FALSE, "Monospace 14");
  require (g_settings_set_boolean (writer, "show-line-numbers", TRUE) &&
           g_settings_set_string (writer, "font-name", PROBE_FONT), "APP_SETTINGS_PERSISTENCE_UNVERIFIED");
  g_settings_apply (writer);
  g_settings_sync ();
  drain_notifications ();
  expect_ordinary (observer, TRUE, PROBE_FONT);
  check_policy (TRUE);
  g_object_unref (observer);
}

int
main (int argc, char **argv)
{
  GSettings *view;
  const gchar *mode;
  require (argc == 2 && (g_str_equal (argv[1], "--check") ||
                        g_str_equal (argv[1], "--exercise") ||
                        g_str_equal (argv[1], "--verify-persistence")), "SETTINGS_PROBE_ARGUMENTS_INVALID");
  mode = argv[1] + 2;
  /* A fallback backend must never produce a passing probe receipt. */
  backend = g_settings_backend_get_default ();
  require (g_str_equal (G_OBJECT_TYPE_NAME (backend), BACKEND_TYPE), "APP_SETTINGS_BACKEND_UNVERIFIED");
  backend_class = G_SETTINGS_BACKEND_GET_CLASS (backend);
  require (backend_class->read_user_value != NULL, "APP_SETTINGS_BACKEND_UNVERIFIED");
  check_policy (FALSE);
  view = settings (VIEW_SCHEMA, VIEW_PATH, "show-line-numbers", G_VARIANT_TYPE_BOOLEAN);
  {
    GSettings *font_check = settings (VIEW_SCHEMA, VIEW_PATH, "font-name", G_VARIANT_TYPE_STRING);
    g_object_unref (font_check);
  }
  require (g_settings_is_writable (view, "font-name") &&
           g_settings_is_writable (view, "show-line-numbers"), "APP_SETTINGS_WRITABILITY_UNVERIFIED");
  if (g_str_equal (mode, "exercise"))
    exercise (view);
  else if (g_str_equal (mode, "verify-persistence"))
    expect_ordinary (view, TRUE, PROBE_FONT);
  g_object_unref (view);
  g_object_unref (backend);
  g_print ("{\"schema\":1,\"status\":\"passed\",\"mode\":\"%s\",\"backend\":\"%s\"}\n", mode, BACKEND_NAME);
  return 0;
}
