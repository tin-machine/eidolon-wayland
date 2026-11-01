# Eidolon Wayland

> Linuxに肉体を残し、その姿だけを異界へ投影する。

**Status: design / early research — not yet implemented.**

Meta Quest 3向けの実験的な空間Waylandデスクトップ。QuestのAndroidアプリにWayland compositorを組み込み、**同じQuestのTermuxへlocalhost SSHで接続**し、**Waypipe**で転送した既存Waylandアプリを**Meta Spatial SDKの独立したXRパネル**として表示することを目指します。

最初の目標は、Termux上のfootをQuest空間に1枚のパネルとして表示し、Bluetoothキーボードとコントローラーで操作することです。Linux PC、WiVRn、root化を必須としない設計ですが、Android側のWaypipe/Wayland server移植は現時点で**未検証**です。

## Docs

- [アーキテクチャ設計](docs/architecture.md) — 通信経路、SSH/Waypipe、組み込みcompositor、Android Surface/描画、入力、セキュリティ
- [実装計画](docs/implementation-plan.md) — 技術検証ゲート、段階的な実装、受け入れ条件、テスト、初期ADR
- [Phase 0 — Ubuntu開発PC・Quest USB接続・Termux/Codexの調査runbook](docs/phase-0-development-environment.md) — APKビルド、ADB/SSH、Codexの認証とGitHub連携

開発を開始する際は、まず[実装計画のP0スパイク](docs/implementation-plan.md#最初に潰すべき不確実性スパイク)から進めてください。
