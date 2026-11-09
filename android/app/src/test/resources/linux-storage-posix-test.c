#include <jni.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

#define FN(name) Java_io_github_tinmachine_eidolon_HostLinuxStorageNative_##name
#define IGNORE_SELF (void)self

static void failure(JNIEnv *env, const char *operation) {
    char message[128];
    snprintf(message, sizeof(message), "%s errno=%d", operation, errno);
    jclass cls = (*env)->FindClass(env, "java/io/IOException");
    if (cls) (*env)->ThrowNew(env, cls, message);
}

static jlongArray stat_array(JNIEnv *env, const struct stat *s) {
    jlong values[] = {s->st_dev, s->st_ino, s->st_mode, s->st_uid, s->st_gid, s->st_nlink,
        s->st_size, s->st_mtim.tv_sec, s->st_mtim.tv_nsec, s->st_ctim.tv_sec, s->st_ctim.tv_nsec};
    jlongArray result = (*env)->NewLongArray(env, 11);
    if (result) (*env)->SetLongArrayRegion(env, result, 0, 11, values);
    return result;
}

JNIEXPORT jint JNICALL FN(uid)(JNIEnv *env, jobject self) {
    (void)env; IGNORE_SELF; return getuid();
}
JNIEXPORT jint JNICALL FN(gid)(JNIEnv *env, jobject self) {
    (void)env; IGNORE_SELF; return getgid();
}
JNIEXPORT jlongArray JNICALL FN(lstat)(JNIEnv *env, jobject self, jstring path) {
    IGNORE_SELF;
    const char *p = (*env)->GetStringUTFChars(env, path, NULL);
    if (!p) return NULL;
    struct stat s;
    int rc = lstat(p, &s), saved = errno;
    (*env)->ReleaseStringUTFChars(env, path, p); errno = saved;
    if (rc < 0) { if (errno != ENOENT) failure(env, "lstat"); return NULL; }
    return stat_array(env, &s);
}
JNIEXPORT jlongArray JNICALL FN(fstat)(JNIEnv *env, jobject self, jint fd) {
    IGNORE_SELF; struct stat s;
    if (fstat(fd, &s) < 0) { failure(env, "fstat"); return NULL; }
    return stat_array(env, &s);
}
JNIEXPORT void JNICALL FN(mkdir)(JNIEnv *env, jobject self, jstring path) {
    IGNORE_SELF; const char *p = (*env)->GetStringUTFChars(env, path, NULL);
    if (!p) return;
    int rc = mkdir(p, 0700), saved = errno;
    (*env)->ReleaseStringUTFChars(env, path, p); errno = saved;
    if (rc < 0) failure(env, "mkdir");
}
JNIEXPORT jint JNICALL FN(open)(JNIEnv *env, jobject self, jstring path, jint kind) {
    IGNORE_SELF; const char *p = (*env)->GetStringUTFChars(env, path, NULL);
    if (!p) return -1;
    int flags = O_NOFOLLOW | O_CLOEXEC;
    if (kind == 0) flags |= O_RDONLY | O_NONBLOCK;
    else if (kind == 1) flags |= O_WRONLY | O_CREAT | O_EXCL;
    else if (kind == 2) flags |= O_RDONLY | O_DIRECTORY;
    else { (*env)->ReleaseStringUTFChars(env, path, p); errno = EINVAL; failure(env, "open kind"); return -1; }
    int fd = open(p, flags, 0600), saved = errno;
    (*env)->ReleaseStringUTFChars(env, path, p); errno = saved;
    if (fd < 0) failure(env, "open");
    return fd;
}
JNIEXPORT void JNICALL FN(rename)(JNIEnv *env, jobject self, jstring source, jstring destination) {
    IGNORE_SELF; const char *s = (*env)->GetStringUTFChars(env, source, NULL);
    if (!s) return;
    const char *d = (*env)->GetStringUTFChars(env, destination, NULL);
    if (!d) { (*env)->ReleaseStringUTFChars(env, source, s); return; }
    int rc = rename(s, d), saved = errno;
    (*env)->ReleaseStringUTFChars(env, source, s);
    (*env)->ReleaseStringUTFChars(env, destination, d); errno = saved;
    if (rc < 0) failure(env, "rename");
}
JNIEXPORT void JNICALL FN(unlink)(JNIEnv *env, jobject self, jstring path) {
    IGNORE_SELF; const char *p = (*env)->GetStringUTFChars(env, path, NULL);
    if (!p) return;
    int rc = unlink(p), saved = errno;
    (*env)->ReleaseStringUTFChars(env, path, p); errno = saved;
    if (rc < 0) failure(env, "unlink");
}
JNIEXPORT jint JNICALL FN(read)(JNIEnv *env, jobject self, jint fd, jbyteArray bytes, jint offset, jint length) {
    IGNORE_SELF; jbyte *buffer = (*env)->GetByteArrayElements(env, bytes, NULL);
    if (!buffer) return -1;
    ssize_t rc; do { rc = read(fd, buffer + offset, length); } while (rc < 0 && errno == EINTR);
    int saved = errno;
    (*env)->ReleaseByteArrayElements(env, bytes, buffer, 0); errno = saved;
    if (rc < 0) failure(env, "read");
    return rc;
}
JNIEXPORT jint JNICALL FN(write)(JNIEnv *env, jobject self, jint fd, jbyteArray bytes, jint offset, jint length) {
    IGNORE_SELF; jbyte *buffer = (*env)->GetByteArrayElements(env, bytes, NULL);
    if (!buffer) return -1;
    ssize_t rc; do { rc = write(fd, buffer + offset, length); } while (rc < 0 && errno == EINTR);
    int saved = errno;
    (*env)->ReleaseByteArrayElements(env, bytes, buffer, JNI_ABORT); errno = saved;
    if (rc < 0) failure(env, "write");
    return rc;
}
JNIEXPORT void JNICALL FN(sync)(JNIEnv *env, jobject self, jint fd) {
    IGNORE_SELF; if (fsync(fd) < 0) failure(env, "fsync");
}
JNIEXPORT void JNICALL FN(close)(JNIEnv *env, jobject self, jint fd) {
    IGNORE_SELF; if (close(fd) < 0) failure(env, "close");
}
