/* L7a A0: public GIO backend, selected explicitly by the trusted launcher.
 *
 * The launcher owns profile locking, recovery-data admission and module pinning.
 * This module does not recover Mousepad sessions or support concurrent writers.
 * Public API contract: gio/gsettingsbackend.h (GLib 2.80.5).
 */
#define _GNU_SOURCE
#define G_SETTINGS_ENABLE_BACKEND
#include <gio/gio.h>
#include <gio/gsettingsbackend.h>
#include <gmodule.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define BACKEND_NAME "eidolon-keyfile-v1"
#define KEYFILE_LIMIT (1024 * 1024)

typedef struct {
  GSettingsBackend parent_instance;
  GSettingsBackend *delegate;
} EidolonKeyfileV1Backend;
typedef GSettingsBackendClass EidolonKeyfileV1BackendClass;

G_DEFINE_DYNAMIC_TYPE (EidolonKeyfileV1Backend, eidolon_keyfile_v1_backend,
                       G_TYPE_SETTINGS_BACKEND)

static const struct {
  const gchar *key;
  const gchar *type;
  const gchar *text;
} policy[] = {
  { "/org/xfce/mousepad/preferences/file/session-restore", "s", "never" },
  { "/org/xfce/mousepad/preferences/file/autosave-timer", "u", NULL },
  { "/org/xfce/mousepad/preferences/window/opening-mode", "s", "tab" }
};

/* A constructor failure must not hand control back to GIO's backend fallback.
 * Do not print a profile path, file contents, or a GLib parser error here. */
static G_NORETURN void
fail_closed (void)
{
  static const char reason[] = "APP_SETTINGS_UNVERIFIED\n";
  ssize_t ignored = write (STDERR_FILENO, reason, sizeof reason - 1);
  (void) ignored;
  _exit (78);
}

static gint
policy_index (const gchar *key)
{
  guint i;
  for (i = 0; i < G_N_ELEMENTS (policy); i++)
    if (g_str_equal (key, policy[i].key))
      return (gint) i;
  return -1;
}

static gboolean
policy_reset_path (const gchar *key)
{
  guint i;
  gsize length = strlen (key);
  if (policy_index (key) >= 0)
    return TRUE;
  if (length == 0 || key[length - 1] != '/')
    return FALSE;
  for (i = 0; i < G_N_ELEMENTS (policy); i++)
    if (g_str_has_prefix (policy[i].key, key))
      return TRUE;
  return FALSE;
}

static GVariant *
policy_value (gint index, const GVariantType *expected_type)
{
  if (expected_type == NULL ||
      !g_variant_type_equal (expected_type, G_VARIANT_TYPE (policy[index].type)))
    fail_closed ();
  return g_variant_ref_sink (policy[index].text != NULL
                            ? g_variant_new_string (policy[index].text)
                            : g_variant_new_uint32 (0));
}

static void
private_directory (int fd)
{
  struct stat info;
  if (fstat (fd, &info) != 0 || !S_ISDIR (info.st_mode) ||
      info.st_uid != getuid () || (info.st_mode & 07777) != 0700)
    fail_closed ();
}

/* Reject every symlink component, including above the private profile. Sticky
 * root-owned ancestors (e.g. /tmp for A0) are allowed; the profile itself is 0700.
 * Same-UID replacement/concurrent editing is excluded by the launch contract. */
