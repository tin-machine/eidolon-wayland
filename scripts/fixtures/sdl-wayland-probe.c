/* NX11: SDL software rendererの小窓probe。SHM判定は外側のprotocol観測で行う。 */
#include <SDL.h>
#include <dirent.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile sig_atomic_t interrupted = 0;
static void stop(int value) { (void)value; interrupted = 1; }

static int fd_count(void)
{
    DIR *directory = opendir("/proc/self/fd");
    if (directory == NULL) return -1;
    int count = 0;
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL)
        if (entry->d_name[0] != '.') ++count;
    closedir(directory);
    return count - 1; /* この観測用directory自身は数えない。 */
}

static void json_text(const char *text)
{
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 32 || *p >= 127) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

int main(int argc, char **argv)
{
    unsigned requested = 120;
    int synthetic = 0;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--frames") && i + 1 < argc) {
            char *end;
            unsigned long value = strtoul(argv[++i], &end, 10);
            if (*end || value < 2 || value > 600) return 64;
            requested = (unsigned)value;
        } else if (!strcmp(argv[i], "--inject-events")) synthetic = 1;
        else return 64;
    }
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    const int before = fd_count();
    SDL_version compiled, linked;
    SDL_VERSION(&compiled);
    SDL_GetVersion(&linked);
    SDL_Window *window = NULL;
    SDL_Renderer *renderer = NULL;
    char video[64] = "", render[64] = "", error[384] = "";
    const char *stage = "init", *reason = "error";
    unsigned frames = 0, pixel_checks = 0, down = 0, up = 0, focus_lost = 0, closes = 0;
    Uint32 flags = 0;
    int result = 1;
    if (SDL_Init(SDL_INIT_VIDEO) != 0) goto done;
    SDL_StopTextInput();
    const char *driver = SDL_GetCurrentVideoDriver();
    if (driver != NULL) snprintf(video, sizeof(video), "%s", driver);
    /* 音声、全画面、相対マウス、text inputは開始しない。 */
    stage = "window";
    window = SDL_CreateWindow("Eidolon SDL probe", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED,
                              640, 400, SDL_WINDOW_SHOWN);
    if (window == NULL) goto done;
    SDL_SetWindowGrab(window, SDL_FALSE);
    stage = "renderer";
    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (renderer == NULL) goto done;
    SDL_RendererInfo info;
    if (SDL_GetRendererInfo(renderer, &info) != 0) goto done;
    snprintf(render, sizeof(render), "%s", info.name);
    flags = info.flags;
    if (!(flags & SDL_RENDERER_SOFTWARE)) {
        SDL_SetError("software renderer was not selected");
        goto done;
    }
    stage = "render";
    reason = "frames";
    while (frames < requested && !interrupted) {
        if (synthetic && frames == 1) {
            SDL_Event event;
            SDL_zero(event);
            event.type = SDL_KEYDOWN;
            event.key.keysym.sym = SDLK_a;
            event.key.windowID = SDL_GetWindowID(window);
            if (SDL_PushEvent(&event) != 1) goto done;
            event.type = SDL_KEYUP;
            if (SDL_PushEvent(&event) != 1) goto done;
        }
        SDL_Event event;
        int quit = 0;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_KEYDOWN) {
                ++down;
                if (event.key.keysym.sym == SDLK_ESCAPE) { reason = "escape"; quit = 1; }
            } else if (event.type == SDL_KEYUP) ++up;
            else if (event.type == SDL_QUIT) { ++closes; reason = "window_close"; quit = 1; }
            else if (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_CLOSE) { ++closes; reason = "window_close"; quit = 1; }
            else if (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_FOCUS_LOST) ++focus_lost;
        }
        if (quit) break;
        const Uint8 shift = (Uint8)(frames % 128);
        if (SDL_SetRenderDrawColor(renderer, (Uint8)(30 + shift), 32, (Uint8)(200 - shift), 255) != 0 ||
            SDL_RenderClear(renderer) != 0) goto done;
        for (int corner = 0; corner < 4; ++corner) {
            SDL_Rect rectangle = {40 + (corner % 2) * 320, 30 + (corner / 2) * 200, 240, 140};
            if (SDL_SetRenderDrawColor(renderer, corner & 1 ? 255 : 40, corner & 2 ? 230 : 60,
                                      (Uint8)(50 + shift), 255) != 0 ||
                SDL_RenderFillRect(renderer, &rectangle) != 0) goto done;
        }
        SDL_Rect sample = {10, 10, 1, 1};
        Uint32 pixel = 0;
        if (SDL_RenderReadPixels(renderer, &sample, SDL_PIXELFORMAT_ARGB8888, &pixel, sizeof(pixel)) != 0) goto done;
        Uint32 expected = 0xff000000u | ((Uint32)(30 + shift) << 16) | (32u << 8) | (Uint32)(200 - shift);
        if (pixel != expected) { SDL_SetError("PIXEL_MISMATCH"); goto done; }
        ++pixel_checks;
        SDL_RenderPresent(renderer);
        ++frames;
        SDL_Delay(16);
    }
    if (interrupted) reason = "signal";
    stage = "complete";
    result = 0;
done:
    if (result != 0) { reason = "error"; snprintf(error, sizeof(error), "%s", SDL_GetError()); }
    if (renderer != NULL) SDL_DestroyRenderer(renderer);
    if (window != NULL) SDL_DestroyWindow(window);
    SDL_Quit();
    const int after = fd_count();
    printf("{\"schema\":1,\"probe\":\"eidolon-sdl-v1\",\"completed\":%s,\"stage\":", result ? "false" : "true");
    json_text(stage);
    printf(",\"exit_reason\":"); json_text(reason);
    printf(",\"video_driver\":"); json_text(video);
    printf(",\"renderer\":"); json_text(render);
    printf(",\"renderer_flags\":%u,\"compiled_sdl\":\"%u.%u.%u\",\"linked_sdl\":\"%u.%u.%u\","
           "\"requested_frames\":%u,\"frames\":%u,\"pixel_checks\":%u,\"key_down\":%u,\"key_up\":%u,"
           "\"focus_lost\":%u,\"close_events\":%u,\"synthetic_events\":%s,"
           "\"fd_before\":%d,\"fd_after\":%d,\"error\":",
           flags, compiled.major, compiled.minor, compiled.patch, linked.major, linked.minor, linked.patch,
           requested, frames, pixel_checks, down, up, focus_lost, closes, synthetic ? "true" : "false", before, after);
    json_text(error);
    puts("}");
    return result;
}
