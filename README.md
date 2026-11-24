# Eidolon Wayland

**Languages:** English | [日本語](README.ja.md)

> Keep Linux where it runs. Bring its windows into another world.

<p align="center">
  <img src="docs/assets/readme/10-home-lab.webp" alt="Concept illustration of Linux application windows floating in a Quest 3 mixed-reality workspace, with a person interacting with them" width="100%" />
</p>
<p align="center"><sub>Concept illustration — not an actual headset screenshot.</sub></p>

**Eidolon Wayland** is an **experimental open-source spatial desktop** aiming to let you place and interact with individual Linux application windows in **Meta Quest 3 mixed reality**.

Instead of mirroring a conventional desktop as one large screen, Eidolon aims to bring terminals and GUI applications into a spatial workspace, arranged in front of you, to the sides, and at different depths.

## The experience we're building

- **A workspace beyond the screen** — Arrange multiple application windows in space; move, resize, and group them.
- **Use existing Linux applications** — Run Wayland applications in Termux on the Quest or on other Linux hosts, and forward their windows to the headset.
- **Interaction designed for MR** — Combine passthrough with keyboard, pointer, and XR input.
- **A workspace spanning hosts** — Eventually choose and launch applications from multiple Linux hosts in the same space.

## How it works

Eidolon embeds a **Rust-based Wayland compositor** in an Android application running on Quest. Linux applications continue to run where they are hosted; **SSH and Waypipe** connect them to the compositor, and the **Meta Spatial SDK** displays their windows in XR space.

```text
Termux on Quest / Remote Linux host
       └─ Wayland app (foot, GTK, etc.)
                    │
              SSH + Waypipe
                    │
                    ▼
       Eidolon (Quest 3 / Android)
         Rust / Smithay / EGL / GLES
                    │ JNI
         Kotlin / Meta Spatial SDK
                    │
                    ▼
            Windows in MR space
```

The core technology stack is **Kotlin, Rust, Smithay, EGL/OpenGL ES, SSH, and Waypipe**. The focus is on individual application windows rather than mirroring an entire desktop.

## Project status

**This project is under active development and validation.** Displaying terminals from Termux on Quest and manipulating multiple windows have implementation and test history. Using arbitrary Linux GUI applications concurrently across multiple hosts remains a development goal, **not a completed feature**.

The illustration above represents the vision; it is not a screenshot and does not guarantee current functionality or behavior on a headset.

For detailed implementation status, CI results, and device validation records, see the [documentation index](docs/overview/README.md) and [progress and validation records](docs/evidence/progress.md). Most detailed technical documentation is currently written in Japanese.

## Getting started and development

- [Usage and initial setup](docs/guides/usage.md)
- [Architecture](docs/architecture/architecture.md)
- [Development and debugging](docs/development/development-automation-and-debugging.md)
- [Design and feature documentation index](docs/overview/README.md)

## License

Code and documentation owned by the Eidolon Wayland project are published under the [MIT License](LICENSE). Third-party libraries and assets, including the Meta Spatial SDK, are covered by their own licenses. MIT does **not** automatically permit redistribution of APKs or bundled third-party SDKs. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for details.