static int
open_absolute_directory (const gchar *path)
{
  gchar **parts;
  int fd;
  guint i;
  if (path == NULL || path[0] != '/' || path[1] == '\0' ||
      path[strlen (path) - 1] == '/' || strstr (path, "//") != NULL)
    fail_closed ();
  parts = g_strsplit (path + 1, "/", -1);
  fd = open ("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0)
    fail_closed ();
  for (i = 0; parts[i] != NULL; i++)
    {
      struct stat info;
      int next;
      if (g_str_equal (parts[i], ".") || g_str_equal (parts[i], ".."))
        fail_closed ();
      next = openat (fd, parts[i], O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      if (next < 0 || fstat (next, &info) != 0 ||
          (info.st_uid != 0 && info.st_uid != getuid ()) ||
          ((info.st_mode & 0022) != 0 &&
           !(info.st_uid == 0 && (info.st_mode & S_ISVTX) != 0)))
        fail_closed ();
      close (fd);
      fd = next;
    }
  g_strfreev (parts);
  return fd;
}

static int
open_private_child (int parent, const gchar *name, gboolean create)
{
  int fd;
  if (create && mkdirat (parent, name, 0700) != 0 && errno != EEXIST)
    fail_closed ();
  fd = openat (parent, name, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0)
    fail_closed ();
  private_directory (fd);
  return fd;
}

static void
validate_keyfile (int directory)
{
  struct stat before, after;
  GKeyFile *keyfile;
  gchar *data;
  gsize used = 0;
  int fd = openat (directory, "keyfile", O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  if (fd < 0)
    {
      if (errno == ENOENT)
        return;
      fail_closed ();
    }
  if (fstat (fd, &before) != 0 || !S_ISREG (before.st_mode) ||
      before.st_uid != getuid () || (before.st_mode & 07777) != 0600 ||
      before.st_nlink != 1 || before.st_size < 0 || before.st_size > KEYFILE_LIMIT)
    fail_closed ();
  data = g_malloc ((gsize) before.st_size + 1);
  while (used < (gsize) before.st_size)
    {
      ssize_t count = read (fd, data + used, (gsize) before.st_size - used);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0)
        fail_closed ();
      used += (gsize) count;
    }
  data[used] = '\0';
  if (fstat (fd, &after) != 0 || before.st_dev != after.st_dev ||
      before.st_ino != after.st_ino || before.st_size != after.st_size ||
      before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
      before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
      !g_utf8_validate (data, (gssize) used, NULL) || memchr (data, '\0', used) != NULL)
    fail_closed ();
  keyfile = g_key_file_new ();
  if (used != 0 && !g_key_file_load_from_data (keyfile, data, used, G_KEY_FILE_NONE, NULL))
    fail_closed ();
  g_key_file_unref (keyfile);
  g_free (data);
  close (fd);
}

static gchar *
profile_filename (void)
{
  const gchar *config = g_getenv ("XDG_CONFIG_HOME");
  const gchar *home = g_getenv ("HOME");
  gchar *parent, *basename, *expected_home, *filename;
  int root_fd, home_fd, config_fd, glib_fd, settings_fd;
  if (g_strcmp0 (g_getenv ("GSETTINGS_BACKEND"), BACKEND_NAME) != 0 ||
      config == NULL || home == NULL || !g_path_is_absolute (config))
    fail_closed ();
  parent = g_path_get_dirname (config);
  basename = g_path_get_basename (config);
  expected_home = g_build_filename (parent, "home", NULL);
  if (!g_str_equal (basename, "config") || !g_str_equal (home, expected_home))
    fail_closed ();
  root_fd = open_absolute_directory (parent);
  private_directory (root_fd);
  home_fd = open_private_child (root_fd, "home", FALSE);
  config_fd = open_private_child (root_fd, "config", FALSE);
  glib_fd = open_private_child (config_fd, "glib-2.0", TRUE);
  settings_fd = open_private_child (glib_fd, "settings", TRUE);
  validate_keyfile (settings_fd);
  close (settings_fd);
  close (glib_fd);
  close (config_fd);
  close (home_fd);
  close (root_fd);
  filename = g_build_filename (parent, "config", "glib-2.0", "settings", "keyfile", NULL);
  g_free (parent);
  g_free (basename);
  g_free (expected_home);
  return filename;
}

static GSettingsBackend *
inner (GSettingsBackend *backend)
{
  return ((EidolonKeyfileV1Backend *) backend)->delegate;
}

static GVariant *
backend_read (GSettingsBackend *backend, const gchar *key,
              const GVariantType *expected_type, gboolean default_value)
{
  gint index = policy_index (key);
  GSettingsBackend *delegate = inner (backend);
  if (index >= 0)
    return policy_value (index, expected_type);
  return G_SETTINGS_BACKEND_GET_CLASS (delegate)->read (delegate, key, expected_type, default_value);
}

static GVariant *
backend_read_user_value (GSettingsBackend *backend, const gchar *key,
                         const GVariantType *expected_type)
{
  gint index = policy_index (key);
  GSettingsBackend *delegate = inner (backend);
  GSettingsBackendClass *klass = G_SETTINGS_BACKEND_GET_CLASS (delegate);
  if (index >= 0)
    return policy_value (index, expected_type);
  if (klass->read_user_value != NULL)
    return klass->read_user_value (delegate, key, expected_type);
  return klass->read (delegate, key, expected_type, FALSE);
}

static gboolean
backend_get_writable (GSettingsBackend *backend, const gchar *key)
{
  GSettingsBackend *delegate = inner (backend);
  return !policy_reset_path (key) &&
         G_SETTINGS_BACKEND_GET_CLASS (delegate)->get_writable (delegate, key);
}

static gboolean
backend_write (GSettingsBackend *backend, const gchar *key,
               GVariant *value, gpointer origin_tag)
{
  GSettingsBackend *delegate = inner (backend);
  gboolean success;
  if (policy_reset_path (key))
    return FALSE;
  success = G_SETTINGS_BACKEND_GET_CLASS (delegate)->write (delegate, key, value, origin_tag);
  /* The delegate cannot notify our subscribers. Its cache can change before
   * an I/O failure returns FALSE, so report "possibly changed" in that case
   * too (gkeyfilesettingsbackend.c, GLib 2.80.5). Policy refusals stay silent. */
  g_settings_backend_changed (backend, key, origin_tag);
  return success;
}

static gboolean
contains_policy (gpointer key, gpointer value, gpointer data)
{
  gboolean *found = data;
  (void) value;
  *found = policy_reset_path (key);
  return *found;
}

static gboolean
backend_write_tree (GSettingsBackend *backend, GTree *tree, gpointer origin_tag)
{
  gboolean found = FALSE;
  gboolean success;
  GSettingsBackend *delegate = inner (backend);
  /* Validate the entire tree before calling the delegate: no partial commit. */
  g_tree_foreach (tree, contains_policy, &found);
  if (found)
    return FALSE;
  success = G_SETTINGS_BACKEND_GET_CLASS (delegate)->write_tree (delegate, tree, origin_tag);
  /* As for write(), FALSE can follow an in-memory change on I/O failure. */
  g_settings_backend_changed_tree (backend, tree, origin_tag);
  return success;
}

static void
backend_reset (GSettingsBackend *backend, const gchar *key, gpointer origin_tag)
{
  GSettingsBackend *delegate = inner (backend);
  if (!backend_get_writable (backend, key))
    return;
  G_SETTINGS_BACKEND_GET_CLASS (delegate)->reset (delegate, key, origin_tag);
  g_settings_backend_changed (backend, key, origin_tag);
}

static void
backend_subscribe (GSettingsBackend *backend, const gchar *name)
{
  GSettingsBackend *delegate = inner (backend);
  GSettingsBackendClass *klass = G_SETTINGS_BACKEND_GET_CLASS (delegate);
  if (klass->subscribe != NULL)
    klass->subscribe (delegate, name);
}

static void
backend_unsubscribe (GSettingsBackend *backend, const gchar *name)
{
  GSettingsBackend *delegate = inner (backend);
  GSettingsBackendClass *klass = G_SETTINGS_BACKEND_GET_CLASS (delegate);
  if (klass->unsubscribe != NULL)
    klass->unsubscribe (delegate, name);
}

static void
backend_sync (GSettingsBackend *backend)
{
  GSettingsBackend *delegate = inner (backend);
  GSettingsBackendClass *klass = G_SETTINGS_BACKEND_GET_CLASS (delegate);
  if (klass->sync != NULL)
    klass->sync (delegate);
}

static GPermission *
backend_get_permission (GSettingsBackend *backend, const gchar *path)
{
  GSettingsBackend *delegate = inner (backend);
  GSettingsBackendClass *klass = G_SETTINGS_BACKEND_GET_CLASS (delegate);
  if (klass->get_permission != NULL)
    return klass->get_permission (delegate, path);
  return g_simple_permission_new (TRUE);
}

static void
eidolon_keyfile_v1_backend_finalize (GObject *object)
{
  EidolonKeyfileV1Backend *self = (EidolonKeyfileV1Backend *) object;
  g_clear_object (&self->delegate);
  G_OBJECT_CLASS (eidolon_keyfile_v1_backend_parent_class)->finalize (object);
}

static void
eidolon_keyfile_v1_backend_init (EidolonKeyfileV1Backend *self)
{
  gchar *filename = profile_filename ();
  GSettingsBackendClass *klass;
  self->delegate = g_keyfile_settings_backend_new (filename, "/", NULL);
  g_free (filename);
  if (self->delegate == NULL)
    fail_closed ();
  klass = G_SETTINGS_BACKEND_GET_CLASS (self->delegate);
  if (klass->read == NULL || klass->get_writable == NULL || klass->write == NULL ||
      klass->write_tree == NULL || klass->reset == NULL)
    fail_closed ();
}

static void
eidolon_keyfile_v1_backend_class_init (EidolonKeyfileV1BackendClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  object_class->finalize = eidolon_keyfile_v1_backend_finalize;
  klass->read = backend_read;
  klass->read_user_value = backend_read_user_value;
  klass->get_writable = backend_get_writable;
  klass->write = backend_write;
  klass->write_tree = backend_write_tree;
  klass->reset = backend_reset;
  klass->subscribe = backend_subscribe;
  klass->unsubscribe = backend_unsubscribe;
  klass->sync = backend_sync;
  klass->get_permission = backend_get_permission;
}

static void
eidolon_keyfile_v1_backend_class_finalize (EidolonKeyfileV1BackendClass *klass)
{
  (void) klass;
}

G_MODULE_EXPORT void
g_io_module_load (GIOModule *module)
{
  eidolon_keyfile_v1_backend_register_type (G_TYPE_MODULE (module));
  g_io_extension_point_implement (G_SETTINGS_BACKEND_EXTENSION_POINT_NAME,
                                  eidolon_keyfile_v1_backend_get_type (),
                                  BACKEND_NAME, -100);
}

G_MODULE_EXPORT void
g_io_module_unload (GIOModule *module)
{
  (void) module;
}

G_MODULE_EXPORT gchar **
g_io_module_query (void)
{
  gchar **result = g_new0 (gchar *, 2);
  result[0] = g_strdup (G_SETTINGS_BACKEND_EXTENSION_POINT_NAME);
  return result;
}
